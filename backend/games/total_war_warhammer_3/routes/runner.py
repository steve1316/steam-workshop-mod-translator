"""HTTP routes for the TW3 script runner."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from backend import config
from backend.games.total_war_warhammer_3 import script_runner as sr

router = APIRouter()


def _settings() -> dict:
    """Read the per-game runner settings from `backend.config` env vars.

    Returns:
        Settings dict consumed by `script_runner._preflight` and `start_run`.
    """
    return {
        "helper_scripts_path": config.TW3_HELPER_PATH,
        "rpfm_cli_path": config.TW3_RPFM_CLI_PATH,
        "steam_library_drive": config.TW3_STEAM_LIBRARY_DRIVE,
    }


@router.get("/run")
def get_run():
    """Return the current run state.

    Returns:
        `{"status": "idle"}` if no run is active. Otherwise a dict with
        `status`, `run_id`, `script_id`, `started_at`, `lines_emitted`, and `steps` (per-script progress of a pipeline run).
    """
    handle = sr.current_run()
    if sr.is_idle() or handle is None:
        return {"status": "idle"}
    return {
        "status": "running",
        "run_id": handle.run_id,
        "script_id": handle.script_id,
        "started_at": handle.started_at.isoformat(),
        "lines_emitted": len(sr._log),
        "steps": {script_id: status.value for script_id, status in handle.steps.items()},
    }


@router.post("/run/{script_id}")
def post_run(script_id: str):
    """Start a script run.

    Args:
        script_id: Key into `SCRIPT_REGISTRY`.

    Raises:
        HTTPException(404): When `script_id` is not registered.
        HTTPException(400): When preflight checks fail. Detail body is `{"missing": [...]}`.
        HTTPException(409): When another run is already in progress.

    Returns:
        `{"run_id", "script_id", "started_at"}` on success.
    """
    try:
        handle = sr.start_run(script_id, _settings())
    except sr.UnknownScriptError:
        raise HTTPException(status_code=404, detail=f"unknown script_id: {script_id}")
    except sr.PreflightError as exc:
        raise HTTPException(status_code=400, detail={"missing": exc.missing})
    except sr.RunInProgressError:
        raise HTTPException(status_code=409, detail="a run is already in progress")
    return {
        "run_id": handle.run_id,
        "script_id": handle.script_id,
        "started_at": handle.started_at.isoformat(),
    }


@router.delete("/run", status_code=204)
def delete_run():
    """Cancel the active run.

    Raises:
        HTTPException(404): When no run is active.
    """
    if sr.is_idle():
        raise HTTPException(status_code=404, detail="no active run")
    sr.cancel_run()
    return Response(status_code=204)


@router.get("/run/stream")
async def run_stream():
    """Server-sent events: stream buffered + new log lines until the run ends.

    Each `data:` event carries one line; a final `event: done` arrives when the
    subprocess exits (or immediately if no run has ever started in this session).

    Returns:
        A `StreamingResponse` with `text/event-stream` content type.
    """

    async def event_source():
        async for event in sr.stream_lines():
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
            elif kind == "error":
                yield f"event: error\ndata: {json.dumps({'detail': event.get('detail', '')})}\n\n"
                break

    return StreamingResponse(event_source(), media_type="text/event-stream")
