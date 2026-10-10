"""Tests for WH3 translation mod changenotes: the shipped string set, the published baseline, and the note text."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from backend.games.total_war_warhammer_3 import translation_change_notes as tcn
from backend.games.total_war_warhammer_3.loc_extractor import LocRow
from backend.games.total_war_warhammer_3.routes import translation as routes_module
from backend.games.total_war_warhammer_3.translation_drift import DriftRow, hash_text
from backend.games.total_war_warhammer_3.translation_mods import WH3TranslationMod

MOD_ID = "3315737452"


def _row(key: str, text: str | None, status: str = "translated", filename: str = "units.loc.tsv") -> DriftRow:
    """Build a drift row with the fields the shipped set reads."""
    return DriftRow(source_filename=filename, key=key, parent_text="src", translation_text=text, status=status, provider=None)


@pytest.fixture
def storage(monkeypatch, tmp_path: Path) -> Path:
    """Isolate game storage under tmp_path and return the mod's storage folder."""
    monkeypatch.setattr("backend.config.STORAGE_PATH", tmp_path, raising=True)
    return tmp_path / "games" / "total_war_warhammer_3" / "mods" / MOD_ID


@pytest.fixture
def fake_mod(monkeypatch, storage: Path, tmp_path: Path) -> WH3TranslationMod:
    """Register a synthetic mod whose parent has k1 and k2 and whose .loc.tsv translates only k1."""
    mod = WH3TranslationMod(workshop_id=MOD_ID, display_name="Test Mod", parent_workshop_ids=("p",), local_source_dir=tmp_path / "src")
    monkeypatch.setattr(routes_module, "get_translation_mod", lambda mid: mod if mid == MOD_ID else None)
    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", lambda m: {"units.loc.tsv": {"k1": LocRow("k1", "原", True), "k2": LocRow("k2", "新", True)}})
    monkeypatch.setattr(routes_module, "_extract_translation_strings", lambda m: {"units.loc.tsv": {"k1": LocRow("k1", "Existing", True)}})
    return mod


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# Shipped set


def test_shipped_strings_hashes_translated_rows_per_file():
    rows = [_row("k1", "One"), _row("k2", "Two", filename="skills.loc.tsv")]

    assert tcn.shipped_strings(rows) == {"units.loc.tsv": {"k1": hash_text("One")}, "skills.loc.tsv": {"k2": hash_text("Two")}}


def test_shipped_strings_skips_orphans_and_empty_text():
    rows = [_row("k1", "One", status="orphan"), _row("k2", ""), _row("k3", None, status="untranslated"), _row("k4", "Four", status="stale")]

    assert tcn.shipped_strings(rows) == {"units.loc.tsv": {"k4": hash_text("Four")}}


def test_current_shipped_strings_applies_translations_json(fake_mod, storage: Path):
    storage.mkdir(parents=True)
    (storage / "translations.json").write_text(json.dumps({"k2": {"text": "New", "provider": "claude"}}), encoding="utf-8")

    assert routes_module.current_shipped_strings(MOD_ID) == {"units.loc.tsv": {"k1": hash_text("Existing"), "k2": hash_text("New")}}


def test_current_shipped_strings_leaves_out_cleared_overrides(fake_mod, storage: Path):
    storage.mkdir(parents=True)
    (storage / "translations.json").write_text(json.dumps({"k1": {"text": "", "provider": "manual"}}), encoding="utf-8")

    assert routes_module.current_shipped_strings(MOD_ID) == {}


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# Baseline


def test_baseline_round_trips_and_lives_outside_snapshots(storage: Path):
    strings = {"units.loc.tsv": {"k1": "h1"}}

    tcn.save_baseline(MOD_ID, strings, "[u]Translation update[/u]")

    loaded = tcn.load_baseline(MOD_ID)
    assert loaded["strings"] == strings
    assert loaded["changenote"] == "[u]Translation update[/u]"
    assert loaded["published_at"]
    assert (storage / "published_baseline.json").is_file()
    assert not (storage / "published_baseline.json.tmp").exists()


