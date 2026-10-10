"""Tests for the translation drift / staleness algorithm."""

from backend.games.total_war_warhammer_3.loc_extractor import LocRow
from backend.games.total_war_warhammer_3.translation_drift import (
    DriftRow,
    compute_drift,
    hash_text,
)


def _row(key: str, text: str) -> LocRow:
    return LocRow(key=key, text=text, tooltip=False)


def test_hash_text_is_stable_sha256_hex():
    assert hash_text("hello") == hash_text("hello")
    assert hash_text("hello") != hash_text("world")
    assert len(hash_text("x")) == 64


def test_compute_drift_marks_missing_translation_as_untranslated():
    parent = {"units.loc.tsv": {"k1": _row("k1", "原始")}}
    translation = {"units.loc.tsv": {}}
    snapshot = {}

    result = compute_drift(parent=parent, translation=translation, snapshot=snapshot)

    assert result[0].status == "untranslated"
    assert result[0].key == "k1"
    assert result[0].parent_text == "原始"
    assert result[0].translation_text is None


def test_compute_drift_marks_empty_translation_text_as_untranslated():
    # An empty .loc.tsv entry is not a translation, even when the parent source changed since the snapshot.
    parent = {"units.loc.tsv": {"k1": _row("k1", "原始"), "k2": _row("k2", "新文本")}}
    translation = {"units.loc.tsv": {"k1": _row("k1", ""), "k2": _row("k2", "")}}
    snapshot = {"units.loc.tsv": {"k1": hash_text("原始"), "k2": hash_text("")}}

    result = compute_drift(parent=parent, translation=translation, snapshot=snapshot)

    assert [r.status for r in result] == ["untranslated", "untranslated"]


def test_compute_drift_marks_unchanged_parent_as_translated():
    parent = {"units.loc.tsv": {"k1": _row("k1", "原始")}}
    translation = {"units.loc.tsv": {"k1": _row("k1", "Original")}}
    snapshot = {"units.loc.tsv": {"k1": hash_text("原始")}}

    result = compute_drift(parent=parent, translation=translation, snapshot=snapshot)

    assert len(result) == 1
    assert result[0].status == "translated"
    assert result[0].translation_text == "Original"


def test_compute_drift_marks_changed_parent_as_stale():
    parent = {"units.loc.tsv": {"k1": _row("k1", "新文本")}}
    translation = {"units.loc.tsv": {"k1": _row("k1", "Original")}}
    snapshot = {"units.loc.tsv": {"k1": hash_text("原始")}}

    result = compute_drift(parent=parent, translation=translation, snapshot=snapshot)

    assert result[0].status == "stale"
    assert result[0].parent_text == "新文本"
    assert result[0].translation_text == "Original"


def test_compute_drift_marks_orphan_when_only_in_translation():
    parent = {"units.loc.tsv": {}}
    translation = {"units.loc.tsv": {"k1": _row("k1", "Orphaned")}}
    snapshot = {}

    result = compute_drift(parent=parent, translation=translation, snapshot=snapshot)

    assert result[0].status == "orphan"
    assert result[0].translation_text == "Orphaned"
    assert result[0].parent_text is None


def test_compute_drift_treats_missing_snapshot_for_known_key_as_translated():
    """A key the user already translated but for which we never recorded a snapshot
    must NOT be flagged as stale. Treat absence-of-snapshot as 'baseline = current parent'."""
    parent = {"units.loc.tsv": {"k1": _row("k1", "原始")}}
    translation = {"units.loc.tsv": {"k1": _row("k1", "Original")}}
    snapshot: dict = {}

    result = compute_drift(parent=parent, translation=translation, snapshot=snapshot)

    assert result[0].status == "translated"


def test_compute_drift_sorts_by_filename_then_key():
    parent = {
        "b.loc.tsv": {"k2": _row("k2", "b2"), "k1": _row("k1", "b1")},
        "a.loc.tsv": {"k1": _row("k1", "a1")},
    }
    result = compute_drift(parent=parent, translation={}, snapshot={})
    assert [(r.source_filename, r.key) for r in result] == [
        ("a.loc.tsv", "k1"),
        ("b.loc.tsv", "k1"),
        ("b.loc.tsv", "k2"),
    ]


def test_drift_row_default_translation_text_is_none():
    row = DriftRow(
        source_filename="a.loc.tsv",
        key="k",
        parent_text="x",
        translation_text=None,
        status="untranslated",
    )
    assert row.translation_text is None


def test_drift_row_default_provider_is_none():
    row = DriftRow(
        source_filename="a.loc.tsv",
        key="k",
        parent_text="x",
        translation_text=None,
        status="untranslated",
    )
    assert row.provider is None


from backend.games.total_war_warhammer_3.translation_drift import entry_hash, entry_text, make_entry


def test_make_entry_holds_hash_and_text():
    assert make_entry("abc") == {"hash": hash_text("abc"), "text": "abc"}


def test_entry_helpers_accept_legacy_hash_strings_and_none():
    assert entry_hash(hash_text("x")) == hash_text("x")
    assert entry_text(hash_text("x")) is None
    assert entry_hash(None) is None
    assert entry_text(None) is None
    assert entry_hash(make_entry("x")) == hash_text("x")
    assert entry_text(make_entry("x")) == "x"


def test_compute_drift_reads_dict_snapshot_entries():
    parent = {"units.loc.tsv": {"k1": _row("k1", "新文本")}}
    translation = {"units.loc.tsv": {"k1": _row("k1", "Original")}}
    snapshot = {"units.loc.tsv": {"k1": make_entry("原始")}}

    assert compute_drift(parent=parent, translation=translation, snapshot=snapshot)[0].status == "stale"
    snapshot = {"units.loc.tsv": {"k1": make_entry("新文本")}}
    assert compute_drift(parent=parent, translation=translation, snapshot=snapshot)[0].status == "translated"
