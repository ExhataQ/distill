"""
CSS language adapter: compress(source) -> (llm_text, stats) and
expand(llm_text) -> source.

Compression strategy (spec sections 3-5):
  1. Parse the stylesheet into top-level items. Only `SimpleRule` items
     (flat declaration lists, no nesting/at-rules) are eligible.
  2. Enumerate candidate contiguous declaration spans (n-grams) within each
     rule's declaration list, for span lengths 1..MAX_SPAN.
  3. Group candidates by their *exact raw text* (conservative: no
     whitespace normalization). A candidate is only interesting if it
     recurs (frequency >= 2).
  4. Economically score each group: does replacing every occurrence with a
     `@Xnn;` reference actually save tokens once the one-time macro
     definition is paid for? Reject anything that doesn't clear a minimum
     savings bar.
  5. Greedily accept the highest-savings groups first, claiming declaration
     index ranges per rule so macros never overlap.
  6. Render: a macro-definitions section (human readable) followed by the
     stylesheet with claimed spans replaced by `@Xnn;` placeholders. All
     text outside a replaced span is untouched raw source, so this is a
     pure substring substitution -- decompression is the exact inverse and
     needs no external state at all (see expand()).

This intentionally treats "which declarations are grouped together" as a
straightforward frequent-substring problem rather than trying to find a
global optimum -- see spec section 3/28: the goal is meaningful,
explainable savings, not a proof of optimality.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..cssparse import Item, SimpleRule, parse_stylesheet, reconstruct
from ..tokenizer import TokenCounter, DEFAULT_TOKENIZER, estimate_tokens

MACROS_MARKER = "/*LLM:MACROS*/"
SOURCE_MARKER = "/*LLM:SOURCE*/"
MAX_SPAN = 8
MIN_FREQUENCY = 2
MIN_SAVINGS_TOKENS = 4
DEF_OVERHEAD_TOKENS = 4  # rough cost of the "@Xnn {" / "}" wrapper in the defs section

_MACRO_ID_RE = re.compile(r"^X(\d+)$")
_MACRO_BLOCK_RE = re.compile(r"@(?P<id>X\d+) \{\n(?P<val>.*?)\n\}\n", re.DOTALL)
_MACRO_REF_RE = re.compile(r"@(X\d+);")


def _next_id(used_numbers: set[int]) -> str:
    n = 1
    while n in used_numbers:
        n += 1
    return f"X{n:03d}"


@dataclass
class MacroCandidate:
    span_text: str
    occurrences: list[tuple[int, int, int]]  # (rule_index_in_simple_rules, start_decl, end_decl)


def _span_text(rule: SimpleRule, start: int, end: int) -> str:
    parts = []
    for k in range(start, end):
        if k > start:
            parts.append(rule.decls[k].prefix)
        parts.append(rule.decls[k].text)
    return "".join(parts)


def _select_macros(simple_rules: list[SimpleRule], tokenizer: TokenCounter,
                    previous_macros: dict | None = None):
    """Returns (assignments, macro_defs) where:
      assignments: dict[rule_index] -> list of (start, end, macro_id) non-overlapping, sorted
      macro_defs: dict[macro_id] -> span_text
    """
    # 1. enumerate all candidate spans grouped by exact text
    groups: dict[str, list[tuple[int, int, int]]] = {}
    for ri, rule in enumerate(simple_rules):
        n = len(rule.decls)
        for span_len in range(1, min(MAX_SPAN, n) + 1):
            for start in range(0, n - span_len + 1):
                end = start + span_len
                text = _span_text(rule, start, end)
                groups.setdefault(text, []).append((ri, start, end))

    # 2. score candidates (only those with the same text appearing >=2 times
    #    anywhere, and prefer longer spans first so we greedily claim big
    #    reusable chunks before smaller ones fight over the leftovers)
    candidates = []
    for text, occ in groups.items():
        if len(occ) < MIN_FREQUENCY:
            continue
        span_len_decls = occ[0][2] - occ[0][1]
        candidates.append((span_len_decls, text, occ))

    # longer spans first, then by raw occurrence count (more reuse first)
    candidates.sort(key=lambda c: (-c[0], -len(c[2])))

    claimed: dict[int, list[tuple[int, int]]] = {}  # rule_index -> list of (start,end) claimed

    def overlaps(ri: int, start: int, end: int) -> bool:
        for (s, e) in claimed.get(ri, []):
            if start < e and s < end:
                return True
        return False

    assignments: dict[int, list[tuple[int, int, str]]] = {}
    macro_defs: dict[str, str] = {}
    used_numbers: set[int] = set()
    if previous_macros:
        for mid in previous_macros:
            m = _MACRO_ID_RE.match(mid)
            if m:
                used_numbers.add(int(m.group(1)))
    # reverse lookup for stable IDs: previous span_text -> id
    prev_by_value = {}
    if previous_macros:
        for mid, info in previous_macros.items():
            prev_by_value[info.get("value")] = mid

    for span_len_decls, text, occ in candidates:
        free_occ = [(ri, s, e) for (ri, s, e) in occ if not overlaps(ri, s, e)]
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

        # accept: claim occurrences, assign (reuse) macro id
        macro_id = prev_by_value.get(text)
        if macro_id is None or _MACRO_ID_RE.match(macro_id) is None:
            macro_id = _next_id(used_numbers)
        m = _MACRO_ID_RE.match(macro_id)
        if m:
            used_numbers.add(int(m.group(1)))

        macro_defs[macro_id] = text
        for (ri, s, e) in free_occ:
            claimed.setdefault(ri, []).append((s, e))
            assignments.setdefault(ri, []).append((s, e, macro_id))

    for ri in assignments:
        assignments[ri].sort(key=lambda t: t[0])

    return assignments, macro_defs


def _render_rule_with_macros(rule: SimpleRule, spans: list[tuple[int, int, str]]) -> str:
    n = len(rule.decls)
    out = []
    i = 0
    span_iter = iter(sorted(spans, key=lambda t: t[0]))
    next_span = next(span_iter, None)
    while i < n:
        if next_span is not None and next_span[0] == i:
            start, end, macro_id = next_span
            out.append(rule.decls[start].prefix)
            out.append(f"@{macro_id};")
            i = end
            next_span = next(span_iter, None)
            continue
        out.append(rule.decls[i].prefix)
        out.append(rule.decls[i].text)
        i += 1
    out.append(rule.trailing)
    return rule.prelude + "{" + "".join(out) + "}"


def compress(source: str, tokenizer: TokenCounter | None = None,
             previous_macros: dict | None = None) -> tuple[str, dict]:
    tokenizer = tokenizer or DEFAULT_TOKENIZER
    items = parse_stylesheet(source)

    simple_rule_items = [(idx, it) for idx, it in enumerate(items) if isinstance(it, SimpleRule)]
    simple_rules = [it for _, it in simple_rule_items]

    assignments, macro_defs = _select_macros(simple_rules, tokenizer, previous_macros)

    rendered_items = list(items)
    for local_ri, (item_idx, rule) in enumerate(simple_rule_items):
        spans = assignments.get(local_ri, [])
        if spans:
            new_text = _render_rule_with_macros(rule, spans)
            rendered_items[item_idx] = _RawText(new_text)
        # else: leave the SimpleRule item as-is; reconstruct() handles it

    body = "".join(_raw(it) for it in rendered_items)

    defs_section = []
    for mid in sorted(macro_defs, key=lambda x: int(_MACRO_ID_RE.match(x).group(1))):
        defs_section.append(f"@{mid} {{\n{macro_defs[mid]}\n}}\n")

    llm_text = (
        MACROS_MARKER + "\n" +
        "".join(defs_section) +
        SOURCE_MARKER + "\n" +
        body
    )

    ref_count = sum(len(v) for v in assignments.values())
    original_tokens = estimate_tokens(source, tokenizer)
    compressed_tokens = estimate_tokens(llm_text, tokenizer)
    stats = {
        "tokenizer": tokenizer.name,
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
        "saved_tokens": original_tokens - compressed_tokens,
        "reduction_pct": round(100 * (original_tokens - compressed_tokens) / original_tokens, 2) if original_tokens else 0.0,
        "macro_count": len(macro_defs),
        "macro_references": ref_count,
        "simple_rule_count": len(simple_rules),
        "opaque_item_count": len(items) - len(simple_rules) - sum(1 for it in items if type(it).__name__ == "RawItem"),
    }
    macros_manifest = {mid: {"value": val, "references": sum(
        1 for spans in assignments.values() for (_, _, m) in spans if m == mid
    )} for mid, val in macro_defs.items()}

    return llm_text, {"stats": stats, "macros": macros_manifest}


class _RawText:
    """Adapter so compressed SimpleRule text can sit in the same item list
    as parser Items for reconstruction purposes only."""
    def __init__(self, text: str):
        self.raw = text


def _raw(item) -> str:
    if isinstance(item, _RawText):
        return item.raw
    from ..cssparse import item_raw
    return item_raw(item)


class ExpandError(Exception):
    pass


def expand(llm_text: str) -> str:
    if MACROS_MARKER not in llm_text or SOURCE_MARKER not in llm_text:
        raise ExpandError("missing required LLMSOURCE markers; representation looks corrupted")

    _, rest = llm_text.split(MACROS_MARKER, 1)
    defs_section, body = rest.split(SOURCE_MARKER, 1)
    if body.startswith("\n"):
        body = body[1:]

    macro_map: dict[str, str] = {}
    for m in _MACRO_BLOCK_RE.finditer(defs_section):
        macro_map[m.group("id")] = m.group("val")

    def _replace(m: re.Match) -> str:
        mid = m.group(1)
        if mid not in macro_map:
            raise ExpandError(f"dangling macro reference @{mid}; with no matching definition")
        return macro_map[mid]

    expanded = _MACRO_REF_RE.sub(_replace, body)
    return expanded
