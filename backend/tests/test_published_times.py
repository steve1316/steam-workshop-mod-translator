"""Tests for the recorded Workshop publish times."""

from backend.games.total_war_warhammer_3 import published_times


def test_get_returns_none_when_nothing_recorded():
    assert published_times.get("123") is None


def test_record_then_get_round_trips_and_keeps_other_items():
    published_times.record("123", 1_750_000_000.0)
    published_times.record("456", 1_760_000_000.0)
    assert published_times.get("123") == 1_750_000_000.0
    assert published_times.get("456") == 1_760_000_000.0


def test_get_ignores_a_corrupt_file():
    published_times._path().write_text("not json", encoding="utf-8")
    assert published_times.get("123") is None
