/**
 * Shared TypeScript types mirroring the backend API response shapes.
 *
 * These types are consumed across the React frontend to ensure type-safe
 * communication with the FastAPI backend. Keep them in sync with the
 * Pydantic models defined in `backend/web_server.py`.
 */

import type { RowStatus } from "./utils/stringFilters"

/**
 * Summary status for a single mod, used in the dashboard listing.
 */
export type ModStatus = {
    /** Unique identifier for the mod (derived from its folder name). */
    id: string
    /** Human-readable mod name. */
    name: string
    /** Mod author name. */
    author: string
    /** Whether the mod contains CSV localization files. */
    has_csv: boolean
    /** Whether the mod contains a DLL with embedded strings. */
    has_dll: boolean
    /** Total number of localizable strings in the mod. */
    total: number
    /** Number of strings that have an English translation (user-translated + untouched). */
    translated: number
    /** Number of strings still missing an English translation. */
    untranslated: number
    /** Number of strings translated by the user/tool. */
    user_translated: number
    /** Number of strings that already had English in the raw mod files. */
    untouched: number
    /** Translation progress as a 0-100 percentage. */
    percentage: number
    /** ISO timestamp of when translation data was last modified. */
    last_updated: string
    /** Optional URL to the mod's Steam Workshop or external page. */
    url?: string
    /** Optional URL/path to the mod's preview image; null if none exists. */
    preview_image?: string | null
    /** Whether the mod has pending translation changes that need to be synced to CSV files. */
    has_changes: boolean
}

/**
 * A single localizable string extracted from a mod's data files.
 *
 * Each LocString represents one translatable entry (e.g., a skill name,
 * character dialogue, item description) along with its source-language
 * text and current English translation state.
 */
export type LocString = {
    /** Unique key identifying this string within the mod (e.g., "Skill/FireBolt_Name"). */
    key: string
    /** Category/type of the string (e.g., "Character", "Skill", "Buff"). */
    type: string
    /** Descriptor column from the CSV, providing context about where the string appears. */
    desc: string
    /** Original source-language text (e.g., Korean or Chinese). */
    source: string
    /** Detected source language code (e.g., "Korean", "Chinese"), or null if undetectable. */
    source_lang: string | null
    /** Current English translation text; empty string if not yet translated. */
    english: string
    /** Whether this string has been translated to English. */
    is_translated: boolean
    /** The original English text from the mod files before any user edits, used for diffing. */
    original_english: string
    /** Whether this string has been synced (exported) to the mod's CSV files. */
    is_synced: boolean
    /** Whether this string already had English in the raw mod files and has not been modified. */
    is_untouched: boolean
    /** The English value at the time of sync; used to restore synced status if the user re-enters it. */
    synced_english: string
    /** The source file (CSV/JSON/DLL) this string was extracted from. */
    source_file: string
    /** Which translation provider produced this translation (e.g., "claude", "ollama", "manual"), or empty if unknown. */
    translated_by: string
    /** If non-empty, this string cannot be translated via CSV and this field explains why. */
    untranslatable_reason: string
}

/**
 * Aggregated translation statistics across all mods, shown on the Statistics page.
 */
export type Stats = {
    /** Number of entries stored in the translation memory database. */
    tm_entries: number
    /** Cumulative number of translation memory matches applied. */
    tm_hits: number
    /** Total number of mods detected in the game's mod directory. */
    total_mods: number
    /** Overall translation progress across all mods (0-100 percentage). */
    global_progress: number
    /** Total number of localizable strings across all mods. */
    total_strings: number
}

/**
 * A single term in the glossary, mapping a canonical English term to its
 * source-language equivalents. Used to ensure consistent translations of
 * recurring game terminology (character names, skill names, mechanics, etc.).
 */
