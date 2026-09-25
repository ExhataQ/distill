import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmsource.jsonparse import JSONScanError, parse_document
from llmsource.languages import json as json_lang


# ---- jsonparse.py: round-trip invariant ----

ROUNDTRIP_CASES = [
    "{}",
    "[]",
    "  {}  ",
    '{"a":1}',
    '{ "a" : 1 , "b" : 2 }',
    "[1,2,3]",
    "[\n  1,\n  2,\n  3\n]",
    '{\n  "name": "test",\n  "nested": {"x": 1, "y": [1,2,3]},\n'
    '  "list": [1, 2, {"a": true}],\n  "n": null,\n  "esc": "a\\"b\\n"\n}\n',
    "42",
    '"just a string"',
    "true",
    "false",
    "null",
    "  [1, 2, 3]  ",
    '{"unicode_escape": "\\u00e9"}',
    "[1e10, -2.5, 0, -0.001]",
]


def test_parser_reconstructs_exactly():
    for c in ROUNDTRIP_CASES:
        doc = parse_document(c)
        assert doc.reconstruct() == c, f"round-trip failed for {c!r}"


def test_malformed_json_raises_scan_error():
    for bad in ["{", "[1,2", '{"a":}', "{,}", "[1,,2]", "{'a':1}", "nul"]:
        try:
            parse_document(bad)
            raise AssertionError(f"expected JSONScanError for {bad!r}")
        except JSONScanError:
            pass


# ---- languages/json.py: compress/expand round trip ----

def test_basic_round_trip_no_repetition():
    src = '{"name": "widget", "price": 9.99, "tags": ["a", "b"]}'
    llm_text, meta = json_lang.compress(src)
    assert json_lang.expand(llm_text) == src


def test_array_repetition_produces_macro():
    src = (
        "[\n"
        '  {"status": "active", "role": "admin", "verified": true},\n'
        '  {"status": "active", "role": "admin", "verified": true},\n'
        '  {"status": "active", "role": "admin", "verified": true},\n'
        '  {"status": "pending", "role": "guest", "verified": false}\n'
        "]\n"
    )
    llm_text, meta = json_lang.compress(src)
    assert meta["stats"]["macro_count"] >= 1
    assert json_lang.expand(llm_text) == src
    # every claimed reference must actually appear in the rendered body --
    # regression test for the self-overlap accounting bug
    body = llm_text.split(json_lang.SOURCE_MARKER + "\n", 1)[1]
    for mid, info in meta["macros"].items():
        assert body.count(f"@{mid};") == info["references"]


def test_object_members_with_distinct_keys_never_macro_by_themselves():
    """A top-level object's members each carry their own (distinct) key,
    so whole-member text can never repeat even if the VALUES are
    identical -- this is expected, not a bug; see the JSON design notes."""
    src = '{"a": {"x": 1, "y": 2}, "b": {"x": 1, "y": 2}, "c": {"x": 1, "y": 2}}'
    llm_text, meta = json_lang.compress(src)
    assert meta["stats"]["macro_count"] == 0
    assert json_lang.expand(llm_text) == src


def test_self_overlapping_repetition_never_double_claims():
    """3+ identical consecutive array elements used to let the selector
    accept overlapping occurrences of the same candidate span (a latent
    bug shared with the original CSS implementation). Whatever is
    selected must round-trip and must never report more references than
    actually appear in the rendered body."""
    src = '[{"a":1},{"a":1},{"a":1},{"a":2}]'
    llm_text, meta = json_lang.compress(src)
    assert json_lang.expand(llm_text) == src
    body = llm_text.split(json_lang.SOURCE_MARKER + "\n", 1)[1]
    for mid, info in meta["macros"].items():
        assert body.count(f"@{mid};") == info["references"]


def test_macro_reference_collision_inside_string_value_is_safe():
    """A string value that is textually identical to a macro reference
    (or to a marker) must never be misinterpreted during expand()."""
    src = '[{"x":1,"y":2},{"x":1,"y":2},{"x":1,"y":2},"@X001;"]'
    llm_text, meta = json_lang.compress(src)
    assert meta["stats"]["macro_count"] >= 1
    result = json_lang.expand(llm_text)
    assert result == src


def test_dangling_macro_reference_raises():
    bad = json_lang.MACROS_MARKER + "\n" + json_lang.SOURCE_MARKER + "\n[@X999;]"
    try:
        json_lang.expand(bad)
        raise AssertionError("expected ExpandError")
    except json_lang.ExpandError:
        pass


def test_local_macro_override_does_not_affect_other_occurrences():
    """Same guarantee CSS provides: an AI can delete one `@Xnnn;`
    reference and type a literal value instead; only that occurrence
    changes on expand."""
    src = '[{"a":1,"b":2},{"a":1,"b":2},{"a":1,"b":2},{"a":9,"b":9}]'
    llm_text, meta = json_lang.compress(src)
    assert meta["stats"]["macro_count"] >= 1
    edited = llm_text.replace("@X001;", '{"a":5,"b":5},', 1)  # override first occurrence only
    result = json_lang.expand(edited)
    assert result.startswith('[{"a":5,"b":5},'), result
    assert result.count('{"a":1,"b":2}') == 2


def test_minify_item_strips_whitespace_outside_strings_only():
    raw = '"key" :   1  ,'
    mini = json_lang.minify_item(raw)
    assert mini == '"key":1,'
    raw2 = '"note": "  has   internal   spaces  ",'
    mini2 = json_lang.minify_item(raw2)
    assert mini2 == '"note":"  has   internal   spaces  ",'


def test_parse_to_items_reconstructs_exactly():
    for c in ROUNDTRIP_CASES:
        items = json_lang._parse_to_items(c)
        assert "".join(items) == c, f"parse_to_items round-trip failed for {c!r}"


def test_empty_container_round_trip():
    for src in ["{}", "[]", "{ }", "[ ]", "{\n}\n", "[\n]\n"]:
        llm_text, meta = json_lang.compress(src)
        assert json_lang.expand(llm_text) == src


if __name__ == "__main__":
    import traceback
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"OK   {name}")
            except Exception:
                fails += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{'ALL PASSED' if fails == 0 else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
