"""
Aggressive-mode reconstruction: minify for the AI's benefit, but never let
that minification leak into parts of the real source the AI didn't touch.

The trick is per-item alignment. We parse the real source into its ordered
top-level items (same parser as everything else -- rules, opaque blocks,
raw whitespace/comments) and minify each item independently, one-to-one.
That gives us two parallel lists, ORIGINAL_ITEMS and MINIFIED_ITEMS, where
ORIGINAL_ITEMS[i] and MINIFIED_ITEMS[i] are the same piece of CSS in two
formattings.

The AI only ever sees/edits a compressed representation built from
MINIFIED_ITEMS. When it's done, we expand its edits back into a flat
"new minified source", re-split that into items, and align it against our
old MINIFIED_ITEMS list with a sequence diff (handles the AI adding,
deleting, or reordering rules, not just editing in place). Wherever an item
is unchanged, we substitute the ORIGINAL (nicely formatted) text instead of
the minified text. Only genuinely new/edited items come out minified.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from .languages.base import LanguageAdapter


@dataclass
class ItemPairs:
    original_texts: list[str]
    minified_texts: list[str]

    @property
    def minified_source(self) -> str:
        return "".join(self.minified_texts)


def build_item_pairs(source_text: str, adapter: LanguageAdapter) -> ItemPairs:
    """Parse the source into its natural top-level items (via the language
    adapter) and minify each independently.

    Important subtlety: a language's `parse_to_items` is not guaranteed to
    emit a standalone "gap" item between two rules/statements -- for CSS,
    whitespace/comments between one rule's '}' and the next rule's selector
    get folded into the *next* rule's own prelude (only a single trailing
    chunk after the very last rule can be its own item). So the readability
    newline we want between items in the AI-facing view has to be modeled
    the same way: prepended directly onto the start of each (non-first)
    item's own minified text, not inserted as a separate pseudo-item.
    That's what keeps this list aligned with a fresh re-parse of the
    edited, concatenated result later -- a fresh parse will also see that
    leading "\\n" as part of the following item, not as something
    standalone.
    """
    items = adapter.parse_to_items(source_text)
    original_texts: list[str] = []
    minified_texts: list[str] = []
    for idx, orig in enumerate(items):
        mini = adapter.minify_item(orig)
        if idx > 0 and mini:
            mini = "\n" + mini
        original_texts.append(orig)
        minified_texts.append(mini)

    # A trailing item that had real content (even if it's pure whitespace,
    # e.g. the file's final newline) but minified away to "" becomes
    # structurally invisible: nothing marks its position once concatenated,
    # so a fresh re-parse after expand() can never find it again and it's
    # silently dropped forever. Force a minimal, honest placeholder for
    # JUST the trailing slot so it survives the round trip.
    if minified_texts and minified_texts[-1] == "" and original_texts[-1] != "":
        minified_texts[-1] = "\n"

    return ItemPairs(original_texts=original_texts, minified_texts=minified_texts)


def reconstruct_preserving_untouched_formatting(
    old_pairs: ItemPairs, new_minified_source: str, adapter: LanguageAdapter
) -> str:
    """Given the item pairs from the last prepare() and the AI's edited
    (still-minified) full text after expand(), produce the final source:
    original formatting wherever nothing changed, the AI's new/edited text
    wherever it did.
    """
    new_texts = adapter.parse_to_items(new_minified_source)

    sm = difflib.SequenceMatcher(a=old_pairs.minified_texts, b=new_texts, autojunk=False)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend(old_pairs.original_texts[i1:i2])
        else:  # replace / insert / delete
            out.extend(new_texts[j1:j2])
    return "".join(out)
