import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmsource.minify import minify_css
from llmsource.cssparse import parse_stylesheet


def test_descendant_combinator_space_preserved():
    css = ".a .b { color: red; }"
    out = minify_css(css)
    assert ".a .b" in out, f"descendant combinator space must survive: {out!r}"
    assert ".a.b" not in out


def test_calc_spacing_preserved():
    css = ".a { width: calc(100% - 10px); }"
    out = minify_css(css)
    assert "calc(100% - 10px)" in out


def test_comments_stripped():
    css = "/* hi */ .a { color: red; /* inline */ }"
    out = minify_css(css)
    assert "hi" not in out and "inline" not in out


def test_strings_untouched_including_internal_punctuation():
    css = '.a::before { content: "a { fake: brace, ; }  spaced"; }'
    out = minify_css(css)
    assert '"a { fake: brace, ; }  spaced"' in out


def test_idempotent():
    css = ".a .b {\n\n  color:   red;\n\n\n}\n\n.c { color: blue; }\n"
    once = minify_css(css)
    twice = minify_css(once)
    assert once == twice


def test_minified_output_still_parses_structurally():
    css = """
    .a {
        display: flex;
        gap: 10px;
    }
    @media (max-width: 600px) {
        .a { display: none; }
    }
    """
    out = minify_css(css)
    items = parse_stylesheet(out)
    from llmsource.cssparse import reconstruct
    assert reconstruct(items) == out


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
