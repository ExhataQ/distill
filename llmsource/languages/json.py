"""
JSON language adapter: compress(source) -> (llm_text, stats) and
expand(llm_text) -> source. Deliberately parallel in structure to
languages/css.py -- same macro-definitions-header + `@Xnnn;` placeholder
representation, same economic macro selection, same span-tracked
(not regex-over-text) expand() -- but adapted to JSON's shape:

  - CSS has many top-level rules, each with its own flat declaration
    list; a JSON document has exactly ONE root container (object or
    array), so there is only one "rule" to find macro candidates within:
    contiguous spans of the root's direct members/elements.
  - Macro decompression is still pure literal-text substitution, so it is
    just as safe here as for CSS: if two spans are byte-identical,
    replacing both with the same `@Xnnn;` reference and expanding them
    back is meaning-preserving regardless of what JSON values they are.
  - The compressed `.llm` representation is intentionally NOT required to
    stay valid JSON (same choice CSS made: its `.llm` isn't valid CSS
    either) -- `@Xnnn;` sits in a member's value position as an opaque
    token our own expand() understands, not something a JSON parser needs
    to accept.
  - This only catches EXACT duplicate spans (same limitation as CSS). It
    will NOT compress "same shape, different values" -- e.g. an array of
    otherwise-identical-looking records with different field values. That
    is a genuinely different technique (a columnar/tabular re-encoding),
    deliberately out of scope here; see the project's architecture notes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..jsonparse import JSONDocument, JSONItem, JSONScanError, parse_document
from ..tokenizer import TokenCounter, DEFAULT_TOKENIZER, estimate_tokens
from .base import LanguageAdapter

MACROS_MARKER = "/*LLM:MACROS*/"
SOURCE_MARKER = "/*LLM:SOURCE*/"
MAX_SPAN = 8
MIN_FREQUENCY = 2
MIN_SAVINGS_TOKENS = 4
DEF_OVERHEAD_TOKENS = 4

_MACRO_ID_RE = re.compile(r"^X(\d+)$")
_MACRO_BLOCK_RE = re.compile(r"@(?P<id>X\d+) \{\n(?P<val>.*?)\n\}\n", re.DOTALL)
_MACRO_REF_RE = re.compile(r"@(X\d+);")


def _next_id(used_numbers: set[int]) -> str:
    n = 1
    while n in used_numbers:
        n += 1
    return f"X{n:03d}"


def _span_text(items: list[JSONItem], start: int, end: int) -> str:
    parts = []
    for k in range(start, end):
        if k > start:
            parts.append(items[k].prefix)
        parts.append(items[k].text)
    return "".join(parts)


def _select_macros(items: list[JSONItem], tokenizer: TokenCounter,
                    previous_macros: dict | None = None):
    """Same algorithm as css.py's _select_macros, specialized to a single
    container's item list (a JSON document has exactly one root
    container, unlike a stylesheet's many rules)."""
    groups: dict[str, list[tuple[int, int]]] = {}
    n = len(items)
    for span_len in range(1, min(MAX_SPAN, n) + 1):
        for start in range(0, n - span_len + 1):
            end = start + span_len
            text = _span_text(items, start, end)
            groups.setdefault(text, []).append((start, end))

    candidates = []
    for text, occ in groups.items():
        if len(occ) < MIN_FREQUENCY:
            continue
        span_len_items = occ[0][1] - occ[0][0]
        candidates.append((span_len_items, text, occ))

    candidates.sort(key=lambda c: (-c[0], -len(c[2])))

    claimed: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        for (s, e) in claimed:
            if start < e and s < end:
                return True
        return False

    assignments: list[tuple[int, int, str]] = []
    macro_defs: dict[str, str] = {}
    used_numbers: set[int] = set()
    if previous_macros:
        for mid in previous_macros:
            m = _MACRO_ID_RE.match(mid)
            if m:
                used_numbers.add(int(m.group(1)))
    prev_by_value = {}
    if previous_macros:
        for mid, info in previous_macros.items():
            prev_by_value[info.get("value")] = mid

    for span_len_items, text, occ in candidates:
        # Same self-overlap fix as css.py's _select_macros: filter out
        # occurrences overlapping a PREVIOUS candidate's claims, and
        # occurrences overlapping an earlier occurrence of THIS candidate
        # (e.g. 3+ identical consecutive array elements produce
        # self-overlapping span_len=2 candidates).
        free_occ: list[tuple[int, int]] = []
        locally_claimed: list[tuple[int, int]] = []
        for (s, e) in occ:
            if overlaps(s, e):
                continue
            if any(s < le and ls < e for (ls, le) in locally_claimed):
                continue
            free_occ.append((s, e))
            locally_claimed.append((s, e))
        if len(free_occ) < MIN_FREQUENCY:
            continue

        def_tokens = estimate_tokens(text, tokenizer) + DEF_OVERHEAD_TOKENS
        ref_tokens = estimate_tokens("@X000;", tokenizer)
        occ_tokens = estimate_tokens(text, tokenizer)
        original_total = occ_tokens * len(free_occ)
        compressed_total = def_tokens + ref_tokens * len(free_occ)
        savings = original_total - compressed_total
        if savings < MIN_SAVINGS_TOKENS:
            continue

        macro_id = prev_by_value.get(text)
        if macro_id is None or _MACRO_ID_RE.match(macro_id) is None:
            macro_id = _next_id(used_numbers)
        m = _MACRO_ID_RE.match(macro_id)
        if m:
            used_numbers.add(int(m.group(1)))

        macro_defs[macro_id] = text
        for (s, e) in free_occ:
            claimed.append((s, e))
            assignments.append((s, e, macro_id))

    assignments.sort(key=lambda t: t[0])
    return assignments, macro_defs


