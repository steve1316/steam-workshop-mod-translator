"""Tests for the CA-parity WH3 translate router (preview / batch / cancel).

This router exposes the same `/translate/preview`, `/translate/batch`, and `/translate/cancel` contract as Chrono Ark so the shared `useIterativeTranslation`
hook and `TranslationConfirmModal` drive WH3 translation identically.
"""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.games.total_war_warhammer_3.loc_extractor import LocRow
from backend.games.total_war_warhammer_3.routes import translate as translate_module
from backend.games.total_war_warhammer_3.routes import translation as routes_module
from backend.games.total_war_warhammer_3.translation_mods import WH3TranslationMod


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    """Build a TestClient with all I/O isolated to tmp_path and parent extraction stubbed.

    Mirrors the fixture in `test_translation_routes_v3.py`. `k1` has an existing `.loc.tsv` translation; `k2` is untranslated.
    """
    monkeypatch.setattr("backend.config.STORAGE_PATH", tmp_path, raising=True)

    fake_source = tmp_path / "translation_mod_source"
    (fake_source / "text").mkdir(parents=True, exist_ok=True)
    fake_mod = WH3TranslationMod(
        workshop_id="3315737452",
        display_name="Test Mod",
        parent_workshop_ids=("p",),
        local_source_dir=fake_source,
    )
    monkeypatch.setattr(routes_module, "get_translation_mod", lambda mid: fake_mod if mid == "3315737452" else None)

    def fake_parent(mod):
        return {"units.loc.tsv": {"k1": LocRow("k1", "原", True), "k2": LocRow("k2", "新", True)}}

    def fake_translation(mod):
        return {"units.loc.tsv": {"k1": LocRow("k1", "Existing", True)}}

    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", fake_parent)
    monkeypatch.setattr(routes_module, "_extract_translation_strings", fake_translation)

    app = FastAPI()
    app.include_router(routes_module.router, prefix="/api/games/total_war_warhammer_3")
    app.include_router(translate_module.router, prefix="/api/games/total_war_warhammer_3")
    return TestClient(app)


PREFIX = "/api/games/total_war_warhammer_3/translate"


def test_preview_builds_batch_plan_for_untranslated(client: TestClient):
    """Preview returns a flat batch plan plus prompt previews and estimates for the untranslated rows only."""
    resp = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452"})
    assert resp.status_code == 200
    body = resp.json()
    # Only k2 is untranslated (k1 has existing .loc.tsv text).
    assert body["total_strings"] == 1
    assert body["total_batches"] == 1
    assert body["batch_plan"][0]["keys"] == ["k2"]
    assert body["batch_plan"][0]["source_lang"] == "Chinese"
    assert "Chinese" in body["previews"]
    assert body["previews"]["Chinese"]["strings_in_language"] == 1
    assert "Chinese" in body["estimates"]


def test_preview_returns_zero_when_all_translated(client: TestClient, monkeypatch):
    """Preview reports zero strings when every parent key already has a translation."""

    def all_translated(mod):
        return {"units.loc.tsv": {"k1": LocRow("k1", "Existing 1", True), "k2": LocRow("k2", "Existing 2", True)}}

    monkeypatch.setattr(routes_module, "_extract_translation_strings", all_translated)
    resp = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452"})
    assert resp.status_code == 200
    assert resp.json()["total_strings"] == 0


