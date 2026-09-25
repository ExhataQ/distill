"""
TokenCounter abstraction (spec section 14).

We don't have network access to fetch a real tokenizer (e.g. tiktoken), so
the default implementation is a deliberately simple, documented
approximation: it is NOT "characters / 4". It splits on word boundaries and
common punctuation/symbol runs, which is much closer to how BPE tokenizers
actually break up code (identifiers as one token, each punctuation run as
roughly one token). This is clearly labeled as an estimate everywhere it's
reported, per spec section 14/15.

The architecture supports plugging in a real tokenizer later: implement
TokenCounter and pass it to CSSCompressor / estimate_tokens.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*|[0-9]+(?:\.[0-9]+)?|\s+|.", re.DOTALL)


class TokenCounter(ABC):
    name: str = "abstract"

    @abstractmethod
    def count(self, text: str) -> int:
        ...


class ApproxTokenCounter(TokenCounter):
    """Regex-based approximation. A run of whitespace counts as roughly 1
    token (this mirrors how real BPE tokenizers usually merge a run of
    spaces/an indent into a single token -- it is NOT free, unlike naive
    'strip whitespace, count 0' approximations, which would hide the real
    cost of formatting/indentation). Identifiers/numbers count as 1 token
    each; every other character (punctuation, braces, colons) counts as 1
    token. This is a reasonable stand-in for BPE behavior on code-like
    text without requiring a real tokenizer.
    """
    name = "approx-v2"

    def count(self, text: str) -> int:
        if not text:
            return 0
        n = 0
        for m in _TOKEN_RE.finditer(text):
            n += 1
        return n


def estimate_tokens(text: str, tokenizer: TokenCounter | None = None) -> int:
    tokenizer = tokenizer or ApproxTokenCounter()
    return tokenizer.count(text)


DEFAULT_TOKENIZER = ApproxTokenCounter()
