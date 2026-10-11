"""Subprocess-based runner for the helper_scripts/update_*.py family.

Provides single-flight execution (one run at a time), a bounded stdout deque
for log buffering, a daemon-thread reader, and an async generator (`stream_lines`)
that drives the SSE route. Public surface: `start_run`, `current_run`, `is_idle`,
`cancel_run`, `stream_lines`.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import sys
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import AsyncIterator


@dataclass(frozen=True)
class ScriptDef:
    """A registered helper_scripts entry: how to launch it plus its CLI args.

    Set exactly one of `module` (run with `python -m` from the helper_scripts root, as the
    reorganized generators and tools expect) or `filename` (a script file at the root, like `update.py`).
    """

    module: str = ""
    """ Dotted module path run with `python -m`, e.g. `generators.update_dynamic_rors`. """
    filename: str = ""
    """ Script file within the helper_scripts directory, run directly. """
    args: list[str] = field(default_factory=list)
    """ CLI arguments passed to the script on each invocation. """

    def argv(self) -> list[str]:
        """Return the arguments that follow the Python executable to launch this script, without `args`.

        Returns:
            `["-m", module]` for a module entry, otherwise `[filename]`.
        """
        return ["-m", self.module] if self.module else [self.filename]


SCRIPT_REGISTRY: dict[str, ScriptDef] = {
    "update_dynamic_rors": ScriptDef(module="generators.update_dynamic_rors", args=["--reset"]),
    "update_dynamic_rors_vanilla": ScriptDef(module="generators.update_dynamic_rors", args=["--reset", "--vanilla"]),
    "update_double_unit_size": ScriptDef(module="generators.update_double_unit_size", args=["--reset"]),
    "update_modified_attribute_mods": ScriptDef(module="generators.update_modified_attribute_mods", args=["--reset"]),
    "process_main_units_tables": ScriptDef(module="generators.process_main_units_tables"),
    "glf_inner_join": ScriptDef(module="tools.glf_inner_join"),
    "update_ttc_compat": ScriptDef(module="generators.update_ttc_compat"),
    "update": ScriptDef(filename="update.py"),
}

PIPELINE_SCRIPT_ID = "update"
""" Registry key of the top-level pipeline whose per-script progress is tracked in `RunHandle.steps`. """

# Each `update.py` delta unit name mapped to the registry script that rebuilds the same pack on its own.
PIPELINE_UNIT_SCRIPTS: dict[str, str] = {
    "land_encounters_factions": "process_main_units_tables",
    "dynamic_rors": "update_dynamic_rors",
    "dynamic_rors_leftover_vanilla": "update_dynamic_rors_vanilla",
    "modified_attributes": "update_modified_attribute_mods",
    "double_unit_size": "update_double_unit_size",
    "ttc_compat": "update_ttc_compat",
}


class RunStatus(str, Enum):
    """Lifecycle state of the runner."""

    IDLE = "idle"
    RUNNING = "running"


class StepStatus(str, Enum):
    """Progress of one script inside a pipeline run."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


# `update.py` log messages that move a unit to a new status. Group 1 is the delta unit name.
_STEP_PATTERNS: list[tuple[re.Pattern[str], StepStatus]] = [
    (re.compile(r"Rebuilding (\w+): "), StepStatus.RUNNING),
    (re.compile(r"(\w+): up to date\s*$"), StepStatus.SKIPPED),
    (re.compile(r"(\w+) exited with code "), StepStatus.FAILED),
]
# Logged once every rebuild has finished, so the last running unit is done.
_PIPELINE_SUMMARY_MARKER = "Delta update summary"


@dataclass
class RunHandle:
    """In-memory record of the currently or most-recently running script.

    Set when `start_run` succeeds. `ended_at` and `exit_code` populate when the
    subprocess exits (including via cancel).
    """

    run_id: str
    """ Unique identifier for this run, generated at start time. """
    script_id: str
    """ Key from `SCRIPT_REGISTRY` identifying which script was launched. """
    started_at: datetime
    """ UTC timestamp when the subprocess was spawned. """
    ended_at: datetime | None = None
    """ UTC timestamp when the subprocess exited. `None` while still running. """
    exit_code: int | None = None
    """ Return code from the subprocess. `None` while still running. """
    steps: dict[str, StepStatus] = field(default_factory=dict)
    """ Per-script progress of a pipeline run, keyed by registry script id. Empty for every other run. """