def test_batch_translates_keys_and_persists(client: TestClient, monkeypatch, tmp_path: Path):
    """A batch call translates the given keys via the provider, returns the translations + suggestions, and writes translations.json."""

    def fake_translate_batch(self, entries, source_lang, glossary_prompt, **kwargs):
        return ({"k2": "New Translation"}, [])

    monkeypatch.setattr("backend.translator.claude_provider.ClaudeProvider.translate_batch", fake_translate_batch)

    resp = client.post(
        f"{PREFIX}/batch",
        json={"mod_id": "3315737452", "provider": "claude", "keys": ["k2"], "source_lang": "Chinese", "is_first_batch": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["translations"] == {"k2": "New Translation"}
    assert body["translated"] == 1
    assert body["suggestions"] == []

    raw_path = tmp_path / "games" / "total_war_warhammer_3" / "mods" / "3315737452" / "translations.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw["k2"]["text"] == "New Translation"
    assert raw["k2"]["provider"] == "claude"


def test_batch_persists_and_returns_new_glossary_suggestions(client: TestClient, monkeypatch, tmp_path: Path):
    """The provider's glossary suggestions are returned and persisted for the review modal; terms already in the glossary are filtered out."""
    from backend.data import suggestion_manager
    from backend.games.storage_paths import game_storage_path
    from backend.games.total_war_warhammer_3 import glossary_store

    # "Lord" is already in the glossary, so it must be filtered out; only "Cathay" should survive.
    glossary_store.add_term("3315737452", {"english": "Lord", "source": "卿", "category": "title"})

    def fake_translate_batch(self, entries, source_lang, glossary_prompt, **kwargs):
        return (
            {"k2": "New Translation"},
            [
                {"english": "Lord", "source": "卿", "source_lang": "Chinese", "category": "title", "reason": "already known"},
                {"english": "Cathay", "source": "震旦", "source_lang": "Chinese", "category": "faction", "reason": "new term"},
            ],
        )

    monkeypatch.setattr("backend.translator.claude_provider.ClaudeProvider.translate_batch", fake_translate_batch)

    resp = client.post(
        f"{PREFIX}/batch",
        json={"mod_id": "3315737452", "provider": "claude", "keys": ["k2"], "source_lang": "Chinese", "is_first_batch": True},
    )
    assert resp.status_code == 200
    returned = {s["english"] for s in resp.json()["suggestions"]}
    assert returned == {"Cathay"}

    persisted = {s["english"] for s in suggestion_manager.load_suggestions("3315737452", storage_path=game_storage_path("total_war_warhammer_3"))}
    assert persisted == {"Cathay"}


def test_preview_splits_batches_by_source_character_budget(client: TestClient, monkeypatch):
    """Long strings get fewer keys per batch so the reply fits Claude's output cap."""

    def long_parent(mod):
        return {"units.loc.tsv": {k: LocRow(k, "长" * 60, True) for k in ("k2", "k3", "k4")}}

    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", long_parent)
    monkeypatch.setattr("backend.config.BATCH_MAX_CHARS", 100)
    body = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452"}).json()
    assert [b["keys"] for b in body["batch_plan"]] == [["k2"], ["k3"], ["k4"]]
    assert body["total_batches"] == 3


def _log_entries(tmp_path: Path) -> list[dict]:
    """Read the test mod's API-responses log.

    Args:
        tmp_path: The isolated storage root.

    Returns:
        The logged entries.
    """
    path = tmp_path / "games" / "total_war_warhammer_3" / "mods" / "3315737452" / "api_responses.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_batch_logs_claudes_raw_text_model_and_usage(client: TestClient, monkeypatch, tmp_path: Path):
    """The log keeps what Claude actually returned, not the parsed translations."""

    def fake_translate_batch(self, entries, source_lang, glossary_prompt, **kwargs):
        self.last_raw_responses = [
            {"model": "claude-sonnet-5", "input_tokens": 100, "output_tokens": 40, "cost_usd": 0.001, "stop_reason": "end_turn", "raw_text": "RAW-1"},
            {"model": "claude-sonnet-5", "input_tokens": 50, "output_tokens": 20, "cost_usd": 0.0005, "stop_reason": "end_turn", "raw_text": "RAW-2"},
        ]
        return ({"k2": "New Translation"}, [])

    monkeypatch.setattr("backend.translator.claude_provider.ClaudeProvider.translate_batch", fake_translate_batch)
    client.post(f"{PREFIX}/batch", json={"mod_id": "3315737452", "provider": "claude", "keys": ["k2"], "source_lang": "Chinese", "is_first_batch": True})

    entry = _log_entries(tmp_path)[-1]
    assert entry["model"] == "claude-sonnet-5"
    assert entry["input_tokens"] == 150
    assert entry["output_tokens"] == 60
    assert entry["cost_usd"] == pytest.approx(0.0015)
    assert "RAW-1" in entry["raw_response"] and "RAW-2" in entry["raw_response"]


def test_batch_logs_a_failed_call_with_its_error(client: TestClient, monkeypatch, tmp_path: Path):
    """A failed batch is still logged, with the error and whatever Claude returned, and the route returns 502."""

    def fake_translate_batch(self, entries, source_lang, glossary_prompt, **kwargs):
        self.last_raw_responses = [{"model": "claude-sonnet-5", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0, "stop_reason": "end_turn", "raw_text": "not json"}]
        raise ValueError("Could not parse any translations from Claude's response")

    monkeypatch.setattr("backend.translator.claude_provider.ClaudeProvider.translate_batch", fake_translate_batch)
    resp = client.post(f"{PREFIX}/batch", json={"mod_id": "3315737452", "provider": "claude", "keys": ["k2"], "source_lang": "Chinese", "is_first_batch": True})
    assert resp.status_code == 502

    entry = _log_entries(tmp_path)[-1]
    assert entry["raw_response"].startswith("ERROR: Could not parse any translations")
    assert "not json" in entry["raw_response"]
    assert entry["keys_or_inputs"] == ["k2"]


def test_batch_rejects_keys_with_no_source_text(client: TestClient):
    """A batch whose keys have no source text returns 400, matching Chrono Ark."""
    resp = client.post(
        f"{PREFIX}/batch",
        json={"mod_id": "3315737452", "provider": "claude", "keys": ["does_not_exist"], "source_lang": "Chinese", "is_first_batch": True},
    )
    assert resp.status_code == 400


def test_cancel_returns_ok(client: TestClient):
    """Cancel is a safe no-op for WH3's non-streaming Claude path and returns a cancelled flag."""
    resp = client.post(f"{PREFIX}/cancel?mod_id=3315737452")
    assert resp.status_code == 200
    assert "cancelled" in resp.json()


def test_preview_scope_names_includes_only_name_keys(client: TestClient, monkeypatch):
    """preview(scope='names') restricts the batch plan to name keys, excluding description keys."""

    def parent_with_names(mod):
        return {
            "units.loc.tsv": {
                "land_units_onscreen_name_dragon": LocRow("land_units_onscreen_name_dragon", "龙卫", True),
                "unit_description_short_texts_text_dragon": LocRow("unit_description_short_texts_text_dragon", "描述", True),
            }
        }

    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", parent_with_names)
    monkeypatch.setattr(routes_module, "_extract_translation_strings", lambda mod: {})

    resp = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452", "scope": "names"})
    assert resp.status_code == 200
    body = resp.json()
    planned = [k for batch in body["batch_plan"] for k in batch["keys"]]
    assert planned == ["land_units_onscreen_name_dragon"]


def test_preview_scope_names_zero_state(client: TestClient, monkeypatch):
    """preview(scope='names') reports zero with a names-specific message when no name keys are untranslated."""

    def parent_no_names(mod):
        return {"units.loc.tsv": {"unit_description_short_texts_text_dragon": LocRow("unit_description_short_texts_text_dragon", "描述", True)}}

    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", parent_no_names)
    monkeypatch.setattr(routes_module, "_extract_translation_strings", lambda mod: {})

    resp = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452", "scope": "names"})
    assert resp.status_code == 200
    assert resp.json()["total_strings"] == 0
    assert "names" in resp.json()["message"].lower()


def test_name_suggestions_builds_categorized_suggestions(client: TestClient, monkeypatch, tmp_path: Path):
    """After a names pass, name-suggestions maps each translated name row to a categorized suggestion and persists it."""
    from backend.data import suggestion_manager
    from backend.games.storage_paths import game_storage_path

    mod_id = "3315737452"

    def parent_with_names(mod):
        return {
            "units.loc.tsv": {
                "land_units_onscreen_name_dragon": LocRow("land_units_onscreen_name_dragon", "龙卫", True),
                "effects_description_foo": LocRow("effects_description_foo", "效果", True),
            }
        }

    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", parent_with_names)
    mod_dir = tmp_path / "games" / "total_war_warhammer_3" / "mods" / mod_id
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "translations.json").write_text(
        json.dumps(
            {
                "land_units_onscreen_name_dragon": {"text": "Dragon Guard", "provider": "claude"},
                "effects_description_foo": {"text": "Some effect", "provider": "claude"},
            }
        ),
        encoding="utf-8",
    )

    resp = client.post(f"{PREFIX}/name-suggestions", json={"mod_id": mod_id})
    assert resp.status_code == 200
    sugg = resp.json()["suggestions"]
    assert len(sugg) == 1
    assert sugg[0]["english"] == "Dragon Guard"
    assert sugg[0]["source"] == "龙卫"
    assert sugg[0]["category"] == "unit"

    persisted = {s["english"] for s in suggestion_manager.load_suggestions(mod_id, storage_path=game_storage_path("total_war_warhammer_3"))}
    assert persisted == {"Dragon Guard"}


def test_name_suggestions_skips_terms_already_in_glossary(client: TestClient, monkeypatch, tmp_path: Path):
    """A translated name already present in the mod glossary is not re-suggested."""
    from backend.games.total_war_warhammer_3 import glossary_store

    mod_id = "3315737452"
    glossary_store.add_term(mod_id, {"english": "Dragon Guard", "source": "龙卫", "category": "unit"})

    def parent_with_names(mod):
        return {"units.loc.tsv": {"land_units_onscreen_name_dragon": LocRow("land_units_onscreen_name_dragon", "龙卫", True)}}

    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", parent_with_names)
    mod_dir = tmp_path / "games" / "total_war_warhammer_3" / "mods" / mod_id
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "translations.json").write_text(
        json.dumps({"land_units_onscreen_name_dragon": {"text": "Dragon Guard", "provider": "claude"}}),
        encoding="utf-8",
    )

    resp = client.post(f"{PREFIX}/name-suggestions", json={"mod_id": mod_id})
    assert resp.status_code == 200
    assert resp.json()["suggestions"] == []


