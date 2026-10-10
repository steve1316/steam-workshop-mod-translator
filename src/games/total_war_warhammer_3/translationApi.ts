import type {
    Glossary,
    TermSuggestion,
    WH3ApiResponseEntry,
    WH3DriftRow,
    WH3DriftStatus,
    WH3GlossaryEntry,
    WH3ModContext,
    WH3RescanSummary,
    WH3SnapshotMeta,
    WH3SyncResult,
    WH3TranslationModSummary,
} from "../../shared_types"
import { gameApi } from "../../api/games"
import type { TranslationPreview } from "../../components/TranslationConfirmModal"
import type { BatchDescriptor } from "../../hooks/useIterativeTranslation"
import { RegistryError } from "./api"

/** Preview response for a WH3 translation run: the `TranslationConfirmModal` data plus the flat `batch_plan` the iterative hook consumes. */
export type WH3TranslationPreview = TranslationPreview & {
    /** Total untranslated strings the run would cover. */
    total_strings: number
    /** Flat per-batch plan the `useIterativeTranslation` hook iterates over. */
    batch_plan: BatchDescriptor[]
    /** Present only when `total_strings` is 0 (e.g. "All strings already translated"). */
    message?: string
    /** Translate Names previews only: the run split into stages, in the order they run. */
    stages?: WH3NameStage[]
}

/** One stage of the Translate Names pass. Its names are reviewed into the glossary before the next stage runs. */
export type WH3NameStage = {
    /** Stable stage id, e.g. `units`. */
    id: string
    /** Display name, e.g. "Units & Lords". */
    label: string
    /** Name categories this stage covers, used to fetch only its names for review. */
    categories: string[]
    /** Untranslated names in this stage. */
    total_strings: number
    /** This stage's batches, in the order the iterative hook runs them. */
    batch_plan: BatchDescriptor[]
}

const api = gameApi("total_war_warhammer_3")

/**
 * List all WH3 translation mods registered in `WH3_TRANSLATION_MODS`.
 *
 * @returns Array of summary rows, one per translation mod.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function listTranslationMods(): Promise<WH3TranslationModSummary[]> {
    const res = await api.get("/translation/mods")
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Re-extract parent strings, re-read local translations, recompute drift.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param signal Optional abort signal that cancels the request, e.g. when the dashboard unmounts.
 * @returns Per-status counts and the scan timestamp.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function rescanMod(workshopId: string, signal?: AbortSignal): Promise<WH3RescanSummary> {
    const res = await api.post(`/translation/mods/${encodeURIComponent(workshopId)}/rescan`, undefined, { signal })
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Fetch drift rows for a mod, optionally filtered by status.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param status Optional drift status to filter by. Omitting returns all rows.
 * @returns Drift rows sorted by `(filename, key)`.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function fetchStrings(workshopId: string, status?: WH3DriftStatus): Promise<WH3DriftRow[]> {
    const path = `/translation/mods/${encodeURIComponent(workshopId)}/strings${status ? `?status=${status}` : ""}`
    const res = await api.get(path)
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Save a translation edit for one row. Writes into `translations.json`; does NOT
 * write back to the `.loc.tsv` file (known limitation from Plan 1).
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param key Localization key being edited.
 * @param text New translation text.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function saveString(workshopId: string, key: string, text: string): Promise<void> {
    const res = await api.put(`/translation/mods/${encodeURIComponent(workshopId)}/strings/${encodeURIComponent(key)}`, { text })
    if (!res.ok) throw await registryError(res)
}

/**
 * Fetch the per-mod context used to enrich LLM translation prompts.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @returns Mod context (`source_game`, `character_name`, `background`).
 * @throws `RegistryError` On any non-2xx response.
 */
