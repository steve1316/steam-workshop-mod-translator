"""Canonical translation status model shared across games.

Defines the five-state `RowStatus` every game adapter maps its rows into,
`classify_status()` which encodes the shared classification rules, and the
`StatusRow` response model returned to the frontend strings table.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

RowStatus = Literal["synced", "untouched", "pending", "changed", "missing", "untranslatable"]


def classify_status(
    *,
    untranslatable_reason: Optional[str] = None,
    has_translation: bool = False,
    is_synced: bool = False,
    is_untouched: bool = False,
    source_changed: bool = False,
) -> RowStatus:
    """Map a row's building-block flags to the canonical six-state status.

    Rules, in priority order:
    1. An untranslatable_reason always wins -> 'untranslatable'.
    2. A translated row whose source text drifted since it was last synced or
       translated is 'changed' and needs re-review or an update. This is a shared rule for every game (WH3 'stale' folds in here).
    3. A synced row with no source drift -> 'synced'.
    4. A row whose target text shipped with the mod and was never edited -> 'untouched'.
    5. Any other row that has a translation -> 'pending'.
    6. Everything else (no translation) -> 'missing'.

    WH3 'orphan' rows (a translation with no parent source) are mapped to 'missing'
    by the WH3 adapter before this function is reached.

    Args:
        untranslatable_reason: Non-empty when the row cannot be translated.
        has_translation: True when the row has target-language text.
        is_synced: True when the translation was written to the game's real files.
        is_untouched: True when the target text shipped with the mod and was never edited.
        source_changed: True when the source text drifted since the last sync/translate.

    Returns:
        One of 'synced', 'untouched', 'pending', 'changed', 'missing', 'untranslatable'.
    """
    if untranslatable_reason:
        return "untranslatable"
    if has_translation and source_changed:
        return "changed"
    if is_synced:
        return "synced"
    if is_untouched:
        return "untouched"
    if has_translation:
        return "pending"
    return "missing"


class StatusRow(BaseModel):
    """One row in the unified strings table, shared by all games.

    Attributes:
        key: Localization key.
        source_file: File the key belongs to. Empty string when not applicable.
        source_text: Source-language text, or None when absent (orphan rows).
        target_text: Target-language (translated) text, or None when missing.
        status: Canonical five-state status from `classify_status`.
        provider: Who produced the translation ('claude', 'manual', ...), or None.
        original_target: Target text that shipped with the mod, for the prev-translation hint.
        untranslatable_reason: User-facing reason the row cannot be translated, when applicable.
    """

    key: str
    source_file: str = ""
    source_text: Optional[str] = None
    target_text: Optional[str] = None
    status: RowStatus
    provider: Optional[str] = None
    original_target: Optional[str] = None
    untranslatable_reason: Optional[str] = None
