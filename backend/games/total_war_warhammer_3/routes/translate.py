"""CA-parity translate endpoints for WH3: `/translate/preview`, `/translate/batch`, `/translate/cancel`.

These mirror Chrono Ark's `/translate` contract so the shared `useIterativeTranslation` hook, `TranslationConfirmModal`, and `GlossarySuggestionModal` drive
WH3 translation identically. The heavy lifting (parent extraction, drift/overlay, glossary prompt, persistence) is reused from the sibling `translation` module
so monkeypatched test fakes and any future changes stay in one place. The provider call goes through the game-agnostic `run_batch` orchestrator.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.data import suggestion_manager
from backend.games.storage_paths import game_storage_path
from backend.games.total_war_warhammer_3.name_keys import NAME_STAGES, classify_name_key, is_name_key
from backend.games.total_war_warhammer_3.name_spacing import glossary_words, space_pinyin_names
from backend.games.total_war_warhammer_3.routes import translation as _t
from backend.games.total_war_warhammer_3.source_snapshot import accept_keys
from backend.games.total_war_warhammer_3.translation_drift import entry_text
from backend.routes.models import BatchTranslationRequest, TranslationRequest
from backend.translation.orchestrator import chunk_entries, run_batch
from backend.translator.base import Revision

GAME_ID = "total_war_warhammer_3"

# WH3 colour and icon tags such as `[[col:red]]` and `[[img:ui/x.png]][[/img]]`, left out of name terms.
_MARKUP_RE = re.compile(r"\[\[[^\]]*\]\]")
router = APIRouter(prefix="/translate", tags=["translate"])


class NameSuggestionsRequest(BaseModel):
    """Body of `POST /translate/name-suggestions`."""

    # Workshop ID of the translation mod.
    mod_id: str
    # Name categories to suggest (e.g. `["unit"]` for one stage's review). None suggests every category.
    categories: list[str] | None = None


def _glossary_prompt(mod_id: str, source_lang: str, target_lang: str, mod=None) -> str:
    """Build the combined base + mod glossary prompt section for a batch.

    When `mod` is given and the mod's `include_translated_names` setting is on, translated names that are not in the glossary are sent as extra terms.

    Args:
        mod_id: Workshop ID of the translation mod.
        source_lang: Source language name.
        target_lang: Target language name.
        mod: The resolved `WH3TranslationMod`, needed to read translated names. None leaves them out.

    Returns:
        The combined glossary prompt, or a placeholder when no terms apply.
    """
    base_glossary = _t.glossary_store.load_base_glossary()
    mod_terms = _t.glossary_store.mod_glossary_as_terms(mod_id, source_lang)
    if mod is not None and _t.store.load_include_translated_names(mod_id):
        for term in _translated_name_terms(mod, mod_id):
            mod_terms["terms"][term["english"]] = {"english": term["english"], "category": term["category"], "key": "", "source_mappings": {source_lang: term["source"]}}
    prompt = _t.get_combined_glossary_prompt(
        base_glossary,
        mod_terms,
        source_lang=source_lang,
        target_lang=target_lang,
        allowed_categories=_t.tc.BASE_GLOSSARY_PROMPT_CATEGORIES,
    )
    return prompt or "No glossary available."


def _overlaid_rows(mod, mod_id: str) -> tuple[list, dict[str, str]]:
    """Return the mod's drift rows with translations.json overlaid, plus each key's parent source text.

    Args:
        mod: The resolved `WH3TranslationMod`.
        mod_id: Workshop ID of the translation mod.

    Returns:
        The overlaid `DriftRow` list and a key -> parent source text map.
    """
    parent = _t._extract_all_parent_strings(mod)
    translation = _t._extract_translation_strings(mod)
    snapshot = _t.store.load_parent_snapshot(mod_id)
    drift = _t.compute_drift(parent=parent, translation=translation, snapshot=snapshot)
    overlaid = _t._overlay_translations(drift, _t.store.load_translations_raw(mod_id))

    src_by_key: dict[str, str] = {}
    for rows in parent.values():
        for key, row in rows.items():
            src_by_key[key] = row.text
    return overlaid, src_by_key


def _translated_name_terms(mod, mod_id: str, categories: list[str] | None = None) -> list[dict]:
    """Return one term per translated name row that the mod glossary does not already cover.

    A name is covered when its English is a glossary term or its source text is already a glossary term's source. Rows come from the translation pack with
    translations.json overlaid, so names translated by hand or already in the pack count too. Colour and icon tags are stripped, and each English name is
    returned once.

    Args:
        mod: The resolved `WH3TranslationMod`.
        mod_id: Workshop ID of the translation mod.
        categories: Only include these name categories. None includes every category.

    Returns:
        `{english, source, category}` dicts in parent order.
    """
    overlaid, src_by_key = _overlaid_rows(mod, mod_id)
    glossary = _t.glossary_store.load_glossary(mod_id)
    known_sources = {info.get("source") for info in glossary.values() if info.get("source")}
    keep = glossary_words(glossary)

    terms: list[dict] = []
    seen: set[str] = set()
    for row in overlaid:
        category = classify_name_key(row.key)
        source = _MARKUP_RE.sub("", src_by_key.get(row.key, "")).strip()
        english = space_pinyin_names(source, _MARKUP_RE.sub("", row.translation_text or "").strip(), keep)
        if category is None or not english or not source or (categories is not None and category not in categories):
            continue
        if english in glossary or english in seen or source in known_sources:
            continue
        terms.append({"english": english, "source": source, "category": category})
        seen.add(english)
    return terms


def _untranslated_entries(mod, mod_id: str, key_filter=None) -> list[tuple[str, str]]:
    """Return (key, source_text) tuples for every untranslated row, honoring the translations.json overlay.

    Args:
        mod: The resolved `WH3TranslationMod`.
        mod_id: Workshop ID of the translation mod.
        key_filter: Optional predicate `(key) -> bool`; when given, only keys for which it returns True are included.

    Returns:
        (key, source_text) tuples for rows whose overlaid drift status is `untranslated` and which still have parent source text.
    """
    overlaid, src_by_key = _overlaid_rows(mod, mod_id)
    return [(r.key, src_by_key.get(r.key, "")) for r in overlaid if r.status == "untranslated" and src_by_key.get(r.key) and (key_filter is None or key_filter(r.key))]


def _changed_entries(mod, mod_id: str) -> tuple[list[tuple[str, str]], dict[str, Revision]]:
    """Return (key, new_source) tuples and update baselines for every changed row.

    A row is changed when its source drifted from the snapshot and it still has a translation. Rows whose new source is blank are left out.

    Args:
        mod: The resolved `WH3TranslationMod`.
        mod_id: Workshop ID of the translation mod.

    Returns:
        The entries in drift order, and a key -> `Revision` map holding the recorded old source and the translation shown in the table.
    """
    parent = _t._extract_all_parent_strings(mod)
    snapshot = _t.store.load_parent_snapshot(mod_id)
    drift = _t.compute_drift(parent=parent, translation=_t._extract_translation_strings(mod), snapshot=snapshot)
    entries: list[tuple[str, str]] = []
    revisions: dict[str, Revision] = {}
    for row in _t._overlay_translations(drift, _t.store.load_translations_raw(mod_id)):
        if row.status != "stale" or not (row.parent_text or "").strip() or not row.translation_text:
            continue
        entries.append((row.key, row.parent_text))
        revisions[row.key] = Revision(old_source=entry_text(snapshot.get(row.source_filename, {}).get(row.key)), previous_translation=row.translation_text)
    return entries, revisions


@router.get("/system-prompt")
async def get_system_prompt(source_lang: str = "Chinese", target_lang: str = "English") -> dict:
    """Return the system prompt a WH3 translation run would send, for the Settings preview.

    Mirrors `preview`: Claude (WH3 always translates with Claude), the WH3 game context, format rules and style examples, and the base glossary
    filtered to the prompt categories. No mod is involved, so a mod's own glossary terms are not included.

    Args:
        source_lang: Source language to build the prompt for (e.g. `"Chinese"`).
        target_lang: Language being translated into.

    Returns:
        A dict with `provider`, `source_lang` and `system_prompt`.
    """
    provider = _t.ClaudeProvider()
    adapter = _t.TotalWarWarhammer3Adapter()
    glossary_prompt = adapter.get_base_glossary_prompt(source_lang=source_lang, target_lang=target_lang) or "No glossary available."
    system_prompt, _ = provider.build_prompt(
        [("example_unit_description", "示例文本")],
        source_lang,
        glossary_prompt,
        game_context=adapter.get_translation_context(),
        format_rules=adapter.get_format_preservation_rules(),
        style_examples=adapter.get_style_examples(source_lang),
        character_context=None,
        target_lang=target_lang,
    )
    return {"provider": provider.name, "source_lang": source_lang, "system_prompt": system_prompt}


@router.post("/preview")
async def preview(req: TranslationRequest) -> dict:
    """Preview the prompts, cost estimate, and batch plan for translating a WH3 mod's untranslated rows (or its changed rows when `scope == "changed"`).

    Args:
        req: Translation request carrying the mod id and optional provider override.

    Returns:
        `total_strings`, `total_batches`, `batch_size`, `provider`, `previews` (keyed by source language), `estimates` (keyed by source language), and
        a flat `batch_plan` the frontend hook iterates over. A names preview also returns `stages`, each with its own `batch_plan`, in `NAME_STAGES` order.
        When nothing is untranslated, returns `total_strings == 0` with an empty `previews`.

    Raises:
        HTTPException: 404 if `mod_id` is not registered.
    """
    mod = _t._require_mod(req.mod_id)
    source_lang = mod.source_language
    target_lang = mod.target_language

    names_only = req.scope == "names"
    changed_only = req.scope == "changed"
    revisions: dict[str, Revision] = {}
    if changed_only:
        entries, revisions = _changed_entries(mod, req.mod_id)
    else:
        entries = _untranslated_entries(mod, req.mod_id, key_filter=is_name_key if names_only else None)
    if not entries:
        message = "No changed strings to update" if changed_only else ("All names already translated" if names_only else "All strings already translated")
        return {"total_strings": 0, "message": message, "previews": {}}

    batch_size = _t.config.BATCH_SIZE
    max_chars = _t.config.BATCH_MAX_CHARS
    chunks: list[list[tuple[str, str]]] = []
    stages: list[dict] = []
    if names_only:
        # One stage at a time, so a batch never mixes two stages and the frontend can pause for review between them.
        for stage in NAME_STAGES:
            stage_entries = [e for e in entries if classify_name_key(e[0]) in stage["categories"]]
            if not stage_entries:
                continue
            stage_chunks = chunk_entries(stage_entries, batch_size, max_chars)
            chunks.extend(stage_chunks)
            stages.append(
                {
                    "id": stage["id"],
                    "label": stage["label"],
                    "categories": stage["categories"],
                    "total_strings": len(stage_entries),
                    "batch_plan": [{"source_lang": source_lang, "keys": [k for k, _ in c], "size": len(c)} for c in stage_chunks],
                }
            )
        entries = [e for c in chunks for e in c]
    else:
        chunks = chunk_entries(entries, batch_size, max_chars)

    provider = _t.ClaudeProvider()
    provider.revisions = revisions or None
    adapter = _t.TotalWarWarhammer3Adapter()
    glossary_prompt = _glossary_prompt(req.mod_id, source_lang, target_lang, mod=mod)
    game_context = adapter.get_translation_context()
    format_rules = adapter.get_format_preservation_rules()
    style_examples = adapter.get_style_examples(source_lang)

    num_batches = len(chunks)
    system_prompt = ""
    user_messages: list[str] = []
    for batch in chunks:
        sp, um = provider.build_prompt(
            batch,
            source_lang,
            glossary_prompt,
            game_context=game_context,
            format_rules=format_rules,
            style_examples=style_examples,
            character_context=None,
            target_lang=target_lang,
        )
        if not system_prompt:
            system_prompt = sp
        user_messages.append(um)

    previews = {source_lang: {"system_prompt": system_prompt, "user_messages": user_messages, "strings_in_language": len(entries), "batches": num_batches}}
    estimates = {
        source_lang: provider.estimate_cost(
            entries,
            source_lang=source_lang,
            glossary_prompt=glossary_prompt,
            game_context=game_context,
            format_rules=format_rules,
            style_examples=style_examples,
            character_context=None,
            target_lang=target_lang,
        )
    }
    batch_plan = [{"source_lang": source_lang, "keys": [k for k, _ in c], "size": len(c)} for c in chunks]

    result = {
        "total_strings": len(entries),
        "total_batches": num_batches,
        "batch_size": batch_size,
        "provider": provider.name,
        "previews": previews,
        "estimates": estimates,
        "batch_plan": batch_plan,
    }
    if names_only:
        result["stages"] = stages
    return result


def _log_batch_call(mod_id: str, keys: list[str], raw_responses: list[dict], error: str | None = None) -> None:
    """Record a translate-batch Claude call in the mod's API-responses log, with Claude's raw reply text.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.
        keys: Keys that were sent.
        raw_responses: The provider's raw response records for this batch.
        error: Error message when the call failed.
    """
    _t.api_responses_store.append(
        mod_id,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": "translate-batch",
            "provider": "claude",
            **_t.api_responses_store.provider_call_fields(raw_responses, error=error),
            "keys_or_inputs": keys,
        },
    )


@router.post("/batch")
async def translate_batch(req: BatchTranslationRequest) -> dict:
    """Translate one batch of keys via Claude and persist into translations.json.

    Designed for the iterative frontend loop: the hook posts one batch at a time so the user can review glossary suggestions between batches. Results are
    written incrementally and the provider's suggested glossary terms are returned for review.

    Args:
        req: Batch request with `mod_id`, explicit `keys`, `source_lang`, optional provider override, `is_first_batch`, and `mode` (`update` revises changed rows).

    Returns:
        `{"status": "success", "translated": N, "translations": {key: text}, "suggestions": [...]}`.

    Raises:
        HTTPException: 404 if `mod_id` is not registered.
        HTTPException: 400 if none of the provided keys have source text.
        HTTPException: 502 if the provider errors.
    """
    mod = _t._require_mod(req.mod_id)
    source_lang = req.source_lang
    target_lang = mod.target_language

    parent = _t._extract_all_parent_strings(mod)
    wanted = set(req.keys)
    revisions: dict[str, Revision] = {}
    entries: list[tuple[str, str]] = []
    if req.mode == "update":
        changed_entries, changed_revisions = _changed_entries(mod, req.mod_id)
        entries = [e for e in changed_entries if e[0] in wanted]
        revisions = {k: v for k, v in changed_revisions.items() if k in wanted}
    else:
        for rows in parent.values():
            for key, row in rows.items():
                if key in wanted and row.text:
                    entries.append((key, row.text))
    if not entries:
        raise HTTPException(status_code=400, detail="No translatable text found for the provided keys")

    adapter = _t.TotalWarWarhammer3Adapter()
    glossary_prompt = _glossary_prompt(req.mod_id, source_lang, target_lang, mod=mod)
    provider = _t.ClaudeProvider()
    provider.revisions = revisions or None
    provider.last_raw_responses = []
    keys = [k for k, _ in entries]

    try:
        translations, _suggestions = await run_batch(
            provider,
            entries,
            source_lang,
            glossary_prompt,
            game_context=adapter.get_translation_context(),
            format_rules=adapter.get_format_preservation_rules(),
            style_examples=adapter.get_style_examples(source_lang),
            character_context=None,
            target_lang=target_lang,
        )
    except Exception as e:
        _log_batch_call(req.mod_id, keys, provider.last_raw_responses, error=str(e))
        raise HTTPException(status_code=502, detail=str(e))

    # Split joined pinyin names ("Miaoying" -> "Miao Ying") in the translations and suggested terms before anything is saved. The API log keeps
    # Claude's raw output.
    # Words the mod glossary spells joined ("Fu Yuanshan") are kept.
    existing_glossary = _t.glossary_store.load_glossary(req.mod_id)
    keep = glossary_words(existing_glossary)
    source_by_key = dict(entries)
    spaced = {key: space_pinyin_names(source_by_key.get(key, ""), text, keep) for key, text in translations.items()}
    _suggestions = [{**s, "english": space_pinyin_names(s.get("source", ""), s["english"], keep)} if s.get("english") else s for s in _suggestions]

    # Persist the provider's glossary suggestions so the shared review modal can accept/dismiss them between batches. Drop any term already in the mod
    # glossary so the user only reviews genuinely new terms (matching Chrono Ark's review behavior).
    suggestions = [s for s in _suggestions if s.get("english") and s["english"] not in existing_glossary]
    if suggestions:
        suggestion_manager.add_suggestions(req.mod_id, suggestions, storage_path=game_storage_path(GAME_ID))

    raw = _t.store.load_translations_raw(req.mod_id)
    now = datetime.now(timezone.utc).isoformat()
    for key, text in spaced.items():
        existing = raw.get(key, {})
        raw[key] = {"text": text, "created_at": existing.get("created_at") or now, "updated_at": now, "provider": "claude"}
    _t.store.save_translations_raw(req.mod_id, raw)
    # Each saved row was translated from the current source, so that source becomes its baseline. It shows as pending until Sync.
    _t.store.save_parent_snapshot(req.mod_id, accept_keys(_t.store.load_parent_snapshot(req.mod_id), parent, set(spaced)))

    _log_batch_call(req.mod_id, keys, provider.last_raw_responses)

    return {"status": "success", "translated": len(spaced), "translations": spaced, "suggestions": suggestions}


@router.post("/name-suggestions")
async def name_suggestions(req: NameSuggestionsRequest) -> dict:
    """Turn the mod's translated name rows into glossary suggestions and persist them for review.

    Called after each stage of the Translate Names pass (with that stage's `categories`) and by Suggest from Translated Names (with no filter). Names the
    glossary already covers are skipped. Results go through the shared suggestion store, so the existing review modal and accept/dismiss endpoints handle them.

    Args:
        req: The mod id and an optional category filter.

    Returns:
        `{"suggestions": [...]}` - the newly persisted name suggestions (may be empty).

    Raises:
        HTTPException: 404 if `mod_id` is not registered.
    """
    mod = _t._require_mod(req.mod_id)
    source_lang = mod.source_language
    suggestions = [{**term, "source_lang": source_lang, "reason": f"Translated {term['category']} name"} for term in _translated_name_terms(mod, req.mod_id, req.categories)]
    if suggestions:
        suggestion_manager.add_suggestions(req.mod_id, suggestions, storage_path=game_storage_path(GAME_ID))
    return {"suggestions": suggestions}


@router.post("/cancel")
async def cancel(mod_id: str = "") -> dict:
    """Signal cancellation of an in-progress translation run.

    WH3 uses non-streaming Claude, so a batch already in flight cannot be aborted mid-call. Cancellation is driven client-side by stopping the batch loop.
    This endpoint exists for parity with Chrono Ark and is a safe no-op.

    Args:
        mod_id: Workshop ID of the translation mod (accepted for parity; unused).

    Returns:
        `{"cancelled": False}` - nothing was aborted server-side.
    """
    return {"cancelled": False}
