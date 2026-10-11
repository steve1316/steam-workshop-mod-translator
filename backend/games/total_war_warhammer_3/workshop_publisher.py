"""Subprocess-based publisher for pushing TW3 mod updates to the Steam Workshop via SteamCMD.

Mirrors the `script_runner` pattern: a single in-flight publish at a time, guarded by a module
lock, with a daemon thread draining stdout into a deque and an async generator that bridges
the buffer into Server-Sent Events for the frontend.

The VDF descriptor is intentionally minimal - only `appid`, `publishedfileid`, `contentfolder`,
and (when non-empty) `changenote`. Title, description, visibility, tags, and the preview file
are deliberately omitted so updates do not blank existing Workshop metadata.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Callable

from backend.games.total_war_warhammer_3 import published_times
from backend.games.total_war_warhammer_3.routes._paths import TW3_APPID


@dataclass
class PublishHandle:
    """In-memory record of the currently or most-recently running publish."""

    publish_id: str
    """ Unique identifier for this publish, generated at start time. """
    workshop_id: str
    """ Steam Workshop item id being updated. """
    started_at: datetime
    """ UTC timestamp when SteamCMD was spawned. """
    ended_at: datetime | None = None
    """ UTC timestamp when SteamCMD exited. `None` while still running. """
    exit_code: int | None = None
    """ Return code from SteamCMD. `None` while still running. """


@dataclass
class _LogLine:
    """One stdout line from the SteamCMD subprocess plus its capture timestamp."""

    line: str
    """ The raw text line captured from subprocess stdout. """
    ts: datetime
    """ UTC timestamp at which the line was captured. """


class PublisherError(Exception):
    """Base for publisher errors."""


class PublishInProgressError(PublisherError):
    """Raised when `start_publish` is called while another publish is active."""


class PublisherPreflightError(PublisherError):
    """Raised when `start_publish` preflight checks fail.

    Attributes:
        missing: List of human-readable strings naming each failed check.
    """

    def __init__(self, missing: list[str]):
        super().__init__(f"preflight failed: {missing}")
        self.missing: list[str] = missing


# Module-level state guarded by `_lock`. Accessed by `start_publish`, `current_publish`,
# `stream_lines`, and the daemon reader thread.
_lock = threading.Lock()
_current: PublishHandle | None = None
_proc: subprocess.Popen | None = None
_log: "deque[_LogLine]" = deque(maxlen=5000)
_event_new_line = threading.Event()
_active_vdf: Path | None = None


def _vdf_escape(value: str) -> str:
    """Make a string safe to embed inside a double-quoted VDF value.

    SteamCMD parses build VDFs with escape sequences off, so `\\"` is not an escaped quote and a double quote always ends the value.
    Double quotes become single quotes and backslashes are left literal.

    Args:
        value: The raw string value (may contain backslashes or double quotes).

    Returns:
        The value with every `"` replaced by `'`.
    """
    return value.replace('"', "'")


def build_vdf(appid: str, workshop_id: str, content_folder: Path, changenote: str) -> str:
    """Render a minimal `workshop_build_item` VDF descriptor.

    Only includes `appid`, `publishedfileid`, `contentfolder`, and (when non-empty) `changenote`.
    Title, description, visibility, tags, and preview are omitted on purpose so existing Workshop
    metadata is preserved across updates.

    Args:
        appid: Steam app id as a string (e.g. `"1142710"` for TW3).
        workshop_id: Numeric Steam Workshop item id of the existing entry to update.
        content_folder: Local directory whose contents will be uploaded.
        changenote: Update note shown in the Workshop changelog. Empty -> omitted.

    Returns:
        The VDF text ready to write to a `.vdf` file.
    """
    folder_str = _vdf_escape(str(content_folder).replace("\\", "/"))
    lines = [
        '"workshopitem"',
        "{",
        f'\t"appid"           "{_vdf_escape(appid)}"',
        f'\t"publishedfileid" "{_vdf_escape(workshop_id)}"',
        f'\t"contentfolder"   "{folder_str}"',
    ]
    if changenote:
        lines.append(f'\t"changenote"      "{_vdf_escape(changenote)}"')
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _preflight(steamcmd_path: str, steam_username: str, content_folder: Path) -> Path:
    """Validate publisher settings and resolve the SteamCMD executable path.

    Args:
        steamcmd_path: Configured `SWMT_STEAMCMD_PATH` value.
        steam_username: Configured `SWMT_STEAM_USERNAME` value.
        content_folder: Local Steam Workshop folder for the mod being published.

    Raises:
        PublisherPreflightError: When one or more required settings are missing or invalid.
            The exception's `missing` attribute lists each failed check.

    Returns:
        Resolved `Path` to `steamcmd.exe` (verified to exist).
    """
    missing: list[str] = []

    steamcmd = Path(steamcmd_path) if steamcmd_path else None
    if not steamcmd or not steamcmd.is_file():
        missing.append("steamcmd_path")

    if not steam_username:
        missing.append("steam_username")

    if not content_folder.is_dir():
        missing.append(f"content_folder at {content_folder}")

    if missing:
        raise PublisherPreflightError(missing)
    return steamcmd  # type: ignore[return-value]


def current_publish() -> PublishHandle | None:
    """Return the current or most-recent publish handle.

    Returns:
        The active or last `PublishHandle`, or `None` if no publish has started this session.
    """
    return _current


def is_idle() -> bool:
    """Return True when no publish is active.

    Acquires `_lock` so the check is consistent with `start_publish` writes. A publish stays active until its handle's
    `exit_code` is set, which happens after `on_exit` runs, so a slow recording hook still blocks a second publish
    even though the SteamCMD subprocess itself has already exited.

    Returns:
        True when there is no current publish, or the current publish's handle has `exit_code` set and its subprocess has exited.
    """
    with _lock:
        proc = _proc
        handle = _current
    if handle is None:
        return True
    if handle.exit_code is None:
        return False
    if proc is None:
        return True
    return proc.poll() is not None


def start_publish(
    workshop_id: str,
    content_folder: Path,
    changenote: str,
    *,
    steamcmd_path: str,
    steam_username: str,
    appid: str = TW3_APPID,
    on_exit: Callable[[int | None], str | None] | None = None,
) -> PublishHandle:
    """Spawn `steamcmd +workshop_build_item` for an existing Workshop item. Single-flight.

    Writes a temp `.vdf` descriptor, then runs SteamCMD with cached-credential login (no
    password supplied - SteamCMD must already have a valid sentry file from a prior interactive
    `steamcmd +login <username>` session).

    Args:
        workshop_id: Numeric Steam Workshop item id of the existing entry to update.
        content_folder: Local directory whose contents will be uploaded.
        changenote: Update note shown in the Workshop changelog.
        steamcmd_path: Path to `steamcmd.exe` (from `config.STEAMCMD_PATH`).
        steam_username: Steam account username (from `config.STEAM_USERNAME`).
        appid: Steam app id; defaults to TW3.
        on_exit: Called with SteamCMD's return code once it exits, before the publish is reported as done. Used to record the upload.
            May return a status line to log, or None to log nothing.

    Raises:
        PublisherPreflightError: When required settings/paths are missing.
        PublishInProgressError: When another publish is already active.

    Returns:
        The created `PublishHandle` for the new subprocess.
    """
    global _current, _proc, _active_vdf
    steamcmd = _preflight(steamcmd_path, steam_username, content_folder)

    with _lock:
        if _current is not None and _current.exit_code is None:
            raise PublishInProgressError(workshop_id)
        if _proc is not None and _proc.poll() is None:
            raise PublishInProgressError(workshop_id)

        publish_id = uuid.uuid4().hex
        vdf_text = build_vdf(appid, workshop_id, content_folder, changenote)
        vdf_fd, vdf_str = tempfile.mkstemp(prefix=f"workshop_{workshop_id}_", suffix=".vdf")
        try:
            with os.fdopen(vdf_fd, "w", encoding="utf-8") as f:
                f.write(vdf_text)
        except Exception:
            os.unlink(vdf_str)
            raise
        vdf_path = Path(vdf_str)

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        proc = subprocess.Popen(
            [str(steamcmd), "+login", steam_username, "+workshop_build_item", str(vdf_path), "+quit"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            creationflags=creationflags,
        )

        handle = PublishHandle(
            publish_id=publish_id,
            workshop_id=workshop_id,
            started_at=datetime.now(timezone.utc),
        )
        _current = handle
        _proc = proc
        _active_vdf = vdf_path
        _log.clear()
        _event_new_line.clear()

    threading.Thread(target=_reader_thread, args=(proc, handle, vdf_path, on_exit), daemon=True).start()
    return handle


def _reader_thread(proc: subprocess.Popen, handle: PublishHandle, vdf_path: Path, on_exit: Callable[[int | None], str | None] | None = None) -> None:
    """Daemon thread: read stdout into `_log`, notify SSE consumers, run `on_exit`, clean up the VDF file.

    Args:
        proc: The Popen object whose stdout to drain.
        handle: The `PublishHandle` to update on exit.
        vdf_path: Tempfile path to delete once SteamCMD has exited.
        on_exit: Optional hook called with SteamCMD's return code. May return a status line, which is appended to `_log`.
            A failure inside it is logged as a line instead of failing the publish.
    """
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        _log.append(_LogLine(line=line, ts=datetime.now(timezone.utc)))
        _event_new_line.set()
    proc.wait()
    if proc.returncode == 0:
        try:
            published_times.record(handle.workshop_id)
        except OSError as exc:
            _log.append(_LogLine(line=f"Published, but saving the publish time failed: {exc}", ts=datetime.now(timezone.utc)))
    # Run the hook before exit_code is set, since `stream_lines` ends the stream as soon as it sees exit_code.
    if on_exit is not None:
        try:
            result = on_exit(proc.returncode)
            if result:
                _log.append(_LogLine(line=result, ts=datetime.now(timezone.utc)))
        except Exception as exc:
            _log.append(_LogLine(line=f"Published, but recording the upload failed: {exc}", ts=datetime.now(timezone.utc)))
    handle.exit_code = proc.returncode
    handle.ended_at = datetime.now(timezone.utc)
    _event_new_line.set()
    try:
        vdf_path.unlink(missing_ok=True)
    except OSError:
        pass


async def stream_lines() -> AsyncIterator[dict]:
    """Async generator: yield buffered lines, then tail new ones until SteamCMD exits.

    Yields:
        For each captured line: `{"event": "data", "line": str, "ts": iso8601}`.
        On exit: a single `{"event": "done", "exit_code": int, "duration_seconds": float}`.
        If no publish has ever started: a single `{"event": "done", "exit_code": None}`.
    """
    handle = _current
    if handle is None:
        yield {"event": "done", "exit_code": None}
        return

    seen = 0
    started = handle.started_at
    while True:
        snapshot = list(_log)
        for entry in snapshot[seen:]:
            yield {"event": "data", "line": entry.line, "ts": entry.ts.isoformat()}
        seen = len(snapshot)

        if handle.exit_code is not None and seen >= len(_log):
            duration = (handle.ended_at - started).total_seconds() if handle.ended_at else 0.0
            yield {"event": "done", "exit_code": handle.exit_code, "duration_seconds": duration}
            return

        # Wait briefly for new lines.
        await asyncio.get_event_loop().run_in_executor(None, _event_new_line.wait, 0.5)
        _event_new_line.clear()