export type GlossaryTerm = {
    /** Canonical English translation text. */
    english?: string
    /** Semantic category (e.g., "characters", "skills", "mechanics", "custom"). */
    category: string
    /** Localization key that originally defined this term, or empty string for manual entries. */
    key: string
    /** CSV filename this term was extracted from, or empty string for manual/seed entries. */
    source_file?: string
    /** Maps source language name to its native text (e.g., { "Korean": "화염구" }). */
    source_mappings: Record<string, string>
    /** ISO timestamp of when this term was first created. */
    created_at?: string
    /** ISO timestamp of when this term was last modified. */
    updated_at?: string
}

/**
 * The complete glossary for a mod (or the global glossary), keyed by
 * a unique identifier (CSV key for base game terms, source text for mod terms).
 */
export type Glossary = {
    /** Map from English term to its glossary entry. */
    terms: Record<string, GlossaryTerm>
}

/**
 * An AI-generated suggestion for a new glossary term, returned by the
 * backend's term-suggestion endpoint.
 */
export type TermSuggestion = {
    /** Suggested canonical English term. */
    english: string
    /** Original source-language text for this term. */
    source: string
    /** Language of the source text (e.g., "Korean"). */
    source_lang: string
    /** Suggested category for the term (e.g., "characters", "skills"). */
    category: string
    /** Human-readable explanation of why this term was suggested. */
    reason: string
    /** If this is an edit suggestion, the original term being replaced. */
    edit_of?: string
}

/**
 * Full detail payload for a single mod, returned when the user opens a
 * mod for editing. Includes all extractable strings and metadata.
 */
export type ModDetail = {
    /** Unique identifier for the mod. */
    id: string
    /** Human-readable mod name. */
    name: string
    /** Mod author name. */
    author: string
    /** URL to the mod's external page, or null if unavailable. */
    url: string | null
    /** URL/path to the mod's preview image, or null if unavailable. */
    preview_image: string | null
    /** All localizable strings extracted from the mod. */
    strings: LocString[]
}

// //////////////////////////////////////////////////////////////////////////////////////////////////
// //////////////////////////////////////////////////////////////////////////////////////////////////
// Total War: Warhammer III - translation mod types
//
// Mirrors the response shapes from `backend/games/total_war_warhammer_3/routes/translation.py`.

/** Summary row returned by `GET /api/games/total_war_warhammer_3/translation/mods`. */
export interface WH3TranslationModSummary {
    /** Steam Workshop ID of the published translation mod. */
    workshop_id: string
    /** Human-readable display name shown in the dashboard. */
    display_name: string
    /** Steam Workshop IDs of the parent mod(s) whose strings this translation covers. */
    parent_workshop_ids: string[]
    /** Absolute filesystem path to the local `.loc.tsv` source folder. */
    local_source_dir: string
    /** Source language of the parent mod's text (e.g. `"Chinese"`). */
    source_language: string
    /** Target language of the translation (e.g. `"English"`). */
    target_language: string
    /** Relative URL serving the parent mod's preview `.png`. `null` when no preview file is reachable. */
    preview_image_url: string | null
}

/** Status of one row in the drift report. */
export type WH3DriftStatus = "translated" | "untranslated" | "stale" | "orphan"

/**
 * One row in `GET /translation/mods/{id}/strings` (extended for Plan 3b).
 */
export interface WH3DriftRow {
    /** Normalized parent `.loc.tsv` filename this row belongs to. */
    source_filename: string
    /** Localization key. */
    key: string
    /** Current parent source text. `null` only when `status === "orphan"`. */
    parent_text: string | null
    /** Current translation text. `null` only when `status === "untranslated"`. */
    translation_text: string | null
    /** Drift status. */
    status: WH3DriftStatus
    /** Who/what produced the current translation. `null` when untranslated. */
    provider: string | null
    /** Canonical six-state status (synced/untouched/pending/changed/missing/untranslatable) for the shared status chips. `null` if the backend did not supply it. */
    canonical_status: RowStatus | null
    /** The on-disk `.loc.tsv` translation text (the previously-synced value), for the prev-translation strikethrough. `null` when none on disk. */
    previous_text: string | null
}