@dataclass
class _LogLine:
    """One stdout line from the running subprocess plus its capture timestamp."""

    line: str
    """ The raw text line captured from subprocess stdout. """
    ts: datetime
    """ UTC timestamp at which the line was captured. """
    seq: int = 0
    """ Position of the line within its run, counting from 0. Keeps climbing after the bounded buffer starts dropping old lines. """


class RunnerError(Exception):
    """Base for runner errors."""


class RunInProgressError(RunnerError):
    """Raised when `start_run` is called while another run is active."""


class PreflightError(RunnerError):
    """Raised when `start_run` preflight checks fail.

    Attributes:
        missing: List of human-readable strings naming each failed check.
    """

    def __init__(self, missing: list[str]):
        super().__init__(f"preflight failed: {missing}")
        self.missing: list[str] = missing


class UnknownScriptError(RunnerError):
    """Raised when `start_run` is called with an unknown `script_id`."""


# Module-level state guarded by `_lock`. Accessed by `start_run`, `cancel_run`,
# `current_run`, `stream_lines`, and the daemon reader thread. All reads and
# writes outside the daemon thread go through `_lock`.
_lock = threading.Lock()
_current: RunHandle | None = None
_proc: subprocess.Popen | None = None
_log: "deque[_LogLine]" = deque(maxlen=5000)
_event_new_line = threading.Event()


_TEST_SCRIPT_REGISTRY: dict[str, ScriptDef] = {
    "_test_echo": ScriptDef(filename="_test_echo.py"),
    "_test_sleep": ScriptDef(filename="_test_sleep.py"),
    "_test_module": ScriptDef(module="_test_generators.echo_module"),
}


def _preflight(settings: dict) -> Path:
    """Validate runner settings and resolve the helper_scripts directory.

    Args:
        settings: Per-game settings dict with `helper_scripts_path`, `rpfm_cli_path`,
            and `steam_library_drive` keys. Empty strings are treated as unset.

    Raises:
        PreflightError: When one or more required paths/settings are missing.
            The exception's `missing` attribute lists each failed check.

    Returns:
        Resolved `Path` to the helper_scripts directory (verified to exist).
    """
    missing: list[str] = []

    raw_helper = settings.get("helper_scripts_path", "") or ""
    helper = Path(raw_helper) if raw_helper else None
    if not helper or not helper.is_dir():
        missing.append("helper_scripts_path")

    raw_rpfm = settings.get("rpfm_cli_path", "") or ""
    rpfm = Path(raw_rpfm) if raw_rpfm else (helper / "rpfm_cli.exe" if helper else None)
    if not rpfm or not rpfm.is_file():
        missing.append(f"rpfm_cli at {rpfm}" if rpfm else "rpfm_cli_path")

    # Only the .ron is required. The helper scripts regenerate schema_wh3.json from it on startup.
    if helper and helper.is_dir():
        if not (helper / "schemas" / "schema_wh3.ron").is_file():
            missing.append("schemas/schema_wh3.ron")

    if not (settings.get("steam_library_drive") or ""):
        missing.append("steam_library_drive")

    if missing:
        raise PreflightError(missing)
    return helper  # type: ignore[return-value]


def current_run() -> RunHandle | None:
    """Return the current or most-recent run handle.

    Returns:
        The active or last `RunHandle`, or `None` if no run has started this session.
    """
    return _current


def is_idle() -> bool:
    """Return True when no run is active.

    Acquires `_lock` so the check is consistent with `start_run` writes.

    Returns:
        True when there is no current run, or the current run's subprocess has exited.
    """
    with _lock:
        proc = _proc
        handle = _current
    if handle is None:
        return True
    if proc is None:
        return True
    return proc.poll() is not None


