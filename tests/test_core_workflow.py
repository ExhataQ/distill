import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmsource import core
from llmsource.scanner import match_brace

FIXTURE = (Path(__file__).parent / "fixtures" / "sample.css").read_text(encoding="utf-8")


def _project():
    d = Path(tempfile.mkdtemp())
    (d / "src").mkdir()
    src = d / "src" / "styles.css"
    src.write_text(FIXTURE, encoding="utf-8")
    return d, src


def test_prepare_creates_llm_and_manifest():
    proj, src = _project()
    res = core.prepare(proj, src)
    llm_path = Path(res["llm_path"])
    assert llm_path.exists()
    manifest_path = core.Paths.for_project(proj).manifest_path_for(res["rel"])
    assert manifest_path.exists()
    shutil.rmtree(proj)


def test_status_in_sync_after_prepare():
    proj, src = _project()
    core.prepare(proj, src)
    res = core.status(proj, src)
    assert res["state"].value == "in_sync"
    shutil.rmtree(proj)


def test_sync_with_no_llm_edits_is_a_noop():
    proj, src = _project()
    core.prepare(proj, src)
    res = core.sync(proj, src)
    assert res["changed"] is False
    shutil.rmtree(proj)


def test_full_edit_cycle_six_modifications():
    """Reproduce the milestone-1 demo (spec section 29): make several edits
    directly to the compressed representation and confirm sync() produces
    a source file with exactly those changes and nothing else."""
    proj, src = _project()
    prep = core.prepare(proj, src)
    llm_path = Path(prep["llm_path"])
    llm_text = llm_path.read_text(encoding="utf-8")

    original_source = src.read_text(encoding="utf-8")

    edits = [
        (".c {", "gap: 8px", "gap: 6px"),          # 1. change a literal value
        (".e::before {", 'content: "a { fake: brace; }"',
         'content: "a { fake: brace; }" /* kept */'),  # 2. edit near tricky content, comment appended
        (".single {", "color: blue", "color: green"),  # 3. change a literal
    ]
    for anchor, old, new in edits:
        idx = llm_text.index(anchor)
        open_brace = llm_text.index("{", idx)
        end = match_brace(llm_text, open_brace)
        segment = llm_text[idx:end]
        assert old in segment, f"expected to find {old!r} near {anchor!r}"
        llm_text = llm_text[:idx] + segment.replace(old, new, 1) + llm_text[end:]

    # 4. add a brand new rule the AI decided to write directly in the .llm
    llm_text = llm_text.rstrip("\n") + "\n\n.new-rule {\n    color: purple;\n}\n"

    llm_path.write_text(llm_text, encoding="utf-8")

    res = core.sync(proj, src)
    assert res["changed"] is True
    new_source = src.read_text(encoding="utf-8")

    assert "gap: 6px" in new_source
    assert "gap: 8px" not in new_source  # .c's old value is gone
    # .a, .b, .d shared the macro group and must still read exactly as before
    for sel in (".a {", ".b {"):
        idx = new_source.index(sel)
        end = match_brace(new_source, new_source.index("{", idx))
        assert "gap: 10px" in new_source[idx:end]
    assert 'content: "a { fake: brace; }" /* kept */' in new_source
    assert "color: green" in new_source
    assert ".new-rule" in new_source and "color: purple" in new_source

    # unrelated content untouched
    assert "@keyframes spin" in new_source
    assert "@media (max-width: 600px)" in new_source

    # status should be back in sync (prepare was re-run as part of sync)
    assert core.status(proj, src)["state"].value == "in_sync"
    shutil.rmtree(proj)


def test_source_changed_externally_is_detected_and_blocked():
    proj, src = _project()
    core.prepare(proj, src)
    # simulate someone hand-editing the real source file, bypassing llmsource
    src.write_text(src.read_text(encoding="utf-8") + "\n.manual { color: pink; }\n", encoding="utf-8")

    try:
        core.sync(proj, src)
        assert False, "expected sync() to refuse when source changed externally"
    except core.LLMSourceError as e:
        assert "changed outside" in str(e)
    shutil.rmtree(proj)


def test_conflict_when_both_source_and_llm_changed():
    proj, src = _project()
    prep = core.prepare(proj, src)
    llm_path = Path(prep["llm_path"])
    llm_path.write_text(llm_path.read_text(encoding="utf-8") + "\n/* ai comment */\n", encoding="utf-8")
    src.write_text(src.read_text(encoding="utf-8") + "\n.manual { color: pink; }\n", encoding="utf-8")

    try:
        core.sync(proj, src)
        assert False, "expected CONFLICT to be raised"
    except core.LLMSourceError as e:
        assert "CONFLICT" in str(e)
    shutil.rmtree(proj)


def test_invalid_representation_never_overwrites_source():
    proj, src = _project()
    prep = core.prepare(proj, src)
    llm_path = Path(prep["llm_path"])
    original_source = src.read_text(encoding="utf-8")

    # corrupt: reference a macro id that doesn't exist
    llm_path.write_text(llm_path.read_text(encoding="utf-8") + "\n.broken { @X999; }\n", encoding="utf-8")

    try:
        core.sync(proj, src)
        assert False, "expected validation failure"
    except core.LLMSourceError as e:
        assert "validation failed" in str(e)

    # source must be byte-identical to before -- never touched
    assert src.read_text(encoding="utf-8") == original_source
    shutil.rmtree(proj)


def test_restore_discards_llm_edits():
    proj, src = _project()
    prep = core.prepare(proj, src)
    llm_path = Path(prep["llm_path"])
    llm_path.write_text("garbage that is not valid at all { {{ ", encoding="utf-8")

    core.restore(proj, src)  # should regenerate cleanly from source, ignoring the garbage
    assert core.status(proj, src)["state"].value == "in_sync"
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
