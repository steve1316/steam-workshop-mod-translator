"""Tests for the update-mode (CHANGED) prompt built by `TranslationProvider.build_prompt`."""

from backend.translator.base import Revision
from backend.translator.claude_provider import ClaudeProvider


def _build(provider: ClaudeProvider, entries):
    return provider.build_prompt(entries, "Chinese", "No glossary available.", game_context="a game", format_rules=["Keep tags."], style_examples={}, target_lang="English")


def test_default_prompt_has_no_update_mode():
    system, user = _build(ClaudeProvider(), [("k1", "原文")])
    assert "Update Mode" not in system
    assert user.startswith("Translate the following Chinese strings to English:")


def test_update_mode_adds_rules_and_lists_old_new_and_previous():
    provider = ClaudeProvider()
    provider.revisions = {"k1": Revision(old_source="旧文", previous_translation="Old text")}
    system, user = _build(provider, [("k1", "新文")])
    assert "## Update Mode" in system
    assert "previous translation as the base" in system
    assert user.startswith("Revise the following English translations to match the updated Chinese text:")
    assert user.index("Old original: 旧文") < user.index("New original: 新文") < user.index("Previous translation: Old text")
    assert "**k1**" in user


def test_update_mode_omits_old_original_when_unknown():
    provider = ClaudeProvider()
    provider.revisions = {"k1": Revision(old_source=None, previous_translation="Old text")}
    _, user = _build(provider, [("k1", "新文")])
    assert "Old original" not in user
    assert "New original: 新文" in user


def test_update_mode_escapes_newlines():
    provider = ClaudeProvider()
    provider.revisions = {"k1": Revision(old_source="a\nb", previous_translation="x\ny")}
    _, user = _build(provider, [("k1", "c\nd")])
    assert "Old original: a\\nb" in user
    assert "New original: c\\nd" in user
    assert "Previous translation: x\\ny" in user
