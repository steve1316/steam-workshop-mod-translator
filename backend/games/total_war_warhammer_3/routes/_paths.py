"""Shared internals for TW3 route modules."""

from __future__ import annotations

from pathlib import Path

from backend import config


def helper_scripts_path() -> Path:
    """Resolve the configured helper_scripts directory from `config.TW3_HELPER_PATH`.

    Returns:
        `Path` object. May not exist on disk; loaders validate that.
    """
    return Path(config.TW3_HELPER_PATH or "")


TW3_APPID = "1142710"


def tw3_workshop_content_dir(workshop_id: str) -> Path | None:
    """Resolve the local Steam Workshop folder for a TW3 workshop item.

    Builds `<steam_library_drive>\\SteamLibrary\\steamapps\\workshop\\content\\1142710\\<workshop_id>`
    from `config.TW3_STEAM_LIBRARY_DRIVE`. The folder may not exist on disk. Callers should validate before use.

    Args:
        workshop_id: Numeric Steam Workshop item id (the caller is responsible for sanitizing).

    Returns:
        Resolved `Path` to the workshop content directory, or `None` when the
        Steam library drive setting is empty.
    """
    workshop_dir = _tw3_workshop_dir()
    return None if workshop_dir is None else workshop_dir / "content" / TW3_APPID / workshop_id


def tw3_workshop_manifest_path() -> Path | None:
    """Resolve Steam's TW3 Workshop manifest, which records when each subscribed item was last updated.

    Returns:
        `<steam_library_drive>\\SteamLibrary\\steamapps\\workshop\\appworkshop_1142710.acf`, or `None` when the Steam library drive setting is
        empty. The file may not exist on disk.
    """
    workshop_dir = _tw3_workshop_dir()
    return None if workshop_dir is None else workshop_dir / f"appworkshop_{TW3_APPID}.acf"


def _tw3_workshop_dir() -> Path | None:
    """Resolve the Steam library's Workshop folder from `config.TW3_STEAM_LIBRARY_DRIVE`.

    Returns:
        `<steam_library_drive>\\SteamLibrary\\steamapps\\workshop`, or `None` when the Steam library drive setting is empty.
    """
    drive = (config.TW3_STEAM_LIBRARY_DRIVE or "").strip()
    if not drive:
        return None
    # A bare drive spec like "F:" is drive-relative on Windows. Promote it to "F:\" so the join produces an absolute path
    # instead of one relative to the current directory on that drive.
    if len(drive) == 2 and drive.endswith(":"):
        drive = drive + "\\"
    return Path(drive) / "SteamLibrary" / "steamapps" / "workshop"