def _names_parent(mod):
    """Parent strings with one name per stage (listed out of stage order) plus a description row."""
    return {
        "units.loc.tsv": {
            "unit_abilities_onscreen_name_roar": LocRow("unit_abilities_onscreen_name_roar", "龙吼", True),
            "rituals_display_name_rite": LocRow("rituals_display_name_rite", "仪式", True),
            "land_units_onscreen_name_dragon": LocRow("land_units_onscreen_name_dragon", "龙卫", True),
            "regions_battle_name_city": LocRow("regions_battle_name_city", "城", True),
            "building_culture_variants_name_tower": LocRow("building_culture_variants_name_tower", "塔", True),
            "unit_description_short_texts_text_dragon": LocRow("unit_description_short_texts_text_dragon", "描述", True),
        }
    }


def _write_translations(tmp_path: Path, mod_id: str, entries: dict[str, str]) -> None:
    """Write `translations.json` for a mod with the given key -> English text entries.

    Args:
        tmp_path: The isolated storage root.
        mod_id: Workshop ID of the mod.
        entries: Key -> English text.
    """
    mod_dir = tmp_path / "games" / "total_war_warhammer_3" / "mods" / mod_id
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "translations.json").write_text(json.dumps({k: {"text": v, "provider": "claude"} for k, v in entries.items()}), encoding="utf-8")