def start_run(
    script_id: str,
    settings: dict,
    *,
    registry: dict[str, ScriptDef] | None = None,
) -> RunHandle:
    """Start a script run. Single-flight: raises `RunInProgressError` if active.

    Args:
        script_id: Key into `SCRIPT_REGISTRY` (or `registry` if overridden).
        settings: Per-game settings dict (`helper_scripts_path`, `rpfm_cli_path`,
            `steam_library_drive`).
        registry: Optional override registry; tests pass `_TEST_SCRIPT_REGISTRY`
            to point at fixture scripts.

    Raises:
        UnknownScriptError: When `script_id` is not in the registry.
        PreflightError: When required paths/settings are missing.
        RunInProgressError: When another run is already active.

    Returns:
        The created `RunHandle` for the new subprocess.
    """
    global _current, _proc
    reg = registry if registry is not None else SCRIPT_REGISTRY
    if script_id not in reg:
        raise UnknownScriptError(script_id)
    helper_path = _preflight(settings)
    script_def = reg[script_id]

    with _lock:
        if _proc is not None and _proc.poll() is None:
            raise RunInProgressError(script_id)

        run_id = uuid.uuid4().hex
        env = {**os.environ, "STEAM_LIBRARY_DRIVE": settings.get("steam_library_drive", "")}
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        proc = subprocess.Popen(
            [sys.executable, *script_def.argv(), *script_def.args],
            cwd=str(helper_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            env=env,
            creationflags=creationflags,
        )

        handle = RunHandle(
            run_id=run_id,
            script_id=script_id,
            started_at=datetime.now(timezone.utc),
        )
        if script_id == PIPELINE_SCRIPT_ID:
            handle.steps = {sid: StepStatus.QUEUED for sid in PIPELINE_UNIT_SCRIPTS.values()}
        _current = handle
        _proc = proc
        _log.clear()
        _event_new_line.clear()

    threading.Thread(target=_reader_thread, args=(proc, handle), daemon=True).start()
    return handle


def _finish_running_step(handle: RunHandle) -> None:
    """Mark the pipeline step that is currently running as done.

    Args:
        handle: The pipeline `RunHandle` whose steps to update.
    """
    for script_id, status in handle.steps.items():
        if status is StepStatus.RUNNING:
            handle.steps[script_id] = StepStatus.DONE


def _track_step(handle: RunHandle, line: str) -> None:
    """Update the pipeline step statuses from one `update.py` log line. No-op for runs that track no steps.

    Args:
        handle: The `RunHandle` of the active run.
        line: One captured stdout line.
    """
    if not handle.steps:
        return
    if _PIPELINE_SUMMARY_MARKER in line:
        _finish_running_step(handle)
        return
    for pattern, status in _STEP_PATTERNS:
        match = pattern.search(line)
        script_id = PIPELINE_UNIT_SCRIPTS.get(match.group(1)) if match else None
        if script_id is None:
            continue
        if status is StepStatus.RUNNING:
            _finish_running_step(handle)
        handle.steps[script_id] = status
        return


def _reader_thread(proc: subprocess.Popen, handle: RunHandle) -> None:
    """Daemon thread: read stdout into `_log` and notify SSE consumers.

    Args:
        proc: The Popen object whose stdout to drain.
        handle: The `RunHandle` to update on exit.
    """
    assert proc.stdout is not None
    for seq, raw in enumerate(proc.stdout):
        line = raw.rstrip("\n")
        _log.append(_LogLine(line=line, ts=datetime.now(timezone.utc), seq=seq))
        _track_step(handle, line)
        _event_new_line.set()
    proc.wait()
    handle.exit_code = proc.returncode
    handle.ended_at = datetime.now(timezone.utc)
    _event_new_line.set()


def cancel_run() -> None:
    """Terminate the active run, if any. Idempotent when idle.

    On Windows the runner sends `CTRL_BREAK_EVENT` to the process group so child
    rpfm_cli processes also die. On non-Windows it falls back to `terminate()`.
    Forces a kill after a 10-second grace period.
    """
    global _proc
    with _lock:
        proc = _proc
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            pass
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


async def stream_lines() -> AsyncIterator[dict]:
    """Async generator: yield buffered lines, then tail new ones until run ends.

    Yields:
        For each captured line: `{"event": "data", "line": str, "ts": iso8601}`.
        On exit: a single `{"event": "done", "exit_code": int, "duration_seconds": float}`.
        If no run has ever started: a single `{"event": "done", "exit_code": None}`.
    """
    handle = _current
    if handle is None:
        yield {"event": "done", "exit_code": None}
        return

    # Track the last sent sequence number rather than a buffer index. The buffer is bounded, so an index stops advancing once it fills.
    last_seq = -1
    started = handle.started_at
    while True:
        snapshot = list(_log)
        for entry in snapshot:
            if entry.seq > last_seq:
                yield {"event": "data", "line": entry.line, "ts": entry.ts.isoformat()}
                last_seq = entry.seq

        if handle.exit_code is not None and (not _log or _log[-1].seq <= last_seq):
            duration = (handle.ended_at - started).total_seconds() if handle.ended_at else 0.0
            yield {"event": "done", "exit_code": handle.exit_code, "duration_seconds": duration}
            return

        # Wait briefly for new lines.
        await asyncio.get_event_loop().run_in_executor(None, _event_new_line.wait, 0.5)
        _event_new_line.clear()
