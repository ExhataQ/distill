from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

REPRESENTATION_VERSION = 1
COMPRESSOR_VERSION = "0.1.0"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SyncState(str, Enum):
    NO_MANIFEST = "no_manifest"          # never prepared
    IN_SYNC = "in_sync"                  # source and .llm both match manifest
    LLM_EDITED = "llm_edited"            # only the .llm changed -> normal sync
    SOURCE_CHANGED = "source_changed"    # only the source changed externally -> needs reconcile
    CONFLICT = "conflict"                # both changed independently -> needs reconcile
    LLM_MISSING = "llm_missing"          # manifest exists but .llm file is gone


@dataclass
class Manifest:
    source: str
    source_hash: str
    llm_hash: str
    representation_version: int = REPRESENTATION_VERSION
    language: str = "css"
    compressor_version: str = COMPRESSOR_VERSION
    macros: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    item_cache: dict = field(default_factory=dict)  # aggressive mode only: {"original": [...], "minified": [...]}

    def to_json(self) -> str:
        return json.dumps({
            "source": self.source,
            "source_hash": self.source_hash,
            "llm_hash": self.llm_hash,
            "representation_version": self.representation_version,
            "language": self.language,
            "compressor_version": self.compressor_version,
            "macros": self.macros,
            "stats": self.stats,
            "item_cache": self.item_cache,
        }, indent=2, sort_keys=False)

    @staticmethod
    def from_json(text: str) -> "Manifest":
        d = json.loads(text)
        return Manifest(
            source=d["source"],
            source_hash=d["source_hash"],
            llm_hash=d["llm_hash"],
            representation_version=d.get("representation_version", REPRESENTATION_VERSION),
            language=d.get("language", "css"),
            compressor_version=d.get("compressor_version", COMPRESSOR_VERSION),
            macros=d.get("macros", {}),
            stats=d.get("stats", {}),
            item_cache=d.get("item_cache", {}),
        )


def load_manifest(path: Path) -> Manifest | None:
    if not path.exists():
        return None
    return Manifest.from_json(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: Manifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.to_json(), encoding="utf-8")


def detect_sync_state(manifest: Manifest | None, current_source: str | None,
                       current_llm: str | None) -> SyncState:
    if manifest is None:
        return SyncState.NO_MANIFEST
    if current_llm is None:
        return SyncState.LLM_MISSING

    source_matches = current_source is not None and sha256_text(current_source) == manifest.source_hash
    llm_matches = sha256_text(current_llm) == manifest.llm_hash

    if source_matches and llm_matches:
        return SyncState.IN_SYNC
    if source_matches and not llm_matches:
        return SyncState.LLM_EDITED
    if not source_matches and llm_matches:
        return SyncState.SOURCE_CHANGED
    return SyncState.CONFLICT
