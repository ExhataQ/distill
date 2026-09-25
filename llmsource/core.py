from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def read_text_exact(path: Path) -> str:
    """Read a file without Python's universal-newline translation, so CRLF/
    CR/LF line endings survive exactly as they are on disk. Path.read_text()
    silently converts everything to '\\n' by default -- for a tool whose
    whole promise is byte-exact reversibility, that's a correctness bug,
    not a convenience."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write_text_exact(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


from .aggressive import ItemPairs, build_item_pairs, reconstruct_preserving_untouched_formatting
from .languages.registry import adapter_for_language, adapter_for_path
from .manifest import Manifest, SyncState, detect_sync_state, load_manifest, save_manifest, sha256_text
from .tokenizer import DEFAULT_TOKENIZER, estimate_tokens


class LLMSourceError(Exception):
    pass


@dataclass
class Paths:
    project_root: Path
    llm_dir: Path
    compressed_dir: Path
    manifests_dir: Path

    @staticmethod
    def for_project(project_root: str | Path) -> "Paths":
        root = Path(project_root).resolve()
        llm_dir = root / ".llm"
        return Paths(root, llm_dir, llm_dir / "compressed", llm_dir / "manifests")

    def llm_path_for(self, source_rel: str) -> Path:
        return self.compressed_dir / f"{source_rel}.llm"

    def manifest_path_for(self, source_rel: str) -> Path:
        return self.manifests_dir / f"{source_rel}.json"


def _rel(project_root: Path, source_path: Path) -> str:
    return str(source_path.resolve().relative_to(project_root))


def prepare(project_root: str | Path, source_path: str | Path, aggressive: bool = False) -> dict:
    """(Re)generate the .llm representation and manifest from the current
    source file, discarding any existing .llm edits for this file.

    The real source file is NEVER modified by prepare(), in either mode --
    matching the spec's core invariant (.llm/ is a cache, the real source
    stays authoritative and untouched until you explicitly sync() edited
    content back). aggressive=True only changes what the AI *sees*: the
    .llm representation is built from a minified view of the source
    (comments stripped, whitespace collapsed -- see minify.py). On sync(),
    that minification is only ever applied to whatever the AI actually
    changed; everything else is written back in your original formatting
    (see aggressive.py).
    """
    paths = Paths.for_project(project_root)
    src = Path(source_path).resolve()
    if not src.exists():
        raise LLMSourceError(f"source file not found: {src}")
    rel = _rel(paths.project_root, src)

    source_text = read_text_exact(src)
    adapter = adapter_for_path(src)

    prev_manifest = load_manifest(paths.manifest_path_for(rel))
    previous_macros = prev_manifest.macros if prev_manifest else None

    item_cache = {}
    if aggressive:
        pairs = build_item_pairs(source_text, adapter)
        compress_input = pairs.minified_source
        item_cache = {"original": pairs.original_texts, "minified": pairs.minified_texts}
    else:
        compress_input = source_text

    llm_text, meta = adapter.compress(compress_input, DEFAULT_TOKENIZER, previous_macros)

    llm_path = paths.llm_path_for(rel)
    llm_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_exact(llm_path, llm_text)

    meta["stats"]["mode"] = "aggressive" if aggressive else "safe"
    # report savings against the TRUE original file, not an intermediate
    meta["stats"]["true_original_tokens"] = estimate_tokens(source_text, DEFAULT_TOKENIZER)

    manifest = Manifest(
        source=rel,
        source_hash=sha256_text(source_text),
        llm_hash=sha256_text(llm_text),
        language=adapter.name,
        macros=meta["macros"],
        stats=meta["stats"],
        item_cache=item_cache,
    )
    save_manifest(paths.manifest_path_for(rel), manifest)

    return {"rel": rel, "llm_path": str(llm_path), "manifest": manifest, "stats": meta["stats"]}


def status(project_root: str | Path, source_path: str | Path) -> dict:
    paths = Paths.for_project(project_root)
    src = Path(source_path).resolve()
    rel = _rel(paths.project_root, src)
    manifest = load_manifest(paths.manifest_path_for(rel))
    llm_path = paths.llm_path_for(rel)

    current_source = read_text_exact(src) if src.exists() else None
    current_llm = read_text_exact(llm_path) if llm_path.exists() else None

    state = detect_sync_state(manifest, current_source, current_llm)
    return {"rel": rel, "state": state, "manifest": manifest}


def validate_llm_text(llm_text: str, adapter) -> tuple[bool, str | None, str | None]:
    """Expand + re-parse using the given LanguageAdapter. Returns
    (ok, reconstructed_source, error_message)."""
    try:
        reconstructed = adapter.expand(llm_text)
    except Exception as e:  # adapters raise their own ExpandError subtypes
        return False, None, f"expand failed: {e}"

    try:
        items = adapter.parse_to_items(reconstructed)
        if "".join(items) != reconstructed:
            return False, None, "internal error: reconstruct(parse(x)) != x"
    except Exception as e:  # defensive: never let a parser crash corrupt the source
        return False, None, f"reconstructed source failed structural validation: {e}"

    if adapter.extra_validate is not None:
        err = adapter.extra_validate(reconstructed)
        if err:
            return False, None, err

    return True, reconstructed, None


def sync(project_root: str | Path, source_path: str | Path, recompress: bool = True,
         force: bool = False) -> dict:
    paths = Paths.for_project(project_root)
    src = Path(source_path).resolve()
    rel = _rel(paths.project_root, src)
    manifest = load_manifest(paths.manifest_path_for(rel))
    llm_path = paths.llm_path_for(rel)

    if manifest is None:
        raise LLMSourceError(f"no manifest for {rel}; run `prepare` first")
    if not llm_path.exists():
        raise LLMSourceError(f"{llm_path} is missing; run `prepare` first")

    adapter = adapter_for_language(manifest.language)
    current_source = read_text_exact(src) if src.exists() else None
    llm_text = read_text_exact(llm_path)

    state = detect_sync_state(manifest, current_source, llm_text)

    if state == SyncState.IN_SYNC:
        return {"rel": rel, "state": state, "changed": False}

    if state == SyncState.SOURCE_CHANGED and not force:
        raise LLMSourceError(
            f"{rel}: source file changed outside llmsource since last prepare; "
            f"run `llmsource prepare` to reconcile (this will discard the stale .llm), "
            f"or re-run sync with force=True if you're sure the .llm edits should win"
        )

    if state == SyncState.CONFLICT and not force:
        raise LLMSourceError(
            f"{rel}: CONFLICT -- both the source file and the .llm representation "
            f"changed independently since the last prepare. Refusing to guess which "
            f"one wins. Resolve manually, then run prepare."
        )

    ok, reconstructed, err = validate_llm_text(llm_text, adapter)
    if not ok:
        raise LLMSourceError(f"{rel}: validation failed, source NOT modified: {err}")

    is_aggressive = manifest.stats.get("mode") == "aggressive"
    if is_aggressive:
        if not manifest.item_cache:
            raise LLMSourceError(
                f"{rel}: manifest is missing its item cache; run `prepare` again with "
                f"aggressive=True before syncing"
            )
        old_pairs = ItemPairs(
            original_texts=manifest.item_cache["original"],
            minified_texts=manifest.item_cache["minified"],
        )
        final_source = reconstruct_preserving_untouched_formatting(old_pairs, reconstructed, adapter)
    else:
        final_source = reconstructed

    # Safe write: temp file first, then atomic replace.
    tmp_path = src.with_suffix(src.suffix + ".llmsource.tmp")
    write_text_exact(tmp_path, final_source)
    tmp_path.replace(src)

    result = {"rel": rel, "state": state, "changed": True, "source_written": str(src)}

    if recompress:
        prep = prepare(project_root, source_path, aggressive=is_aggressive)
        result["stats"] = prep["stats"]
    else:
        new_manifest = Manifest(
            source=rel,
            source_hash=sha256_text(final_source),
            llm_hash=sha256_text(llm_text),
            language=adapter.name,
            macros=manifest.macros,
            stats=manifest.stats,
            item_cache=manifest.item_cache,
        )
        save_manifest(paths.manifest_path_for(rel), new_manifest)

    return result


def restore(project_root: str | Path, source_path: str | Path, aggressive: bool | None = None) -> dict:
    """Discard any .llm edits and regenerate the representation fresh from
    the current source (the inverse of `sync`: source wins). If aggressive
    is not specified, keeps whatever mode was last used."""
    if aggressive is None:
        paths = Paths.for_project(project_root)
        rel = _rel(paths.project_root, Path(source_path).resolve())
        prev = load_manifest(paths.manifest_path_for(rel))
        aggressive = bool(prev and prev.stats.get("mode") == "aggressive")
    return prepare(project_root, source_path, aggressive=aggressive)
