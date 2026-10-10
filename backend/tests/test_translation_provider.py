import json
import pytest
from backend.translator.claude_provider import ClaudeProvider
from backend.translator.base import build_style_examples_section, build_character_context_section


def test_parse_response_new_format():
    provider = ClaudeProvider.__new__(ClaudeProvider)
    entries = [("Buff/B_Test_Name", "테스트")]
    response = json.dumps(
        {
            "translations": {"Buff/B_Test_Name": "Test"},
            "suggested_terms": [{"english": "Dark Mage", "source": "흑마법사", "source_lang": "Korean", "category": "characters", "reason": "Recurring name"}],
        }
    )
    translations, suggestions = provider._parse_response(response, entries)
    assert translations == {"Buff/B_Test_Name": "Test"}
    assert len(suggestions) == 1
    assert suggestions[0]["english"] == "Dark Mage"


def test_parse_response_old_flat_format_fallback():
    """If the AI returns the old flat format, still parse translations."""
    provider = ClaudeProvider.__new__(ClaudeProvider)
    entries = [("Buff/B_Test_Name", "테스트")]
    response = json.dumps({"Buff/B_Test_Name": "Test"})
    translations, suggestions = provider._parse_response(response, entries)
    assert translations == {"Buff/B_Test_Name": "Test"}
    assert suggestions == []


def test_parse_response_with_markdown_code_block():
    provider = ClaudeProvider.__new__(ClaudeProvider)
    entries = [("Skill/S_1_Name", "스킬")]
    response = '```json\n{"translations": {"Skill/S_1_Name": "Skill"}, "suggested_terms": []}\n```'
    translations, suggestions = provider._parse_response(response, entries)
    assert translations == {"Skill/S_1_Name": "Skill"}


def test_build_style_examples_section():
    examples = {
        "skills": [("적에게 피해를 줍니다.", "Deal damage to an enemy.")],
        "buffs/debuffs": [("공격력 증가", "Attack is increased.")],
    }
    section = build_style_examples_section(examples)
    assert "Deal damage to an enemy." in section
    assert "Attack is increased." in section
    assert "## Style Reference" in section


def test_build_character_context_section_full():
    ctx = {"source_game": "Library of Ruina", "character_name": "Roland", "background": "A cynical fixer."}
    section = build_character_context_section(ctx)
    assert "## Character Background" in section
    assert "**Roland**" in section
    assert "**Library of Ruina**" in section
    assert "A cynical fixer." in section


def test_build_character_context_section_partial_no_game():
    ctx = {"source_game": "", "character_name": "Roland", "background": "A cynical fixer."}
    section = build_character_context_section(ctx)
    assert "## Character Background" in section
    assert "**Roland**" in section
    assert "Library of Ruina" not in section
    assert "A cynical fixer." in section


def test_build_character_context_section_only_background():
    ctx = {"source_game": "", "character_name": "", "background": "A mysterious warrior."}
    section = build_character_context_section(ctx)
    assert "## Character Background" in section
    assert "A mysterious warrior." in section


def test_build_character_context_section_empty():
    ctx = {"source_game": "", "character_name": "", "background": ""}
    section = build_character_context_section(ctx)
    assert section == ""


def test_build_character_context_section_none():
    section = build_character_context_section(None)
    assert section == ""


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# Claude model table and request shape


class _Block:
    """Minimal stand-in for an Anthropic content block."""

    def __init__(self, type: str, **fields):
        self.type = type
        for k, v in fields.items():
            setattr(self, k, v)


class _FakeAnthropic:
    """Captures `messages.create` kwargs and returns a canned response."""

    last_kwargs: dict = {}
    content: list = []

    def __init__(self, api_key: str):
        self.messages = self

    def create(self, **kwargs):
        _FakeAnthropic.last_kwargs = kwargs

        class _Usage:
            input_tokens = 100
            output_tokens = 50

        class _Response:
            content = _FakeAnthropic.content
            usage = _Usage()

        return _Response()


def _run_batch(monkeypatch, model: str, content: list) -> tuple[dict, list]:
    """Run `translate_batch` for one entry against the fake client.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        model: Claude model id to construct the provider with.
        content: Content blocks the fake response returns.

    Returns:
        The `(translations, suggestions)` tuple from `translate_batch`.
    """
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)
    _FakeAnthropic.content = content
    provider = ClaudeProvider(api_key="test-key", model=model)
    return provider.translate_batch([("k1", "테스트")], "Korean", "")


def test_claude_models_lists_current_models_with_pricing():
    from backend.translator.claude_provider import CLAUDE_MODELS

    assert {k: (v["input_per_mtok"], v["output_per_mtok"]) for k, v in CLAUDE_MODELS.items()} == {
        "claude-opus-5-5": (4.0, 20.0),
        "claude-opus-5": (5.0, 25.0),
        "claude-sonnet-5": (2.0, 10.0),
        "claude-sonnet-4-6": (3.0, 15.0),
        "claude-haiku-5-5": (0.1, 0.5),
    }


def test_default_pricing_is_sonnet_5():
    from backend.translator.claude_provider import _DEFAULT_PRICING, CLAUDE_MODELS

    assert _DEFAULT_PRICING is CLAUDE_MODELS["claude-sonnet-5"]


def test_translate_batch_reads_the_text_block_after_a_thinking_block(monkeypatch):
    text = json.dumps({"translations": {"k1": "Test"}, "suggested_terms": []})
    translations, _ = _run_batch(monkeypatch, "claude-opus-5-5", [_Block("thinking", thinking=""), _Block("text", text=text)])
    assert translations == {"k1": "Test"}


