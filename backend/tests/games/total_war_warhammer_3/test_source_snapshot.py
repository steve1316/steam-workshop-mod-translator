"""Tests for the WH3 parent source snapshot helpers."""

from backend.games.total_war_warhammer_3 import translation_store_helpers as store
from backend.games.total_war_warhammer_3.loc_extractor import LocRow
from backend.games.total_war_warhammer_3.source_snapshot import accept_keys, refresh_unchanged
from backend.games.total_war_warhammer_3.translation_drift import hash_text, make_entry


def _parent(**texts: str) -> dict:
    return {"units.loc.tsv": {k: LocRow(k, v, True) for k, v in texts.items()}}


def test_refresh_adds_entries_for_new_keys():
    out = refresh_unchanged({}, _parent(k1="new"))
    assert out == {"units.loc.tsv": {"k1": make_entry("new")}}


def test_refresh_keeps_a_drifted_entry_untouched():
    snap = {"units.loc.tsv": {"k1": make_entry("old")}}
    out = refresh_unchanged(snap, _parent(k1="new"))
    assert out["units.loc.tsv"]["k1"] == make_entry("old")


def test_refresh_backfills_text_on_a_matching_legacy_hash():
    snap = {"units.loc.tsv": {"k1": hash_text("same")}}
    out = refresh_unchanged(snap, _parent(k1="same"))
    assert out["units.loc.tsv"]["k1"] == make_entry("same")


def test_refresh_does_not_mutate_its_input_and_keeps_keys_missing_from_parent():
    snap = {"units.loc.tsv": {"gone": make_entry("x")}}
    out = refresh_unchanged(snap, _parent(k1="a"))
    assert snap == {"units.loc.tsv": {"gone": make_entry("x")}}
    assert "gone" in out["units.loc.tsv"]


def test_accept_keys_rebaselines_only_the_given_keys():
    snap = {"units.loc.tsv": {"k1": make_entry("old1"), "k2": make_entry("old2")}}
    out = accept_keys(snap, _parent(k1="new1", k2="new2"), {"k1"})
    assert out["units.loc.tsv"]["k1"] == make_entry("new1")
    assert out["units.loc.tsv"]["k2"] == make_entry("old2")


def test_load_parent_snapshot_normalizes_legacy_hash_strings(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.config.STORAGE_PATH", tmp_path, raising=True)
    mod_dir = tmp_path / "games" / "total_war_warhammer_3" / "mods" / "mod1"
    mod_dir.mkdir(parents=True)
    (mod_dir / "parent_snapshot.json").write_text('{"units.loc.tsv": {"k1": "abc"}}', encoding="utf-8")

    snap = store.load_parent_snapshot("mod1")

    assert snap == {"units.loc.tsv": {"k1": {"hash": "abc", "text": None}}}
