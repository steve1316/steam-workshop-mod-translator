"""Pydantic request/response models for the REST API."""

from typing import Optional
from pydantic import BaseModel


class GlossaryTerm(BaseModel):
    """A global glossary term mapping a source-language word to its English translation.

    Attributes:
        source: The original term in the source language.
        english: The English translation of the term.
    """

    source: str
    english: str


class ModGlossaryTerm(BaseModel):
    """A mod-specific glossary term with per-language source mappings.

    Attributes:
        english: The English translation of the term.
        source_mappings: Mapping of source language codes to the original term
            in each language (e.g. `{"Korean": "마법"}`).
        category: The category for this term (e.g. `"custom"`, `"skill"`).
    """

    english: str
    source_mappings: dict[str, str] = {}
    category: str = "custom"


class SuggestionAction(BaseModel):
    """Payload for accepting or dismissing glossary term suggestions.

    Attributes:
        terms: List of specific English term strings to act on.
        all: If `True`, the action applies to every pending suggestion
            regardless of `terms`.
        renames: Accept only. Maps a suggestion's English to the edited English to save
            instead, which is also renamed in translations whose source contains the term.
    """

    terms: list[str] = []
    all: bool = False
    renames: dict[str, str] = {}


class TranslationRequest(BaseModel):
    """Request body for translation, estimation, and preview endpoints.

    Attributes:
        mod_id: The unique workshop identifier of the mod to translate.
        provider: Optional override for the translation provider name.
            Defaults to the value in `config.TRANSLATION_PROVIDER` when
            `None`.
        retranslate: When True, re-translate already-translated rows too.
        scope: Which rows to include. `"all"` (default) is every untranslated
            row; `"names"` restricts the WH3 preview to name strings only
            (unit/skill/building/location/...), for the Translate-Names-first pass.
            `"changed"` restricts the WH3 preview to changed rows for Update Changed.
    """

    mod_id: str
    provider: Optional[str] = None
    retranslate: bool = False
    scope: str = "all"


class BatchTranslationRequest(BaseModel):
    """Request body for translating a single batch of strings.

    Attributes:
        mod_id: The unique workshop identifier of the mod to translate.
        provider: Optional override for the translation provider name.
        keys: Explicit localization keys to translate in this batch.
        source_lang: The source language for all keys in this batch.
        is_first_batch: When True, creates a backup before translating.
        mode: `"translate"` (default) or `"update"`. Update revises the previous translations of changed rows (WH3).
    """

    mod_id: str
    provider: Optional[str] = None
    keys: list[str]
    source_lang: str
    is_first_batch: bool = False
    mode: str = "translate"


class TranslationUpdate(BaseModel):
    """Payload for manually updating a single translated string.

    Attributes:
        key: The localization key identifying the string (e.g.
            `"LangDataDB::Skill_FireBall::Desc"`).
        english: The new English translation text. An empty string clears
            the existing translation.
    """

    key: str
    english: str


class CharacterContext(BaseModel):
    """Optional character lore context used to improve translation quality.

    Attributes:
        source_game: The name of the game the character originates from.
        character_name: The character's display name.
        background: Free-text description of the character's lore, personality,
            or speech style that should inform translations.
    """

    source_game: str = ""
    character_name: str = ""
    background: str = ""


class SourceLanguageOverride(BaseModel):
    """Payload for setting a per-mod source language override.

    Attributes:
        source_language: Language name to use (e.g. `"Korean"`), or None
            to revert to auto-detection.
    """

    source_language: Optional[str] = None


class TargetLanguageOverride(BaseModel):
    """Payload for setting a per-mod target language override.

    Attributes:
        target_language: Language name to translate into (e.g. `"Chinese"`),
            or None to revert to default (English).
    """

    target_language: Optional[str] = None


class HistorySnapshotRequest(BaseModel):
    """Payload for creating a manual history snapshot.

    Attributes:
        label: User-provided description for the snapshot. Falls back to a
            default label when blank.
    """

    label: str = ""


