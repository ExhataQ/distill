"""
LanguageAdapter: the seam between core.py/aggressive.py (orchestration,
sync-state, safe atomic writes -- all language-agnostic) and a specific
language's parsing/compression logic (currently only CSS).

This is a plain data holder, not a class hierarchy: each language module
builds one `LanguageAdapter` instance wrapping its own functions and
registers it in `registry.py`. core.py and aggressive.py only ever call
through this interface -- they must never import a specific language
module's parser/minifier directly, so that adding a new language is
"write an adapter and register it," not "touch core.py."

Fields:
  name            -- language identifier, e.g. "css". Stored in the
                      manifest so a prepared file remembers which adapter
                      produced it.
  compress        -- (source, tokenizer, previous_macros) -> (llm_text, meta)
                      meta must contain "stats" (dict) and "macros" (dict),
                      matching what core.py's Manifest expects today.
  expand          -- llm_text -> reconstructed source. Must raise on any
                      representation it isn't confident about (never guess).
  parse_to_items  -- source -> list[str], an ORDERED list of raw text
                      spans whose concatenation reproduces `source` exactly.
                      This is the safety invariant aggressive.py's item-diff
                      alignment depends on.
  minify_item     -- one item's raw text -> a denser text representation of
                      the SAME item, safe to show the AI. Must not change
                      the item's meaning.
  extra_validate  -- optional: reconstructed_source -> error message (str)
                      or None. Language-specific structural sanity checks
                      beyond "did it parse" (e.g. CSS's balanced-brace
                      check). Defaults to a no-op for languages that don't
                      need one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class LanguageAdapter:
    name: str
    compress: Callable[..., tuple[str, dict]]
    expand: Callable[[str], str]
    parse_to_items: Callable[[str], list]
    minify_item: Callable[[str], str]
    extra_validate: Callable[[str], "str | None"] | None = None
