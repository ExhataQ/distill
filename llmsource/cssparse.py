"""
Structural CSS parsing on top of scanner.py.

Design philosophy (see project spec): we are NOT trying to be a full CSS
parser. We only need to identify regions we are fully confident about
(simple selector rules containing a flat list of declarations) so we can
offer them up for macro compression. Everything else -- @media, @keyframes,
@font-face, nested rules, malformed CSS, anything we're not 100% sure about
-- is preserved as opaque raw text, byte-for-byte, untouched.

Top-level stylesheet items:
  RawItem     -- passthrough text (whitespace, comments, @import/@charset
                 statements, or anything between rules)
  OpaqueBlock -- an at-rule with a `{ ... }` block (@media, @keyframes,
                 @font-face, @supports, etc). Preserved verbatim, never
                 descended into, never compressed. Safe by construction.
  SimpleRule  -- `selector { declarations }` where the block is a flat,
                 unambiguous list of `prop: value;` declarations with no
                 nested braces. Eligible for compression.
  OpaqueRule  -- `selector { ... }` where the block didn't parse as a flat
                 declaration list (nested rules / CSS nesting / anything
                 unexpected). Preserved verbatim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .scanner import ScanError, match_brace, split_top_level

_DECL_RE = re.compile(r"^[A-Za-z_\-\*][A-Za-z0-9_\-]*\s*:\s*.+$", re.DOTALL)


@dataclass
class DeclUnit:
    """One declaration inside a simple rule, plus the raw separator text
    that preceded it (whitespace/comments since the previous unit, or
    since the opening '{')."""
    prefix: str  # raw text before this declaration (whitespace/comments)
    text: str    # raw declaration text, e.g. "display: flex;" (trailing ';' included if present)
    prop: str    # lowercased property name, for matching/reporting only


@dataclass
class SimpleRule:
    prelude: str  # raw selector text, exactly as written, including trailing whitespace up to '{'
    decls: list[DeclUnit]
    trailing: str  # raw text after the last declaration, before '}'
    start: int
    end: int  # index just past the closing '}'

    def block_inner_raw(self) -> str:
        out = []
        for d in self.decls:
            out.append(d.prefix)
            out.append(d.text)
        out.append(self.trailing)
        return "".join(out)


@dataclass
class OpaqueRule:
    raw: str
    start: int
    end: int


@dataclass
class OpaqueBlock:
    raw: str
    start: int
    end: int


@dataclass
class RawItem:
    raw: str
    start: int
    end: int


Item = "RawItem | OpaqueBlock | SimpleRule | OpaqueRule"


def parse_declarations(block_inner: str) -> list[DeclUnit] | None:
    """Try to parse a rule's block interior as a flat list of declarations.
    Returns None (do NOT compress, treat as opaque) if anything is
    ambiguous: a nested brace, a segment that doesn't look like
    `prop: value`, or a scan error.
    """
    try:
        spans = split_top_level(block_inner, sep=";")
    except ScanError:
        return None

    decls: list[DeclUnit] = []
    cursor = 0
    for (s, e) in spans:
        chunk = block_inner[s:e]
        stripped = chunk.strip()
        if stripped == "":
            # Pure whitespace/comment-only trailing chunk (e.g. after the
            # last ';', before '}'). Attach it as prefix of nothing --
            # handled by caller via `trailing`.
            continue
        # Find where the actual declaration text starts within chunk
        # (skip leading whitespace/comments) so we can split prefix vs text.
        lead_len = len(chunk) - len(chunk.lstrip())
        prefix = chunk[:lead_len]
        text = chunk[lead_len:]
        if not _DECL_RE.match(stripped):
            return None
        prop_match = re.match(r"^([A-Za-z_\-\*][A-Za-z0-9_\-]*)\s*:", stripped)
        prop = prop_match.group(1).lower() if prop_match else ""
        decls.append(DeclUnit(prefix=prefix, text=text, prop=prop))
        cursor = e

    return decls


def _rebuild_trailing(block_inner: str, decls: list[DeclUnit]) -> str:
    consumed = sum(len(d.prefix) + len(d.text) for d in decls)
    return block_inner[consumed:]


def parse_stylesheet(source: str) -> list[Item]:
    """Parse a full stylesheet into a flat ordered list of top-level items.
    Concatenating the raw text of every item in order reproduces `source`
    exactly (this is the core safety invariant, checked by tests).
    """
    items: list[Item] = []
    n = len(source)
    i = 0
    raw_start = 0

    while i < n:
        ch = source[i]

        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            end = source.find("*/", i + 2)
            if end == -1:
                # Unterminated comment: bail, treat rest as raw.
                i = n
                continue
            i = end + 2
            continue

        if ch in ("'", '"'):
            quote = ch
            j = i + 1
            while j < n:
                if source[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if source[j] == quote:
                    j += 1
                    break
                j += 1
            i = j
            continue

        if ch == "{":
            # Flush raw text before this brace up to the start of the
            # prelude (selector/at-rule text). We need to find where the
            # prelude "began" -- i.e. walk back from `i` to the end of the
            # previous item.
            try:
                close = match_brace(source, i)
            except ScanError:
                i += 1
                continue

            prelude_start = raw_start
            prelude = source[prelude_start:i]

            block_inner = source[i + 1:close]
            block_end = close + 1

            is_at_rule = prelude.lstrip().startswith("@")

            if is_at_rule:
                if raw_start < prelude_start:
                    items.append(RawItem(source[raw_start:prelude_start], raw_start, prelude_start))
                items.append(OpaqueBlock(source[prelude_start:block_end], prelude_start, block_end))
            else:
                decls = parse_declarations(block_inner)
                if decls is None:
                    if raw_start < prelude_start:
                        items.append(RawItem(source[raw_start:prelude_start], raw_start, prelude_start))
                    items.append(OpaqueRule(source[prelude_start:block_end], prelude_start, block_end))
                else:
                    trailing = _rebuild_trailing(block_inner, decls)
                    if raw_start < prelude_start:
                        items.append(RawItem(source[raw_start:prelude_start], raw_start, prelude_start))
                    items.append(SimpleRule(
                        prelude=prelude, decls=decls, trailing=trailing,
                        start=prelude_start, end=block_end,
                    ))

            raw_start = block_end
            i = block_end
            continue

        i += 1

    if raw_start < n:
        items.append(RawItem(source[raw_start:n], raw_start, n))

    return items


def item_raw(item) -> str:
    if isinstance(item, RawItem):
        return item.raw
    if isinstance(item, OpaqueBlock):
        return item.raw
    if isinstance(item, OpaqueRule):
        return item.raw
    if isinstance(item, SimpleRule):
        return item.prelude + "{" + item.block_inner_raw() + "}"
    raise TypeError(type(item))


def reconstruct(items: list[Item]) -> str:
    return "".join(item_raw(it) for it in items)