def test_preview_scope_names_orders_batches_by_stage(client: TestClient, monkeypatch):
    """A names preview groups the batches into stages: units, then skills, then buildings and locations, then everything else."""
    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", _names_parent)
    monkeypatch.setattr(routes_module, "_extract_translation_strings", lambda mod: {})

    body = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452", "scope": "names"}).json()
    stages = body["stages"]
    assert [s["id"] for s in stages] == ["units", "skills", "places", "other"]
    assert [s["label"] for s in stages] == ["Units & Lords", "Skills & Abilities", "Buildings & Locations", "Items, Traits, Techs & Rituals"]
    assert stages[0]["categories"] == ["unit"]
    assert sorted(k for b in stages[2]["batch_plan"] for k in b["keys"]) == ["building_culture_variants_name_tower", "regions_battle_name_city"]
    assert stages[3]["total_strings"] == 1
    # The flat plan is the stages in order, and no batch mixes two stages.
    flat = [k for b in body["batch_plan"] for k in b["keys"]]
    assert flat[:2] == ["land_units_onscreen_name_dragon", "unit_abilities_onscreen_name_roar"]
    assert body["total_batches"] == 4
    assert body["total_strings"] == 5


def test_preview_scope_names_leaves_out_empty_stages(client: TestClient, monkeypatch):
    """Stages with no untranslated names are not returned."""
    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", _names_parent)
    monkeypatch.setattr(
        routes_module,
        "_extract_translation_strings",
        lambda mod: {"units.loc.tsv": {"unit_abilities_onscreen_name_roar": LocRow("unit_abilities_onscreen_name_roar", "Dragon Roar", True)}},
    )

    body = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452", "scope": "names"}).json()
    assert [s["id"] for s in body["stages"]] == ["units", "places", "other"]


