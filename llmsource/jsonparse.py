"""
Lossless JSON text scanner/parser, mirroring cssparse.py's structural
philosophy but scoped to JSON's much smaller, fully-specified grammar --
no comments, no url()/interpolation ambiguity, a small fixed set of
escapes. Unlike CSS, this scanner IS a complete parser for valid JSON
(strict RFC 8259): there is no "give up, preserve as opaque raw text"
fallback, because JSON's grammar doesn't have CSS's open-ended edge cases
that make a conservative subset the safer choice. Malformed input raises
JSONScanError; callers must not guess at repairing it.

Top-level items (used both for macro-candidate selection and for
aggressive-mode's per-item formatting-preservation diff) are the direct
members of the document's ROOT container:
  - root is an object -> each `"key": value` member is an item
  - root is an array   -> each element is an item
  - root is a bare scalar (string/number/true/false/null) -> the whole
    document is a single item (nothing to itemize)

This is the JSON analogue of CSS's rule-level granularity: a document's
top-level members are the closest thing JSON has to a stylesheet's
top-level rules. Values nested *inside* a member (a nested object/array)
are captured as opaque raw text within that member's own item -- they are
not itemized further by this parser. That's a deliberate scope choice,
not a limitation.
"""
from __future__ import annotations

from dataclasses import dataclass

_WS = " \t\r\n"


class JSONScanError(Exception):
    pass


def _skip_ws(s: str, i: int) -> int:
    n = len(s)
    while i < n and s[i] in _WS:
        i += 1
    return i


def _scan_string(s: str, i: int) -> int:
    """s[i] must be '\"'. Returns the index just past the closing quote."""
    n = len(s)
    if i >= n or s[i] != '"':
        raise JSONScanError(f"expected string at {i}")
    j = i + 1
    while j < n:
        ch = s[j]
        if ch == "\\":
            if j + 1 >= n:
                raise JSONScanError(f"unterminated escape at {j}")
            if s[j + 1] == "u":
                if j + 6 > n or not all(c in "0123456789abcdefABCDEF" for c in s[j + 2:j + 6]):
                    raise JSONScanError(f"invalid \\u escape at {j}")
                j += 6
                continue
            j += 2
            continue
        if ch == '"':
            return j + 1
        if ch == "\n":
            raise JSONScanError(f"unterminated string starting at {i} (raw newline)")
        j += 1
    raise JSONScanError(f"unterminated string starting at {i}")


def _scan_number(s: str, i: int) -> int:
    n = len(s)
    j = i
    if j < n and s[j] == "-":
        j += 1
    if j >= n or not s[j].isdigit():
        raise JSONScanError(f"invalid number at {i}")
    if s[j] == "0":
        j += 1
    else:
        while j < n and s[j].isdigit():
            j += 1
    if j < n and s[j] == ".":
        j += 1
        if j >= n or not s[j].isdigit():
            raise JSONScanError(f"invalid number at {i}")
        while j < n and s[j].isdigit():
            j += 1
    if j < n and s[j] in "eE":
        j += 1
        if j < n and s[j] in "+-":
            j += 1
        if j >= n or not s[j].isdigit():
            raise JSONScanError(f"invalid number at {i}")
        while j < n and s[j].isdigit():
            j += 1
    return j


def scan_value(s: str, i: int) -> int:
    """Return the index just past the JSON value starting exactly at
    s[i] (caller has already skipped leading whitespace). Recursively
    skips nested objects/arrays to find their end without building a
    tree for them -- only boundaries are needed; raw text is sliced by
    the caller from (i, returned index)."""
    n = len(s)
    if i >= n:
        raise JSONScanError("unexpected end of input, expected a value")
    ch = s[i]
    if ch == '"':
        return _scan_string(s, i)
    if ch == "{":
        return _scan_container_end(s, i, "{", "}")
    if ch == "[":
        return _scan_container_end(s, i, "[", "]")
    if s.startswith("true", i):
        return i + 4
    if s.startswith("false", i):
        return i + 5
    if s.startswith("null", i):
        return i + 4
    if ch == "-" or ch.isdigit():
        return _scan_number(s, i)
    raise JSONScanError(f"unexpected character {ch!r} at {i}, expected a value")


def _scan_container_end(s: str, i: int, open_ch: str, close_ch: str) -> int:
    n = len(s)
    j = i + 1
    j = _skip_ws(s, j)
    if j < n and s[j] == close_ch:
        return j + 1
    while True:
        if open_ch == "{":
            if j >= n or s[j] != '"':
                raise JSONScanError(f"expected string key at {j}")
            j = _scan_string(s, j)
            j = _skip_ws(s, j)
            if j >= n or s[j] != ":":
                raise JSONScanError(f"expected ':' at {j}")
            j = _skip_ws(s, j + 1)
        j = scan_value(s, j)
        j = _skip_ws(s, j)
        if j < n and s[j] == ",":
            j = _skip_ws(s, j + 1)
            continue
        if j < n and s[j] == close_ch:
            return j + 1
        raise JSONScanError(f"expected ',' or {close_ch!r} at {j}")


