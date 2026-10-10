"""Tests for the canonical translation status model."""

from backend.translation.status import RowStatus, StatusRow, classify_status


def test_untranslatable_reason_wins_over_everything():
    assert classify_status(untranslatable_reason="machine code", has_translation=True, is_synced=True) == "untranslatable"


def test_source_drift_demotes_a_synced_row_to_changed():
    # The shared rule: a translated row whose source changed needs re-review, even if it was synced.
    assert classify_status(has_translation=True, is_synced=True, source_changed=True) == "changed"


def test_source_drift_wins_over_an_unsynced_edit():
    assert classify_status(has_translation=True, is_synced=False, source_changed=True) == "changed"


def test_untranslatable_wins_over_source_drift():
    assert classify_status(untranslatable_reason="blank", has_translation=True, source_changed=True) == "untranslatable"


def test_synced_with_no_drift_is_synced():
    assert classify_status(has_translation=True, is_synced=True, source_changed=False) == "synced"


def test_untouched_row_is_untouched():
    assert classify_status(has_translation=True, is_synced=False, is_untouched=True) == "untouched"


def test_translated_but_not_synced_is_pending():
    assert classify_status(has_translation=True, is_synced=False, is_untouched=False) == "pending"


def test_no_translation_is_missing():
    assert classify_status(has_translation=False) == "missing"


def test_source_drift_without_translation_is_still_missing():
    # Drift only matters once there is a translation to re-review.
    assert classify_status(has_translation=False, source_changed=True) == "missing"


def test_status_row_defaults_and_required_fields():
    row = StatusRow(key="k1", status="missing")
    assert row.key == "k1"
    assert row.status == "missing"
    assert row.source_file == ""
    assert row.source_text is None
    assert row.target_text is None
    assert row.provider is None
    assert row.original_target is None
    assert row.untranslatable_reason is None


def test_status_row_serializes_all_fields():
    row = StatusRow(
        key="k1",
        source_file="db/text.loc.tsv",
        source_text="원본",
        target_text="Origin",
        status="synced",
        provider="claude",
        original_target="Origin (old)",
        untranslatable_reason=None,
    )
    dumped = row.model_dump()
    assert dumped == {
        "key": "k1",
        "source_file": "db/text.loc.tsv",
        "source_text": "원본",
        "target_text": "Origin",
        "status": "synced",
        "provider": "claude",
        "original_target": "Origin (old)",
        "untranslatable_reason": None,
    }


def test_row_status_is_the_six_canonical_states():
    # Guards against accidental drift of the canonical vocabulary.
    assert set(RowStatus.__args__) == {"synced", "untouched", "pending", "changed", "missing", "untranslatable"}
