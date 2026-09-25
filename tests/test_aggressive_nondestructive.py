import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmsource import core

FIXTURE = (Path(__file__).parent / "fixtures" / "sample.css").read_text(encoding="utf-8")


def _project():
    d = Path(tempfile.mkdtemp())
    (d / "src").mkdir()
    src = d / "src" / "styles.css"
    src.write_text(FIXTURE, encoding="utf-8")
    return d, src


def test_prepare_aggressive_never_touches_real_file():
    proj, src = _project()
    before = src.read_text(encoding="utf-8")
    core.prepare(proj, src, aggressive=True)
    after = src.read_text(encoding="utf-8")
    assert after == before, "prepare(aggressive=True) must never modify the real source file"
    shutil.rmtree(proj)


def test_llm_file_is_actually_minified():
    proj, src = _project()
    res = core.prepare(proj, src, aggressive=True)
    llm_text = Path(res["llm_path"]).read_text(encoding="utf-8")
    # the .llm working copy should be denser than the untouched real source
    assert len(llm_text) < len(FIXTURE)
    shutil.rmtree(proj)


def test_sync_preserves_original_formatting_for_untouched_rules():
    proj, src = _project()
    prep = core.prepare(proj, src, aggressive=True)
    llm_path = Path(prep["llm_path"])
    llm_text = llm_path.read_text(encoding="utf-8")

    # AI edits exactly one thing: .single's color
    assert ".single{color: blue;}" in llm_text or ".single{color:blue;}" in llm_text
    edited = llm_text.replace("color: blue", "color: green").replace("color:blue", "color:green")
    llm_path.write_text(edited, encoding="utf-8")

    core.sync(proj, src)
    new_source = src.read_text(encoding="utf-8")

    # the edited rule changed
    assert "color: green" in new_source

    # everything the AI didn't touch kept its ORIGINAL formatting exactly --
    # multi-line, original indentation, original comments -- not minified
    assert "/* Player controls */" in new_source
    assert ".a {\r\n    display: flex;" in new_source or ".a {\n    display: flex;" in new_source
    assert "@keyframes spin {" in new_source
    assert "    from { transform: rotate(0deg); }" in new_source
    shutil.rmtree(proj)


def test_sync_aggressive_handles_new_rule_without_reformatting_everything():
    proj, src = _project()
    prep = core.prepare(proj, src, aggressive=True)
    llm_path = Path(prep["llm_path"])
    llm_text = llm_path.read_text(encoding="utf-8")

    llm_text = llm_text.rstrip("\n") + "\n.brand-new{color:purple;}\n"
    llm_path.write_text(llm_text, encoding="utf-8")

    core.sync(proj, src)
    new_source = src.read_text(encoding="utf-8")

    assert ".brand-new" in new_source and "color:purple" in new_source
    # unrelated original rules still preserved with original formatting
    assert "/* Player controls */" in new_source
    assert "@keyframes spin {" in new_source
    shutil.rmtree(proj)


def test_status_reports_true_original_tokens():
    proj, src = _project()
    res = core.prepare(proj, src, aggressive=True)
    assert "true_original_tokens" in res["stats"]
    assert res["stats"]["true_original_tokens"] > 0
    shutil.rmtree(proj)


def test_restore_preserves_last_used_mode():
    proj, src = _project()
    core.prepare(proj, src, aggressive=True)
    llm_path = core.Paths.for_project(proj).llm_path_for("src/styles.css")
    llm_path.write_text("garbage {{{ not valid", encoding="utf-8")
    core.restore(proj, src)  # no aggressive= passed -- should remember aggressive mode
    m = core.load_manifest(core.Paths.for_project(proj).manifest_path_for("src/styles.css"))
    assert m.stats["mode"] == "aggressive"
    assert m.item_cache  # item cache should have been rebuilt
    shutil.rmtree(proj)


def test_crlf_line_endings_survive_byte_exact():
    """Regression test: Path.read_text()/write_text() silently convert CRLF
    to LF by default. The real uploaded CSS file that exposed this used
    CRLF throughout -- prepare()+sync() with zero AI edits must round-trip
    it byte-for-byte, \\r and all."""
    proj, src = _project()
    crlf_css = ".a {\r\n    color: red;\r\n}\r\n\r\n.b {\r\n    color: blue;\r\n}\r\n"
    src.write_bytes(crlf_css.encode("utf-8"))

    core.prepare(proj, src, aggressive=True)
    assert src.read_bytes() == crlf_css.encode("utf-8"), \
        "prepare() must never touch the real file, CRLF included"

    # sync with no edits should also be a byte-exact no-op
    res = core.sync(proj, src)
    assert res["changed"] is False
    assert src.read_bytes() == crlf_css.encode("utf-8")
    shutil.rmtree(proj)


def test_trailing_newline_survives_an_unrelated_edit():
    """Regression test: the file's final newline must survive even when
    an edit happens elsewhere -- the no-edit IN_SYNC shortcut doesn't
    exercise the reconstruction path at all, so this needs its own test."""
    proj, src = _project()
    crlf_css = ".a {\r\n    color: red;\r\n}\r\n\r\n.b {\r\n    color: blue;\r\n}\r\n"
    src.write_bytes(crlf_css.encode("utf-8"))

    prep = core.prepare(proj, src, aggressive=True)
    llm_path = Path(prep["llm_path"])
    llm_text = llm_path.read_text(encoding="utf-8")
    edited = llm_text.replace("color: red", "color: green")
    llm_path.write_text(edited, encoding="utf-8")

    core.sync(proj, src)
    final_bytes = src.read_bytes()
    assert final_bytes.endswith(b"}\r\n"), \
        f"expected the file's trailing newline to survive, got tail: {final_bytes[-10:]!r}"
    shutil.rmtree(proj)


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
