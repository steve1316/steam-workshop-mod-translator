"""Changenotes for WH3 translation mods, built by diffing the strings about to ship against those shipped at the last publish.

The baseline lives in `<root>/mods/{id}/published_baseline.json`, outside `snapshots/`, so snapshot pruning never removes it. It stores a hash per
`(source file, key)` because only equality matters for the diff.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from backend.games.storage_paths import game_storage_path
from backend.games.total_war_warhammer_3.routes._paths import tw3_workshop_manifest_path
from backend.games.total_war_warhammer_3.translation_drift import DriftRow, hash_text

GAME_ID = "total_war_warhammer_3"
BASELINE_FILENAME = "published_baseline.json"
GENERIC_NOTE = "[u]Translation update[/u]"
# One `"<workshop id>" { ... }` block in Steam's Workshop manifest. Each item appears in more than one section.
_MANIFEST_ITEM = re.compile(r'"(\d+)"\s*\{([^{}]*)\}')
_MANIFEST_UPDATED = re.compile(r'"timeupdated"\s*"(\d+)"')


def _baseline_path(mod_id: str) -> Path:
    """Return the published baseline path for a mod.

    Args:
        mod_id: Steam Workshop ID of the translation mod.

    Returns:
        `<root>/mods/{mod_id}/published_baseline.json`.
    """
    return game_storage_path(GAME_ID) / "mods" / mod_id / BASELINE_FILENAME


def shipped_strings(rows: Iterable[DriftRow]) -> dict[str, dict[str, str]]:
    """Hash the translated text a publish will ship, per source file and key.

    Args:
        rows: Drift rows with `translations.json` already overlaid.

    Returns:
        `{source_filename: {key: hash}}` for every non-orphan row with non-empty translated text.
    """
    shipped: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.status == "orphan" or not row.translation_text:
            continue
        shipped.setdefault(row.source_filename, {})[row.key] = hash_text(row.translation_text)
    return shipped


def load_baseline(mod_id: str) -> dict | None:
    """Read the strings recorded at the mod's last successful publish.

    Args:
        mod_id: Steam Workshop ID of the translation mod.

    Returns:
        `{"published_at", "changenote", "strings"}`, or None when the mod was never published from the app or the file is unreadable.
    """
    try:
        with _baseline_path(mod_id).open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_baseline(mod_id: str, strings: dict[str, dict[str, str]], changenote: str) -> None:
    """Atomically record the strings a successful publish shipped.

    Args:
        mod_id: Steam Workshop ID of the translation mod.
        strings: The shipped set captured when the upload started.
        changenote: The changenote sent with the upload.
    """
    path = _baseline_path(mod_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"published_at": datetime.now(timezone.utc).isoformat(), "changenote": changenote, "strings": strings}
    tmp = path.with_name(f"{BASELINE_FILENAME}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def diff_counts(baseline: dict[str, dict[str, str]], shipped: dict[str, dict[str, str]]) -> tuple[int, int, int]:
    """Count how the shipped set differs from the baseline, matching rows by source file and key.

    Args:
        baseline: `{source_filename: {key: hash}}` from the last publish.
        shipped: `{source_filename: {key: hash}}` about to be published.

    Returns:
        `(new, revised, removed)` row counts.
    """
    old = {(fn, key): h for fn, rows in baseline.items() for key, h in rows.items()}
    new = {(fn, key): h for fn, rows in shipped.items() for key, h in rows.items()}
    added = sum(1 for k in new if k not in old)
    revised = sum(1 for k, h in new.items() if k in old and old[k] != h)
    removed = sum(1 for k in old if k not in new)
    return added, revised, removed


def parent_updated_at(parent_ids: Iterable[str]) -> datetime | None:
    """Find when the most recently updated parent mod was last updated, from Steam's Workshop manifest.

    Args:
        parent_ids: Workshop ids of the translation mod's parent mods.

    Returns:
        The latest `timeupdated` among the parents as a local time, or None when the manifest is missing or lists none of them.
    """
    path = tw3_workshop_manifest_path()
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    wanted = set(parent_ids)
    times = [int(m) for wid, body in _MANIFEST_ITEM.findall(text) if wid in wanted for m in _MANIFEST_UPDATED.findall(body)]
    return datetime.fromtimestamp(max(times)) if times else None


def format_update_time(when: datetime) -> str:
    """Format a time the way the Steam Workshop shows it.

    Args:
        when: The time to format.

    Returns:
        e.g. `Oct 5 @ 10:53am`.
    """
    hour = when.hour % 12 or 12
    return f"{when:%b} {when.day} @ {hour}:{when:%M}{'am' if when.hour < 12 else 'pm'}"


def build_note(baseline: dict | None, shipped: dict[str, dict[str, str]], parent_updated: datetime | None) -> tuple[str, bool]:
    """Build a translation mod's changenote, naming when its parent mods were last updated.

    Args:
        baseline: The mod's published baseline, or None when it has none.
        shipped: The shipped set about to be published.
        parent_updated: When the most recently updated parent mod was last updated, or None when unknown.

    Returns:
        `(note, pending)`. The note is `GENERIC_NOTE` alone when `parent_updated` is unknown. It is pending without a baseline or when the
        shipped set differs from it.
    """
    note = GENERIC_NOTE
    if parent_updated is not None:
        note += f"\n\nUpdated the translations to the latest mod changes as of {format_update_time(parent_updated)}."
    if baseline is None:
        return note, True
    return note, any(diff_counts(baseline.get("strings") or {}, shipped))
