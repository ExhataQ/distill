"""
A minimal, defensive CSS text scanner.

This is NOT a full CSS parser. It only does the one thing we need to do
safely: walk raw CSS text and know, at every character, whether we are
inside a string, a comment, or nested parentheses -- so that we can find
*top-level* braces `{ }` and semicolons `;` without being fooled by things
like:

    content: "a { b; }";
    background: url(foo;bar.png);
    /* comment; with a fake { brace } in it */

If the scanner hits anything it isn't confident about (unterminated string,
unterminated comment, unbalanced parens/braces), it raises ScanError. Callers
are expected to catch this and fall back to treating the region as opaque,
preserved-as-is text -- never guess, never corrupt.
"""
from __future__ import annotations

from dataclasses import dataclass


class ScanError(Exception):
    pass


def find_top_level_positions(text: str) -> "ScanIndex":
    """Walk `text` once and record, for every position, enough state to
    answer: is this a top-level '{' or '}' or ';' (i.e. not inside a
    string/comment/parens)? Returns a ScanIndex with helper methods.
    """
    n = len(text)
    i = 0
    paren_depth = 0
    # Positions (index in text) of top-level '{', '}', ';' characters.
    top_braces_open: list[int] = []
    top_braces_close: list[int] = []
    top_semicolons: list[int] = []

    while i < n:
        ch = text[i]

        # Comments
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                raise ScanError(f"unterminated comment starting at {i}")
            i = end + 2
            continue

        # Strings
        if ch in ("'", '"'):
            quote = ch
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if text[j] == quote:
                    break
                if text[j] == "\n":
                    # CSS strings can't contain a literal newline unescaped.
                    raise ScanError(f"unterminated string starting at {i}")
                j += 1
            else:
                raise ScanError(f"unterminated string starting at {i}")
            i = j + 1
            continue

        if ch == "(":
            paren_depth += 1
            i += 1
            continue
        if ch == ")":
            paren_depth -= 1
            if paren_depth < 0:
                raise ScanError(f"unbalanced ')' at {i}")
            i += 1
            continue

        if paren_depth == 0:
            if ch == "{":
                top_braces_open.append(i)
            elif ch == "}":
                top_braces_close.append(i)
            elif ch == ";":
                top_semicolons.append(i)

        i += 1

    if paren_depth != 0:
        raise ScanError("unbalanced parentheses")

    return ScanIndex(text, top_braces_open, top_braces_close, top_semicolons)


@dataclass
class ScanIndex:
    text: str
    braces_open: list[int]
    braces_close: list[int]
    semicolons: list[int]

    def is_top_level_brace_open(self, pos: int) -> bool:
        return pos in self._open_set()

    def _open_set(self):
        if not hasattr(self, "_open_set_cache"):
            self._open_set_cache = set(self.braces_open)
        return self._open_set_cache


def match_brace(text: str, open_pos: int) -> int:
    """Given the index of a top-level '{', return the index of its
    matching '}' using the same string/comment/paren-aware walk, this
    time tracking brace depth too. Raises ScanError if unmatched.
    """
    n = len(text)
    i = open_pos
    assert text[i] == "{"
    depth = 0
    paren_depth = 0
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                raise ScanError(f"unterminated comment starting at {i}")
            i = end + 2
            continue
        if ch in ("'", '"'):
            quote = ch
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if text[j] == quote:
                    break
                if text[j] == "\n":
                    raise ScanError(f"unterminated string starting at {i}")
                j += 1
            else:
                raise ScanError(f"unterminated string starting at {i}")
            i = j + 1
            continue
        if ch == "(":
            paren_depth += 1
            i += 1
            continue
        if ch == ")":
            paren_depth -= 1
            i += 1
            continue
        if paren_depth == 0:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    raise ScanError(f"unmatched '{{' at {open_pos}")


def split_top_level(text: str, sep: str = ";") -> list[tuple[int, int]]:
    """Split `text` on top-level occurrences of `sep` (default ';'),
    respecting strings/comments/parens. Returns a list of (start, end)
    spans covering the *whole* text including separators, i.e. consecutive
    spans concatenate back to `text` exactly. Does not itself raise on
    braces since callers already know there are none at top level here
    (block bodies with nested rules should be rejected by the caller
    before calling this).
    """
    n = len(text)
    i = 0
    paren_depth = 0
    spans: list[tuple[int, int]] = []
    start = 0
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                raise ScanError(f"unterminated comment starting at {i}")
            i = end + 2
            continue
        if ch in ("'", '"'):
            quote = ch
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if text[j] == quote:
                    break
                if text[j] == "\n":
                    raise ScanError(f"unterminated string starting at {i}")
                j += 1
            else:
                raise ScanError(f"unterminated string starting at {i}")
            i = j + 1
            continue
        if ch == "(":
            paren_depth += 1
            i += 1
            continue
        if ch == ")":
            paren_depth -= 1
            i += 1
            continue
        if ch == "{" or ch == "}":
            raise ScanError(f"unexpected brace at {i} inside declaration block")
        if paren_depth == 0 and ch == sep:
            spans.append((start, i + 1))
            start = i + 1
            i += 1
            continue
        i += 1
    if start < n:
        spans.append((start, n))
    return spans
