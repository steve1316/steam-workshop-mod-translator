"""HTTP routes for TW3 Steam Workshop preview images served from disk."""

from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from backend import config
from backend.games.total_war_warhammer_3 import published_times, publish_notes
from backend.games.total_war_warhammer_3 import workshop_batch_publisher as wbp
from backend.games.total_war_warhammer_3 import workshop_publisher as wp
from backend.games.total_war_warhammer_3.routes._paths import tw3_workshop_content_dir
from backend.games.total_war_warhammer_3.translation_change_notes import parent_updated_at

router = APIRouter()

_WORKSHOP_ID_RE = re.compile(r"^\d+$")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@router.get("/packs/{workshop_id}/preview")
def get_pack_preview(workshop_id: str):
    """Stream the first image file inside the local Steam Workshop folder for `workshop_id`.

    Args:
        workshop_id: Numeric Steam Workshop item id.

    Returns:
        `FileResponse` for the resolved image file.

    Raises:
        HTTPException(400): When `workshop_id` is not purely numeric.
        HTTPException(404): When the Steam library drive is unset, the workshop
            folder does not exist on disk, or no image file is found inside.
    """
    if not _WORKSHOP_ID_RE.fullmatch(workshop_id):
        raise HTTPException(status_code=400, detail="workshop_id must be numeric")

    folder = tw3_workshop_content_dir(workshop_id)
    if folder is None or not folder.is_dir():
        raise HTTPException(status_code=404, detail="workshop folder not found")

    # Sort entries for deterministic ordering when a folder contains multiple images.
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and entry.suffix.lower() in _IMAGE_SUFFIXES:
            return FileResponse(entry, media_type=_MEDIA_TYPES[entry.suffix.lower()])

    raise HTTPException(status_code=404, detail="no preview image found")


@router.get("/packs/{workshop_id}/last_modified")
def get_pack_last_modified(workshop_id: str):
    """Return the newest mtime among files inside the local Workshop folder for `workshop_id`.

    Used by the TW3 Dashboard to surface "Rebuilt Nh ago" beneath each pack card. The newest mtime across the
    folder's files captures the last time the user rebuilt or otherwise touched the pack on disk. Also returns the Steam Workshop
    `timeupdated` for the item from Steam's manifest, which is when it was last published.

    Args:
        workshop_id: Numeric Steam Workshop item id.

    Returns:
        `{"last_modified_unix": float | None, "published_unix": float | None}` - the most recent mtime in the folder as a unix timestamp
        (`None` when the folder has no files), and the Steam `timeupdated` as a unix timestamp (`None` when the manifest lacks the item).

    Raises:
        HTTPException(400): When `workshop_id` is not purely numeric.
        HTTPException(404): When the Steam library drive is unset or the workshop folder does not exist on disk.
    """
    if not _WORKSHOP_ID_RE.fullmatch(workshop_id):
        raise HTTPException(status_code=400, detail="workshop_id must be numeric")

    folder = tw3_workshop_content_dir(workshop_id)
    if folder is None or not folder.is_dir():
        raise HTTPException(status_code=404, detail="workshop folder not found")

    newest: float | None = None
    for entry in folder.iterdir():
        if entry.is_file():
            mtime = entry.stat().st_mtime
            if newest is None or mtime > newest:
                newest = mtime

    # Steam's manifest lags behind a fresh upload, so take whichever is newer of it and the time this app recorded.
    manifest = parent_updated_at([workshop_id])
    candidates = [t for t in (manifest.timestamp() if manifest else None, published_times.get(workshop_id)) if t is not None]
    return {"last_modified_unix": newest, "published_unix": max(candidates) if candidates else None}