/** Summary returned by `POST /api/games/total_war_warhammer_3/translation/mods/{id}/rescan`. */
export interface WH3RescanSummary {
    /** Workshop ID that was rescanned. */
    mod_id: string
    /** Per-status row counts. */
    counts: Record<WH3DriftStatus, number>
    /** Zero-filled tally of the canonical six-state model, mirroring each row's `canonical_status`. */
    canonical_counts: Record<RowStatus, number>
    /** ISO timestamp of when the rescan completed. */
    scanned_at: string
    /** True when translations.json holds entries whose text differs from the user's `.loc.tsv` files. */
    has_unsynced_changes: boolean
    /** True when any of `source_game / character_name / background` in mod-context is non-empty. */
    has_mod_context: boolean
}

/** Body of `GET/PUT /api/games/total_war_warhammer_3/translation/mods/{id}/mod-context`. */
export interface WH3ModContext {
    /** Source-game identifier (e.g. `"WH3"`). Free-form. */
    source_game: string
    /** Short name or role (e.g. `"Zerooz Cathy"`). Free-form. */
    character_name: string
    /** Multi-line background / lore that gets injected into the LLM prompt. */
    background: string
    /** Per-mod override for source language. `null` falls back to the registry default. */
    source_language_override: string | null
    /** Per-mod override for target language. `null` falls back to the registry default. */
    target_language_override: string | null
    /** When true, translated names that are not in the glossary are also sent with every prompt. Missing on older backends. */
    include_translated_names?: boolean
}

/** One row in `GET /translation/mods/{id}/glossary` response (also used in POST/PUT bodies). */
export interface WH3GlossaryEntry {
    /** Canonical English term (also the dict key on the wire). */
    english: string
    /** Source-language form (e.g. Chinese characters). */
    source: string
    /** Category label (e.g. `"factions"`). */
    category: string
}

/** Metadata for one entry in `GET /translation/mods/{id}/snapshots`. */
export interface WH3SnapshotMeta {
    /** Sortable-by-time snapshot identifier. */
    ulid: string
    /** ISO timestamp when the snapshot was taken. */
    created_at: string
    /** Human-readable description (e.g. `"pre-clear-translations"`). */
    label: string
    /** `"auto"` (snapshot taken by the backend before a destructive op) or `"manual"` (user-initiated). */
    kind: "auto" | "manual"
}

/** One entry in `GET /translation/mods/{id}/api-responses`. */
export interface WH3ApiResponseEntry {
    /** ISO timestamp when the call returned. */
    timestamp: string
    /** Kind of API call: `"translate-batch"`, `"scan-terms"`, or `"suggest-edits"`. */
    kind: "translate-batch" | "scan-terms" | "suggest-edits"
    /** Provider that fielded the call (e.g. `"claude"`). */
    provider: string
    /** Model identifier (e.g. `"claude-sonnet-5"`), or `"claude"` for calls that do not report one. */
    model: string
    /** Token usage in. `null` when the provider does not report it. */
    input_tokens: number | null
    /** Token usage out. */
    output_tokens: number | null
    /** Estimated cost in USD. */
    cost_usd: number | null
    /** Keys (translate-batch) or input strings (scan-terms / suggest-edits) sent to the provider. */
    keys_or_inputs: string[]
    /** Raw text the provider returned. A failed call leads with an `ERROR:` line. */
    raw_response: string
}

/** Response body of `POST /translation/mods/{id}/sync`. */
export interface WH3SyncResult {
    /** Map from absolute `.loc.tsv` path to number of keys written into it. */
    per_file: Record<string, number>
    /** How many orphan rows (translated keys with no parent source) were pruned from the mod during this sync. */
    removed_orphans: number
    /** True when the mod's `.pack` was rebuilt from the synced loose files during this sync. */
    pack_built: boolean
    /** Human-readable reason the pack rebuild was not completed (failed, or not attempted); null when it succeeded or was not applicable. */
    pack_error: string | null
}