@dataclass
class JSONItem:
    """One top-level member of the document's root container, analogous
    to cssparse.DeclUnit: `prefix` is raw text (whitespace) preceding this
    item that belongs to *this* item when rendering, but is deliberately
    excluded when this item participates in a macro span's matched text
    (see languages/json.py's _span_text) since it's positional, not part
    of the reusable content. `text` is the item's own content -- for an
    object member, the key, colon, whitespace and value; for an array
    element, just the value -- plus a trailing comma if one followed."""
    prefix: str
    text: str
    key: str | None  # decoded-free (still JSON-escaped) key text, or None for array elements / scalar docs


@dataclass
class JSONDocument:
    items: list[JSONItem]
    root_kind: str  # "object" | "array" | "scalar"
    open_wrap: str  # leading trivia + opening bracket ("" + whole text, for a scalar doc)
    close_wrap: str  # closing bracket + trailing trivia ("" for a scalar doc)

    def reconstruct(self) -> str:
        body = "".join(it.prefix + it.text for it in self.items)
        return self.open_wrap + body + self.close_wrap


def parse_document(source: str) -> JSONDocument:
    """Parse a whole JSON text document into a flat, ordered item list of
    its root container's direct members. Concatenating
    open_wrap + (item.prefix + item.text for each item) + close_wrap
    reproduces `source` exactly -- the same safety invariant cssparse.py's
    parse_stylesheet()/reconstruct() provide for CSS."""
    n = len(source)
    lead = _skip_ws(source, 0)
    if lead >= n:
        raise JSONScanError("empty document")
    ch = source[lead]

    if ch not in "{[":
        end = scan_value(source, lead)
        return JSONDocument(
            items=[JSONItem(prefix=source[:lead], text=source[lead:end], key=None)],
            root_kind="scalar",
            open_wrap="",
            close_wrap=source[end:],
        )

    open_ch = ch
    close_ch = "}" if ch == "{" else "]"
    root_kind = "object" if ch == "{" else "array"

    cursor = lead + 1
    items: list[JSONItem] = []
    prev_end = cursor  # end of previous item's own text (start of this item's prefix)

    ws_after_open = _skip_ws(source, cursor)
    if ws_after_open < n and source[ws_after_open] == close_ch:
        # empty object/array: no items at all
        return JSONDocument(
            items=[],
            root_kind=root_kind,
            open_wrap=source[:lead] + open_ch,
            close_wrap=source[cursor:ws_after_open] + close_ch + source[ws_after_open + 1:],
        )

    cursor = ws_after_open
    while True:
        prefix_start = prev_end
        member_content_start = cursor  # key(if any)/value start, whitespace already skipped
        key = None
        if root_kind == "object":
            if cursor >= n or source[cursor] != '"':
                raise JSONScanError(f"expected string key at {cursor}")
            key_end = _scan_string(source, cursor)
            key = source[cursor + 1:key_end - 1]
            cursor = _skip_ws(source, key_end)
            if cursor >= n or source[cursor] != ":":
                raise JSONScanError(f"expected ':' at {cursor}")
            cursor = _skip_ws(source, cursor + 1)
        value_end = scan_value(source, cursor)
        after_value = _skip_ws(source, value_end)

        if after_value < n and source[after_value] == ",":
            item_end = after_value + 1
            items.append(JSONItem(
                prefix=source[prefix_start:member_content_start],
                text=source[member_content_start:item_end],
                key=key,
            ))
            prev_end = item_end
            cursor = _skip_ws(source, item_end)
            if cursor < n and source[cursor] == close_ch:
                close_pos = cursor
                trailing_ws = source[item_end:cursor]
                # fold the trailing whitespace before ']'/'}' onto the
                # close_wrap rather than inventing a phantom empty item
                open_wrap = source[:lead] + open_ch
                close_wrap = trailing_ws + close_ch + source[close_pos + 1:]
                return JSONDocument(items=items, root_kind=root_kind, open_wrap=open_wrap, close_wrap=close_wrap)
            continue

        if after_value < n and source[after_value] == close_ch:
            items.append(JSONItem(
                prefix=source[prefix_start:member_content_start],
                text=source[member_content_start:after_value],
                key=key,
            ))
            open_wrap = source[:lead] + open_ch
            close_wrap = close_ch + source[after_value + 1:]
            return JSONDocument(items=items, root_kind=root_kind, open_wrap=open_wrap, close_wrap=close_wrap)

        raise JSONScanError(f"expected ',' or {close_ch!r} at {after_value}")
