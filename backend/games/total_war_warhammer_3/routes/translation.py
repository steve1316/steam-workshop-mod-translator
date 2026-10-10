"""REST endpoints for the WH3 translation pipeline.

All endpoints are mounted under `/api/games/total_war_warhammer_3/translation`.
Heavy I/O (RPFM extract, TSV scan) is wrapped behind module-level helpers so
tests can monkeypatch them in isolation.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend import config
from backend.data.glossary_manager import get_combined_glossary_prompt, replace_whole_term
from backend.games.total_war_warhammer_3 import translation_context as tc
from backend.data.mod_settings import (
    load_source_language_override,
    load_target_language_override,
    save_source_language_override,
    save_target_language_override,
)
from backend.games.storage_paths import game_storage_path
from backend.games.total_war_warhammer_3 import (
    api_responses_store,
    glossary_store,
    snapshot_store,
    translation_store_helpers as store,
)
from backend.games.total_war_warhammer_3 import translation_change_notes as tcn
from backend.games.total_war_warhammer_3.adapter import TotalWarWarhammer3Adapter
from backend.games.total_war_warhammer_3.name_spacing import glossary_words, space_pinyin_names
from backend.games.total_war_warhammer_3.canonical_status import to_status_rows
from backend.games.total_war_warhammer_3.loc_extractor import (
    LocRow,
    extract_parent_pack_strings,
    normalize_loc_filename,
    read_translation_loc_tsv,
)
from backend.games.total_war_warhammer_3.loc_tsv_packer import (
    PackBuildError,
    RpfmNotConfiguredError,
    build_translation_pack,
    resolve_rpfm_cli_path,
)
from backend.games.total_war_warhammer_3.loc_tsv_writeback import _find_existing_user_file, remove_keys_from_loc_tsv, sync_translations_to_loc_tsv
from backend.games.total_war_warhammer_3.routes._paths import tw3_workshop_content_dir
from backend.games.total_war_warhammer_3.source_snapshot import accept_keys, refresh_unchanged
from backend.games.total_war_warhammer_3.translation_drift import (
    DriftRow,
    compute_drift,
)
from backend.games.total_war_warhammer_3.translation_mods import (
    WH3_TRANSLATION_MODS,
    WH3TranslationMod,
    get_translation_mod,
)
from backend.translator.claude_provider import ClaudeProvider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/translation", tags=["translation"])


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# Helpers (extracted as module-level functions so tests can monkeypatch them)


def _extract_translation_strings(mod: WH3TranslationMod) -> dict[str, dict[str, LocRow]]:
    """Read all `.loc.tsv` files under `mod.local_source_dir/text/**`.

    Args:
        mod: The translation mod whose local source dir to scan.

    Returns:
        Dict mapping normalized filename to `{key: LocRow}`.
    """
    out: dict[str, dict[str, LocRow]] = {}
    text_dir = mod.local_source_dir / "text"
    if not text_dir.exists():
        return out
    for tsv in text_dir.rglob("*.loc.tsv"):
        norm = normalize_loc_filename(tsv.name)
        out[norm] = read_translation_loc_tsv(tsv)
    return out


def _extract_all_parent_strings(mod: WH3TranslationMod) -> dict[str, dict[str, LocRow]]:
    """Extract `.loc` strings from every parent `.pack` declared on `mod`.

    Resolves `rpfm_cli_path` the same way `script_runner._preflight` does: prefer
    `config.TW3_RPFM_CLI_PATH` when set, otherwise fall back to `rpfm_cli.exe`
    inside `config.TW3_HELPER_PATH`.

    Args:
        mod: The translation mod whose parent packs to extract.

    Returns:
        Merged dict mapping normalized filename to `{key: LocRow}` across all parents.

    Raises:
        HTTPException: If neither `TW3_RPFM_CLI_PATH` nor `<TW3_HELPER_PATH>/rpfm_cli.exe`
            resolves to a file on disk, or the Steam library drive is not configured.
    """
    rpfm_str = config.TW3_RPFM_CLI_PATH or ""
    if rpfm_str:
        rpfm: Path | None = Path(rpfm_str)
    else:
        helper_str = config.TW3_HELPER_PATH or ""
        rpfm = Path(helper_str) / "rpfm_cli.exe" if helper_str else None
    if rpfm is None or not rpfm.is_file():
        raise HTTPException(500, f"rpfm_cli not found (tried {rpfm or 'TW3_RPFM_CLI_PATH'}); set SWMT_TW3_RPFM_CLI_PATH or place rpfm_cli.exe in SWMT_TW3_HELPER_PATH")

    merged: dict[str, dict[str, LocRow]] = {}
    for parent_id in mod.parent_workshop_ids:
        parent_dir = tw3_workshop_content_dir(parent_id)
        if parent_dir is None:
            raise HTTPException(500, "TW3 Steam library drive is not configured")
        if not parent_dir.exists():
            continue
        packs = sorted(parent_dir.glob("*.pack"))
        if not packs:
            continue
        cache_dir = store.parent_pack_cache_dir(parent_id)
        for pack in packs:
            extracted = extract_parent_pack_strings(pack, rpfm, cache_dir)
            for filename, rows in extracted.items():
                merged.setdefault(filename, {}).update(rows)
    return merged


def _require_mod(mod_id: str) -> WH3TranslationMod:
    """Look up a mod by ID or raise 404.

    Args:
        mod_id: Steam Workshop ID of the translation mod.

    Returns:
        The matching `WH3TranslationMod`.

    Raises:
        HTTPException: 404 if `mod_id` is not in the registry.
    """
    mod = get_translation_mod(mod_id)
    if mod is None:
        raise HTTPException(404, f"WH3 translation mod {mod_id} not registered")
    return mod


def _serialize_drift_row(row: DriftRow) -> dict:
    """Convert a `DriftRow` to a JSON-serializable dict.

    Args:
        row: The drift row to serialize.

    Returns:
        Dict with `source_filename`, `key`, `parent_text`, `translation_text`, `status`, and `provider`.
    """
    return {
        "source_filename": row.source_filename,
        "key": row.key,
        "parent_text": row.parent_text,
        "translation_text": row.translation_text,
        "status": row.status,
        "provider": row.provider,
    }


def _overlay_translations(drift: list[DriftRow], raw_translations: dict) -> list[DriftRow]:
    """Overlay `translations.json` entries onto computed drift rows so in-flight edits and clears reflect immediately.

    A non-empty override supplies its text/provider and promotes an untranslated row to `translated`. An empty-string override is an explicit clear: the row
    shows empty text and is marked `untranslated` (when it has parent text) so the `.loc.tsv` content does not show through. Rows with no override keep their
    `.loc.tsv` status, with `provider` defaulting to `manual` when text is present and `None` when absent. Both `get_strings` and `rescan` use this so the
    table and the per-status counts never disagree.

    Args:
        drift: Rows produced by `compute_drift` from the parent and `.loc.tsv` sources.
        raw_translations: The mod's `translations.json` mapping (`key -> {text, provider}`).

    Returns:
        A new list of `DriftRow` with overrides applied, preserving the input order.
    """
    overlaid: list[DriftRow] = []
    for row in drift:
        entry = raw_translations.get(row.key)
        has_override = isinstance(entry, dict) and "text" in entry
        if has_override and entry["text"]:
            override_provider = entry.get("provider") or "manual"
            # If the row was untranslated on the file system but translations.json has it, promote it to translated per the parent hash.
            new_status = "translated" if row.status == "untranslated" else row.status
            overlaid.append(
                DriftRow(
                    source_filename=row.source_filename,
                    key=row.key,
                    parent_text=row.parent_text,
                    translation_text=entry["text"],
                    status=new_status,
                    provider=override_provider,
                )
            )
        elif has_override:
            # Empty-string override = explicitly cleared. Show empty and mark untranslated so the .loc.tsv content does not show through.
            cleared_status = "untranslated" if row.parent_text is not None else row.status
            overlaid.append(
                DriftRow(
                    source_filename=row.source_filename,
                    key=row.key,
                    parent_text=row.parent_text,
                    translation_text="",
                    status=cleared_status,
                    provider=None,
                )
            )
        else:
            # Existing .loc.tsv translation (or untranslated). Default provider to "manual" when text is present, None when absent.
            provider = "manual" if row.translation_text else None
            overlaid.append(
                DriftRow(
                    source_filename=row.source_filename,
                    key=row.key,
                    parent_text=row.parent_text,
                    translation_text=row.translation_text,
                    status=row.status,
                    provider=provider,
                )
            )
    return overlaid


def _find_parent_preview_image(mod: WH3TranslationMod) -> Path | None:
    """Resolve a preview `.png` for the translation mod by inspecting its parent workshop folder.

    Each Steam Workshop mod includes a single preview `.png` in its content folder. For a
    translation mod we surface the FIRST parent's preview so dashboard cards visually anchor
    to the game content being translated.

    Args:
        mod: Translation mod whose parent folder(s) to scan.

    Returns:
        Filesystem `Path` to the first `.png` found in the first existing parent folder. `None`
        when no parent folder is reachable on disk or no `.png` is present.
    """
    for parent_id in mod.parent_workshop_ids:
        folder = tw3_workshop_content_dir(parent_id)
        if folder is None or not folder.exists():
            continue
        for png in sorted(folder.glob("*.png")):
            return png
    return None


def _preview_url_for(mod: WH3TranslationMod) -> str | None:
    """Build the preview-image route URL for a mod, or `None` when no preview file is found.

    Returns a path relative to `API_BASE` so the frontend can concatenate `${API_BASE}${url}`
    the same way it does for Chrono Ark mod previews.

    Args:
        mod: Translation mod to check.

    Returns:
        Relative path `/games/total_war_warhammer_3/translation/mods/{id}/preview` when a preview
        file exists, otherwise `None`.
    """
    if _find_parent_preview_image(mod) is None:
        return None
    return f"/games/total_war_warhammer_3/translation/mods/{mod.workshop_id}/preview"


def _has_unsynced_changes(mod_id: str, translation: dict[str, dict[str, LocRow]]) -> bool:
    """Return `True` when translations.json holds entries whose text differs from the user's `.loc.tsv`.

    Compares each `translations.json` entry against the current `.loc.tsv` content for the same key. A key present in `translations.json` with text
    that does not match (or has no matching `.loc.tsv` row) counts as unsynced. An empty-string override against non-empty `.loc.tsv` text is a pending
    clear and counts too. Mirrors Chrono Ark's `has_changes` semantics.

    Args:
        mod_id: Steam Workshop ID of the translation mod.
        translation: Already-extracted translation map (`source_filename -> {key: LocRow}`). The caller
            passes the same value returned by `_extract_translation_strings(mod)` to avoid a second
            filesystem scan on the rescan hot path.

    Returns:
        `True` when any translations.json entry text differs from on-disk `.loc.tsv` text. `False` otherwise.
    """
    raw = store.load_translations_raw(mod_id)
    if not raw:
        return False
    loc_by_key: dict[str, str] = {}
    for rows in translation.values():
        for key, row in rows.items():
            loc_by_key[key] = row.text
    for key, entry in raw.items():
        if not isinstance(entry, dict) or "text" not in entry:
            continue
        text = entry["text"] or ""
        # An empty override that differs from non-empty .loc.tsv text is a pending clear, so it still counts as unsynced.
        if (loc_by_key.get(key) or "") != text:
            return True
    return False


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# Route models


class TranslationModSummary(BaseModel):
    """One translation-mod summary row."""

    workshop_id: str
    display_name: str
    parent_workshop_ids: list[str]
    local_source_dir: str
    source_language: str
    target_language: str
    # Relative URL to the parent mod's preview image. `None` when no preview file is reachable on disk.
    preview_image_url: str | None = None


class RescanSummary(BaseModel):
    """Per-status row counts after a rescan."""

    mod_id: str
    counts: dict[str, int]
    # Zero-filled tally of the canonical five-state model (synced/untouched/pending/missing/untranslatable), mirroring the per-row `canonical_status`.
    canonical_counts: dict[str, int]
    scanned_at: str
    # True when translations.json holds entries whose text does not match the user's `.loc.tsv` files.
    has_unsynced_changes: bool = False
    # True when any of `source_game / character_name / background` in mod-context is non-empty.
    has_mod_context: bool = False


class TranslationEdit(BaseModel):
    """Request body for `PUT /strings/{key}`."""

    text: str


class ModContext(BaseModel):
    """Body of `GET/PUT /mod-context`."""

    source_game: str = ""
    character_name: str = ""
    background: str = ""
    # Per-mod override for source language. None falls back to the registry default.
    source_language_override: str | None = None
    # Per-mod override for target language. None falls back to the registry default.
    target_language_override: str | None = None
    # When True, translated names that are not in the glossary are also sent with every prompt.
    include_translated_names: bool = False


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# Routes


@router.get("/mods", response_model=list[TranslationModSummary])
def list_translation_mods() -> list[TranslationModSummary]:
    """Return the static registry of WH3 translation mods.

    Returns:
        List of `TranslationModSummary` entries, one per registered mod.
    """
    return [
        TranslationModSummary(
            workshop_id=m.workshop_id,
            display_name=m.display_name,
            parent_workshop_ids=list(m.parent_workshop_ids),
            local_source_dir=str(m.local_source_dir),
            source_language=m.source_language,
            target_language=m.target_language,
            preview_image_url=_preview_url_for(m),
        )
        for m in WH3_TRANSLATION_MODS
    ]


@router.get("/mods/{mod_id}/preview")
def get_preview(mod_id: str) -> FileResponse:
    """Stream the parent mod's preview `.png` image for the given translation mod.

    Args:
        mod_id: Steam Workshop ID of the translation mod.

    Returns:
        `FileResponse` serving the first `.png` found in the first existing parent workshop folder.

    Raises:
        HTTPException: 404 when the mod is not registered or no preview file is reachable on disk.
    """
    mod = _require_mod(mod_id)
    png = _find_parent_preview_image(mod)
    if png is None:
        raise HTTPException(404, f"no preview image found for {mod_id}")
    return FileResponse(png, media_type="image/png")


@router.post("/mods/{mod_id}/rescan", response_model=RescanSummary)
def rescan(mod_id: str) -> RescanSummary:
    """Re-extract parent strings, re-read local translations, compute drift.

    Args:
        mod_id: Steam Workshop ID of the translation mod.

    Returns:
        `RescanSummary` with per-status counts, scan timestamp, `has_unsynced_changes`, and `has_mod_context`.

    Raises:
        HTTPException: 404 if `mod_id` is not registered.
    """
    mod = _require_mod(mod_id)
    parent = _extract_all_parent_strings(mod)
    translation = _extract_translation_strings(mod)
    snapshot = store.load_parent_snapshot(mod_id)

    drift = compute_drift(parent=parent, translation=translation, snapshot=snapshot)

    # Add entries for never-before-seen keys and backfill text on matching ones. Drifted keys keep their old entry so they stay changed.
    store.save_parent_snapshot(mod_id, refresh_unchanged(snapshot, parent))

    # Count from the overlaid rows (not raw drift) so empty-string clears and in-flight overrides match the strings view exactly.
    raw_translations = store.load_translations_raw(mod_id)
    overlaid = _overlay_translations(drift, raw_translations)
    # Rows with a blank parent source have nothing to translate, so they are left out of the progress counts like Chrono Ark does.
    counts: dict[str, int] = {"translated": 0, "untranslated": 0, "stale": 0, "orphan": 0}
    for row in overlaid:
        if row.parent_text is not None and not row.parent_text.strip():
            continue
        counts[row.status] += 1

    # Canonical six-state tally for the shared status pills. Zero-filled so the dict always carries every state, matching Chrono Ark. Orphan rows are
    # excluded so the counts match the orphan-free strings table.
    canonical_counts: dict[str, int] = {"synced": 0, "untouched": 0, "pending": 0, "changed": 0, "missing": 0, "untranslatable": 0}
    for srow in to_status_rows([d for d in drift if d.status != "orphan"], raw_translations):
        canonical_counts[srow.status] += 1

    ctx = store.load_character_context(mod_id)
    has_mod_context = bool((ctx.get("source_game") or "").strip() or (ctx.get("character_name") or "").strip() or (ctx.get("background") or "").strip())

    return RescanSummary(
        mod_id=mod_id,
        counts=counts,
        canonical_counts=canonical_counts,
        scanned_at=datetime.now(timezone.utc).isoformat(),
        has_unsynced_changes=_has_unsynced_changes(mod_id, translation),
        has_mod_context=has_mod_context,
    )


def _drift_and_overlay(mod_id: str, mod: WH3TranslationMod) -> tuple[list[DriftRow], dict, list[DriftRow]]:
    """Compute a mod's drift rows and overlay `translations.json` onto them.

    Args:
        mod_id: Steam Workshop ID of the translation mod.
        mod: The registered translation mod.

    Returns:
        `(drift, raw_translations, overlaid)`, where `overlaid` has `translations.json` applied and orphan rows removed.
    """
    parent = _extract_all_parent_strings(mod)
    translation = _extract_translation_strings(mod)
    drift = compute_drift(parent=parent, translation=translation, snapshot=store.load_parent_snapshot(mod_id))
    raw_translations = store.load_translations_raw(mod_id)
    # Orphan strings (translated keys whose parent source no longer exists) are hidden from the table; they are pruned from disk on the next sync.
    overlaid = [r for r in _overlay_translations(drift, raw_translations) if r.status != "orphan"]
    return drift, raw_translations, overlaid


@router.get("/mods/{mod_id}/strings")
def get_strings(mod_id: str, status: Literal["translated", "untranslated", "stale", "orphan"] | None = None) -> list[dict]:
    """Return drift rows for a mod, optionally filtered by status.

    Overlays `translations.json` onto the drift rows so any in-flight edits (user PUTs, Claude batches) reflect immediately - their `translation_text`
    and `provider` come from translations.json when present, otherwise from the user's `.loc.tsv` content with `provider` defaulting to `"manual"`.
    An empty-string override is treated as an explicit clear: the row shows empty text and `untranslated` status rather than the `.loc.tsv` content.

    Args:
        mod_id: Steam Workshop ID of the translation mod.
        status: Optional status filter (`"translated"`, `"untranslated"`, `"stale"`, `"orphan"`).

    Returns:
        List of serialized `DriftRow` dicts.

    Raises:
        HTTPException: 404 if `mod_id` is not registered.
    """
    mod = _require_mod(mod_id)
    drift, raw_translations, overlaid = _drift_and_overlay(mod_id, mod)
    # Pre-overlay translation_text is the on-disk .loc.tsv value - the "previous" translation (analogous to Chrono Ark's original_english).
    previous_by_key = {r.key: r.translation_text for r in drift}
    canonical = {(r.source_file, r.key): r.status for r in to_status_rows(drift, raw_translations)}

    if status:
        overlaid = [r for r in overlaid if r.status == status]

    return [{**_serialize_drift_row(r), "canonical_status": canonical.get((r.source_filename, r.key)), "previous_text": previous_by_key.get(r.key)} for r in overlaid]


def current_shipped_strings(mod_id: str) -> dict[str, dict[str, str]]:
    """Return what the next publish of a mod will ship, hashed for changenote diffing.

    Uses the same overlaid rows as `get_strings`, which are what Sync writes, so the result is already right before the sync-first step has run.

    Args:
        mod_id: Steam Workshop ID of the translation mod.

    Returns:
        `{source_filename: {key: hash}}` from `translation_change_notes.shipped_strings`.

    Raises:
        HTTPException: 404 if `mod_id` is not registered.
    """
    mod = _require_mod(mod_id)
    _, _, overlaid = _drift_and_overlay(mod_id, mod)
    return tcn.shipped_strings(overlaid)


@router.put("/mods/{mod_id}/strings/{key}")
def put_string(mod_id: str, key: str, edit: TranslationEdit) -> dict:
    """Persist a translation edit.

    Args:
        mod_id: Steam Workshop ID of the translation mod.
        key: The string key to save.
        edit: Request body containing the translated text.

    Returns:
        `{"status": "ok"}` on success.

    Raises:
        HTTPException: 404 if `mod_id` is not registered.
    """
    mod = _require_mod(mod_id)
    raw = store.load_translations_raw(mod_id)
    now = datetime.now(timezone.utc).isoformat()
    existing = raw.get(key, {})
    raw[key] = {
        "text": edit.text,
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "provider": "manual",
    }
    store.save_translations_raw(mod_id, raw)
    # A manual edit settles a changed row: accept the current source as its baseline. Best effort, since extraction may need RPFM.
    try:
        store.save_parent_snapshot(mod_id, accept_keys(store.load_parent_snapshot(mod_id), _extract_all_parent_strings(mod), {key}))
    except Exception as e:
        logger.warning("Could not accept the current source for %s in mod %s: %s", key, mod_id, e)
    return {"status": "ok"}


@router.get("/mods/{mod_id}/mod-context")
def get_mod_context(mod_id: str) -> dict:
    """Return the mod context for a registered translation mod.

    Args:
        mod_id: Steam Workshop ID of the translation mod.

    Returns:
        Dict with mod context fields (`source_game`, `character_name`, `background`), the per-mod language overrides
        (`source_language_override`, `target_language_override`), and `include_translated_names`.

    Raises:
        HTTPException: 404 if `mod_id` is not registered.
    """
    _require_mod(mod_id)
    ctx = store.load_character_context(mod_id)
    wh3_root = game_storage_path(store.GAME_ID)
    ctx["source_language_override"] = load_source_language_override(mod_id, storage_path=wh3_root)
    ctx["target_language_override"] = load_target_language_override(mod_id, storage_path=wh3_root)
    ctx["include_translated_names"] = store.load_include_translated_names(mod_id)
    return ctx


class TranslateBatchRequest(BaseModel):
    """Request body for `POST /mods/{mod_id}/translate`."""

    keys: list[str]


@router.post("/mods/{mod_id}/translate")
def translate_batch(mod_id: str, req: TranslateBatchRequest) -> dict:
    """Translate the given keys via Claude and persist into `translations.json`.

    `ClaudeProvider.translate_batch` takes `entries: list[tuple[str, str]]` and
    builds the system + user prompts internally via `TranslationProvider.build_prompt`.
    We pass the WH3 adapter's translation-context strings as kwargs so the prompt
    is WH3-specific.

    Args:
        mod_id: Workshop ID of the WH3 translation mod.
        req: List of loc keys to translate. Source text is looked up from the
            most recent parent extraction.

    Returns:
        `{"translated": N, "suggested_terms": [...]}`.
    """
    mod = _require_mod(mod_id)
    parent = _extract_all_parent_strings(mod)

    # Flatten parent into {key: source_text} for the keys the user wants translated.
    src: dict[str, str] = {}
    for rows in parent.values():
        for key, row in rows.items():
            if key in req.keys:
                src[key] = row.text
    if not src:
        return {"translated": 0, "suggested_terms": []}

    adapter = TotalWarWarhammer3Adapter()
    entries = list(src.items())

    base_glossary = glossary_store.load_base_glossary()
    mod_terms = glossary_store.mod_glossary_as_terms(mod_id, mod.source_language)
    glossary_prompt = get_combined_glossary_prompt(
        base_glossary,
        mod_terms,
        source_lang=mod.source_language,
        target_lang=mod.target_language,
        allowed_categories=tc.BASE_GLOSSARY_PROMPT_CATEGORIES,
    )
    if not glossary_prompt:
        glossary_prompt = "No glossary available."

    provider = ClaudeProvider()
    provider.last_raw_responses = []
    translations, suggested_terms = provider.translate_batch(
        entries,
        mod.source_language,
        glossary_prompt,
        game_context=adapter.get_translation_context(),
        format_rules=adapter.get_format_preservation_rules(),
        style_examples=adapter.get_style_examples(mod.source_language),
        character_context=None,  # mod-context wiring deferred to Plan 2
        target_lang=mod.target_language,
    )

    raw = store.load_translations_raw(mod_id)
    now = datetime.now(timezone.utc).isoformat()
    keep = glossary_words(mod_terms["terms"])
    for key, text in translations.items():
        existing = raw.get(key, {})
        raw[key] = {
            # Split joined pinyin names ("Miaoying" -> "Miao Ying"), keeping the glossary's spelling ("Fu Yuanshan").
            "text": space_pinyin_names(src.get(key, ""), text, keep),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
            "provider": "claude",
        }
    store.save_translations_raw(mod_id, raw)

    api_responses_store.append(
        mod_id,
        {
            "timestamp": now,
            "kind": "translate-batch",
            "provider": "claude",
            **api_responses_store.provider_call_fields(getattr(provider, "last_raw_responses", [])),
            "keys_or_inputs": list(src.keys()),
        },
    )

    return {"translated": len(translations), "suggested_terms": suggested_terms}


@router.put("/mods/{mod_id}/mod-context")
def put_mod_context(mod_id: str, ctx: ModContext) -> dict:
    """Save mod context for a registered translation mod.

    Args:
        mod_id: Steam Workshop ID of the translation mod.
        ctx: Request body containing the context fields plus optional language overrides.

    Returns:
        `{"status": "ok"}` on success.

    Raises:
        HTTPException: 404 if `mod_id` is not registered.
    """
    _require_mod(mod_id)
    store.save_character_context(mod_id, ctx.model_dump())
    wh3_root = game_storage_path(store.GAME_ID)
    save_source_language_override(mod_id, ctx.source_language_override, storage_path=wh3_root)
    save_target_language_override(mod_id, ctx.target_language_override, storage_path=wh3_root)
    store.save_include_translated_names(mod_id, ctx.include_translated_names)
    return {"status": "ok"}


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# Plan 3 routes


class SnapshotCreateRequest(BaseModel):
    """Request body for `POST /mods/{mod_id}/snapshots`."""

    label: str = "manual save"


class GlossaryEntry(BaseModel):
    """One glossary entry. `english` is the key; `source` and `category` are the values."""

    english: str
    source: str = ""
    category: str = ""


class GlossaryApplyAllRequest(BaseModel):
    """Body for `POST /glossary/apply-all`."""

    old_english: str
    new_english: str


@router.post("/mods/{mod_id}/clear-translations")
def clear_translations(mod_id: str) -> dict:
    """Clear all English by writing empty-string overrides for every key that currently has text. Takes a pre-clear auto-snapshot.

    The empty overrides mask the user's `.loc.tsv` content so cleared rows show as untranslated. The `.loc.tsv` files are not touched here - the blanks
    are persisted to disk on the next Sync, mirroring how AI/manual translations land in translations.json first and sync later.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.

    Returns:
        `{"cleared": N}` - number of keys that had English and were overridden with an empty string.
    """
    mod = _require_mod(mod_id)
    snapshot_store.create_snapshot(mod_id, label="pre-clear-translations", kind="auto", local_source_dir=mod.local_source_dir)

    raw = store.load_translations_raw(mod_id)
    translation = _extract_translation_strings(mod)

    # Collect every key that currently shows English, from translations.json or the user's .loc.tsv files.
    keys_with_english: set[str] = set()
    for key, entry in raw.items():
        if isinstance(entry, dict) and entry.get("text"):
            keys_with_english.add(key)
    for rows in translation.values():
        for key, row in rows.items():
            if row.text:
                keys_with_english.add(key)

    # Write empty-string overrides so the cleared state masks the .loc.tsv content and shows as untranslated until synced.
    now = datetime.now(timezone.utc).isoformat()
    cleared: dict[str, dict] = {}
    for key in keys_with_english:
        existing = raw.get(key) if isinstance(raw.get(key), dict) else {}
        cleared[key] = {
            "text": "",
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
            "provider": existing.get("provider") or "manual",
        }
    store.save_translations_raw(mod_id, cleared)
    return {"cleared": len(cleared)}


@router.post("/mods/{mod_id}/sync")
def sync_changes(mod_id: str) -> dict:
    """Apply translations.json into the user's .loc.tsv files (surgical patch).

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.

    Returns:
        `{"per_file": {absolute_path: count}, "removed_orphans": N, "pack_built": bool, "pack_error": str | None}` describing the writeback, orphan pruning, and pack rebuild.
    """
    mod = _require_mod(mod_id)
    snapshot_store.create_snapshot(mod_id, label="pre-sync", kind="auto", local_source_dir=mod.local_source_dir)

    # Prune orphan strings (translated keys whose parent source no longer exists) from the user's .loc.tsv files and translations.json. The pre-sync
    # snapshot above keeps them recoverable.
    parent = _extract_all_parent_strings(mod)
    translation = _extract_translation_strings(mod)
    snapshot = store.load_parent_snapshot(mod_id)
    orphans = [d for d in compute_drift(parent=parent, translation=translation, snapshot=snapshot) if d.status == "orphan"]
    removed_orphans = 0
    if orphans:
        keys_by_file: dict[str, set[str]] = {}
        for d in orphans:
            keys_by_file.setdefault(d.source_filename, set()).add(d.key)
        removed_orphans = sum(remove_keys_from_loc_tsv(mod, keys_by_file).values())
        orphan_keys = {d.key for d in orphans}
        raw = store.load_translations_raw(mod_id)
        pruned = {k: v for k, v in raw.items() if k not in orphan_keys}
        if len(pruned) != len(raw):
            store.save_translations_raw(mod_id, pruned)

    drift = get_strings(mod_id)
    per_file = sync_translations_to_loc_tsv(mod, drift)

    # Keys whose source drifted keep their old snapshot entry so they stay changed. Update Changed and Accept re-baseline those explicitly.
    store.save_parent_snapshot(mod_id, refresh_unchanged(store.load_parent_snapshot(mod_id), parent))

    # Rebuild the published .pack from the freshly written loose files. Non-fatal: the writeback already succeeded, so any
    # build problem is reported as a warning rather than failing the sync. Skipped when nothing was written.
    pack_built = False
    pack_error: str | None = None
    if sum(per_file.values()) > 0:
        try:
            rpfm = resolve_rpfm_cli_path()
            if rpfm is None:
                raise RpfmNotConfiguredError("RPFM not configured; set SWMT_TW3_RPFM_CLI_PATH or place rpfm_cli.exe in SWMT_TW3_HELPER_PATH")
            content_dir = tw3_workshop_content_dir(mod_id)
            if content_dir is None:
                raise PackBuildError("TW3 Steam library drive is not configured")
            build_translation_pack(mod, rpfm_cli_path=rpfm, workshop_content_dir=content_dir)
            pack_built = True
        except PackBuildError as exc:
            pack_error = str(exc)

    return {"per_file": per_file, "removed_orphans": removed_orphans, "pack_built": pack_built, "pack_error": pack_error}


class AcceptChangedRequest(BaseModel):
    """Body of `POST /mods/{mod_id}/accept-changed`."""

    # Keys to accept. None accepts every currently changed key.
    keys: list[str] | None = None


@router.post("/mods/{mod_id}/accept-changed")
def accept_changed(mod_id: str, req: AcceptChangedRequest) -> dict:
    """Accept the current source text as the baseline for changed rows, keeping their existing translations.

    Args:
        mod_id: Steam Workshop ID of the translation mod.
        req: The keys to accept, or `None` for every changed row.

    Returns:
        `{"accepted": N}` where N counts the rows re-baselined.

    Raises:
        HTTPException: 404 if `mod_id` is not registered.
    """
    mod = _require_mod(mod_id)
    parent = _extract_all_parent_strings(mod)
    snapshot = store.load_parent_snapshot(mod_id)
    drift = compute_drift(parent=parent, translation=_extract_translation_strings(mod), snapshot=snapshot)
    changed = {r.key for r in drift if r.status == "stale"}
    keys = changed if req.keys is None else changed & set(req.keys)
    if keys:
        store.save_parent_snapshot(mod_id, accept_keys(snapshot, parent, keys))
    return {"accepted": len(keys)}


@router.get("/mods/{mod_id}/snapshots")
def get_snapshots(mod_id: str) -> list[dict]:
    """Return snapshot metadata for a mod, newest first.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.

    Returns:
        List of `{ulid, created_at, label, kind}` entries; empty when no snapshots exist.
    """
    _require_mod(mod_id)
    return snapshot_store.list_snapshots(mod_id)


@router.post("/mods/{mod_id}/snapshots")
def post_snapshot(mod_id: str, req: SnapshotCreateRequest) -> dict:
    """Create a manual snapshot of the mod's full state.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.
        req: Request body with the human-readable snapshot label.

    Returns:
        `{"ulid": str, "label": str, "kind": "manual"}` for the new snapshot.
    """
    mod = _require_mod(mod_id)
    sid = snapshot_store.create_snapshot(mod_id, label=req.label, kind="manual", local_source_dir=mod.local_source_dir)
    return {"ulid": sid, "label": req.label, "kind": "manual"}


@router.post("/mods/{mod_id}/snapshots/{sid}/restore")
def post_restore_snapshot(mod_id: str, sid: str) -> dict:
    """Restore a mod's state from a snapshot. Auto-creates a pre-restore snapshot first.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.
        sid: ULID of the snapshot to restore.

    Returns:
        `{"status": "ok"}` on success.

    Raises:
        HTTPException: 404 when the snapshot does not exist.
    """
    mod = _require_mod(mod_id)
    try:
        snapshot_store.restore_snapshot(mod_id, sid, local_source_dir=mod.local_source_dir)
    except FileNotFoundError:
        raise HTTPException(404, f"snapshot {sid} not found")
    return {"status": "ok"}


@router.delete("/mods/{mod_id}/snapshots/{sid}")
def delete_snapshot_route(mod_id: str, sid: str) -> dict:
    """Delete one snapshot from a mod's history.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.
        sid: ULID of the snapshot to delete.

    Returns:
        `{"status": "ok"}` on success.
    """
    _require_mod(mod_id)
    snapshot_store.delete_snapshot(mod_id, sid)
    return {"status": "ok"}


@router.get("/glossary")
def get_base_glossary() -> dict:
    """Return the WH3 base-game terminology glossary.

    Returns:
        `{"terms": {...}}`; empty when the glossary has not been built yet.
    """
    return glossary_store.load_base_glossary()


@router.get("/mods/{mod_id}/glossary")
def get_glossary(mod_id: str) -> dict:
    """Return the per-mod glossary.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.

    Returns:
        Glossary dict keyed by English term.
    """
    _require_mod(mod_id)
    return glossary_store.load_glossary(mod_id)


@router.post("/mods/{mod_id}/glossary")
def post_glossary_term(mod_id: str, entry: GlossaryEntry) -> dict:
    """Add or replace a glossary entry.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.
        entry: Glossary entry with `english`, `source`, `category`.

    Returns:
        `{"status": "ok"}` on success.
    """
    _require_mod(mod_id)
    glossary_store.add_term(mod_id, entry.model_dump())
    return {"status": "ok"}


def _rename_in_translations(mod, mod_id: str, source: str, old_english: str, new_english: str) -> int:
    """Rename `old_english` to `new_english` in every translation whose source text contains `source`.

    Rows are read from the translation pack with translations.json overlaid, so a translation that is only in the pack is renamed too. Changed rows are
    saved to translations.json and show as pending until the next Sync.

    Args:
        mod: The resolved `WH3TranslationMod`.
        mod_id: Steam Workshop ID of the WH3 translation mod.
        source: The term's source text. Nothing is renamed when it is empty.
        old_english: The English to replace, matched as a whole term.
        new_english: The English to put in its place.

    Returns:
        The number of translations that changed.
    """
    if not source or old_english == new_english:
        return 0
    parent = _extract_all_parent_strings(mod)
    drift = compute_drift(parent=parent, translation=_extract_translation_strings(mod), snapshot=store.load_parent_snapshot(mod_id))
    raw = store.load_translations_raw(mod_id)

    now = datetime.now(timezone.utc).isoformat()
    changed = 0
    for row in _overlay_translations(drift, raw):
        if not row.translation_text or source not in (row.parent_text or ""):
            continue
        new_text, count = replace_whole_term(row.translation_text, old_english, new_english)
        if count == 0:
            continue
        existing = raw.get(row.key) if isinstance(raw.get(row.key), dict) else {}
        raw[row.key] = {"text": new_text, "created_at": existing.get("created_at") or now, "updated_at": now, "provider": existing.get("provider") or row.provider or "manual"}
        changed += 1
    if changed:
        store.save_translations_raw(mod_id, raw)
    return changed


@router.put("/mods/{mod_id}/glossary/{english}")
def put_glossary_term(mod_id: str, english: str, entry: GlossaryEntry) -> dict:
    """Update (and optionally rename) a glossary entry.

    A new English also replaces the old English in every translation whose source contains the term's old source, after a restorable snapshot.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.
        english: Existing English term key to update.
        entry: New `{english, source, category}` values. A different `english` renames the entry.

    Returns:
        `{"status": "ok", "replaced": N}`, where N counts the translations the rename changed.
    """
    mod = _require_mod(mod_id)
    old_source = (glossary_store.load_glossary(mod_id).get(english) or {}).get("source", "")
    renaming = bool(old_source) and entry.english != english
    if renaming:
        snapshot_store.create_snapshot(mod_id, label=f"Before renaming glossary term {english}", kind="auto", local_source_dir=mod.local_source_dir)
    glossary_store.update_term(mod_id, english, entry.model_dump())
    replaced = _rename_in_translations(mod, mod_id, old_source, english, entry.english) if renaming else 0
    return {"status": "ok", "replaced": replaced}


@router.delete("/mods/{mod_id}/glossary")
def delete_all_glossary_terms(mod_id: str) -> dict:
    """Remove every term from a mod's glossary, taking a restorable snapshot first when there is anything to delete.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.

    Returns:
        `{"status": "success", "deleted": N}` with the number of terms removed.
    """
    mod = _require_mod(mod_id)
    if glossary_store.load_glossary(mod_id):
        snapshot_store.create_snapshot(mod_id, label="Before deleting all glossary terms", kind="auto", local_source_dir=mod.local_source_dir)
    deleted = glossary_store.delete_all_terms(mod_id)
    return {"status": "success", "deleted": deleted}


@router.delete("/mods/{mod_id}/glossary/{english}")
def delete_glossary_term(mod_id: str, english: str) -> dict:
    """Delete a glossary entry by English term.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.
        english: English term key to delete.

    Returns:
        `{"status": "ok"}` on success.
    """
    _require_mod(mod_id)
    glossary_store.delete_term(mod_id, english)
    return {"status": "ok"}


@router.post("/mods/{mod_id}/glossary/apply-all")
def post_glossary_apply_all(mod_id: str, req: GlossaryApplyAllRequest) -> dict:
    """Word-boundary find-and-replace `old_english` -> `new_english` across all translations.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.
        req: Request body with `old_english` and `new_english`.

    Returns:
        `{"replaced": N}` - total count of substitutions performed.
    """
    mod = _require_mod(mod_id)
    snapshot_store.create_snapshot(mod_id, label=f"pre-apply-all rename {req.old_english}", kind="auto", local_source_dir=mod.local_source_dir)
    count = glossary_store.apply_term_rename(mod_id, req.old_english, req.new_english)
    return {"replaced": count}


@router.get("/mods/{mod_id}/api-responses")
def get_api_responses(mod_id: str) -> list[dict]:
    """List all API response entries for a mod, newest first.

    Args:
        mod_id: Steam Workshop ID of the WH3 translation mod.

    Returns:
        List of audit entries (newest first); empty when no log exists.
    """
    _require_mod(mod_id)
    return api_responses_store.list_entries(mod_id)


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# Mod folder open helper


@router.post("/mods/{mod_id}/open-folder")
def open_mod_folder(mod_id: str) -> dict:
    """Open the mod's local source directory in the OS file explorer.

    Args:
        mod_id: Steam Workshop ID of the translation mod.

    Returns:
        `{"status": "ok"}` when the explorer launches successfully.

    Raises:
        HTTPException: 404 when the mod is not registered or the local source dir does not exist.
    """
    mod = _require_mod(mod_id)
    folder = mod.local_source_dir
    if not folder.exists():
        raise HTTPException(404, f"local source dir not found: {folder}")
    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(folder)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])
    return {"status": "ok"}


@router.post("/mods/{mod_id}/open-source-file/{filename}")
def open_source_file(mod_id: str, filename: str) -> dict:
    """Open one of the mod's source `.loc.tsv` files in the OS default application.

    Resolves the normalized parent filename to the user's actual on-disk file (which may carry the mod prefix) and opens it.

    Args:
        mod_id: Steam Workshop ID of the translation mod.
        filename: The normalized `.loc.tsv` filename shown in the Source column (e.g. `"units.loc.tsv"`).

    Returns:
        `{"status": "ok", "path": <resolved path>}` when the file opens.

    Raises:
        HTTPException: 404 when the mod is not registered or the source file cannot be found.
    """
    mod = _require_mod(mod_id)
    target = _find_existing_user_file(mod, filename)
    if target is None or not target.is_file():
        raise HTTPException(404, f"source file not found: {filename}")
    if sys.platform == "win32":
        os.startfile(target)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
    return {"status": "ok", "path": str(target)}
