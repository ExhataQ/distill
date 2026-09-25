"""Registry of available LanguageAdapters. Adding a new language means
adding one entry here (and to _EXTENSION_LANGUAGES) -- core.py never
needs to change."""
from __future__ import annotations

from pathlib import Path

from . import css as _css
from . import json as _json
from .base import LanguageAdapter

ADAPTERS: dict[str, LanguageAdapter] = {
    "css": _css.ADAPTER,
    "json": _json.ADAPTER,
}

_EXTENSION_LANGUAGES: dict[str, str] = {
    ".css": "css",
    ".json": "json",
}


def adapter_for_language(language: str) -> LanguageAdapter:
    try:
        return ADAPTERS[language]
    except KeyError:
        raise ValueError(f"no language adapter registered for {language!r}")


def adapter_for_path(path: str | Path) -> LanguageAdapter:
    ext = Path(path).suffix.lower()
    language = _EXTENSION_LANGUAGES.get(ext)
    if language is None:
        raise ValueError(f"no language adapter for file extension {ext!r} ({path})")
    return adapter_for_language(language)
