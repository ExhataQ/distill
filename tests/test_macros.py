import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmsource.languages import css as css_lang

def _rule(name):
    return f""".{name} {{
    display: flex;
    align-items: center;
    gap: 10px;
}}

"""


# Enough repetition that the one-time representation overhead (the macro
# definition + the two `/*LLM:...*/` markers) is actually worth paying --
# see test_tiny_file_with_low_repetition_may_not_save_tokens below for the
# opposite, equally-important case.
REPEATED_CSS = "".join(_rule(n) for n in "abcdefgh")


def test_macro_reuse_across_rules():
    llm_text, meta = css_lang.compress(REPEATED_CSS)
    assert meta["stats"]["macro_count"] >= 1
    # the shared group should be referenced at least 3 times
    total_refs = sum(v["references"] for v in meta["macros"].values())
    assert total_refs >= 3


def test_local_macro_override_does_not_affect_other_occurrences():
    llm_text, meta = css_lang.compress(REPEATED_CSS)

    # Simulate an AI editing the compressed representation: find the first
    # "@X0..;" reference in rule .a's block and replace it with a literal,
    # different declaration -- exactly the workflow in spec section 5.
    marker_pos = llm_text.index(".a {")
    block_end = llm_text.index("}", marker_pos)
    block = llm_text[marker_pos:block_end]
    m = re.search(r"@X\d+;", block)
    assert m, "expected a macro reference inside .a's block"

    edited_block = block[:m.start()] + "display: flex;\n    align-items: center;\n    gap: 8px;" + block[m.end():]
    edited_llm = llm_text[:marker_pos] + edited_block + llm_text[block_end:]

    result = css_lang.expand(edited_llm)

    # .a must reflect the edit...
    a_start = result.index(".a {")
    a_end = result.index("}", a_start)
    assert "gap: 8px" in result[a_start:a_end]

    # ...but .b and .c must be completely untouched.
    b_start = result.index(".b {")
    b_end = result.index("}", b_start)
    assert "gap: 10px" in result[b_start:b_end]
    assert "gap: 8px" not in result[b_start:b_end]

    c_start = result.index(".c {")
    c_end = result.index("}", c_start)
    assert "gap: 10px" in result[c_start:c_end]


def test_macro_deletion_by_ai_just_removes_the_declarations():
    llm_text, meta = css_lang.compress(REPEATED_CSS)
    a_start = llm_text.index(".a {")
    a_end = llm_text.index("}", a_start)
    block = llm_text[a_start:a_end]
    new_block = re.sub(r"@X\d+;\n?\s*", "", block, count=1)
    edited = llm_text[:a_start] + new_block + llm_text[a_end:]
    result = css_lang.expand(edited)
    a_start2 = result.index(".a {")
    a_end2 = result.index("}", a_start2)
    assert "display: flex" not in result[a_start2:a_end2]


def test_dangling_macro_reference_raises():
    bad = css_lang.MACROS_MARKER + "\n" + css_lang.SOURCE_MARKER + "\n.a { @X999; }\n"
    try:
        css_lang.expand(bad)
        assert False, "expected ExpandError for dangling macro reference"
    except css_lang.ExpandError:
        pass


def test_token_savings_reported_and_positive_for_repeated_css():
    llm_text, meta = css_lang.compress(REPEATED_CSS)
    s = meta["stats"]
    assert s["original_tokens"] > 0
    assert s["saved_tokens"] > 0
    assert s["compressed_tokens"] == s["original_tokens"] - s["saved_tokens"]


def test_tiny_file_with_low_repetition_may_not_save_tokens():
    # This is the important, honest counterpart to the test above: a macro
    # used only twice in a tiny file can lose to the fixed representation
    # overhead (markers + macro definition wrapper). The compressor must
    # not lie about this -- it should still report the true (possibly
    # negative) reduction rather than pretend compression always helps.
    tiny = _rule("a") + _rule("b")
    llm_text, meta = css_lang.compress(tiny)
    s = meta["stats"]
    # We don't assert a sign here -- the point is the number is honest and
    # internally consistent, whichever way it goes.
    assert s["compressed_tokens"] == s["original_tokens"] - s["saved_tokens"]


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
