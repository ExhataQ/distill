from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core
from .manifest import SyncState


def _cmd_prepare(args):
    for f in args.files:
        res = core.prepare(args.project, f, aggressive=args.aggressive)
        s = res["stats"]
        print(f"prepared {res['rel']} [{s['mode']}]: {s['original_tokens']} -> {s['compressed_tokens']} tokens "
              f"({s['reduction_pct']}% reduction, {s['macro_count']} macros)")


def _cmd_sync(args):
    exit_code = 0
    for f in args.files:
        try:
            res = core.sync(args.project, f, recompress=not args.no_recompress, force=args.force)
        except core.LLMSourceError as e:
            print(f"error: {e}", file=sys.stderr)
            exit_code = 1
            continue
        if res["changed"]:
            print(f"synced {res['rel']} ({res['state'].value})")
        else:
            print(f"{res['rel']}: already in sync")
    sys.exit(exit_code)


def _cmd_restore(args):
    for f in args.files:
        res = core.restore(args.project, f)
        print(f"restored {res['rel']} from source (regenerated .llm)")


def _cmd_status(args):
    for f in args.files:
        res = core.status(args.project, f)
        state = res["state"]
        print(f"{res['rel']}: {state.value}")
        if res["manifest"] is not None:
            s = res["manifest"].stats
            if s:
                print(f"  original:   {s.get('original_tokens', '?')} tokens")
                print(f"  compressed: {s.get('compressed_tokens', '?')} tokens")
                print(f"  saved:      {s.get('saved_tokens', '?')} tokens "
                      f"({s.get('reduction_pct', '?')}%)")
                print(f"  macros:     {s.get('macro_count', '?')} "
                      f"({s.get('macro_references', '?')} references)")


def _cmd_validate(args):
    exit_code = 0
    for f in args.files:
        paths = core.Paths.for_project(args.project)
        rel = core._rel(paths.project_root, Path(f).resolve())
        llm_path = paths.llm_path_for(rel)
        if not llm_path.exists():
            print(f"{rel}: no .llm file, nothing to validate", file=sys.stderr)
            exit_code = 1
            continue
        ok, _, err = core.validate_llm_text(core.read_text_exact(llm_path))
        if ok:
            print(f"{rel}: valid")
        else:
            print(f"{rel}: INVALID -- {err}", file=sys.stderr)
            exit_code = 1
    sys.exit(exit_code)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="llmsource")
    parser.add_argument("--project", default=".", help="project root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, fn, help_text in [
        ("prepare", _cmd_prepare, "compress source file(s) into .llm/"),
        ("sync", _cmd_sync, "expand edited .llm file(s) back into source"),
        ("restore", _cmd_restore, "discard .llm edits, regenerate from source"),
        ("status", _cmd_status, "show sync state and token savings"),
        ("validate", _cmd_validate, "validate .llm file(s) without writing source"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("files", nargs="+", help="source file path(s)")
        if name == "sync":
            p.add_argument("--no-recompress", action="store_true",
                            help="don't recompress after sync; just update hashes")
            p.add_argument("--force", action="store_true",
                            help="proceed even if source changed externally or there's a conflict")
        if name == "prepare":
            p.add_argument("--aggressive", action="store_true",
                            help="also minify the real source file in place (strips comments/"
                                 "whitespace) -- one-way formatting change, off by default")
        p.set_defaults(func=fn)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