class SettingsResponse(BaseModel):
    """Current application settings returned by GET /api/settings.

    Attributes:
        provider: Active translation provider ID (claude, openai, deepl, ollama, manual).
        batch_size: Number of strings sent per API request.
        anthropic_api_key_set: Masked Anthropic key (e.g. `"••••ab12"`)
            or empty string if not configured.
        openai_api_key_set: Masked OpenAI key or empty string.
        deepl_api_key_set: Masked DeepL key or empty string.
        claude_model: Selected Claude model identifier.
        openai_model: Selected OpenAI model identifier.
        ollama_base_url: Ollama server base URL.
        ollama_model: Selected Ollama model name.
        ollama_vram_tier: Selected VRAM tier (e.g. `"8gb"`).
        ollama_status: Ollama status — `"running"`, `"stopped"`, or `"not_installed"`.
        llamacpp_base_url: llama-server base URL.
        llamacpp_model: Display-only model name for llama-server.
        llamacpp_binary_path: Path to the llama-server binary.
        llamacpp_model_path: Path to the GGUF model file.
        llamacpp_gpu_layers: Number of layers to offload to GPU (-1 = all).
        llamacpp_ctx_size: Context window size for llama-server.
        ollama_managed: Whether this app spawned the running Ollama process.
        llamacpp_managed: Whether this app spawned the running llama-server process.
        ignored_mods: List of workshop mod IDs hidden from the dashboard.
        tw3_helper_path: Path to the TW3 helper_scripts directory.
        tw3_rpfm_cli_path: Path to rpfm_cli.exe (blank = use default inside helper_scripts).
        tw3_steam_library_drive: Drive letter for the Steam library (e.g. `"F:"`).
        steamcmd_path: Path to `steamcmd.exe` used by the Publish to Workshop feature.
        steam_username: Steam account username for `steamcmd +login` (no password stored).
    """

    provider: str
    batch_size: int
    anthropic_api_key_set: str
    openai_api_key_set: str
    deepl_api_key_set: str
    claude_model: str
    openai_model: str
    ollama_base_url: str
    ollama_model: str
    ollama_vram_tier: str
    ollama_status: str
    llamacpp_base_url: str
    llamacpp_model: str
    llamacpp_binary_path: str
    llamacpp_model_path: str
    llamacpp_gpu_layers: int
    llamacpp_ctx_size: int
    llamacpp_vram_tier: str
    ollama_managed: bool
    llamacpp_managed: bool
    ignored_mods: list[str]
    active_game: str
    tw3_helper_path: str = ""
    tw3_rpfm_cli_path: str = ""
    tw3_steam_library_drive: str = ""
    steamcmd_path: str = ""
    steam_username: str = ""
    games: dict[str, dict] = {}


class SettingsUpdate(BaseModel):
    """Payload for POST /api/settings.

    All fields are optional — only include fields that should change.
    Omitted (`None`) fields are left at their current values.

    Attributes:
        provider: New translation provider ID.
        batch_size: New batch size (must be >= 1).
        anthropic_api_key: New Anthropic API key value.
        openai_api_key: New OpenAI API key value.
        deepl_api_key: New DeepL API key value.
        claude_model: New Claude model identifier.
        openai_model: New OpenAI model identifier.
        ollama_base_url: New Ollama base URL.
        ollama_model: New Ollama model name.
        ollama_vram_tier: New VRAM tier selection.
        llamacpp_base_url: New llama-server base URL.
        llamacpp_model: New llama-server display model name.
        llamacpp_binary_path: New llama-server binary path.
        llamacpp_model_path: New GGUF model file path.
        llamacpp_gpu_layers: New GPU layer count (-1 = all).
        llamacpp_ctx_size: New context window size.
        ignored_mods: New list of workshop mod IDs to hide from the dashboard.
        tw3_helper_path: New path to the TW3 helper_scripts directory.
        tw3_rpfm_cli_path: New path to rpfm_cli.exe.
        tw3_steam_library_drive: New Steam library drive letter.
        steamcmd_path: New path to `steamcmd.exe`.
        steam_username: New Steam account username.
    """

    provider: Optional[str] = None
    batch_size: Optional[int] = None
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    deepl_api_key: Optional[str] = None
    claude_model: Optional[str] = None
    openai_model: Optional[str] = None
    ollama_base_url: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_vram_tier: Optional[str] = None
    llamacpp_base_url: Optional[str] = None
    llamacpp_model: Optional[str] = None
    llamacpp_binary_path: Optional[str] = None
    llamacpp_model_path: Optional[str] = None
    llamacpp_gpu_layers: Optional[int] = None
    llamacpp_ctx_size: Optional[int] = None
    llamacpp_vram_tier: Optional[str] = None
    ignored_mods: Optional[list[str]] = None
    active_game: Optional[str] = None
    tw3_helper_path: Optional[str] = None
    tw3_rpfm_cli_path: Optional[str] = None
    tw3_steam_library_drive: Optional[str] = None
    steamcmd_path: Optional[str] = None
    steam_username: Optional[str] = None


class GlossaryReplacePreview(BaseModel):
    """Request body for previewing glossary term replacements.

    Attributes:
        old_english: The current English term to find in translations.
        new_english: The replacement English term.
    """

    old_english: str
    new_english: str


class OllamaPullRequest(BaseModel):
    """Request body for POST /api/ollama/pull.

    Attributes:
        model: The Ollama model name to pull (e.g. `"qwen2.5:7b"`).
    """

    model: str


class LlamaCppInstallRequest(BaseModel):
    """Request body for POST /api/llamacpp/install.

    Attributes:
        backend: GPU backend to download (e.g. `"vulkan"`, `"cuda-12"`).
    """

    backend: str = "vulkan"


class GGUFDownloadRequest(BaseModel):
    """Request body for POST /api/llamacpp/download.

    Attributes:
        url: URL to download the GGUF model from.
        filename: Target filename for the downloaded model.
    """

    url: str
    filename: str
