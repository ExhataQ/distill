# llm-source

A reversible, LLM-oriented source-compression middleware. This is **Milestone 1**
from the spec: CSS only, proving the core loop works safely.

```
llmsource prepare src/styles.css   # generates .llm/compressed/... + .llm/manifests/...
# an AI agent edits .llm/compressed/src/styles.css.llm directly
llmsource sync src/styles.css      # validates, then reconstructs src/styles.css
llmsource status src/styles.css    # token savings + sync state
llmsource validate src/styles.css  # check the .llm file without touching source
llmsource restore src/styles.css   # discard .llm edits, regenerate from source
```

## What's implemented (Milestone 1 + most of Milestone 2)

- **Lossless CSS scanner/parser** (`scanner.py`, `cssparse.py`): string/comment/
  paren-aware, hand-rolled (no CSS parsing library was available in this
  offline environment — `tinycss2`/`cssutils` could not be installed; see
  "Known limitations" below). Byte-for-byte round trip is a hard test
  invariant (`test_parser_reconstructs_exactly`).
- **Only fully-understood regions are touched.** A rule only becomes eligible
  for compression if its block is an unambiguous flat list of
  `prop: value;` declarations. `@media`, `@keyframes`, `@font-face`, CSS
  nesting, and anything else ambiguous is preserved as opaque, untouched raw
  text — never corrupted, never compressed.
- **Economic macro selection** (`languages/css.py`): candidate declaration
  spans (1..8 declarations, contiguous, exact-text match) are only turned
  into a macro if the measured token savings clear a minimum bar, using a
  pluggable `TokenCounter` (`tokenizer.py`). Selection is greedy
  (longest/most-reused spans claimed first, no overlap), not globally
  optimal — see spec section 28.
- **`.llm` representation** is literally the original source text with
  matched declaration spans replaced in place by `@X001;` references, plus a
  macro-definitions header. Because nothing outside a replaced span is ever
  touched, decompression is a pure, stateless substring substitution — no
  external state is required to expand a `.llm` file back to source.
- **Local macro override is free.** An AI can delete a `@X001;` reference
  inside one rule and type a literal declaration instead; only that rule
  changes on expand. See `test_local_macro_override_does_not_affect_other_occurrences`.
- **Stable macro IDs** across recompression: `prepare` reuses IDs for macro
  values that already existed in the previous manifest.
- **Safety**: `sync` always expands into a temp file, re-parses and
  structurally validates the result, and only then atomically replaces the
  real source. A dangling macro reference, or any expand/parse failure,
  aborts with an error and the source file is never touched
  (`test_invalid_representation_never_overwrites_source`).
- **Hashing & conflict detection**: manifest tracks the source hash and
  `.llm` hash from the last `prepare`. `sync` refuses to guess when the
  source changed externally, or when both source and `.llm` changed
  independently (`SOURCE_CHANGED` / `CONFLICT` states) — see
  `manifest.detect_sync_state`.
- **Token reporting**: `status`/`prepare` output original/compressed/saved
  tokens and reduction %, clearly labeled as estimates from the
  `approx-v1` tokenizer (see Known limitations).

## Compaction modes

```
llmsource prepare src/styles.css               # safe mode (default)
llmsource prepare src/styles.css --aggressive   # aggressive mode
```

- **safe (default)**: byte-for-byte reversible. Your real source file's
  formatting (indentation, blank lines, comments) is never touched — only
  the `.llm` working copy gets macro-compressed.
- **aggressive**: the AI-facing `.llm` view is also minified (comments
  stripped, whitespace collapsed) — but **the real source file is never
  touched by `prepare`, in either mode.** `.llm/` is purely a cache; your
  actual CSS file stays exactly as it was until you `sync`. On `sync`,
  minification only ever lands on whatever the AI actually edited —
  everything else is written back in your **original formatting**, comments
  and all (see `aggressive.py`: an item-level diff aligns the AI's edited
  output against what was there before, using original text for anything
  unchanged).

  Concretely: change one declaration in a 1000-line file and `sync`
  produces a one-hunk diff against your original — not a full-file rewrite.
  This was iterated on live against a real ~1090-line file with CRLF line
  endings, which caught two real bugs now covered by regression tests:
  `Path.read_text()`'s silent CRLF→LF conversion (`test_crlf_line_endings
  _survive_byte_exact`), and a trailing end-of-file newline that
  disappeared on any edit elsewhere (`test_trailing_newline_survives_an
  _unrelated_edit`).

On the sample real-world file in this repo (~1090 lines):

```
                         safe mode   aggressive mode
