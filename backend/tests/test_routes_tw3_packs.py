"""Tests for the TW3 packs preview route and helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.games.total_war_warhammer_3 import publish_notes
from backend.games.total_war_warhammer_3.routes._paths import tw3_workshop_content_dir
from backend.web_server import app


@pytest.fixture(autouse=True)
def _no_real_recorders(monkeypatch):
    """Keep publish routes from running the real change note CLI or touching translation baselines."""
    monkeypatch.setattr(publish_notes, "make_recorder", lambda workshop_id, changenote: None)


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# tw3_workshop_content_dir helper


def test_workshop_content_dir_builds_path_from_drive(monkeypatch):
    """When `TW3_STEAM_LIBRARY_DRIVE` is set to a bare drive like 'F:', the helper joins the well-known suffix and yields an
    absolute path. Previously `Path("F:") / "SteamLibrary"` produced the drive-relative 'F:SteamLibrary', which made SteamCMD
    look for the workshop folder under its own CWD on the F: drive and fail with 'no content'.
    """
    monkeypatch.setattr(config, "TW3_STEAM_LIBRARY_DRIVE", "F:")
    resolved = tw3_workshop_content_dir("1234567890")
    assert resolved == Path("F:\\") / "SteamLibrary" / "steamapps" / "workshop" / "content" / "1142710" / "1234567890"
    assert resolved.is_absolute()


def test_workshop_content_dir_accepts_drive_with_trailing_separator(monkeypatch):
    """A drive value that already includes a trailing separator (e.g. 'F:\\') resolves to the same absolute path."""
    monkeypatch.setattr(config, "TW3_STEAM_LIBRARY_DRIVE", "F:\\")
    assert tw3_workshop_content_dir("1234567890") == Path("F:\\") / "SteamLibrary" / "steamapps" / "workshop" / "content" / "1142710" / "1234567890"


def test_workshop_content_dir_returns_none_when_drive_unset(monkeypatch):
    """An empty drive setting yields None so callers can return 404 cleanly."""
    monkeypatch.setattr(config, "TW3_STEAM_LIBRARY_DRIVE", "")
    assert tw3_workshop_content_dir("1234567890") is None


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# GET /packs/{workshop_id}/preview


def _set_drive(monkeypatch, tmp_path: Path) -> Path:
    """Point `TW3_STEAM_LIBRARY_DRIVE` at a tmp_path-shaped fake Steam library.

    Creates `<tmp_path>/SteamLibrary/steamapps/workshop/content/1142710/` and
    monkeypatches `config.TW3_STEAM_LIBRARY_DRIVE` so the helper resolves there.

    Args:
        monkeypatch: pytest `MonkeyPatch` fixture used to swap `config.TW3_STEAM_LIBRARY_DRIVE` for the test.
        tmp_path: pytest `tmp_path` fixture (a `Path`); the function builds the fake Steam library under it.

    Returns:
        The `1142710` parent directory so tests can drop per-workshop folders in.
    """
    monkeypatch.setattr(config, "TW3_STEAM_LIBRARY_DRIVE", str(tmp_path))
    parent = tmp_path / "SteamLibrary" / "steamapps" / "workshop" / "content" / "1142710"
    parent.mkdir(parents=True)
    return parent


def test_preview_returns_image_when_folder_has_png(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    workshop_dir = parent / "999"
    workshop_dir.mkdir()
    (workshop_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/999/preview")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content == b"\x89PNG\r\n\x1a\nfake"


def test_preview_picks_first_image_when_multiple_present(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    workshop_dir = parent / "999"
    workshop_dir.mkdir()
    (workshop_dir / "thing.pack").write_bytes(b"not an image")
    (workshop_dir / "preview.jpg").write_bytes(b"jpeg-bytes")
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/999/preview")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content == b"jpeg-bytes"


def test_preview_returns_404_when_folder_missing(monkeypatch, tmp_path):
    _set_drive(monkeypatch, tmp_path)
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/999/preview")
    assert res.status_code == 404


def test_preview_returns_404_when_folder_has_no_image(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    workshop_dir = parent / "999"
    workshop_dir.mkdir()
    (workshop_dir / "thing.pack").write_bytes(b"not an image")
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/999/preview")
    assert res.status_code == 404


def test_preview_returns_400_when_workshop_id_non_numeric():
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/abc/preview")
    assert res.status_code == 400


def test_preview_returns_404_when_drive_unset(monkeypatch):
    monkeypatch.setattr(config, "TW3_STEAM_LIBRARY_DRIVE", "")
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/999/preview")
    assert res.status_code == 404


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# GET /packs/{workshop_id}/last_modified


def test_last_modified_returns_newest_mtime_in_folder(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    workshop_dir = parent / "999"
    workshop_dir.mkdir()
    older = workshop_dir / "thumbnail.jpg"
    older.write_bytes(b"img")
    newer = workshop_dir / "thing.pack"
    newer.write_bytes(b"pack")
    import os as _os

    _os.utime(older, (1_700_000_000, 1_700_000_000))
    _os.utime(newer, (1_750_000_000, 1_750_000_000))
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/999/last_modified")
    assert res.status_code == 200
    assert res.json() == {"last_modified_unix": 1_750_000_000.0, "published_unix": None}


MANIFEST_TEXT = '"AppWorkshop"\n{\n\t"WorkshopItemDetails"\n\t{\n\t\t"999"\n\t\t{\n\t\t\t"timeupdated"\t\t"1750000000"\n\t\t}\n\t}\n}\n'


def test_last_modified_returns_published_time_from_manifest(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    (parent / "999").mkdir()
    manifest = parent.parent.parent / "appworkshop_1142710.acf"
    manifest.write_text(MANIFEST_TEXT, encoding="utf-8")
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/999/last_modified")
    assert res.json()["published_unix"] == 1_750_000_000.0


def test_last_modified_returns_null_when_folder_empty(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    (parent / "999").mkdir()
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/999/last_modified")
    assert res.status_code == 200
    assert res.json() == {"last_modified_unix": None, "published_unix": None}


def test_last_modified_returns_404_when_folder_missing(monkeypatch, tmp_path):
    _set_drive(monkeypatch, tmp_path)
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/999/last_modified")
    assert res.status_code == 404


def test_last_modified_returns_400_when_workshop_id_non_numeric():
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/abc/last_modified")
    assert res.status_code == 400


def test_last_modified_returns_404_when_drive_unset(monkeypatch):
    monkeypatch.setattr(config, "TW3_STEAM_LIBRARY_DRIVE", "")
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/999/last_modified")
    assert res.status_code == 404


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# POST /packs/{workshop_id}/open


def test_open_pack_folder_invokes_os_startfile_on_success(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    workshop_dir = parent / "999"
    workshop_dir.mkdir()
    called_with: list[Path] = []
    monkeypatch.setattr("os.startfile", lambda path: called_with.append(Path(path)))
    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/999/open")
    assert res.status_code == 200
    assert res.json() == {"status": "success"}
    assert called_with == [workshop_dir]


def test_open_pack_folder_returns_404_when_folder_missing(monkeypatch, tmp_path):
    _set_drive(monkeypatch, tmp_path)
    monkeypatch.setattr("os.startfile", lambda path: None)
    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/999/open")
    assert res.status_code == 404


def test_open_pack_folder_returns_400_when_workshop_id_non_numeric(monkeypatch):
    monkeypatch.setattr("os.startfile", lambda path: None)
    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/abc/open")
    assert res.status_code == 400


def test_open_pack_folder_returns_404_when_drive_unset(monkeypatch):
    monkeypatch.setattr(config, "TW3_STEAM_LIBRARY_DRIVE", "")
    monkeypatch.setattr("os.startfile", lambda path: None)
    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/999/open")
    assert res.status_code == 404


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# POST /packs/{workshop_id}/publish

from datetime import datetime, timezone

from backend.games.total_war_warhammer_3 import workshop_publisher as wp


def _make_handle(workshop_id: str = "999") -> wp.PublishHandle:
    return wp.PublishHandle(
        publish_id="test-publish-id",
        workshop_id=workshop_id,
        started_at=datetime(2026, 5, 14, 0, 0, 0, tzinfo=timezone.utc),
    )


def test_publish_returns_400_when_workshop_id_non_numeric():
    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/abc/publish", json={"changenote": ""})
    assert res.status_code == 400


def test_publish_returns_404_when_folder_missing(monkeypatch, tmp_path):
    _set_drive(monkeypatch, tmp_path)
    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/999/publish", json={"changenote": ""})
    assert res.status_code == 404


def test_publish_returns_400_with_missing_when_preflight_fails(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    (parent / "999").mkdir()

    def fake_start_publish(*args, **kwargs):
        raise wp.PublisherPreflightError(["steamcmd_path", "steam_username"])

    monkeypatch.setattr(wp, "start_publish", fake_start_publish)
    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/999/publish", json={"changenote": ""})
    assert res.status_code == 400
    assert res.json() == {"detail": {"missing": ["steamcmd_path", "steam_username"]}}


def test_publish_returns_409_when_already_in_progress(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    (parent / "999").mkdir()

    def fake_start_publish(*args, **kwargs):
        raise wp.PublishInProgressError("999")

    monkeypatch.setattr(wp, "start_publish", fake_start_publish)
    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/999/publish", json={"changenote": ""})
    assert res.status_code == 409


def test_publish_returns_handle_on_success(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    (parent / "999").mkdir()

    captured: dict = {}

    def fake_start_publish(workshop_id, content_folder, changenote, *, steamcmd_path, steam_username, **_kw):
        captured["workshop_id"] = workshop_id
        captured["changenote"] = changenote
        captured["content_folder"] = content_folder
        return _make_handle(workshop_id)

    monkeypatch.setattr(wp, "start_publish", fake_start_publish)
    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/999/publish", json={"changenote": "fix typo"})
    assert res.status_code == 200
    body = res.json()
    assert body["publish_id"] == "test-publish-id"
    assert body["workshop_id"] == "999"
    assert captured["workshop_id"] == "999"
    assert captured["changenote"] == "fix typo"


def test_publish_stream_returns_400_when_workshop_id_non_numeric():
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/abc/publish/stream")
    assert res.status_code == 400


def test_publish_hands_the_recorder_to_start_publish(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    (parent / "999").mkdir()
    made: list = []

    def recorder(exit_code):
        pass

    def fake_make_recorder(workshop_id, changenote):
        made.append((workshop_id, changenote))
        return recorder

    captured: dict = {}

    def fake_start_publish(workshop_id, content_folder, changenote, *, steamcmd_path, steam_username, on_exit=None, **_kw):
        captured["on_exit"] = on_exit
        return _make_handle(workshop_id)

    monkeypatch.setattr(publish_notes, "make_recorder", fake_make_recorder)
    monkeypatch.setattr(wp, "start_publish", fake_start_publish)

    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/999/publish", json={"changenote": "fix typo"})

    assert res.status_code == 200
    assert made == [("999", "fix typo")]
    assert captured["on_exit"] is recorder


def test_publish_returns_409_when_a_batch_is_in_progress(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    (parent / "999").mkdir()
    monkeypatch.setattr(wbp, "current_batch", lambda: object())

    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/999/publish", json={"changenote": ""})

    assert res.status_code == 409


def test_publish_returns_409_when_wp_is_not_idle(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    (parent / "999").mkdir()
    monkeypatch.setattr(wp, "is_idle", lambda: False)

    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/999/publish", json={"changenote": ""})

    assert res.status_code == 409


def test_publish_does_not_call_make_recorder_when_a_publish_is_already_active(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    (parent / "999").mkdir()
    called: list = []

    def fake_make_recorder(workshop_id, changenote):
        called.append(workshop_id)
        return None

    monkeypatch.setattr(publish_notes, "make_recorder", fake_make_recorder)
    monkeypatch.setattr(wp, "is_idle", lambda: False)

    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/999/publish", json={"changenote": ""})

    assert res.status_code == 409
    assert called == []


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# POST /packs/publish-all

from backend.games.total_war_warhammer_3 import workshop_batch_publisher as wbp

_PUBLISH_ALL = "/api/games/total_war_warhammer_3/packs/publish-all"


def _stub_batch_publisher(monkeypatch, parent: Path, workshop_ids: list[str], exit_codes: dict[str, int] | None = None):
    """Create real folders for `workshop_ids` and stub `wp.start_publish` + `wp.stream_lines`.

    `exit_codes` maps a workshop_id to the SteamCMD exit code to inject (default 0 for all).
    """
    exit_codes = exit_codes or {}
    for wid in workshop_ids:
        (parent / wid).mkdir(exist_ok=True)

    current_id: dict[str, str] = {"id": ""}

    def fake_start_publish(workshop_id, content_folder, changenote, *, steamcmd_path, steam_username, **_kw):
        current_id["id"] = workshop_id
        return _make_handle(workshop_id)

    async def fake_stream_lines():
        wid = current_id["id"]
        yield {"event": "data", "line": f"line for {wid}", "ts": "t"}
        yield {"event": "done", "exit_code": exit_codes.get(wid, 0), "duration_seconds": 0.1}

    monkeypatch.setattr(wp, "start_publish", fake_start_publish)
    monkeypatch.setattr(wp, "stream_lines", fake_stream_lines)
    monkeypatch.setattr(wp, "is_idle", lambda: True)


def test_publish_all_returns_400_naming_an_item_with_a_blank_changenote(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    wbp._reset_state()
    _stub_batch_publisher(monkeypatch, parent, ["999"])
    res = TestClient(app).post(_PUBLISH_ALL, json={"items": [{"workshop_id": "999", "title": "Mod", "changenote": "  "}]})
    assert res.status_code == 400
    assert "Mod" in res.json()["detail"]


def test_publish_all_returns_400_when_items_empty(monkeypatch):
    wbp._reset_state()
    monkeypatch.setattr(wp, "is_idle", lambda: True)
    res = TestClient(app).post(_PUBLISH_ALL, json={"items": []})
    assert res.status_code == 400


def test_publish_all_returns_400_when_no_valid_workshop_ids(monkeypatch):
    wbp._reset_state()
    monkeypatch.setattr(wp, "is_idle", lambda: True)
    res = TestClient(app).post(
        _PUBLISH_ALL,
        json={
            "items": [
                {"workshop_id": "", "title": "A", "changenote": "notes"},
                {"workshop_id": "abc", "title": "B", "changenote": "notes"},
            ]
        },
    )
    assert res.status_code == 400


def test_publish_all_filters_invalid_workshop_ids_and_returns_skipped_list(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    wbp._reset_state()
    _stub_batch_publisher(monkeypatch, parent, ["999"])
    res = TestClient(app).post(
        _PUBLISH_ALL,
        json={
            "items": [
                {"workshop_id": "999", "title": "Valid", "changenote": "notes"},
                {"workshop_id": "", "title": "Empty", "changenote": "notes"},
                {"workshop_id": "abc", "title": "Bad", "changenote": "notes"},
            ],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["queued"] == 1
    skipped_ids = sorted(s["title"] for s in body["skipped"])
    assert skipped_ids == ["Bad", "Empty"]
    assert "batch_id" in body
    assert "started_at" in body


def test_publish_all_returns_409_when_single_publish_in_progress(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    wbp._reset_state()
    (parent / "999").mkdir()
    monkeypatch.setattr(wp, "is_idle", lambda: False)
    res = TestClient(app).post(_PUBLISH_ALL, json={"items": [{"workshop_id": "999", "title": "Mod", "changenote": "notes"}]})
    assert res.status_code == 409


def test_publish_all_stream_returns_404_for_unknown_batch_id(monkeypatch):
    wbp._reset_state()
    res = TestClient(app).get(f"{_PUBLISH_ALL}/stream/does-not-exist")
    assert res.status_code == 404


def test_publish_all_stream_returns_event_stream_with_batch_started_first(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    wbp._reset_state()
    _stub_batch_publisher(monkeypatch, parent, ["999"])
    post = TestClient(app).post(_PUBLISH_ALL, json={"items": [{"workshop_id": "999", "title": "Mod", "changenote": "notes"}]})
    assert post.status_code == 200
    batch_id = post.json()["batch_id"]

    with TestClient(app) as client:
        res = client.get(f"{_PUBLISH_ALL}/stream/{batch_id}")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        text = res.text
        assert "event: batch_started" in text
        assert "event: batch_done" in text


def test_publish_all_skipped_items_do_not_need_a_changenote(monkeypatch, tmp_path):
    parent = _set_drive(monkeypatch, tmp_path)
    wbp._reset_state()
    _stub_batch_publisher(monkeypatch, parent, ["999"])
    res = TestClient(app).post(
        _PUBLISH_ALL,
        json={"items": [{"workshop_id": "999", "title": "Valid", "changenote": "notes"}, {"workshop_id": "", "title": "Empty"}]},
    )
    assert res.status_code == 200
    assert res.json()["queued"] == 1


def test_last_modified_prefers_newer_recorded_publish_time(monkeypatch, tmp_path):
    from backend.games.total_war_warhammer_3 import published_times

    parent = _set_drive(monkeypatch, tmp_path)
    (parent / "999").mkdir()
    manifest = parent.parent.parent / "appworkshop_1142710.acf"
    manifest.write_text(MANIFEST_TEXT, encoding="utf-8")
    published_times.record("999", 1_760_000_000.0)
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/999/last_modified")
    assert res.json()["published_unix"] == 1_760_000_000.0


def test_last_modified_keeps_manifest_time_when_newer_than_recorded(monkeypatch, tmp_path):
    from backend.games.total_war_warhammer_3 import published_times

    parent = _set_drive(monkeypatch, tmp_path)
    (parent / "999").mkdir()
    manifest = parent.parent.parent / "appworkshop_1142710.acf"
    manifest.write_text(MANIFEST_TEXT, encoding="utf-8")
    published_times.record("999", 1_700_000_000.0)
    res = TestClient(app).get("/api/games/total_war_warhammer_3/packs/999/last_modified")
    assert res.json()["published_unix"] == 1_750_000_000.0
