"""
Abstract base class for translation providers.

Defines the interface that all translation backends (Claude, OpenAI,
DeepL, Manual) must implement. Includes the shared system prompt template
and prompt-building logic used by all LLM-based providers.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Event
from typing import Generator


_SYSTEM_PROMPT_TEMPLATE = """You are a professional game translator specializing in translating {source_lang} text into {target_lang} for the game {game_context}.

## Translation Rules

{format_rules_section}

{style_examples_section}

{glossary_section}

{character_context_section}

{update_section}

## Output Format

{output_format_section}

For suggested_terms: identify any recurring proper nouns, character names, skill names, status effects, or game-specific terms that should be added to the glossary for consistency. Only suggest terms that:
- Appear in multiple strings or are clearly important named entities
- Are NOT already in the glossary above
- Are proper nouns, skill/buff/item names, or game mechanics

If no terms to suggest, return an empty array."""

# Output format section with translations as a key -> text object. Formatted with `source_lang`.
DICT_OUTPUT_FORMAT = """Return a valid JSON object with this structure:
```json
{{
  "translations": {{
    "Buff/B_Example_Name": "Example Buff",
    "Buff/B_Example_Description": "Deals &a damage to all enemies."
  }},
  "suggested_terms": [
    {{
      "english": "Term Name",
      "source": "원본 텍스트",
      "source_lang": "{source_lang}",
      "category": "characters|skills|buffs|items|mechanics",
      "reason": "Brief reason why this should be a glossary term"
    }}
  ]
}}
```

Translate ONLY the values. Keys must remain unchanged."""

# Output format section with translations as a list of key/text items, for providers whose JSON schema cannot hold arbitrary keys.
ITEMS_OUTPUT_FORMAT = """Return a valid JSON object with this structure:
```json
{{
  "translations": [
    {{"key": "Buff/B_Example_Name", "text": "Example Buff"}},
    {{"key": "Buff/B_Example_Description", "text": "Deals &a damage to all enemies."}}
  ],
  "suggested_terms": [
    {{
      "english": "Term Name",
      "source": "원본 텍스트",
      "source_lang": "{source_lang}",
      "category": "characters|skills|buffs|items|mechanics",
      "reason": "Brief reason why this should be a glossary term"
    }}
  ]
}}
```

Give one translations item per string. Copy each key exactly into `key` and put the translation in `text`."""


def build_style_examples_section(examples: dict[str, list[tuple[str, str]]]) -> str:
    """Format style examples as a prompt section for the LLM.

    Builds a Markdown section showing source/English pairs grouped by category
    (`"skills"`, `"buffs/debuffs"`, `"items"`, `"dialogue"`) so the LLM can match the game's existing
    translation style.

    Args:
        examples: Dict mapping category name (e.g., `"skills"`, `"buffs/debuffs"`) to a
            list of (source_text, english_text) example pairs.

    Returns:
        Formatted Markdown section string, or an empty string if no
        examples are provided.
    """
    if not examples:
        return ""

    lines = ["## Style Reference", "", "Match the tone and sentence structure of the base game's English translations:", ""]

    category_titles = {
        "skills": "Skill Descriptions",
        "buffs/debuffs": "Buff/Debuff Descriptions",
        "items": "Item Descriptions",
        "dialogue": "Character Dialogue",
    }

    for category, pairs in examples.items():
        title = category_titles.get(category, category.title())
        lines.append(f"### {title}")
        for source, english in pairs:
            lines.append(f'- Source: "{source}"')
            lines.append(f'  English: "{english}"')
        lines.append("")

    lines.append("Use this style: imperative for skills, declarative for buffs/items, concise throughout.")
    return "\n".join(lines)


def build_character_context_section(ctx: dict | None) -> str:
    """Format character context as a prompt section for the LLM.

    Builds a Markdown section describing the mod character's background so
    the LLM can use appropriate tone and terminology from the source game.

    Args:
        ctx: Character context dictionary with optional keys
            `"character_name"`, `"source_game"`, and `"background"`. May be None.

    Returns:
        Formatted Markdown section string, or an empty string if no
        meaningful context is provided.
    """
    if not ctx:
        return ""
    name = ctx.get("character_name", "").strip()
    game = ctx.get("source_game", "").strip()
    background = ctx.get("background", "").strip()
    if not name and not game and not background:
        return ""

    lines = ["## Character Background", ""]
    if name and game:
        lines.append(f"This mod implements **{name}** from **{game}**.")
    elif name:
        lines.append(f"This mod implements **{name}**.")
    elif game:
        lines.append(f"This mod implements a character from **{game}**.")
    if background:
        lines.append("")
        lines.append(background)
    lines.append("")
    lines.append("Use this context to inform your translation choices — match the character's established tone, personality, and terminology from their source game.")
    return "\n".join(lines)


@dataclass(frozen=True)
class Revision:
    """Baseline for revising one changed row.

    Attributes:
        old_source: The original text the previous translation was made from, or `None` when it was not recorded.
        previous_translation: The translation to revise.
    """

    old_source: str | None
    previous_translation: str


# System prompt section added when `TranslationProvider.revisions` is set. Formatted with `source_lang` and `target_lang`.
UPDATE_RULES_SECTION = """## Update Mode

