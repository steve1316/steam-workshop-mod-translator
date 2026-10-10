"""Thin wrappers binding the shared per-mod storage helpers to the WH3 game id.

`backend/data/translation_store.py` and `backend/data/character_context.py`
default to the Chrono Ark storage root unless an explicit `storage_path` is
passed. These wrappers inject the WH3 root so route code stays terse.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.data import character_context as cc
from backend.data import translation_store as ts
from backend.games.storage_paths import game_storage_path

GAME_ID = "total_war_warhammer_3"


def _root() -> Path:
    return game_storage_path(GAME_ID)


def load_translations(mod_id: str) -> dict[str, str]:
    """Load `{key: text}` for the WH3 mod.

    Args:
        mod_id: The mod identifier.

    Returns:
        Dictionary mapping translation keys to text strings.
    """
    return ts.load_translations(mod_id, storage_path=_root())


def load_translations_raw(mod_id: str) -> dict[str, dict]:
    """Load `{key: {text, created_at, updated_at}}` for the WH3 mod.

    Args:
        mod_id: The mod identifier.

    Returns:
        Dictionary mapping translation keys to timestamped entry dicts.
    """
    return ts.load_translations_raw(mod_id, storage_path=_root())


def save_translations_raw(mod_id: str, data: dict[str, dict]) -> None:
    """Persist the full timestamped dict back to disk for the WH3 mod.

    Args:
        mod_id: The mod identifier.
        data: Dictionary mapping translation keys to timestamped entry dicts.
    """
    path = _root() / "mods" / mod_id / "translations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_character_context(mod_id: str) -> dict:
    """Load mod-context for the WH3 mod (repurposed character_context schema).

    Args:
        mod_id: The mod identifier.

    Returns:
        Dictionary containing mod context fields (source_game, character_name, etc).
    """
    return cc.load_character_context(mod_id, storage_path=_root())


def save_character_context(mod_id: str, ctx: dict) -> None:
    """Save mod-context for the WH3 mod.

    Args:
        mod_id: The mod identifier.
        ctx: Dictionary containing mod context fields to persist.
    """
    cc.save_character_context(mod_id, ctx, storage_path=_root())


def load_include_translated_names(mod_id: str) -> bool:
    """Load whether translated names outside the glossary are sent with every prompt.

    Args:
        mod_id: The mod identifier.

    Returns:
        The saved setting, or False when it was never saved or the file is unreadable.
    """
    path = _root() / "mods" / mod_id / "prompt_options.json"
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as f:
            return bool(json.load(f).get("include_translated_names", False))
    except (json.JSONDecodeError, OSError, AttributeError):
        return False


def save_include_translated_names(mod_id: str, value: bool) -> None:
    """Save whether translated names outside the glossary are sent with every prompt.

    Args:
        mod_id: The mod identifier.
        value: The setting to save.
    """
    path = _root() / "mods" / mod_id / "prompt_options.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"include_translated_names": value}, f, indent=2)


def load_parent_snapshot(mod_id: str) -> dict[str, dict[str, dict]]:
    """Load `{filename: {key: {hash, text}}}` snapshot, defaulting to empty.

    Legacy files stored a bare hash string per key. Those load as `{"hash": <string>, "text": None}`.

    Args:
        mod_id: The mod identifier.

    Returns:
        Dictionary mapping filenames to dicts of key/entry pairs, or empty dict.
    """
    path = _root() / "mods" / mod_id / "parent_snapshot.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return {fn: {k: (v if isinstance(v, dict) else {"hash": v, "text": None}) for k, v in entries.items()} for fn, entries in raw.items()}


def save_parent_snapshot(mod_id: str, snapshot: dict[str, dict[str, str]]) -> None:
    """Persist the parent-text-hash snapshot for the WH3 mod.

    Args:
        mod_id: The mod identifier.
        snapshot: Dictionary mapping filenames to dicts of key/hash pairs.
    """
    path = _root() / "mods" / mod_id / "parent_snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)


def parent_pack_cache_dir(parent_workshop_id: str) -> Path:
    """Return the per-parent RPFM extract cache dir.

    Args:
        parent_workshop_id: The Steam Workshop ID of the parent pack.

    Returns:
        Path to the cache directory for the parent pack.
    """
    return _root() / "parent_pack_cache" / parent_workshop_id
