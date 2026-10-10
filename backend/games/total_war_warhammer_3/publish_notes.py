"""Changenote generation and upload recording for WH3 Workshop publishes.

Registered translation mods diff against their published baseline. Every other Workshop id is treated as a compat pack and goes through
totalwar-modding's change note CLI. `make_recorder` captures what is about to be uploaded and returns the hook `start_publish` calls on exit.
"""

from __future__ import annotations

import logging
from typing import Callable

from backend.games.total_war_warhammer_3 import compat_change_notes as ccn
from backend.games.total_war_warhammer_3 import translation_change_notes as tcn
from backend.games.total_war_warhammer_3.routes.translation import current_shipped_strings
from backend.games.total_war_warhammer_3.translation_mods import get_translation_mod

logger = logging.getLogger(__name__)

OnExit = Callable[[int | None], str | None]


def change_notes_for(ids: list[str]) -> dict:
    """Generate a changenote for each Workshop id from what changed since its last recorded upload.

    Args:
        ids: Workshop ids of translation mods and compat packs, in any mix.

    Returns:
        `{"notes": {id: {"note", "pending", "kind"} | None}, "errors": [str]}`. A failed builder adds its message to `errors` and maps its ids
        to None, so one failure never hides the other notes.
    """
    notes: dict[str, dict | None] = {}
    errors: list[str] = []
    compat_ids: list[str] = []
    for wid in ids:
        mod = get_translation_mod(wid)
        if mod is None:
            compat_ids.append(wid)
            continue
        try:
            note, pending = tcn.build_note(tcn.load_baseline(wid), current_shipped_strings(wid), tcn.parent_updated_at(mod.parent_workshop_ids))
        except Exception as exc:
            errors.append(f"{wid}: {getattr(exc, 'detail', None) or exc}")
            notes[wid] = None
            continue
        notes[wid] = {"note": note, "pending": pending, "kind": "translation"}

    if compat_ids:
        try:
            previews = ccn.preview(compat_ids)
        except ccn.ChangeNoteError as exc:
            errors.append(str(exc))
            previews = {}
        for wid in compat_ids:
            preview = previews.get(wid)
            notes[wid] = None if preview is None else {"note": preview["note"], "pending": bool(preview["pending"]), "kind": "compat"}

    return {"notes": notes, "errors": errors}


def make_recorder(workshop_id: str, changenote: str) -> OnExit | None:
    """Capture what is about to be uploaded and return a hook that records it once SteamCMD exits with code 0.

    Never raises. When the upload state cannot be captured, the publish still goes ahead but is not recorded.

    Args:
        workshop_id: Workshop id being published.
        changenote: The changenote sent with the upload.

    Returns:
        The `on_exit` hook, or None when there is nothing to record for this item (it is not a generated pack).
    """
    try:
        if get_translation_mod(workshop_id) is not None:
            shipped = current_shipped_strings(workshop_id)

            def record_translation(exit_code: int | None) -> str | None:
                """Save the captured strings as the mod's published baseline after a successful upload.

                Args:
                    exit_code: SteamCMD's exit code.

                Returns:
                    A status line on a successful record, or None otherwise.
                """
                if exit_code == 0:
                    tcn.save_baseline(workshop_id, shipped, changenote)
                    return "Recorded this upload for the next changenote."
                return None

            return record_translation

        preview = ccn.preview([workshop_id]).get(workshop_id)
        if preview is None:
            return None
        pack_sha = preview["pack_sha"]

        def record_compat(exit_code: int | None) -> str | None:
            """Record the captured pack hash in totalwar-modding's publish state after a successful upload.

            Args:
                exit_code: SteamCMD's exit code.

            Returns:
                A status line on a successful record, or None otherwise.
            """
            if exit_code == 0:
                ccn.record(workshop_id, pack_sha, changenote)
                return "Recorded this upload for the next changenote."
            return None

        return record_compat
    except Exception as exc:
        logger.warning("Could not capture the upload state for %s, so this publish will not be recorded: %s", workshop_id, exc)
        reason = str(exc)

        def record_capture_failed(exit_code: int | None) -> str | None:
            """Report that the upload was not recorded, since its state could not be captured when it started.

            Args:
                exit_code: SteamCMD's exit code.

            Returns:
                A status line on a successful upload, or None otherwise.
            """
            if exit_code == 0:
                return f"This upload was not recorded, so the next changenote may repeat these changes: {reason}"
            return None

        return record_capture_failed
