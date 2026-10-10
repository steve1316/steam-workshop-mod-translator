"""
Anthropic Claude translation provider.

Translates source language text to English using the Claude API
with glossary enforcement, style examples, and term suggestion.
"""

import time
from typing import Optional
from backend import config
from backend.translation.orchestrator import chunk_entries
from backend.translator.base import ITEMS_OUTPUT_FORMAT, TranslationProvider


CLAUDE_MODELS: dict[str, dict] = {
    "claude-opus-5-5": {"label": "Claude Opus 5.5", "input_per_mtok": 4.0, "output_per_mtok": 20.0},
    "claude-opus-5": {"label": "Claude Opus 5", "input_per_mtok": 5.0, "output_per_mtok": 25.0},
    "claude-sonnet-5": {"label": "Claude Sonnet 5", "input_per_mtok": 2.0, "output_per_mtok": 10.0},
    "claude-sonnet-4-6": {"label": "Claude Sonnet 4.6", "input_per_mtok": 3.0, "output_per_mtok": 15.0},
    "claude-haiku-5-5": {"label": "Claude Haiku 5.5", "input_per_mtok": 0.1, "output_per_mtok": 0.5},
}

_DEFAULT_PRICING = CLAUDE_MODELS["claude-sonnet-5"]

# Output token cap per request. A batch whose reply hits it is split in half and retried.
MAX_OUTPUT_TOKENS = 16384


def _object_schema(properties: dict) -> dict:
    """Build a closed JSON-schema object whose properties are all required.

    Args:
        properties: Property name -> schema.

    Returns:
        The object schema.
    """
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


_STRING = {"type": "string"}

# Structured-output schema for translation replies. The API guarantees the reply parses and matches it, so a stray quote in a translation cannot break
# the JSON. Objects must be closed, so translations are key/text items instead of a key -> text object.
TRANSLATION_SCHEMA = _object_schema(
    {
        "translations": {"type": "array", "items": _object_schema({"key": _STRING, "text": _STRING})},
        "suggested_terms": {
            "type": "array",
            "items": _object_schema({"english": _STRING, "source": _STRING, "source_lang": _STRING, "category": _STRING, "reason": _STRING}),
        },
    }
)

# Extra request fields per model, sent via `extra_body` so they work on SDK versions that predate them. Translation does not need
# thinking and thinking tokens bill as output. Sonnet 5 can turn it off. The Opus 5 models think by default (Opus 5.5 cannot disable it),
# so they run at low effort instead. Older models do not think unless asked, so they need nothing.
_MODEL_REQUEST_OPTIONS: dict[str, dict] = {
    "claude-sonnet-5": {"thinking": {"type": "disabled"}},
    "claude-opus-5": {"output_config": {"effort": "low"}},
    "claude-opus-5-5": {"output_config": {"effort": "low"}},
}


def _request_extra_body(model: str) -> dict:
    """Build the `extra_body` fields for a translation request: the model's options plus the structured-output format.

    Args:
        model: Claude model id.

    Returns:
        Dict to send as `extra_body`.
    """
    options = _MODEL_REQUEST_OPTIONS.get(model, {})
    output_config = {**options.get("output_config", {}), "format": {"type": "json_schema", "schema": TRANSLATION_SCHEMA}}
    return {**options, "output_config": output_config}


def _response_text(response) -> str:
    """Return the text of the first text block in a Messages API response.

    Models that think return thinking blocks before the answer, so `content[0]` is not always the text.

    Args:
        response: The Messages API response object.

    Raises:
        ValueError: If the response contains no text block.

    Returns:
        The text of the first text block.
    """
    for block in response.content:
        if block.type == "text":
            return block.text
    raise ValueError("Claude response contained no text block")


