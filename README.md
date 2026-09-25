# distill

**A reversible, LLM-oriented source-compression layer for AI coding agents.**

`distill` lets an AI agent read and edit a much smaller, denser view of your
source files — while your real files stay exactly as they are until you
explicitly sync the edits back. Not minification. Not a build step. A
working cache between your repo and whatever's editing it.

```
src/styles.css  →  distill prepare  →  .llm/compressed/styles.css.llm
                                              ↑
                                        AI reads/edits this
                                              ↓
src/styles.css  ←  distill sync     ←  (validated, reconstructed)
```

## Why

Handing an AI agent a large source file costs tokens on every read, and
most of those tokens are structural repetition — the same declaration
blocks, the same boilerplate, over and over. `distill` compresses that
repetition into short macro references before the AI ever sees the file,
and safely expands them back on the way out. The agent reads and edits
less; your repo ends up exactly as intended.

Two hard guarantees make this safe to actually use:

1. **Your real files are never silently rewritten.** `distill prepare`
   only ever writes to `.llm/` — a gitignored cache. Nothing touches your
   source until you run `sync`, and `sync` validates before it writes
   anything.
2. **Editing one thing never silently changes another.** If ten rules (or
   array elements, or object members) share a compressed macro and the AI
   edits one of them, only that one changes — the shared macro is locally
   overridden, not mutated.

## Quickstart

```bash
pip install -e .          # no external dependencies

distill prepare src/styles.css      # writes .llm/compressed/src/styles.css.llm
# → hand that .llm file to your AI agent; it edits it directly

distill sync src/styles.css         # validates, then reconstructs src/styles.css
distill status src/styles.css       # token savings + sync state
distill validate src/styles.css     # check the .llm file without touching source
distill restore src/styles.css      # discard .llm edits, regenerate from source
```

`.json` files work exactly the same way — the language is picked
automatically from the file extension.

## Example

```css
.a { display: flex; align-items: center; gap: 10px; }
.b { display: flex; align-items: center; gap: 10px; }
.c { display: flex; align-items: center; gap: 8px;  }
```

becomes:

```
@X001 {
display: flex;
align-items: center;
gap: 10px;
}

.a{@X001;}
.b{@X001;}
.c{display: flex;align-items: center;gap: 8px;}
```

If the AI wants `.a`'s gap to be `8px` instead, it just replaces `@X001;`
inside `.a`'s block with the literal declarations and changes the one
value — `.b` is untouched, because it still points at `@X001`.

The same mechanism works for a JSON array of repeated records:

```json
[
  {"status": "active", "role": "admin", "verified": true},
  {"status": "active", "role": "admin", "verified": true},
  {"status": "pending", "role": "guest", "verified": false}
]
```
compresses to a shared `@X001;` for the two identical records, and expands
back with the same local-override guarantee if the AI edits just one.

## Two compaction modes

```
distill prepare src/styles.css               # safe (default)
distill prepare src/styles.css --aggressive   # aggressive
```

| | safe | aggressive |
|---|---|---|
| Real source file | untouched, byte-for-byte reversible | **also untouched** — `.llm/` is still just a cache |
| `.llm` view | macro-compressed, your original formatting kept | macro-compressed **and** minified (comments stripped, whitespace collapsed) |
| On `sync` | writes back exactly what was there | writes back your **original formatting** for anything unedited; only the AI's actual edits come out minified |
| Typical savings (real ~1090-line CSS file) | ~7% | ~33% |

Change one declaration in a 1000-line file under aggressive mode and
`sync` produces a one-hunk diff against your original — not a full-file
rewrite. Comments and formatting survive everywhere the AI didn't touch.

## Safety details

- **Byte-exact round trip.** `expand(compress(x)) == x` for any unedited
  file — a hard test invariant, not a goal. CRLF line endings, trailing
  newlines, comments, strings, `url()` contents/JSON escapes, and CSS you
  don't recognize (`@media`, `@keyframes`, nested rules) are all preserved
  exactly, never corrupted or guessed at. Malformed JSON is rejected
  outright rather than repaired — JSON's grammar is small and fully
  specified, so there's no safe "preserve as opaque" fallback the way
  there is for CSS.
- **Validate before write.** `sync` always expands into memory, re-parses,
  and structurally validates the result before ever touching your real
  file. A dangling macro reference or any parse failure aborts with an
  error — your source is never overwritten with something broken.
- **Conflict detection.** If your source file changed outside `distill`
  since the last `prepare`, or both the source and the `.llm` file changed
  independently, `sync` refuses to guess and tells you exactly what
  happened.
- **String/comment-aware expansion.** A `@X001;`-shaped substring sitting
  inside real string content (`"note": "see @X001;"`, or a CSS
  `content: "@X001;"`) is never mistaken for an actual macro reference —
  expansion walks the text the same string-aware way parsing does, not a
  blind regex substitution over the whole file.
- **Honest token reporting.** `status` reports real, measured savings
  (with a documented, pluggable `TokenCounter`), not assumed ones — and
  says so plainly when a small file's fixed overhead outweighs its
  savings, rather than pretending compression always helps.

## Language support

CSS and JSON today, built on a shared `LanguageAdapter` interface
(`llmsource/languages/base.py`) so `core.py`/`aggressive.py` never import
a specific language's parser directly — adding a language means writing
an adapter and registering it, not touching the orchestration layer.

**A real limitation worth knowing about JSON specifically:** macro
candidates are currently only found among a document's **top-level**
members — the direct children of the root object/array. If your root is
itself an array of repeated records (`[{...}, {...}, {...}]`), that
compresses well. If your root is an object wrapping the interesting array
(`{"users": [{...}, {...}, {...}], "version": 3}` — the far more common
real-world shape for API responses and config files), the repetition
lives one level below the root and currently isn't seen at all. Verified
directly: the first shape gets a macro and ~20% reduction, the second
gets zero macros and *negative* reduction (pure representation overhead)
on otherwise-identical repeated content. Extending itemization to
recurse into nested containers is the natural next step, not yet done.

## Project layout

```
llmsource/
├── scanner.py          # CSS: string/comment/paren-aware low-level scanning
├── cssparse.py          # CSS: stylesheet -> ordered items
├── jsonparse.py          # JSON: strict RFC 8259 parser, same round-trip invariant
├── minify.py          # CSS: safe, conservative whitespace/comment stripping
├── aggressive.py          # item-level diff so minification never leaks onto untouched code
├── tokenizer.py          # pluggable TokenCounter abstraction
├── manifest.py          # manifest schema, hashing, sync-state detection
├── core.py          # prepare / sync / restore / status / validate orchestration
├── cli.py          # command-line entry point
└── languages/
    ├── base.py          # LanguageAdapter interface
    ├── registry.py          # extension -> adapter lookup
    ├── css.py          # CSS macro selection + expand
    └── json.py          # JSON macro selection + expand
tests/
```

## Running tests

```bash
python3 tests/test_roundtrip.py
python3 tests/test_macros.py
python3 tests/test_core_workflow.py
python3 tests/test_minify.py
python3 tests/test_aggressive_nondestructive.py
python3 tests/test_json.py
```

44 tests, no external dependencies, no network access required.

## License

MIT (or your preference — add a LICENSE file before publishing).