def test_preview_scope_all_has_no_stages(client: TestClient):
    """A normal preview does not return stages."""
    body = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452"}).json()
    assert "stages" not in body


def test_name_suggestions_filters_by_categories(client: TestClient, monkeypatch, tmp_path: Path):
    """Passing `categories` limits the suggestions to those name categories, for the per-stage review."""
    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", _names_parent)
    _write_translations(tmp_path, "3315737452", {"land_units_onscreen_name_dragon": "Dragon Guard", "unit_abilities_onscreen_name_roar": "Dragon Roar"})

    resp = client.post(f"{PREFIX}/name-suggestions", json={"mod_id": "3315737452", "categories": ["skill"]})
    assert resp.status_code == 200
    assert [s["english"] for s in resp.json()["suggestions"]] == ["Dragon Roar"]


def test_name_suggestions_include_names_translated_in_the_pack(client: TestClient, monkeypatch):
    """A name translated in the translation pack's `.loc.tsv` counts even when translations.json has no entry for it."""
    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", _names_parent)
    monkeypatch.setattr(
        routes_module,
        "_extract_translation_strings",
        lambda mod: {"units.loc.tsv": {"land_units_onscreen_name_dragon": LocRow("land_units_onscreen_name_dragon", "Dragon Guard", True)}},
    )

    sugg = client.post(f"{PREFIX}/name-suggestions", json={"mod_id": "3315737452"}).json()["suggestions"]
    assert [(s["english"], s["source"], s["category"]) for s in sugg] == [("Dragon Guard", "龙卫", "unit")]


def test_name_suggestions_skip_names_whose_source_is_in_the_glossary(client: TestClient, monkeypatch, tmp_path: Path):
    """A name whose source text already has a glossary term is not suggested again under a different English."""
    from backend.games.total_war_warhammer_3 import glossary_store

    glossary_store.add_term("3315737452", {"english": "Dragon Sentinel", "source": "龙卫", "category": "unit"})
    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", _names_parent)
    _write_translations(tmp_path, "3315737452", {"land_units_onscreen_name_dragon": "Dragon Guard"})

    assert client.post(f"{PREFIX}/name-suggestions", json={"mod_id": "3315737452"}).json()["suggestions"] == []


def test_name_suggestions_strip_game_markup(client: TestClient, monkeypatch, tmp_path: Path):
    """Colour and icon tags around a name are left out of the suggested term, and a name that is only tags is skipped."""
    tagged = "special_ability_phases_onscreen_name_smoke"
    icon_only = "special_ability_phases_onscreen_name_icon"
    parent = {
        "units.loc.tsv": {
            tagged: LocRow(tagged, "[[col:red]][[img:ui/smoke.png]][[/img]]浓烟[[/col]]", True),
            icon_only: LocRow(icon_only, "[[img:ui/icon.png]][[/img]]", True),
        }
    }
    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", lambda mod: parent)
    _write_translations(tmp_path, "3315737452", {tagged: "[[col:red]][[img:ui/smoke.png]][[/img]]Dense Smoke[[/col]]", icon_only: "[[img:ui/icon.png]][[/img]]"})

    sugg = client.post(f"{PREFIX}/name-suggestions", json={"mod_id": "3315737452"}).json()["suggestions"]
    assert [(s["english"], s["source"]) for s in sugg] == [("Dense Smoke", "浓烟")]


def test_mod_context_round_trips_include_translated_names(client: TestClient):
    """`include_translated_names` defaults to False and is saved through the mod-context PUT."""
    url = "/api/games/total_war_warhammer_3/translation/mods/3315737452/mod-context"
    assert client.get(url).json()["include_translated_names"] is False
    ctx = {"source_game": "", "character_name": "", "background": "", "include_translated_names": True}
    assert client.put(url, json=ctx).status_code == 200
    assert client.get(url).json()["include_translated_names"] is True