class ClaudeProvider(TranslationProvider):
    """Translation provider using Anthropic's Claude API.

    Uses Claude to translate game mod text from a source language to English,
    with support for glossary enforcement, style examples, character context,
    and automatic glossary term suggestions.

    Attributes:
        _api_key: Anthropic API key for authentication.
        _model: Claude model identifier to use for requests.
    """

    output_format_template = ITEMS_OUTPUT_FORMAT

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """Initialize the Claude translation provider.

        Args:
            api_key: Anthropic API key. Falls back to the value from
                `config.ANTHROPIC_API_KEY` when not provided.
            model: Claude model identifier. Falls back to
                `config.CLAUDE_MODEL` when not provided.
        """
        self._api_key = api_key or config.ANTHROPIC_API_KEY
        self._model = model or config.CLAUDE_MODEL

    @property
    def name(self) -> str:
        return f"Claude ({self._model})"

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
        """Translate a batch of strings using the Claude API.

        Sends the entries to Claude with the assembled prompt and parses the
        JSON response. Retries automatically on rate-limit and transient API
        errors (up to 3 attempts with exponential backoff). A reply cut off at
        `MAX_OUTPUT_TOKENS` is retried as two half-size batches.

        Args:
            entries: List of (key, source_text) tuples to translate.
            source_lang: Name of the source language (e.g., `"Korean"`).
            glossary_prompt: Pre-formatted glossary section for the prompt.
            game_context: Game title or description for the system prompt.
            format_rules: Formatting preservation rules to include.
            style_examples: Dict of category -> [(source, english)] pairs
                for few-shot style guidance.
            character_context: Character background info dict with keys like
                `"character_name"`, `"source_game"`, and `"background"`.

        Returns:
            tuple[dict[str, str], list[dict]]: A tuple of (translations dict
                mapping key to English text, suggested_terms list of dicts).

        Raises:
            ValueError: If no Anthropic API key is configured, or the response has no parseable translations.
            RuntimeError: If all retry attempts are exhausted, or a single string alone hits `MAX_OUTPUT_TOKENS`.
        """
        import anthropic

        if not self._api_key:
            raise ValueError("Anthropic API key not set. Set SWMT_ANTHROPIC_API_KEY env var.")

        client = anthropic.Anthropic(api_key=self._api_key)

        system_prompt, user_message = self.build_prompt(
            entries,
            source_lang,
            glossary_prompt,
            game_context=game_context,
            format_rules=format_rules,
            style_examples=style_examples,
            character_context=character_context,
            target_lang=target_lang,
        )

        request = {
            "model": self._model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }
        request["extra_body"] = _request_extra_body(self._model)
        response = self._create_with_retries(client, request)
        raw_text = _response_text(response)
        stop_reason = getattr(response, "stop_reason", None)

        # Store raw response for inspection
        self.last_raw_responses = getattr(self, "last_raw_responses", [])
        in_tok = getattr(response.usage, "input_tokens", None)
        out_tok = getattr(response.usage, "output_tokens", None)
        cost_usd = None
        if in_tok is not None and out_tok is not None:
            pricing = CLAUDE_MODELS.get(self._model, _DEFAULT_PRICING)
            cost_usd = in_tok / 1_000_000 * pricing["input_per_mtok"] + out_tok / 1_000_000 * pricing["output_per_mtok"]
        self.last_raw_responses.append(
            {
                "batch_index": len(self.last_raw_responses),
                "model": self._model,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cost_usd": cost_usd,
                "stop_reason": stop_reason,
                "raw_text": raw_text,
            }
        )

        # A reply cut off at the output cap is unparseable JSON. Retry the batch as two halves until each fits.
        if stop_reason == "max_tokens":
            if len(entries) == 1:
                raise RuntimeError(f"Claude hit max_tokens ({MAX_OUTPUT_TOKENS}) translating a single string ({entries[0][0]})")
            kwargs = {
                "game_context": game_context,
                "format_rules": format_rules,
                "style_examples": style_examples,
                "character_context": character_context,
                "target_lang": target_lang,
            }
            mid = len(entries) // 2
            first, first_suggestions = self.translate_batch(entries[:mid], source_lang, glossary_prompt, **kwargs)
            second, second_suggestions = self.translate_batch(entries[mid:], source_lang, glossary_prompt, **kwargs)
            return {**first, **second}, first_suggestions + second_suggestions

        translations, suggestions = self._parse_response(raw_text, entries)
        if not translations:
            raise ValueError(f"Could not parse any translations from Claude's response (stop_reason: {stop_reason})")
        return translations, suggestions

    def _create_with_retries(self, client, request: dict):
        """Send a Messages API request, retrying on rate limits and transient API errors.

        Args:
            client: The Anthropic client.
            request: Keyword arguments for `client.messages.create`.

        Raises:
            RuntimeError: If all retry attempts are exhausted.

        Returns:
            The Messages API response.
        """
        import anthropic

        max_retries = 3
        for attempt in range(max_retries):
            try:
                return client.messages.create(**request)
            except anthropic.RateLimitError:
                wait_time = 2**attempt * 5
                print(f"  Rate limited. Waiting {wait_time}s...")
                time.sleep(wait_time)
            except anthropic.APIError as e:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"API error after {max_retries} retries: {e}") from e
                wait_time = 2**attempt * 2
                time.sleep(wait_time)

        raise RuntimeError("Translation failed after all retries")

    def estimate_cost(
        self,
        entries: list[tuple[str, str]],
        source_lang: str = "Korean",
        glossary_prompt: str = "",
        game_context: str = "",
        format_rules: list[str] | None = None,
        style_examples: dict[str, list[tuple[str, str]]] | None = None,
        character_context: dict | None = None,
        target_lang: str = "English",
    ) -> dict:
        """Estimate the cost of translating the given entries with Claude.

        Builds the full prompt to get a realistic character count, then
        estimates token counts using heuristic ratios for CJK vs ASCII
        characters. Applies the selected model's pricing rates.

        Args:
            entries: List of (key, source_text) tuples.
            source_lang: Name of the source language (default: `"Korean"`).
            glossary_prompt: Pre-formatted glossary section for the prompt.
            game_context: Game title or description for the system prompt.
            format_rules: Formatting preservation rules to include.
            style_examples: Dict of category -> [(source, english)] pairs.
            character_context: Character background info dict.

        Returns:
            dict: Cost estimation with keys `"estimated_input_tokens"`,
                `"estimated_output_tokens"`, `"estimated_cost_usd"`, `"model"`,
                and `"note"`.
        """
        from backend import config as _cfg

        batches = chunk_entries(entries, _cfg.BATCH_SIZE, _cfg.BATCH_MAX_CHARS)
        num_batches = max(1, len(batches))

        total_input_tokens = 0
        total_output_tokens = 0
        total_cjk = 0
        total_ascii = 0

        for batch in batches:
            system_prompt, user_message = self.build_prompt(
                batch,
                source_lang,
                glossary_prompt,
                game_context=game_context,
                format_rules=format_rules,
                style_examples=style_examples,
                character_context=character_context,
                target_lang=target_lang,
            )
            full_prompt = system_prompt + user_message

            cjk_chars = sum(1 for c in full_prompt if "\u2e80" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7af" or "\uff00" <= c <= "\uffef")
            ascii_chars = len(full_prompt) - cjk_chars
            total_cjk += cjk_chars
            total_ascii += ascii_chars
            total_input_tokens += int(cjk_chars * 1.5 + ascii_chars * 0.35) + 300

            output_chars = sum(len(text) for _, text in batch)
            total_output_tokens += int(output_chars * 1.5) + 500

        pricing = CLAUDE_MODELS.get(self._model, _DEFAULT_PRICING)
        input_cost_per_m = pricing["input_per_mtok"]
        output_cost_per_m = pricing["output_per_mtok"]

        estimated_cost = total_input_tokens / 1_000_000 * input_cost_per_m + total_output_tokens / 1_000_000 * output_cost_per_m

        return {
            "estimated_input_tokens": total_input_tokens,
            "estimated_output_tokens": total_output_tokens,
            "estimated_cost_usd": round(estimated_cost, 4),
            "model": self._model,
            "note": f"Estimated for {len(entries)} strings across {num_batches} batch(es) ({total_cjk} CJK + {total_ascii} ASCII chars)",
        }