reduction vs. original:    7.0%         33.4%
```

Most of that extra ~26 points is indentation/comments that the AI's
compressed view no longer has to carry — none of it comes at the cost of
your real file's formatting. Note: earlier versions of this tool's
`ApproxTokenCounter` counted whitespace as free (0 tokens), which hid this
entirely — see `tokenizer.py`'s `approx-v2` for the fix (a whitespace run
now costs ~1 token, closer to real BPE behavior).

## Benchmark

`tests/fixtures/design_system.css` (755 lines, 80 rules, realistic
utility/button/card/text classes with real repetition):

```
original:   3430 tokens
compressed: 1488 tokens
saved:      1942 tokens (56.6%)
macros:     21 (88 references)
```
Round-trip verified byte-for-byte on this file too.

The small `tests/fixtures/sample.css` fixture (3 tiny repeated rules)
deliberately shows the opposite, equally important case: with only a
little repetition, the fixed representation overhead (markers + macro
definition wrapper) can outweigh the savings, and the tool reports that
honestly instead of pretending compression always helps
(`test_tiny_file_with_low_repetition_may_not_save_tokens`).

## Known limitations / deliberately out of scope for Milestone 1

- **No real tokenizer.** This environment has no network access, so
  `tiktoken` (or any BPE tokenizer) could not be installed. `tokenizer.py`
  defines the `TokenCounter` abstraction the spec asks for, with one
  concrete regex-based `ApproxTokenCounter` implementation. Plug in a real
  tokenizer by implementing `TokenCounter.count()` and passing it through
  `compress()`/`prepare()` — the rest of the pipeline doesn't care.
- **No CSS parsing library.** Same reason (no network). `scanner.py`/
  `cssparse.py` are a hand-rolled, deliberately conservative
  string/comment/paren-aware scanner — not a full CSS grammar. It correctly
  handles comments, strings (with escapes), `url()`/function parens, and
  bails out to "preserve raw" for anything it isn't sure about, per the
  spec's "never corrupt, never guess" rule.
- **`@media`/`@keyframes`/nested rules are never compressed**, only
  preserved verbatim. Extending macro compression into at-rule blocks is
  Milestone 3 territory (spec section 20/31, language adapter interface).
- **Matching is exact-text, not whitespace-normalized** in safe mode. Two
  declarations that differ only in incidental spacing won't be recognized
  as the same macro candidate. Aggressive mode's whitespace normalization
  incidentally helps here too (formatting differences that used to block a
  macro match are gone), which is part of why its macro count is usually
  higher, not just its baseline token count lower.
- **No `llmsource.reconcile` three-way merge** (spec section 19 says not to
  build this yet). `sync --force` exists as an escape hatch when a human
  has confirmed which side should win.
- **No project-wide config / glob discovery** (spec section 31) — the CLI
  takes explicit file paths for now.
- **Only CSS.** `LanguageAdapter` isn't factored out as a formal interface
  yet; `languages/css.py` exposes `compress`/`expand` functions that
  `core.py` calls directly. Extracting the adapter interface (spec section
  20) is the natural next step before adding JSON/HTML.

## Project layout

```
llmsource/
├── scanner.py       # string/comment/paren-aware low-level text scanning
├── cssparse.py       # stylesheet -> ordered items (rules / opaque blocks / raw)
├── tokenizer.py       # TokenCounter abstraction + approx implementation
├── manifest.py       # manifest schema, hashing, sync-state detection
├── core.py       # prepare / sync / restore / status / validate orchestration
├── cli.py       # argparse CLI
└── languages/
    └── css.py       # macro selection (compress) + stateless expand
tests/
├── test_roundtrip.py       # the core safety invariant
├── test_macros.py       # macro selection, local override, token accounting
├── test_core_workflow.py       # prepare/sync/status/restore, conflict detection
└── fixtures/
```

## Running tests

No pytest available offline either — each test file is self-contained and
runnable directly:

```
python3 tests/test_roundtrip.py
python3 tests/test_macros.py
python3 tests/test_core_workflow.py
```