def _capture_batch_prompt(client: TestClient, monkeypatch) -> str:
    """Run one batch for `k2` with a fake provider and return the glossary prompt it was given.

    Args:
        client: The test client.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The glossary prompt passed to the provider.
    """
    seen: dict[str, str] = {}

    def fake_translate_batch(self, entries, source_lang, glossary_prompt, **kwargs):
        seen["prompt"] = glossary_prompt
        return ({"k2": "New"}, [])

    monkeypatch.setattr("backend.translator.claude_provider.ClaudeProvider.translate_batch", fake_translate_batch)
    parent = _names_parent(None)
    parent["units.loc.tsv"]["k2"] = LocRow("k2", "新", True)
    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", lambda mod: parent)
    resp = client.post(f"{PREFIX}/batch", json={"mod_id": "3315737452", "keys": ["k2"], "source_lang": "Chinese", "is_first_batch": True})
    assert resp.status_code == 200
    return seen["prompt"]


def _set_include_translated_names(client: TestClient, value: bool) -> None:
    """Save the mod's `include_translated_names` setting.

    Args:
        client: The test client.
        value: The setting to save.
    """
    ctx = {"source_game": "", "character_name": "", "background": "", "include_translated_names": value}
    client.put("/api/games/total_war_warhammer_3/translation/mods/3315737452/mod-context", json=ctx)


def test_batch_prompt_leaves_out_unaccepted_names_by_default(client: TestClient, monkeypatch, tmp_path: Path):
    """Translated names that are not in the glossary stay out of the prompt while the setting is off."""
    _write_translations(tmp_path, "3315737452", {"land_units_onscreen_name_dragon": "Dragon Guard"})
    assert "Dragon Guard" not in _capture_batch_prompt(client, monkeypatch)


def test_batch_prompt_includes_translated_names_when_enabled(client: TestClient, monkeypatch, tmp_path: Path):
    """With the setting on, translated names that are not in the glossary are sent as glossary terms."""
    _write_translations(tmp_path, "3315737452", {"land_units_onscreen_name_dragon": "Dragon Guard"})
    _set_include_translated_names(client, True)
    prompt = _capture_batch_prompt(client, monkeypatch)
    assert "Dragon Guard" in prompt
    assert "龙卫" in prompt


def test_batch_prompt_prefers_the_glossary_over_a_translated_name(client: TestClient, monkeypatch, tmp_path: Path):
    """When a translated name's source text already has a glossary term, only the glossary term is sent."""
    from backend.games.total_war_warhammer_3 import glossary_store

    glossary_store.add_term("3315737452", {"english": "Dragon Sentinel", "source": "龙卫", "category": "unit"})
    _write_translations(tmp_path, "3315737452", {"land_units_onscreen_name_dragon": "Dragon Guard"})
    _set_include_translated_names(client, True)
    prompt = _capture_batch_prompt(client, monkeypatch)
    assert "Dragon Sentinel" in prompt
    assert "Dragon Guard" not in prompt


def test_preview_prompt_includes_translated_names_when_enabled(client: TestClient, monkeypatch, tmp_path: Path):
    """The preview's system prompt matches what the batches send, so the cost estimate includes the extra names."""
    parent = _names_parent(None)
    parent["units.loc.tsv"]["k2"] = LocRow("k2", "新", True)
    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", lambda mod: parent)
    _write_translations(tmp_path, "3315737452", {"land_units_onscreen_name_dragon": "Dragon Guard"})
    _set_include_translated_names(client, True)
    body = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452"}).json()
    assert "Dragon Guard" in body["previews"]["Chinese"]["system_prompt"]


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# GET /translate/system-prompt


def test_system_prompt_is_built_from_the_wh3_context_rules_and_claude(client):
    """The Settings preview shows the prompt a WH3 run would send: WH3 context and format rules, built by Claude, with no Chrono Ark text."""
    res = client.get("/api/games/total_war_warhammer_3/translate/system-prompt", params={"source_lang": "Chinese"})
    assert res.status_code == 200
    body = res.json()
    assert body["provider"] == routes_module.ClaudeProvider().name
    assert body["source_lang"] == "Chinese"
    prompt = body["system_prompt"]
    assert "Total War: Warhammer III" in prompt
    assert "[Title] Name" in prompt
    assert "Chrono Ark" not in prompt


