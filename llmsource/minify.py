"""
A conservative CSS minifier.

This is intentionally NOT a maximal minifier. A "squeeze every byte" CSS
minifier has real correctness traps that are easy to get wrong without a
full parser and a way to validate against a browser, e.g.:

  - `.a .b` (descendant combinator) vs `.a.b` (compound selector) means
    completely removing the space between two selector tokens silently
    changes what the rule matches. We never fully remove a whitespace run
    between two non-punctuation tokens -- we only ever collapse it to a
    single space.
  - `calc(100% - 10px)` needs the spaces around the `-`; `calc(100%-10px)`
    is invalid CSS. We don't touch spacing inside values/selectors beyond
    the same "collapse to one space" rule, so this is never at risk.
  - Attribute selectors and string values can contain any of `{ } ; ,` --
    we only ever touch punctuation OUTSIDE strings/comments (same
    string/comment-aware walk as scanner.py).

Callers wanting readable "one rule per line" output should insert the
newline separator themselves between minified items (see aggressive.py),
rather than relying on this function to do it -- baking a structural
newline into an individual minified chunk makes it impossible to align
against a fresh re-parse of the concatenated result later (a re-parse
would see that trailing newline as a separate raw item, not part of the
rule it came from). This function only ever transforms whitespace/comments
that were already there; it never adds new structural characters.
"""
from __future__ import annotations

TIGHTEN_BEFORE = {"{", "}", ";", ","}


def minify_css(source: str) -> str:
    n = len(source)
    i = 0
    out: list[str] = []
    pending_space = False

    def emit(ch: str):
        out.append(ch)

    while i < n:
        ch = source[i]

        # Comments: drop entirely (outside strings), treat as if it were
        # whitespace for spacing purposes.
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            end = source.find("*/", i + 2)
            if end == -1:
                i = n
                break
            i = end + 2
            if out and not out[-1].isspace():
                pending_space = True
            continue

        # Strings: copy verbatim, untouched.
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
            if pending_space and out and out[-1] != "\n":
                emit(" ")
            pending_space = False
            emit(source[i:j])
            i = j
            continue

        if ch in " \t\r\n\f":
            if out:
                pending_space = True
            i += 1
            continue

        if ch in TIGHTEN_BEFORE:
            pending_space = False
            emit(ch)
            i += 1
            # swallow whitespace immediately after too
            while i < n and source[i] in " \t\r\n\f":
                i += 1
            continue

        # default: identifier chars, punctuation, combinators, etc.
        if pending_space:
            if out and out[-1] != "\n":
                emit(" ")
            pending_space = False
        emit(ch)
        i += 1

    return "".join(out).strip()
