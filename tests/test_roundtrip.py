import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmsource.cssparse import parse_stylesheet, reconstruct
from llmsource.languages import css as css_lang

FIXTURE = (Path(__file__).parent / "fixtures" / "sample.css").read_text(encoding="utf-8")


def test_parser_reconstructs_exactly():
    items = parse_stylesheet(FIXTURE)
    assert reconstruct(items) == FIXTURE


def test_compress_then_expand_is_byte_identical():
    llm_text, meta = css_lang.compress(FIXTURE)
    result = css_lang.expand(llm_text)
    assert result == FIXTURE, "decompress(compress(source)) must equal source exactly"


def test_compress_creates_macros_for_repeated_group():
    llm_text, meta = css_lang.compress(FIXTURE)
    # display:flex + align-items:center repeats across .a .b .c .d -> should
    # become at least one macro
    assert meta["stats"]["macro_count"] >= 1
    assert "@X" in llm_text


def test_single_occurrence_stays_literal():
    css = ".only { color: blue; }"
    llm_text, meta = css_lang.compress(css)
    assert meta["stats"]["macro_count"] == 0
    assert css_lang.expand(llm_text) == css


def test_comments_strings_urls_preserved_verbatim():
    llm_text, meta = css_lang.compress(FIXTURE)
    result = css_lang.expand(llm_text)
    assert '"a { fake: brace; }"' in result
    assert "url(icons/play;pause.png)" in result
    assert "/* nested-looking content in a comment" in result


def test_media_and_keyframes_untouched():
    llm_text, meta = css_lang.compress(FIXTURE)
    result = css_lang.expand(llm_text)
    assert "@media (max-width: 600px)" in result
    assert "@keyframes spin" in result


def test_empty_stylesheet():
    assert css_lang.expand(css_lang.compress("")[0]) == ""


def test_whitespace_only():
    src = "\n\n   \n"
    assert css_lang.expand(css_lang.compress(src)[0]) == src


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