def test_system_prompt_asks_for_one_word_per_name_syllable(client):
    """The WH3 prompt tells Claude to write each syllable of a romanized name as its own word, and its title example follows that rule."""
    prompt = client.get("/api/games/total_war_warhammer_3/translate/system-prompt", params={"source_lang": "Chinese"}).json()["system_prompt"]
    assert "Miao Ying" in prompt
    assert "[Sentinel] Sui Tang" in prompt
    assert "Suitang" not in prompt


def test_batch_splits_joined_names_in_translations_and_suggested_terms(client: TestClient, monkeypatch, tmp_path: Path):
    """A joined pinyin name from Claude is saved and returned with spaced syllables, in the translation and in its suggested glossary term."""
    from backend.data import suggestion_manager
    from backend.games.storage_paths import game_storage_path

    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", lambda mod: {"units.loc.tsv": {"lord": LocRow("lord", "『狂飙烈风之主』妙影与烛龙", True)}})

    def fake_translate_batch(self, entries, source_lang, glossary_prompt, **kwargs):
        return (
            {"lord": "[Lord of Raging Gale] Miaoying and Zhulong"},
            [{"english": "Zhulong", "source": "烛龙", "source_lang": "Chinese", "category": "unit", "reason": "dragon"}],
        )

    monkeypatch.setattr("backend.translator.claude_provider.ClaudeProvider.translate_batch", fake_translate_batch)
    resp = client.post(f"{PREFIX}/batch", json={"mod_id": "3315737452", "keys": ["lord"], "source_lang": "Chinese", "is_first_batch": True})

    body = resp.json()
    assert body["translations"] == {"lord": "[Lord of Raging Gale] Miao Ying and Zhu Long"}
    assert [s["english"] for s in body["suggestions"]] == ["Zhu Long"]
    raw = json.loads((tmp_path / "games" / "total_war_warhammer_3" / "mods" / "3315737452" / "translations.json").read_text(encoding="utf-8"))
    assert raw["lord"]["text"] == "[Lord of Raging Gale] Miao Ying and Zhu Long"
    persisted = [s["english"] for s in suggestion_manager.load_suggestions("3315737452", storage_path=game_storage_path("total_war_warhammer_3"))]
    assert persisted == ["Zhu Long"]


def test_name_suggestions_split_joined_names(client: TestClient, monkeypatch, tmp_path: Path):
    """A name saved earlier with joined syllables is suggested with spaced syllables, and the saved row is left as it was."""
    key = "land_units_onscreen_name_zhulong"
    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", lambda mod: {"units.loc.tsv": {key: LocRow(key, "烛龙", True)}})
    _write_translations(tmp_path, "3315737452", {key: "Zhulong"})

    sugg = client.post(f"{PREFIX}/name-suggestions", json={"mod_id": "3315737452"}).json()["suggestions"]
    assert [s["english"] for s in sugg] == ["Zhu Long"]
    raw = json.loads((tmp_path / "games" / "total_war_warhammer_3" / "mods" / "3315737452" / "translations.json").read_text(encoding="utf-8"))
    assert raw[key]["text"] == "Zhulong"


def test_system_prompt_shows_the_unlocks_recruitment_example(client):
    """The WH3 prompt carries a worked example of a 获得...招募许可 line."""
    prompt = client.get("/api/games/total_war_warhammer_3/translate/system-prompt", params={"source_lang": "Chinese"}).json()["system_prompt"]
    assert "获得『卫土戍卒(斩马剑)』『卫土戍卒(重型连弩)』招募许可" in prompt
    assert "Unlocks recruitment for Frontier Guards (Zhan Ma Sword) and Frontier Guards (Heavy Repeater)." in prompt


from backend.translator.base import Revision  # noqa: F401,E402  (documents the prompt contract)


def _seed_changed(tmp_path: Path, old_text: str | None = "旧") -> None:
    mod_dir = tmp_path / "games" / "total_war_warhammer_3" / "mods" / "3315737452"
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "parent_snapshot.json").write_text(json.dumps({"units.loc.tsv": {"k1": {"hash": "0" * 64, "text": old_text}}}), encoding="utf-8")


