"""Pure helpers that build new WH3 parent source snapshots.

A snapshot maps `filename -> key -> {hash, text}` for the parent source text the user last translated against. Drifted entries are kept so the row
stays `changed` until it is updated or accepted.
"""

from __future__ import annotations

from backend.games.total_war_warhammer_3.loc_extractor import LocRow
from backend.games.total_war_warhammer_3.translation_drift import entry_hash, hash_text, make_entry


def _copy(snapshot: dict) -> dict:
    return {fn: dict(entries) for fn, entries in snapshot.items()}


def refresh_unchanged(snapshot: dict, parent: dict[str, dict[str, LocRow]]) -> dict:
    """Return a copy of `snapshot` with current entries for every parent key whose source did not drift.

    New keys get an entry. Keys whose recorded hash matches the current text are rewritten as `{hash, text}`, which backfills the text of legacy
    entries. Keys whose recorded hash differs keep their old entry. Keys missing from `parent` are kept as they are.

    Args:
        snapshot: The current snapshot, `{filename: {key: entry}}`.
        parent: Current parent strings, `{filename: {key: LocRow}}`.

    Returns:
        A new snapshot dict. The input is not modified.
    """
    out = _copy(snapshot)
    for filename, rows in parent.items():
        entries = out.setdefault(filename, {})
        for key, row in rows.items():
            recorded = entry_hash(entries.get(key))
            if recorded is None or recorded == hash_text(row.text):
                entries[key] = make_entry(row.text)
    return out


def accept_keys(snapshot: dict, parent: dict[str, dict[str, LocRow]], keys: set[str]) -> dict:
    """Return a copy of `snapshot` where each key in `keys` takes the current parent text as its baseline.

    Args:
        snapshot: The current snapshot, `{filename: {key: entry}}`.
        parent: Current parent strings, `{filename: {key: LocRow}}`.
        keys: Localization keys to re-baseline.

    Returns:
        A new snapshot dict. The input is not modified.
    """
    out = _copy(snapshot)
    for filename, rows in parent.items():
        for key, row in rows.items():
            if key in keys:
                out.setdefault(filename, {})[key] = make_entry(row.text)
    return out