def test_load_baseline_is_none_when_missing_or_corrupt(storage: Path):
    assert tcn.load_baseline(MOD_ID) is None
    storage.mkdir(parents=True)
    (storage / "published_baseline.json").write_text("{not json", encoding="utf-8")
    assert tcn.load_baseline(MOD_ID) is None


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# Note text


def test_diff_counts_new_revised_and_removed_by_file_and_key():
    baseline = {"a.loc.tsv": {"k1": "h1", "k2": "h2", "gone": "h"}, "b.loc.tsv": {"k1": "h1"}}
    shipped = {"a.loc.tsv": {"k1": "h1", "k2": "changed", "k3": "h3"}, "b.loc.tsv": {"k1": "h1", "k9": "h9"}}

    assert tcn.diff_counts(baseline, shipped) == (2, 1, 1)


def test_build_note_names_when_the_parent_mods_were_last_updated():
    note, pending = tcn.build_note(None, {"a": {"k": "h"}}, datetime(2026, 10, 5, 10, 53))

    assert pending is True
    assert note == "[u]Translation update[/u]\n\nUpdated the translations to the latest mod changes as of Oct 5 @ 10:53am."


def test_build_note_is_generic_when_the_parent_update_time_is_unknown():
    assert tcn.build_note(None, {"a": {"k": "h"}}, None) == ("[u]Translation update[/u]", True)


def test_build_note_is_pending_only_when_the_shipped_set_changed():
    shipped = {"a": {"k": "h"}}

    assert tcn.build_note({"strings": shipped}, shipped, None)[1] is False
    assert tcn.build_note({"strings": shipped}, {"a": {"k": "x"}}, None)[1] is True
    assert tcn.build_note({"strings": shipped}, {}, None)[1] is True


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (datetime(2026, 10, 5, 7, 49), "Oct 5 @ 7:49am"),
        (datetime(2026, 10, 12, 0, 5), "Oct 12 @ 12:05am"),
        (datetime(2026, 1, 3, 12, 0), "Jan 3 @ 12:00pm"),
        (datetime(2026, 6, 30, 22, 48), "Jun 30 @ 10:48pm"),
    ],
)
def test_format_update_time_matches_the_steam_workshop_style(when: datetime, expected: str):
    assert tcn.format_update_time(when) == expected


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# Parent update time

MANIFEST = """"AppWorkshop"
{
	"WorkshopItemsInstalled"
	{
		"111"
		{
			"size"		"10"
			"timeupdated"		"1791211788"
		}
		"222"
		{
			"timeupdated"		"1791300000"
		}
		"333"
		{
			"timeupdated"		"1799999999"
		}
	}
	"WorkshopItemDetails"
	{
		"111"
		{
			"timeupdated"		"1791211788"
			"latest_timeupdated"		"1791211788"
		}
	}
}
"""


@pytest.fixture
def manifest(monkeypatch, tmp_path: Path) -> Path:
    """Point the Workshop manifest lookup at a temp file holding `MANIFEST`."""
    path = tmp_path / "appworkshop_1142710.acf"
    path.write_text(MANIFEST, encoding="utf-8")
    monkeypatch.setattr(tcn, "tw3_workshop_manifest_path", lambda: path)
    return path


def test_parent_updated_at_reads_one_parent(manifest: Path):
    assert tcn.parent_updated_at(["111"]) == datetime.fromtimestamp(1791211788)


def test_parent_updated_at_takes_the_latest_parent_of_a_collection(manifest: Path):
    assert tcn.parent_updated_at(["111", "222", "999"]) == datetime.fromtimestamp(1791300000)


def test_parent_updated_at_is_none_for_unknown_parents_or_a_missing_manifest(manifest: Path, monkeypatch, tmp_path: Path):
    assert tcn.parent_updated_at(["999"]) is None
    monkeypatch.setattr(tcn, "tw3_workshop_manifest_path", lambda: tmp_path / "missing.acf")
    assert tcn.parent_updated_at(["111"]) is None
    monkeypatch.setattr(tcn, "tw3_workshop_manifest_path", lambda: None)
    assert tcn.parent_updated_at(["111"]) is None