def test_preview_scope_changed_lists_only_changed_rows_with_update_prompt(client: TestClient, tmp_path: Path):
    _seed_changed(tmp_path)
    body = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452", "scope": "changed"}).json()
    assert body["total_strings"] == 1
    assert body["batch_plan"][0]["keys"] == ["k1"]
    user = body["previews"]["Chinese"]["user_messages"][0]
    assert "Old original: 旧" in user
    assert "New original: 原" in user
    assert "Previous translation: Existing" in user
    assert "Update Mode" in body["previews"]["Chinese"]["system_prompt"]


def test_preview_scope_changed_zero_state(client: TestClient):
    body = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452", "scope": "changed"}).json()
    assert body["total_strings"] == 0
    assert "message" in body


def test_preview_scope_changed_falls_back_when_old_text_is_unknown(client: TestClient, tmp_path: Path):
    _seed_changed(tmp_path, old_text=None)
    user = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452", "scope": "changed"}).json()["previews"]["Chinese"]["user_messages"][0]
    assert "Old original" not in user
    assert "New original: 原" in user


def test_preview_scope_changed_skips_rows_with_blank_new_source(client: TestClient, tmp_path: Path, monkeypatch):
    _seed_changed(tmp_path)
    monkeypatch.setattr(routes_module, "_extract_all_parent_strings", lambda mod: {"units.loc.tsv": {"k1": LocRow("k1", "  ", True)}})
    body = client.post(f"{PREFIX}/preview", json={"mod_id": "3315737452", "scope": "changed"}).json()
    assert body["total_strings"] == 0


def test_update_batch_sends_revisions_saves_and_rebaselines(client: TestClient, monkeypatch, tmp_path: Path):
    _seed_changed(tmp_path)
    seen = {}

    def fake_translate_batch(self, entries, source_lang, glossary_prompt, **kwargs):
        seen["revisions"] = self.revisions
        seen["entries"] = entries
        return ({"k1": "Revised"}, [])

    monkeypatch.setattr("backend.translator.claude_provider.ClaudeProvider.translate_batch", fake_translate_batch)
    resp = client.post(f"{PREFIX}/batch", json={"mod_id": "3315737452", "keys": ["k1"], "source_lang": "Chinese", "mode": "update"})
    assert resp.status_code == 200
    assert seen["entries"] == [("k1", "原")]
    assert seen["revisions"]["k1"].old_source == "旧"
    assert seen["revisions"]["k1"].previous_translation == "Existing"

    mod_dir = tmp_path / "games" / "total_war_warhammer_3" / "mods" / "3315737452"
    saved = json.loads((mod_dir / "translations.json").read_text(encoding="utf-8"))
    assert saved["k1"]["text"] == "Revised"
    snap = json.loads((mod_dir / "parent_snapshot.json").read_text(encoding="utf-8"))
    assert snap["units.loc.tsv"]["k1"]["text"] == "原"

    rows = client.get("/api/games/total_war_warhammer_3/translation/mods/3315737452/strings").json()
    assert next(r for r in rows if r["key"] == "k1")["canonical_status"] == "pending"


def test_update_batch_uses_the_recorded_baseline_after_two_source_changes(client: TestClient, monkeypatch, tmp_path: Path):
    # The snapshot keeps the text from the last accepted baseline, so a second source edit does not replace it.
    _seed_changed(tmp_path, old_text="BASELINE")
    seen = {}

    def fake_translate_batch(self, entries, source_lang, glossary_prompt, **kwargs):
        seen["old"] = self.revisions["k1"].old_source
        return ({"k1": "Revised"}, [])

    monkeypatch.setattr("backend.translator.claude_provider.ClaudeProvider.translate_batch", fake_translate_batch)
    client.post(f"{PREFIX}/batch", json={"mod_id": "3315737452", "keys": ["k1"], "source_lang": "Chinese", "mode": "update"})
    assert seen["old"] == "BASELINE"


def test_update_batch_ignores_unchanged_keys_and_400s_when_none_left(client: TestClient):
    resp = client.post(f"{PREFIX}/batch", json={"mod_id": "3315737452", "keys": ["k2"], "source_lang": "Chinese", "mode": "update"})
    assert resp.status_code == 400
