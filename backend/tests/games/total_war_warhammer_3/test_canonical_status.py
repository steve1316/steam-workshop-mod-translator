"""Tests for mapping WH3 drift rows to the canonical five-state status."""

from backend.games.total_war_warhammer_3.canonical_status import to_status_rows
from backend.games.total_war_warhammer_3.translation_drift import DriftRow


def _drift(key, parent, trans, status):
    return DriftRow(source_filename="text/db.loc.tsv", key=key, parent_text=parent, translation_text=trans, status=status)


def test_untranslated_maps_to_missing():
    rows = to_status_rows([_drift("k", "src", None, "untranslated")], {})
    assert rows[0].status == "missing"
    assert rows[0].target_text is None
    assert rows[0].source_text == "src"
    assert rows[0].provider is None


def test_blank_source_maps_to_untranslatable():
    rows = to_status_rows([_drift("k1", "", "", "translated"), _drift("k2", "  ", None, "untranslated")], {})
    assert [r.status for r in rows] == ["untranslatable", "untranslatable"]
    assert rows[0].untranslatable_reason


def test_orphan_maps_to_missing():
    rows = to_status_rows([_drift("k", None, "Hello", "orphan")], {})
    assert rows[0].status == "missing"
    assert rows[0].source_text is None


def test_translated_on_disk_with_no_override_is_synced():
    rows = to_status_rows([_drift("k", "src", "Hello", "translated")], {})
    assert rows[0].status == "synced"
    assert rows[0].target_text == "Hello"
    assert rows[0].provider == "manual"


def test_stale_maps_to_changed():
    rows = to_status_rows([_drift("k", "src-new", "Hello", "stale")], {})
    assert rows[0].status == "changed"


def test_override_differing_from_disk_is_pending():
    drift = [_drift("k", "src", "OldOnDisk", "translated")]
    rows = to_status_rows(drift, {"k": {"text": "NewEdit", "provider": "claude"}})
    assert rows[0].status == "pending"
    assert rows[0].target_text == "NewEdit"
    assert rows[0].provider == "claude"


def test_override_matching_disk_is_synced():
    drift = [_drift("k", "src", "Hello", "translated")]
    rows = to_status_rows(drift, {"k": {"text": "Hello", "provider": "claude"}})
    assert rows[0].status == "synced"


def test_empty_override_clear_maps_to_missing():
    drift = [_drift("k", "src", "Hello", "translated")]
    rows = to_status_rows(drift, {"k": {"text": ""}})
    assert rows[0].status == "missing"
    assert rows[0].target_text == ""
    assert rows[0].provider is None


def test_unsynced_edit_on_untranslated_file_row_is_pending():
    # File row is untranslated, but translations.json holds a fresh edit not yet synced to disk.
    drift = [_drift("k", "src", None, "untranslated")]
    rows = to_status_rows(drift, {"k": {"text": "New", "provider": "claude"}})
    assert rows[0].status == "pending"
    assert rows[0].target_text == "New"
    assert rows[0].provider == "claude"