def _render_with_macros(items: list[JSONItem], spans: list[tuple[int, int, str]]) -> str:
    n = len(items)
    out = []
    i = 0
    span_iter = iter(spans)
    next_span = next(span_iter, None)
    while i < n:
        if next_span is not None and next_span[0] == i:
            start, end, macro_id = next_span
            out.append(items[start].prefix)
            out.append(f"@{macro_id};")
            i = end
            next_span = next(span_iter, None)
            continue
        out.append(items[i].prefix)
        out.append(items[i].text)
        i += 1
    return "".join(out)


def compress(source: str, tokenizer: TokenCounter | None = None,
             previous_macros: dict | None = None) -> tuple[str, dict]:
    tokenizer = tokenizer or DEFAULT_TOKENIZER
    doc = parse_document(source)

    assignments, macro_defs = _select_macros(doc.items, tokenizer, previous_macros)

    body_inner = _render_with_macros(doc.items, assignments)
    body = doc.open_wrap + body_inner + doc.close_wrap

    defs_section = []
    for mid in sorted(macro_defs, key=lambda x: int(_MACRO_ID_RE.match(x).group(1))):
        defs_section.append(f"@{mid} {{\n{macro_defs[mid]}\n}}\n")

    llm_text = (
        MACROS_MARKER + "\n" +
        "".join(defs_section) +
        SOURCE_MARKER + "\n" +
        body
    )

    original_tokens = estimate_tokens(source, tokenizer)
    compressed_tokens = estimate_tokens(llm_text, tokenizer)
    stats = {
        "tokenizer": tokenizer.name,
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
        "saved_tokens": original_tokens - compressed_tokens,
        "reduction_pct": round(100 * (original_tokens - compressed_tokens) / original_tokens, 2) if original_tokens else 0.0,
        "macro_count": len(macro_defs),
        "macro_references": len(assignments),
        "item_count": len(doc.items),
        "root_kind": doc.root_kind,
    }
    macros_manifest = {mid: {"value": val, "references": sum(
        1 for (_, _, m) in assignments if m == mid
    )} for mid, val in macro_defs.items()}

    return llm_text, {"stats": stats, "macros": macros_manifest}


class ExpandError(Exception):
    pass


def _expand_body(body: str, macro_map: dict[str, str]) -> str:
    """Substitute `@Xnnn;` macro references with their definitions, but
    only at positions NOT inside a JSON string (JSON has no comments, so
    this only needs to be string-aware, not comment-aware like CSS's
    equivalent). This is the same fix applied to css.py's expand(): naive
    regex substitution over the whole flattened body would misfire on a
    literal `@X001;`-shaped substring inside a real string VALUE -- e.g.
    `"note": "see @X001;"` -- which is exactly the kind of free-form text
    JSON string values commonly contain."""
    n = len(body)
    i = 0
    out: list[str] = []
    while i < n:
        ch = body[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if body[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if body[j] == '"':
                    j += 1
                    break
                j += 1
            else:
                out.append(body[i:])
                break
            out.append(body[i:j])
            i = j
            continue

        if ch == "@":
            m = _MACRO_REF_RE.match(body, i)
            if m:
                mid = m.group(1)
                if mid not in macro_map:
                    raise ExpandError(f"dangling macro reference @{mid}; with no matching definition")
                out.append(macro_map[mid])
                i = m.end()
                continue

        out.append(ch)
        i += 1

    return "".join(out)


def expand(llm_text: str) -> str:
    if not llm_text.startswith(MACROS_MARKER + "\n"):
        raise ExpandError("missing required LLMSOURCE markers; representation looks corrupted")

    cursor = len(MACROS_MARKER) + 1
    macro_map: dict[str, str] = {}
    while not llm_text.startswith(SOURCE_MARKER, cursor):
        m = _MACRO_BLOCK_RE.match(llm_text, cursor)
        if not m:
            raise ExpandError("missing required LLMSOURCE markers; representation looks corrupted")
        macro_map[m.group("id")] = m.group("val")
        cursor = m.end()

    cursor += len(SOURCE_MARKER)
    if llm_text.startswith("\n", cursor):
        cursor += 1
    body = llm_text[cursor:]

    return _expand_body(body, macro_map)


def minify_item(item_text: str) -> str:
    """Collapse whitespace in one item's raw text down to the minimum
    JSON needs -- which, unlike CSS, is nothing: JSON whitespace outside
    of strings is 100% semantically insignificant, so this can be far
    more aggressive than CSS's minify_css while remaining completely
    safe. Only whitespace inside string values (which may be meaningful
    data) is left untouched."""
    n = len(item_text)
    i = 0
    out: list[str] = []
    while i < n:
        ch = item_text[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if item_text[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if item_text[j] == '"':
                    j += 1
                    break
                j += 1
            else:
                out.append(item_text[i:])
                break
            out.append(item_text[i:j])
            i = j
            continue
        if ch in " \t\r\n\f":
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_to_items(source: str) -> list[str]:
    doc = parse_document(source)
    items = [it.prefix + it.text for it in doc.items]
    if items:
        items[0] = doc.open_wrap + items[0]
        items[-1] = items[-1] + doc.close_wrap
    else:
        items = [doc.open_wrap + doc.close_wrap]
    return items


ADAPTER = LanguageAdapter(
    name="json",
    compress=compress,
    expand=expand,
    parse_to_items=_parse_to_items,
    minify_item=minify_item,
    extra_validate=None,
)