@router.post("/packs/{workshop_id}/open")
def open_pack_folder(workshop_id: str):
    """Open the local Steam Workshop folder for `workshop_id` in the system file explorer.

    Args:
        workshop_id: Numeric Steam Workshop item id.

    Returns:
        `{"status": "success"}` when the folder is opened.

    Raises:
        HTTPException(400): When `workshop_id` is not purely numeric.
        HTTPException(404): When the Steam library drive is unset or the workshop folder does not exist on disk.
        HTTPException(500): When the OS call to open the folder fails.
    """
    if not _WORKSHOP_ID_RE.fullmatch(workshop_id):
        raise HTTPException(status_code=400, detail="workshop_id must be numeric")

    folder = tw3_workshop_content_dir(workshop_id)
    if folder is None or not folder.is_dir():
        raise HTTPException(status_code=404, detail="workshop folder not found")

    try:
        os.startfile(folder)
        return {"status": "success"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {exc}")


class ChangeNotesBody(BaseModel):
    """Request body for POST /packs/change-notes."""

    ids: list[str]
    """ Workshop ids to generate changenotes for. Non-numeric and duplicate ids are ignored. """


@router.post("/packs/change-notes")
def post_change_notes(body: ChangeNotesBody):
    """Generate a Workshop changenote for each requested item from what changed since its last recorded upload.

    Args:
        body: Request body with the Workshop `ids`.

    Returns:
        `{"notes": {id: {"note", "pending", "kind"} | None}, "errors": [str]}`. Builder failures land in `errors` and map their ids to None.
    """
    ids = [wid for wid in dict.fromkeys(body.ids) if _WORKSHOP_ID_RE.fullmatch(wid)]
    return publish_notes.change_notes_for(ids)


class PublishBody(BaseModel):
    """Request body for POST /packs/{workshop_id}/publish."""

    changenote: str = ""
    """ Update note shown in the Workshop changelog. Empty string omits the field from the VDF. """


@router.post("/packs/{workshop_id}/publish")
def post_pack_publish(workshop_id: str, body: PublishBody):
    """Spawn SteamCMD to push the local workshop folder as an update to the existing Workshop item.

    Single-flight - a 409 is returned if another publish is still running. Live progress is
    available via `GET /packs/{workshop_id}/publish/stream`.

    Args:
        workshop_id: Numeric Steam Workshop item id of the existing entry to update.
        body: Request body with the `changenote` for this update.

    Returns:
        `{"publish_id", "workshop_id", "started_at"}` on success.

    Raises:
        HTTPException(400): When `workshop_id` is not purely numeric, or when preflight checks
            fail (missing `steamcmd_path`, `steam_username`, or content folder). Detail body
            for preflight failures is `{"missing": [...]}`.
        HTTPException(404): When the Steam library drive is unset or the workshop folder does not
            exist on disk.
        HTTPException(409): When another publish is already in progress.
    """
    if not _WORKSHOP_ID_RE.fullmatch(workshop_id):
        raise HTTPException(status_code=400, detail="workshop_id must be numeric")

    folder = tw3_workshop_content_dir(workshop_id)
    if folder is None or not folder.is_dir():
        raise HTTPException(status_code=404, detail="workshop folder not found")

    # Check before make_recorder, since its pack-hashing preview can take up to 60s and would otherwise
    # delay reporting that a publish (single or batch) is already in progress.
    if wbp.current_batch() is not None or not wp.is_idle():
        raise HTTPException(status_code=409, detail="a publish is already in progress")

    # Capture what is about to be uploaded so a successful publish can be recorded for the next changenote.
    on_exit = publish_notes.make_recorder(workshop_id, body.changenote)
    try:
        handle = wp.start_publish(
            workshop_id,
            folder,
            body.changenote,
            steamcmd_path=config.STEAMCMD_PATH,
            steam_username=config.STEAM_USERNAME,
            on_exit=on_exit,
        )
    except wp.PublisherPreflightError as exc:
        raise HTTPException(status_code=400, detail={"missing": exc.missing})
    except wp.PublishInProgressError:
        raise HTTPException(status_code=409, detail="a publish is already in progress")

    return {
        "publish_id": handle.publish_id,
        "workshop_id": handle.workshop_id,
        "started_at": handle.started_at.isoformat(),
    }


@router.get("/packs/{workshop_id}/publish/stream")
async def get_pack_publish_stream(workshop_id: str):
    """Server-sent events: stream buffered + new SteamCMD log lines until publish ends.

    Each `data:` event carries one stdout line. A final `event: done` arrives when SteamCMD
    exits (or immediately if no publish has ever started in this session). The `workshop_id`
    path parameter is validated for shape but not cross-checked against the active publish -
    only one publish runs at a time.

    Args:
        workshop_id: Numeric Steam Workshop item id (validated for shape only).

    Returns:
        A `StreamingResponse` with `text/event-stream` content type.

    Raises:
        HTTPException(400): When `workshop_id` is not purely numeric.
    """
    if not _WORKSHOP_ID_RE.fullmatch(workshop_id):
        raise HTTPException(status_code=400, detail="workshop_id must be numeric")

    async def event_source():
        async for event in wp.stream_lines():
            kind = event["event"]
            if kind == "data":
                payload = {"line": event["line"], "ts": event["ts"]}
                yield f"data: {json.dumps(payload)}\n\n"
            elif kind == "done":
                payload = {
                    "exit_code": event.get("exit_code"),
                    "duration_seconds": event.get("duration_seconds"),
                }
                yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                break

    return StreamingResponse(event_source(), media_type="text/event-stream")


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# POST /packs/publish-all - sequential batch publish across many mods, each with its own changelog


class PublishAllItem(BaseModel):
    """One mod entry in a batch publish request."""

    workshop_id: str
    """ Steam Workshop item id. Empty or non-numeric values are filtered into the skipped list. """
    title: str = ""
    """ Human-readable mod title carried through the SSE stream for UI display. """
    changenote: str = ""
    """ Steam Workshop changelog for this mod. Must be non-empty for every item that is not skipped. """


class PublishAllBody(BaseModel):
    """Request body for POST /packs/publish-all."""

    items: list[PublishAllItem]
    """ Per-mod entries in run order. Empty/invalid workshop_ids are filtered into the skipped list. """


@router.post("/packs/publish-all")
async def post_publish_all(body: PublishAllBody):
    """Start a serialized batch that publishes every eligible mod with its own changenote.

    Pre-filters items whose `workshop_id` is blank or non-numeric; those appear only in the response's `skipped` list and
    in the SSE `mod_skipped` events. The batch runs as a detached background task so the modal can close and reconnect
    via the stream endpoint.

    Args:
        body: Request body with the per-mod items.

    Returns:
        `{"batch_id", "started_at", "queued", "skipped"}` on success.

    Raises:
        HTTPException(400): empty items list, an eligible item with a blank changenote, or no items had a valid workshop_id.
        HTTPException(409): another batch (or a single-mod publish) is already in progress.
    """
    if not body.items:
        raise HTTPException(status_code=400, detail="items must not be empty")

    eligible: list[dict] = []
    skipped: list[dict] = []
    for item in body.items:
        wid = (item.workshop_id or "").strip()
        if not wid:
            skipped.append({"workshop_id": item.workshop_id, "title": item.title, "reason": "no workshopId"})
            continue
        if not _WORKSHOP_ID_RE.fullmatch(wid):
            skipped.append({"workshop_id": item.workshop_id, "title": item.title, "reason": "invalid workshopId"})
            continue
        if not item.changenote.strip():
            raise HTTPException(status_code=400, detail=f"changenote must not be empty for {item.title or wid}")
        eligible.append({"workshop_id": wid, "title": item.title, "changenote": item.changenote})

    if not eligible:
        raise HTTPException(status_code=400, detail="no items with a valid workshop_id")

    try:
        handle = wbp.start_batch(
            eligible,
            steamcmd_path=config.STEAMCMD_PATH,
            steam_username=config.STEAM_USERNAME,
        )
    except wbp.BatchInProgressError:
        raise HTTPException(status_code=409, detail="a batch or single-mod publish is already in progress")

    return {
        "batch_id": handle.batch_id,
        "started_at": handle.started_at.isoformat(),
        "queued": len(eligible),
        "skipped": skipped,
    }


@router.get("/packs/publish-all/stream/{batch_id}")
async def get_publish_all_stream(batch_id: str):
    """Server-sent events: replay every event a batch has emitted so far, then live-tail until it ends.

    Args:
        batch_id: id returned from POST `/packs/publish-all`.

    Returns:
        `StreamingResponse` with `text/event-stream` content type.

    Raises:
        HTTPException(404): when `batch_id` is unknown.
    """
    if wbp.get_batch(batch_id) is None:
        raise HTTPException(status_code=404, detail="batch_id not found")

    async def event_source():
        async for event in wbp.stream_batch(batch_id):
            event_type = event.get("event", "message")
            data = json.dumps(event.get("data", {}))
            yield f"event: {event_type}\ndata: {data}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