The strings below were already translated, but the original {source_lang} text has since changed.
For each key you are given the old original text (when known), the new original text, and the previous {target_lang} translation.

1. Use the previous translation as the base. Change only what the difference between the old and new original requires.
2. Keep the wording, tone, glossary terms, and formatting tags of the previous translation wherever they still fit the new original.
3. If the new original is unrelated to the old one, translate it fresh.
4. Return the full revised translation for every key, not just the changed part."""


class TranslationProvider(ABC):
    """
    Abstract translation provider interface.

    All providers translate batches of (key, source_text) tuples into
    English and return {key: english_translation} mappings.
    """

    # Output format section shown in the system prompt. Providers that request a JSON schema override it to match.
    output_format_template: str = DICT_OUTPUT_FORMAT

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this translation provider.

        Returns:
            Display name including any model information.
        """
        ...

    @abstractmethod
    def translate_batch(
        self,
        entries: list[tuple[str, str]],
        source_lang: str,
        glossary_prompt: str,
        game_context: str = "",
        format_rules: list[str] | None = None,
        style_examples: dict[str, list[tuple[str, str]]] | None = None,
        character_context: dict | None = None,
        target_lang: str = "English",
    ) -> tuple[dict[str, str], list[dict]]:
        """
        Translate a batch of strings.

        Args:
            entries: List of (key, source_text) tuples to translate.
            source_lang: Name of the source language (e.g., `"Chinese"`).
            glossary_prompt: Formatted glossary context for the LLM.
            game_context: Game description for the system prompt.
            format_rules: Game-specific formatting preservation rules.
            style_examples: Dict of category -> [(source, english)] pairs for few-shot.
            character_context: Character background info dict with keys like
                `"character_name"`, `"source_game"`, and `"background"`.
            target_lang: Name of the target language (e.g., `"English"`, `"Chinese"`).

        Returns:
            A tuple of (translations dict mapping key to target language text,
            suggested_terms list of dicts).
        """
        ...

    # Baselines for update mode, keyed by localization key. When set, `build_prompt` revises these translations instead of translating from scratch.
    revisions: dict[str, Revision] | None = None

    def build_prompt(
        self,
        entries: list[tuple[str, str]],
        source_lang: str,
        glossary_prompt: str,
        game_context: str = "",
        format_rules: list[str] | None = None,
        style_examples: dict[str, list[tuple[str, str]]] | None = None,
        character_context: dict | None = None,
        target_lang: str = "English",
    ) -> tuple[str, str]:
        """Build the system and user prompts without sending to the API.

        Args:
            entries: List of (key, source_text) tuples to translate.
            source_lang: Name of the source language (e.g., `"Chinese"`).
            glossary_prompt: Formatted glossary context for the LLM.
            game_context: Game description for the system prompt.
            format_rules: Game-specific formatting preservation rules.
            style_examples: Dict of category -> [(source, english)] pairs
                for few-shot style guidance.
            character_context: Character background info dict with keys like
                `"character_name"`, `"source_game"`, and `"background"`.
            target_lang: Name of the target language (e.g., `"English"`, `"Chinese"`).

        When `self.revisions` is set the prompts switch to update mode.

        Returns:
            A tuple of (system_prompt, user_message).
        """
        glossary_section = glossary_prompt if glossary_prompt else "No glossary available."
        revisions = self.revisions
        update_section = UPDATE_RULES_SECTION.format(source_lang=source_lang, target_lang=target_lang) if revisions else ""
        rules = format_rules or []
        format_rules_section = "\n".join(f"{i+1}. **{rule}**" for i, rule in enumerate(rules)) if rules else ""
        style_examples_section = build_style_examples_section(style_examples or {})
        character_context_section = build_character_context_section(character_context)

        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            source_lang=source_lang,
            target_lang=target_lang,
            game_context=game_context or "a video game",
            format_rules_section=format_rules_section,
            style_examples_section=style_examples_section,
            glossary_section=glossary_section,
            character_context_section=character_context_section,
            update_section=update_section,
            output_format_section=self.output_format_template.format(source_lang=source_lang),
        )
        # Collapse runs of blank lines left by empty template sections.
        while "\n\n\n" in system_prompt:
            system_prompt = system_prompt.replace("\n\n\n", "\n\n")

        def esc(text: str) -> str:
            return text.replace("\n", "\\n")

        if revisions:
            user_lines = [f"Revise the following {target_lang} translations to match the updated {source_lang} text:\n"]
            for key, source_text in entries:
                user_lines.append(f"**{key}**")
                rev = revisions.get(key)
                if rev is not None and rev.old_source is not None:
                    user_lines.append(f"Old original: {esc(rev.old_source)}")
                user_lines.append(f"New original: {esc(source_text)}")
                if rev is not None:
                    user_lines.append(f"Previous translation: {esc(rev.previous_translation)}")
                user_lines.append("")
        else:
            user_lines = [f"Translate the following {source_lang} strings to {target_lang}:\n"]
            for key, source_text in entries:
                user_lines.append(f"**{key}**: {esc(source_text)}")
        user_lines.append('\nReturn a JSON object with "translations" and "suggested_terms".')
        user_message = "\n".join(user_lines)

        return system_prompt, user_message

    @property
    def supports_streaming(self) -> bool:
        """Whether this provider supports streaming token-by-token progress."""
        return False

    def translate_batch_stream(
        self,
        entries: list[tuple[str, str]],
        source_lang: str,
        glossary_prompt: str,
        game_context: str = "",
        format_rules: list[str] | None = None,
        style_examples: dict[str, list[tuple[str, str]]] | None = None,
        character_context: dict | None = None,
        cancel_event: Event | None = None,
        target_lang: str = "English",
    ) -> Generator[dict, None, None]:
        """Stream translation progress as a series of event dicts.

        Default implementation calls `translate_batch` and yields a single
        `complete` event. Override in providers that support token streaming.

        Args:
            entries: List of (key, source_text) tuples to translate.
            source_lang: Name of the source language (e.g., `"Chinese"`).
            glossary_prompt: Formatted glossary context for the LLM.
            game_context: Game description for the system prompt.
            format_rules: Game-specific formatting preservation rules.
            style_examples: Dict of category -> [(source, english)] pairs
                for few-shot style guidance.
            character_context: Character background info dict with keys like
                `"character_name"`, `"source_game"`, and `"background"`.
            cancel_event: When set, the provider should abort the in-progress
                request and close any open HTTP connections (e.g. to Ollama).
            target_lang: Name of the target language (e.g., `"English"`, `"Chinese"`).

        Yields:
            Dicts with a `"type"` key. Common types: `"started"`,
            `"progress"`, `"complete"`, `"cancelled"`, `"error"`.
            See `OllamaProvider` for the full event schema.
        """
        translations, suggestions = self.translate_batch(
            entries,
            source_lang,
            glossary_prompt,
            game_context=game_context,
            format_rules=format_rules,
            style_examples=style_examples,
            character_context=character_context,
            target_lang=target_lang,
        )
        yield {
            "type": "complete",
            "translations": translations,
            "suggestions": suggestions,
        }

    def _parse_response(
        self,
        response_text: str,
        entries: list[tuple[str, str]],
    ) -> tuple[dict[str, str], list[dict]]:
        """Parse a JSON response from the LLM into translations and suggestions.

        Strips markdown code fences, then extracts the translations dict and
        suggested_terms list. Supports the structured format
        (`{"translations": {...}, "suggested_terms": [...]}`), its item-list
        form (`"translations": [{"key": ..., "text": ...}]`), and the legacy
        flat format (`{"key": "translation", ...}`).

        Args:
            response_text: Raw text response from the LLM.
            entries: Original (key, source_text) tuples, used to validate
                returned keys.

        Returns:
            A tuple of (translations dict, suggestions list). Returns
            empty collections if parsing fails.
        """
        text = response_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            result = json.loads(text)
            if isinstance(result, dict):
                expected_keys = {k for k, _ in entries}

                items = result.get("translations")
                if isinstance(items, list):
                    # Structured-output form: a list of {"key", "text"} items.
                    items = {i.get("key"): i.get("text") for i in items if isinstance(i, dict)}
                if isinstance(items, dict):
                    translations = {k: v.replace("\\n", "\n") for k, v in items.items() if k in expected_keys and isinstance(v, str)}
                    suggestions = result.get("suggested_terms", [])
                    if not isinstance(suggestions, list):
                        suggestions = []
                    return translations, suggestions

                translations = {k: v.replace("\\n", "\n") for k, v in result.items() if k in expected_keys and isinstance(v, str)}
                return translations, []

        except json.JSONDecodeError:
            print(f"  Warning: Failed to parse {self.name} response as JSON")
            print(f"  Response: {ascii(text[:200])}")

        return {}, []

    @abstractmethod
    def estimate_cost(self, entries: list[tuple[str, str]], **kwargs) -> dict:
        """
        Estimate the cost of translating the given entries.

        Args:
            entries: List of (key, source_text) tuples.
            **kwargs: Provider-specific context (`source_lang`, `glossary_prompt`, etc.)

        Returns:
            Dictionary with cost estimation details:
            `{"estimated_tokens": int, "estimated_cost_usd": float, "note": str}`
        """
        ...