def test_translate_batch_disables_thinking_for_sonnet_5(monkeypatch):
    text = json.dumps({"translations": {"k1": "Test"}, "suggested_terms": []})
    _run_batch(monkeypatch, "claude-sonnet-5", [_Block("text", text=text)])
    assert _FakeAnthropic.last_kwargs["extra_body"]["thinking"] == {"type": "disabled"}


def test_translate_batch_uses_low_effort_for_opus_5_models(monkeypatch):
    text = json.dumps({"translations": {"k1": "Test"}, "suggested_terms": []})
    for model in ("claude-opus-5", "claude-opus-5-5"):
        _run_batch(monkeypatch, model, [_Block("text", text=text)])
        assert _FakeAnthropic.last_kwargs["extra_body"]["output_config"]["effort"] == "low"
        assert _FakeAnthropic.last_kwargs["extra_body"]["output_config"]["format"]["type"] == "json_schema"


def test_translate_batch_sends_only_the_output_format_for_older_models(monkeypatch):
    text = json.dumps({"translations": {"k1": "Test"}, "suggested_terms": []})
    for model in ("claude-sonnet-4-6", "claude-haiku-5-5"):
        _run_batch(monkeypatch, model, [_Block("text", text=text)])
        assert list(_FakeAnthropic.last_kwargs["extra_body"]) == ["output_config"]
        assert list(_FakeAnthropic.last_kwargs["extra_body"]["output_config"]) == ["format"]


def test_translate_batch_requests_a_json_schema_with_translations_as_key_text_items(monkeypatch):
    text = json.dumps({"translations": [{"key": "k1", "text": "Test"}], "suggested_terms": []})
    translations, _ = _run_batch(monkeypatch, "claude-sonnet-5", [_Block("text", text=text)])
    schema = _FakeAnthropic.last_kwargs["extra_body"]["output_config"]["format"]["schema"]
    item = schema["properties"]["translations"]["items"]
    assert item["required"] == ["key", "text"] and item["additionalProperties"] is False
    assert translations == {"k1": "Test"}


def test_claude_system_prompt_shows_translations_as_key_text_items():
    system_prompt, _ = ClaudeProvider(api_key="x").build_prompt([("k1", "a")], "Chinese", "")
    assert '"key":' in system_prompt and '"text":' in system_prompt


def test_parse_response_accepts_translations_as_key_text_items():
    provider = ClaudeProvider.__new__(ClaudeProvider)
    response = json.dumps({"translations": [{"key": "k1", "text": "Line one\\nLine two"}, {"key": "stray", "text": "x"}], "suggested_terms": [{"english": "Cathay"}]})
    translations, suggestions = provider._parse_response(response, [("k1", "a")])
    assert translations == {"k1": "Line one\nLine two"}
    assert suggestions == [{"english": "Cathay"}]


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# Truncated and unparseable responses


class _ScriptedAnthropic:
    """Fake client that truncates any request carrying more than `max_keys` entries and translates the rest."""

    max_keys = 1
    calls: list[list[str]] = []

    def __init__(self, api_key: str):
        self.messages = self

    def create(self, **kwargs):
        user_message = kwargs["messages"][0]["content"]
        keys = [k for k in ("k1", "k2", "k3", "k4") if f"**{k}**" in user_message]
        _ScriptedAnthropic.calls.append(keys)
        truncated = len(keys) > _ScriptedAnthropic.max_keys
        text = '{"translations": {"k1": "Cut off' if truncated else json.dumps({"translations": {k: k.upper() for k in keys}, "suggested_terms": []})

        class _Usage:
            input_tokens = 10
            output_tokens = 5

        class _Response:
            content = [_Block("text", text=text)]
            usage = _Usage()
            stop_reason = "max_tokens" if truncated else "end_turn"

        return _Response()


def _scripted_provider(monkeypatch, max_keys: int) -> ClaudeProvider:
    """Build a provider wired to `_ScriptedAnthropic`.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        max_keys: Largest batch the fake client answers without truncating.

    Returns:
        A `ClaudeProvider` using the scripted client.
    """
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _ScriptedAnthropic)
    _ScriptedAnthropic.max_keys = max_keys
    _ScriptedAnthropic.calls = []
    return ClaudeProvider(api_key="test-key", model="claude-sonnet-5")


def test_translate_batch_splits_a_truncated_batch_and_merges_the_halves(monkeypatch):
    provider = _scripted_provider(monkeypatch, max_keys=1)
    entries = [("k1", "a"), ("k2", "b"), ("k3", "c"), ("k4", "d")]
    translations, _ = provider.translate_batch(entries, "Chinese", "")
    assert translations == {"k1": "K1", "k2": "K2", "k3": "K3", "k4": "K4"}
    assert _ScriptedAnthropic.calls[0] == ["k1", "k2", "k3", "k4"]


def test_translate_batch_records_the_truncated_raw_text(monkeypatch):
    provider = _scripted_provider(monkeypatch, max_keys=1)
    provider.translate_batch([("k1", "a"), ("k2", "b")], "Chinese", "")
    assert provider.last_raw_responses[0]["stop_reason"] == "max_tokens"
    assert provider.last_raw_responses[0]["raw_text"].startswith('{"translations": {"k1": "Cut off')


def test_translate_batch_raises_when_a_single_entry_is_truncated(monkeypatch):
    provider = _scripted_provider(monkeypatch, max_keys=0)
    with pytest.raises(RuntimeError, match="max_tokens"):
        provider.translate_batch([("k1", "a")], "Chinese", "")


def test_translate_batch_raises_on_an_unparseable_response(monkeypatch):
    with pytest.raises(ValueError, match="parse"):
        _run_batch(monkeypatch, "claude-sonnet-5", [_Block("text", text="Sorry, I cannot help with that.")])
