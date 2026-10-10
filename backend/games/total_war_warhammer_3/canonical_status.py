"""Map WH3 drift rows to the canonical six-state status model.

WH3's native drift model (translated/untranslated/stale/orphan) plus its
`translations.json` overlay are folded into the shared `RowStatus` so the WH3
page renders the same status chips as Chrono Ark. `synced` means the effective
translation already matches what is written on disk in the `.loc.tsv` (no pending
differing override). A source-text drift (`stale`) becomes `changed`, and an unsynced edit with no drift is `pending`. `orphan` and
`untranslated` become `missing`. A row whose parent source is blank becomes `untranslatable`. WH3 has no `untouched` state, so it never emits it.
"""

from __future__ import annotations

from backend.games.total_war_warhammer_3.translation_drift import DriftRow
from backend.translation.status import StatusRow, classify_status


def to_status_rows(drift: list[DriftRow], raw_translations: dict) -> list[StatusRow]:
    """Map drift rows plus the translations.json overlay to canonical StatusRows.

    Args:
        drift: Rows from `compute_drift` (parent vs `.loc.tsv` vs snapshot).
        raw_translations: The mod's `translations.json` mapping (`key -> {text, provider}`).

    Returns:
        One `StatusRow` per drift row, in input order, carrying the canonical status.
    """
    rows: list[StatusRow] = []
    for row in drift:
        entry = raw_translations.get(row.key)
        has_override = isinstance(entry, dict) and "text" in entry
        loc_text = row.translation_text or ""
        target_text = entry["text"] if has_override else row.translation_text
        has_translation = bool(target_text)

        if has_override and target_text:
            provider = entry.get("provider") or "manual"
        elif has_override:
            provider = None
        else:
            provider = "manual" if loc_text else None

        # A blank parent source has nothing to translate.
        untranslatable_reason = "Source text is empty" if row.parent_text is not None and not row.parent_text.strip() else None

        if row.status == "orphan":
            status = "missing"
        else:
            is_synced = has_translation and (target_text or "") == loc_text
            status = classify_status(
                untranslatable_reason=untranslatable_reason,
                has_translation=has_translation,
                is_synced=is_synced,
                source_changed=row.status == "stale",
            )

        rows.append(
            StatusRow(
                key=row.key,
                source_file=row.source_filename,
                source_text=row.parent_text,
                target_text=target_text,
                status=status,
                provider=provider,
                untranslatable_reason=untranslatable_reason,
            )
        )
    return rows
