"""Tests for WH3 changenote dispatch, the upload recorder, and POST /packs/change-notes."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.games.total_war_warhammer_3 import compat_change_notes as ccn
from backend.games.total_war_warhammer_3 import publish_notes as pn
from backend.games.total_war_warhammer_3 import translation_change_notes as tcn
from backend.web_server import app

TRANSLATION_ID = "10"
SHIPPED = {"units.loc.tsv": {"k1": "h1"}}


def _only_translation_mod(monkeypatch) -> None:
    """Treat `TRANSLATION_ID` as the only registered translation mod and give it a fixed shipped set and no known parent update time."""
    mod = SimpleNamespace(parent_workshop_ids=("p",))
    monkeypatch.setattr(pn, "get_translation_mod", lambda wid: mod if wid == TRANSLATION_ID else None)
    monkeypatch.setattr(pn.tcn, "parent_updated_at", lambda ids: None)
    monkeypatch.setattr(pn, "current_shipped_strings", lambda wid: SHIPPED)


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# change_notes_for


def test_change_notes_for_dispatches_translation_and_compat_ids(monkeypatch):
    _only_translation_mod(monkeypatch)
    monkeypatch.setattr(pn.tcn, "load_baseline", lambda wid: None)
    compat_calls: list = []

    def fake_preview(ids):
        compat_calls.append(ids)
        return {"20": {"note": "compat note", "pack_sha": "s", "pending": False}, "30": None}

    monkeypatch.setattr(pn.ccn, "preview", fake_preview)

    result = pn.change_notes_for([TRANSLATION_ID, "20", "30"])

    assert result == {
        "notes": {
            TRANSLATION_ID: {"note": tcn.GENERIC_NOTE, "pending": True, "kind": "translation"},
            "20": {"note": "compat note", "pending": False, "kind": "compat"},
            "30": None,
        },
        "errors": [],
    }
    assert compat_calls == [["20", "30"]]


def test_change_notes_for_reports_compat_failures_and_maps_ids_to_none(monkeypatch):
    _only_translation_mod(monkeypatch)

    def failing_preview(ids):
        raise ccn.ChangeNoteError("helper_scripts path is not configured")

    monkeypatch.setattr(pn.ccn, "preview", failing_preview)

    assert pn.change_notes_for(["20"]) == {"notes": {"20": None}, "errors": ["helper_scripts path is not configured"]}


def test_change_notes_for_reports_translation_failures_per_id(monkeypatch):
    monkeypatch.setattr(pn, "get_translation_mod", lambda wid: object())

    def failing_shipped(wid):
        raise RuntimeError("rpfm_cli not found")

    monkeypatch.setattr(pn, "current_shipped_strings", failing_shipped)

    assert pn.change_notes_for([TRANSLATION_ID]) == {"notes": {TRANSLATION_ID: None}, "errors": [f"{TRANSLATION_ID}: rpfm_cli not found"]}


def test_change_notes_for_skips_the_cli_when_every_id_is_a_translation_mod(monkeypatch):
    _only_translation_mod(monkeypatch)
    monkeypatch.setattr(pn.tcn, "load_baseline", lambda wid: None)

    def unexpected_preview(ids):
        raise AssertionError("compat preview should not run")

    monkeypatch.setattr(pn.ccn, "preview", unexpected_preview)

    assert pn.change_notes_for([TRANSLATION_ID])["errors"] == []


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# make_recorder


def test_make_recorder_translation_saves_baseline_only_on_exit_0(monkeypatch):
    _only_translation_mod(monkeypatch)
    saved: list = []
    monkeypatch.setattr(pn.tcn, "save_baseline", lambda wid, strings, note: saved.append((wid, strings, note)))

    on_exit = pn.make_recorder(TRANSLATION_ID, "the note")
    # The shipped set is captured when the upload starts, not when SteamCMD exits.
    monkeypatch.setattr(pn, "current_shipped_strings", lambda wid: {"changed": {}})

    assert on_exit(1) is None
    assert on_exit(None) is None
    assert saved == []
    assert on_exit(0) == "Recorded this upload for the next changenote."
    assert saved == [(TRANSLATION_ID, SHIPPED, "the note")]


def test_make_recorder_compat_records_only_on_exit_0(monkeypatch):
    _only_translation_mod(monkeypatch)
    monkeypatch.setattr(pn.ccn, "preview", lambda ids: {"20": {"note": "n", "pack_sha": "captured", "pending": True}})
    recorded: list = []
    monkeypatch.setattr(pn.ccn, "record", lambda wid, sha, note: recorded.append((wid, sha, note)))

    on_exit = pn.make_recorder("20", "the note")

    assert on_exit(2) is None
    assert recorded == []
    assert on_exit(0) == "Recorded this upload for the next changenote."
    assert recorded == [("20", "captured", "the note")]


def test_make_recorder_returns_none_for_unknown_compat_ids(monkeypatch):
    _only_translation_mod(monkeypatch)
    monkeypatch.setattr(pn.ccn, "preview", lambda ids: {"20": None})

    assert pn.make_recorder("20", "note") is None


def test_make_recorder_never_raises(monkeypatch):
    _only_translation_mod(monkeypatch)

    def failing_preview(ids):
        raise ccn.ChangeNoteError("helper_scripts path is not configured")

    monkeypatch.setattr(pn.ccn, "preview", failing_preview)

    on_exit = pn.make_recorder("20", "note")

    assert on_exit is not None
    assert on_exit(1) is None
    assert on_exit(0) == "This upload was not recorded, so the next changenote may repeat these changes: helper_scripts path is not configured"


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# POST /packs/change-notes


def test_change_notes_route_drops_invalid_and_duplicate_ids(monkeypatch):
    seen: list = []

    def fake_change_notes_for(ids):
        seen.append(ids)
        return {"notes": {"20": None}, "errors": []}

    monkeypatch.setattr(pn, "change_notes_for", fake_change_notes_for)

    res = TestClient(app).post("/api/games/total_war_warhammer_3/packs/change-notes", json={"ids": ["20", "abc", "20", ""]})

    assert res.status_code == 200
    assert res.json() == {"notes": {"20": None}, "errors": []}
    assert seen == [["20"]]
