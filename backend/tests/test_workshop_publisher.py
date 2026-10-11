"""Unit tests for the TW3 SteamCMD workshop_publisher module's pure helpers.

The subprocess-spawning surface (`start_publish`) is not exercised here - that path is covered
by the route-level tests with a monkeypatched `start_publish`, so the test suite never invokes
the real `steamcmd.exe`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.games.total_war_warhammer_3 import workshop_publisher as wp


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# build_vdf


def test_build_vdf_minimal_omits_changenote_when_empty():
    text = wp.build_vdf("1142710", "12345", Path("C:/mods/foo"), "")
    assert '"appid"           "1142710"' in text
    assert '"publishedfileid" "12345"' in text
    assert '"contentfolder"   "C:/mods/foo"' in text
    assert "changenote" not in text


def test_build_vdf_includes_changenote_when_provided():
    text = wp.build_vdf("1142710", "12345", Path("C:/mods/foo"), "fix typo")
    assert '"changenote"      "fix typo"' in text


def test_build_vdf_normalises_backslashes_in_content_folder():
    # Windows paths use backslashes; SteamCMD accepts forward slashes and they sidestep
    # VDF escaping ambiguity, so the publisher converts them.
    text = wp.build_vdf("1142710", "12345", Path("C:\\mods\\foo"), "")
    assert "C:/mods/foo" in text
    assert "C:\\\\mods" not in text


def test_build_vdf_swaps_double_quotes_in_changenote_for_single_quotes():
    # SteamCMD does not honor \" escapes, so a double quote would end the value early and break the parse.
    text = wp.build_vdf("1142710", "12345", Path("C:/mods/foo"), '- Fixed a startup crash ("The first invalid database record").')
    assert '\t"changenote"      "- Fixed a startup crash (\'The first invalid database record\')."\n' in text


def test_build_vdf_keeps_backslashes_in_changenote_literal():
    text = wp.build_vdf("1142710", "12345", Path("C:/mods/foo"), "path C:\\foo")
    assert '"changenote"      "path C:\\foo"' in text


def test_build_vdf_changenote_with_quotes_parses_as_one_value():
    # Tokenise the way SteamCMD's KeyValues parser does with escapes off: a quoted token runs to the next double quote.
    text = wp.build_vdf("1142710", "12345", Path("C:/mods/foo"), 'crash ("record is x in table y").')
    body = text[text.index("{") + 1 : text.rindex("}")]
    tokens = body.split('"')[1::2]
    assert len(tokens) == 8
    assert tokens[-2:] == ["changenote", "crash ('record is x in table y')."]


def test_build_vdf_wraps_in_workshopitem_block():
    text = wp.build_vdf("1142710", "12345", Path("C:/mods/foo"), "")
    assert text.startswith('"workshopitem"\n{\n')
    assert text.rstrip().endswith("}")


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# _preflight


def test_preflight_lists_missing_steamcmd_path(tmp_path):
    folder = tmp_path / "mod"
    folder.mkdir()
    with pytest.raises(wp.PublisherPreflightError) as exc_info:
        wp._preflight("", "user", folder)
    assert "steamcmd_path" in exc_info.value.missing


def test_preflight_lists_missing_steam_username(tmp_path):
    folder = tmp_path / "mod"
    folder.mkdir()
    fake_steamcmd = tmp_path / "steamcmd.exe"
    fake_steamcmd.write_text("")
    with pytest.raises(wp.PublisherPreflightError) as exc_info:
        wp._preflight(str(fake_steamcmd), "", folder)
    assert "steam_username" in exc_info.value.missing


def test_preflight_lists_missing_content_folder(tmp_path):
    fake_steamcmd = tmp_path / "steamcmd.exe"
    fake_steamcmd.write_text("")
    missing_folder = tmp_path / "does_not_exist"
    with pytest.raises(wp.PublisherPreflightError) as exc_info:
        wp._preflight(str(fake_steamcmd), "user", missing_folder)
    assert any("content_folder" in m for m in exc_info.value.missing)


def test_preflight_returns_steamcmd_path_when_all_set(tmp_path):
    folder = tmp_path / "mod"
    folder.mkdir()
    fake_steamcmd = tmp_path / "steamcmd.exe"
    fake_steamcmd.write_text("")
    result = wp._preflight(str(fake_steamcmd), "user", folder)
    assert result == fake_steamcmd


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# on_exit hook


class _FakeProc:
    """Stand-in for `subprocess.Popen` with canned stdout lines and a fixed return code."""

    def __init__(self, lines: list[str], returncode: int):
        self.stdout = iter(lines)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


def _handle() -> wp.PublishHandle:
    """Build a fresh publish handle for reader-thread tests."""
    return wp.PublishHandle(publish_id="p", workshop_id="1", started_at=datetime.now(timezone.utc))


def test_reader_thread_runs_on_exit_before_the_exit_code_is_published(tmp_path):
    handle = _handle()
    seen: list = []

    wp._reader_thread(_FakeProc(["uploading\n"], 0), handle, tmp_path / "x.vdf", lambda code: seen.append((code, handle.exit_code)))

    assert seen == [(0, None)]
    assert handle.exit_code == 0


def test_reader_thread_passes_failed_exit_codes_to_on_exit(tmp_path):
    seen: list = []

    wp._reader_thread(_FakeProc([], 7), _handle(), tmp_path / "x.vdf", seen.append)

    assert seen == [7]


def test_reader_thread_logs_a_failed_recording_as_a_line(tmp_path):
    wp._log.clear()
    handle = _handle()

    def on_exit(code):
        raise RuntimeError("disk full")

    wp._reader_thread(_FakeProc(["uploading\n"], 0), handle, tmp_path / "x.vdf", on_exit)

    assert [entry.line for entry in wp._log] == ["uploading", "Published, but recording the upload failed: disk full"]
    assert handle.exit_code == 0


def test_reader_thread_without_on_exit_still_finishes(tmp_path):
    handle = _handle()

    wp._reader_thread(_FakeProc([], 0), handle, tmp_path / "x.vdf", None)

    assert handle.exit_code == 0


def test_reader_thread_appends_the_on_exit_returned_line(tmp_path):
    wp._log.clear()
    handle = _handle()

    wp._reader_thread(_FakeProc(["uploading\n"], 0), handle, tmp_path / "x.vdf", lambda code: "Recorded this upload for the next changenote.")

    assert [entry.line for entry in wp._log] == ["uploading", "Recorded this upload for the next changenote."]
    assert handle.exit_code == 0


def test_reader_thread_does_not_append_a_line_when_on_exit_returns_none(tmp_path):
    wp._log.clear()
    handle = _handle()

    wp._reader_thread(_FakeProc(["uploading\n"], 1), handle, tmp_path / "x.vdf", lambda code: None)

    assert [entry.line for entry in wp._log] == ["uploading"]
    assert handle.exit_code == 1


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# is_idle / start_publish single-flight while on_exit is still running


class _FakePopen:
    """Stand-in for `subprocess.Popen` exposing only `.poll()`, whose subprocess has already exited."""

    def __init__(self, returncode: int):
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode


def test_is_idle_false_while_handle_has_no_exit_code_even_after_proc_exits(monkeypatch):
    handle = _handle()
    monkeypatch.setattr(wp, "_current", handle)
    monkeypatch.setattr(wp, "_proc", _FakePopen(0))

    assert wp.is_idle() is False


def test_is_idle_true_once_exit_code_is_set(monkeypatch):
    handle = _handle()
    handle.exit_code = 0
    monkeypatch.setattr(wp, "_current", handle)
    monkeypatch.setattr(wp, "_proc", _FakePopen(0))

    assert wp.is_idle() is True


def test_start_publish_raises_in_progress_while_previous_handle_has_no_exit_code(monkeypatch, tmp_path):
    folder = tmp_path / "mod"
    folder.mkdir()
    steamcmd = tmp_path / "steamcmd.exe"
    steamcmd.write_text("")
    handle = _handle()
    monkeypatch.setattr(wp, "_current", handle)
    monkeypatch.setattr(wp, "_proc", _FakePopen(0))

    with pytest.raises(wp.PublishInProgressError):
        wp.start_publish("2", folder, "note", steamcmd_path=str(steamcmd), steam_username="user")


def test_reader_thread_records_the_publish_time_only_on_success(tmp_path):
    from backend.games.total_war_warhammer_3 import published_times

    handle = _handle()
    wp._reader_thread(_FakeProc([], 1), handle, tmp_path / "x.vdf", None)
    assert published_times.get(handle.workshop_id) is None

    wp._reader_thread(_FakeProc([], 0), handle, tmp_path / "x.vdf", None)
    assert published_times.get(handle.workshop_id) is not None