export async function fetchModContext(workshopId: string): Promise<WH3ModContext> {
    const res = await api.get(`/translation/mods/${encodeURIComponent(workshopId)}/mod-context`)
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Save the per-mod context.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param ctx Mod context to persist.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function saveModContext(workshopId: string, ctx: WH3ModContext): Promise<void> {
    const res = await api.put(`/translation/mods/${encodeURIComponent(workshopId)}/mod-context`, ctx)
    if (!res.ok) throw await registryError(res)
}

/**
 * Hand the given keys to Claude for batch translation. Source text is looked up from the most
 * recent parent extraction. Results are persisted into `translations.json` server-side.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param keys Localization keys to translate.
 * @returns Count of translated rows and any suggested glossary terms.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function translateBatch(workshopId: string, keys: string[]): Promise<{ translated: number; suggested_terms: TermSuggestion[] }> {
    const res = await api.post(`/translation/mods/${encodeURIComponent(workshopId)}/translate`, { keys })
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Preview a translation run: the prompts, cost estimate, and batch plan for every untranslated row. Mirrors Chrono Ark's `/translate/preview` so the
 * shared `TranslationConfirmModal` and `useIterativeTranslation` hook drive WH3 identically.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param provider Optional provider override (defaults to the server's configured provider).
 * @param scope `"all"` (default) previews every untranslated row; `"names"` previews only name strings for the Translate-Names-first pass,
 * `"changed"` previews only changed rows for Update Changed.
 * @returns The preview payload, including `batch_plan`. When nothing is untranslated, `total_strings` is 0.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function previewTranslation(workshopId: string, provider?: string, scope: "all" | "names" | "changed" = "all"): Promise<WH3TranslationPreview> {
    const res = await api.post("/translate/preview", { mod_id: workshopId, provider, scope })
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Accept the current source text as the baseline for changed rows, keeping their translations.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param keys Keys to accept. Leave out to accept every changed row.
 * @returns How many rows were accepted.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function acceptChanged(workshopId: string, keys?: string[]): Promise<number> {
    const res = await api.post(`/translation/mods/${encodeURIComponent(workshopId)}/accept-changed`, { keys: keys ?? null })
    if (!res.ok) throw await registryError(res)
    return (await res.json()).accepted
}

/**
 * Build glossary suggestions from the mod's translated name rows that the glossary does not cover yet, and persist them for review.
 * @param workshopId - Steam Workshop ID of the translation mod.
 * @param categories - Only suggest these name categories (one Translate Names stage). Leave out to suggest every category.
 * @returns The newly persisted name suggestions (may be empty).
 * @throws `RegistryError` On any non-2xx response.
 */
export async function loadNameSuggestions(workshopId: string, categories?: string[]): Promise<TermSuggestion[]> {
    const res = await api.post("/translate/name-suggestions", categories ? { mod_id: workshopId, categories } : { mod_id: workshopId })
    if (!res.ok) throw await registryError(res)
    const body = await res.json()
    return body.suggestions ?? []
}

/**
 * Apply translations.json into the user's `.loc.tsv` files via surgical patch.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @returns The full `WH3SyncResult`: per-file key counts, orphan removal tally, and pack-rebuild outcome.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function syncChanges(workshopId: string): Promise<WH3SyncResult> {
    const res = await api.post(`/translation/mods/${encodeURIComponent(workshopId)}/sync`)
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Wipe every `text` field on translations.json for this mod.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @returns Count of rows that had their text wiped.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function clearTranslations(workshopId: string): Promise<{ cleared: number }> {
    const res = await api.post(`/translation/mods/${encodeURIComponent(workshopId)}/clear-translations`)
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Fetch snapshot metadata for a mod (newest first).
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @returns Snapshot metadata, sorted newest first.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function listSnapshots(workshopId: string): Promise<WH3SnapshotMeta[]> {
    const res = await api.get(`/translation/mods/${encodeURIComponent(workshopId)}/snapshots`)
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Create a manual snapshot of the mod's current state.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param label Human-readable label for the snapshot.
 * @returns Metadata of the new snapshot.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function createSnapshot(workshopId: string, label: string): Promise<WH3SnapshotMeta> {
    const res = await api.post(`/translation/mods/${encodeURIComponent(workshopId)}/snapshots`, { label })
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Restore a mod's state from a snapshot. Backend auto-creates a pre-restore snapshot first.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param ulid Snapshot identifier to restore.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function restoreSnapshot(workshopId: string, ulid: string): Promise<void> {
    const res = await api.post(`/translation/mods/${encodeURIComponent(workshopId)}/snapshots/${encodeURIComponent(ulid)}/restore`)
    if (!res.ok) throw await registryError(res)
}

/**
 * Delete one snapshot from a mod's history.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param ulid Snapshot identifier to delete.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function deleteSnapshot(workshopId: string, ulid: string): Promise<void> {
    const res = await api.delete(`/translation/mods/${encodeURIComponent(workshopId)}/snapshots/${encodeURIComponent(ulid)}`)
    if (!res.ok) throw await registryError(res)
}

/**
 * Fetch the per-mod glossary.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @returns Map from English term to `{source, category}`.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function loadGlossary(workshopId: string): Promise<Record<string, { source: string; category: string }>> {
    const res = await api.get(`/translation/mods/${encodeURIComponent(workshopId)}/glossary`)
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Fetch the WH3 base-game terminology glossary.
 *
 * @returns The base glossary as `{ terms: Record<string, GlossaryTerm> }`.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function fetchBaseGlossary(): Promise<Glossary> {
    const res = await api.get("/translation/glossary")
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Add or replace one glossary entry.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param entry Glossary entry to persist.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function addGlossaryTerm(workshopId: string, entry: WH3GlossaryEntry): Promise<void> {
    const res = await api.post(`/translation/mods/${encodeURIComponent(workshopId)}/glossary`, entry)
    if (!res.ok) throw await registryError(res)
}

/**
 * Update one glossary entry. A new English also renames the old English in translations whose source contains the term's source.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param oldEnglish The current English key.
 * @param entry New entry (may have a different `english` value to trigger rename).
 * @returns How many translations the rename changed.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function updateGlossaryTerm(workshopId: string, oldEnglish: string, entry: WH3GlossaryEntry): Promise<{ replaced: number }> {
    const res = await api.put(`/translation/mods/${encodeURIComponent(workshopId)}/glossary/${encodeURIComponent(oldEnglish)}`, entry)
    if (!res.ok) throw await registryError(res)
    const body = await res.json().catch(() => ({}))
    return { replaced: typeof body?.replaced === "number" ? body.replaced : 0 }
}

/**
 * Delete one glossary entry by English term.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param english English term to delete.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function deleteGlossaryTerm(workshopId: string, english: string): Promise<void> {
    const res = await api.delete(`/translation/mods/${encodeURIComponent(workshopId)}/glossary/${encodeURIComponent(english)}`)
    if (!res.ok) throw await registryError(res)
}

/**
 * Delete every glossary entry for the mod. The backend saves a History snapshot first, so the terms can be restored.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @returns How many terms were deleted.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function deleteAllGlossaryTerms(workshopId: string): Promise<{ status: string; deleted: number }> {
    const res = await api.delete(`/translation/mods/${encodeURIComponent(workshopId)}/glossary`)
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Word-boundary find-and-replace `oldEnglish` -> `newEnglish` across all translations for the mod.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param oldEnglish Term currently used in translations.json.
 * @param newEnglish Term to substitute in.
 * @returns Count of replacements performed.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function glossaryApplyAll(workshopId: string, oldEnglish: string, newEnglish: string): Promise<{ replaced: number }> {
    const res = await api.post(`/translation/mods/${encodeURIComponent(workshopId)}/glossary/apply-all`, { old_english: oldEnglish, new_english: newEnglish })
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Ask Claude for glossary refinements and save them as pending suggestions for review. Logs to api_responses.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @returns How many new suggestions were saved.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function glossarySuggestEdits(workshopId: string): Promise<{ status: string; new: number }> {
    const res = await api.post(`/mods/${encodeURIComponent(workshopId)}/glossary/suggest-edits`)
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Fetch the audit log of recent Claude API calls (newest first, cap 20).
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @returns Recent audit entries.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function listApiResponses(workshopId: string): Promise<WH3ApiResponseEntry[]> {
    const res = await api.get(`/translation/mods/${encodeURIComponent(workshopId)}/api-responses`)
    if (!res.ok) throw await registryError(res)
    return res.json()
}

/**
 * Open the mod's local source directory in the OS file explorer.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function openModFolder(workshopId: string): Promise<void> {
    const res = await api.post(`/translation/mods/${encodeURIComponent(workshopId)}/open-folder`)
    if (!res.ok) throw await registryError(res)
}

/**
 * Open one of the mod's source `.loc.tsv` files in the OS default application.
 *
 * @param workshopId Steam Workshop ID of the translation mod.
 * @param filename The normalized `.loc.tsv` filename shown in the Source column.
 * @throws `RegistryError` On any non-2xx response.
 */
export async function openSourceFile(workshopId: string, filename: string): Promise<void> {
    const res = await api.post(`/translation/mods/${encodeURIComponent(workshopId)}/open-source-file/${encodeURIComponent(filename)}`)
    if (!res.ok) throw await registryError(res)
}

async function registryError(res: Response): Promise<RegistryError> {
    let detail = res.statusText
    try {
        const body = await res.json()
        if (typeof body.detail === "string") detail = body.detail
    } catch {
        /* ignore */
    }
    return new RegistryError(res.status, detail, null)
}
