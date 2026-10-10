import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"

import GlossarySuggestionModal from "../../../../components/GlossarySuggestionModal"
import TranslationConfirmModal from "../../../../components/TranslationConfirmModal"
import { API_BASE } from "../../../../config"
import { useIterativeTranslation } from "../../../../hooks/useIterativeTranslation"
import { useGameSlug } from "../../../useGameSlug"
import { StatusBadge } from "../../../../translation/StatusBadge"
import { TranslationPage } from "../../../../translation/TranslationPage"
import { usePendingSuggestions } from "../../../../translation/usePendingSuggestions"
import { TranslationToolbar } from "../../../../translation/TranslationToolbar"
import { SyncButton } from "../../../../translation/SyncButton"
import { BatchReviewBanner } from "../../../../translation/BatchReviewBanner"
import { ContextPanel, type ContextFields } from "../../../../translation/ContextPanel"
import SplitButton from "../../../../ui/SplitButton"
import { LanguageControls } from "../../../../translation/LanguageControls"
import { OpenFolderButton, PendingSyncPill, SteamLink } from "../../../../translation/TitleAdornments"
import { TranslationCell } from "../../../../translation/TranslationCell"
import { canonicalRowStyle } from "../../../../translation/rowStyle"
import { TRANSLATION_COLUMN_WIDTHS, translationColumns } from "../../../../translation/columns"
import HighlightText from "../../../../ui/HighlightText"
import LoadingState from "../../../../ui/LoadingState"
import type { RowStatus } from "../../../../utils/stringFilters"
import type { TermSuggestion, WH3DriftRow, WH3ModContext, WH3RescanSummary, WH3TranslationModSummary } from "../../../../shared_types"
import ApiResponsesModal from "../../components/ApiResponsesModal"
import HistoryModal from "../../components/HistoryModal"
import ModGlossaryModal from "../../components/ModGlossaryModal"
import {
    clearTranslations,
    fetchModContext,
    fetchStrings,
    listTranslationMods,
    loadGlossary,
    loadNameSuggestions,
    openModFolder,
    openSourceFile,
    previewTranslation,
    acceptChanged,
    rescanMod,
    saveModContext,
    saveString,
    syncChanges,
    type WH3NameStage,
    type WH3TranslationPreview,
} from "../../translationApi"
import { useConfirm } from "../../../../ui/useConfirm"

// Canonical status filter pills. WH3 never emits "untouched", but the pill is kept for parity with Chrono Ark.
const STATUS_FILTERS: Array<{ value: RowStatus | "all"; label: string }> = [
    { value: "all", label: "All" },
    { value: "missing", label: "Missing" },
    { value: "untouched", label: "Untouched" },
    { value: "pending", label: "Pending" },
    { value: "changed", label: "Changed" },
    { value: "synced", label: "Synced" },
]

type ModalKey = "glossary" | "responses" | "history" | "reset" | null

/** A staged Translate Names run in progress. */
type NamesRun = {
    /** Stages from the names preview, in run order. */
    stages: WH3NameStage[]
    /** Index of the stage that is running or was just reviewed. */
    index: number
    /** Provider the run was started with. */
    provider: string
    /** Names translated so far across all stages. */
    translated: number
}

/** The finished stage a review dialog or paused banner describes. */
type StageStep = {
    /** Zero-based index of the finished stage. */
    index: number
    /** Stages in the run. */
    total: number
    /** Finished stage's display name. */
    label: string
    /** Next stage's display name, or null when the finished stage was the last one. */
    nextLabel: string | null
}

/**
 * Describe the stage a names run just finished.
 *
 * @param run The names run.
 * @returns The finished stage's position and labels.
 */
function stageStep(run: NamesRun): StageStep {
    return { index: run.index, total: run.stages.length, label: run.stages[run.index].label, nextLabel: run.stages[run.index + 1]?.label ?? null }
}

type SortField = "status" | "provider" | "source_filename" | "key" | "parent_text" | "translation_text"

/**
 * Confirm text for WH3's Sync button.
 *
 * @param resync True for a re-sync with nothing new pending.
 * @returns The dialog body.
 */
const wh3SyncMessage = (resync: boolean) =>
    `${resync ? "Nothing has changed since the last sync. Re-syncing writes every translation again.\n\n" : ""}This will overwrite the mod's .loc.tsv files with your translations and rebuild the translation pack. Continue?`

const COLUMN_WIDTH_KEY = "wh3-translation-column-widths"

const DEFAULT_COLUMN_WIDTHS: Record<SortField, number> = {
    status: TRANSLATION_COLUMN_WIDTHS.status,
    provider: TRANSLATION_COLUMN_WIDTHS.mode,
    source_filename: TRANSLATION_COLUMN_WIDTHS.source,
    key: TRANSLATION_COLUMN_WIDTHS.key,
    parent_text: TRANSLATION_COLUMN_WIDTHS.original,
    translation_text: TRANSLATION_COLUMN_WIDTHS.translation,
}

/**
 * Per-mod WH3 translation review page. Renders the shared `<TranslationPage>` shell so it mirrors Chrono Ark 1-to-1: canonical status chips, the same
 * status-filter pills, header identity (preview / steam link / open-folder / pending-sync badge) and language controls, the toolbar groups, and the modals.
 * @returns The rendered page.
 */
const TranslationDetailsPage: React.FC = () => {
    const { workshopId = "" } = useParams<{ workshopId: string }>()
    const navigate = useNavigate()
    const slug = useGameSlug()

    const [mod, setMod] = useState<WH3TranslationModSummary | null>(null)
    const [progress, setProgress] = useState<WH3RescanSummary | null>(null)
    const [strings, setStrings] = useState<WH3DriftRow[]>([])
    const [filter, setFilter] = useState<RowStatus | "all">("all")
    const [search, setSearch] = useState("")
    const [loading, setLoading] = useState(true)
    const [banner, setBanner] = useState<{ type: "success" | "error"; message: string } | null>(null)
    const [openModal, setOpenModal] = useState<ModalKey>(null)
    const [showContext, setShowContext] = useState(false)
    const { suggestions, refresh: refreshSuggestions, scan, scanning } = usePendingSuggestions("total_war_warhammer_3", workshopId, setBanner)
    const [showSuggestions, setShowSuggestions] = useState(false)

    // Keep the Mod Glossary count current after suggestions are accepted.
    const refreshGlossaryCount = useCallback(() => {
        loadGlossary(workshopId)
            .then((d) => setGlossaryCount(Object.keys(d).length))
            .catch(() => {})
    }, [workshopId])
    const { confirm, confirmDialog } = useConfirm()
    const [showReviewModal, setShowReviewModal] = useState(false)
    // Translate Names runs one stage at a time. pendingScope drives the confirm-modal title. namesRunRef holds the active run (read in the batch effect,
    // not a dependency, so advancing it does not re-fire the effect). stageReview is the review for the stage that just finished, and pausedStep is a
    // finished stage whose review was closed. nameReviewSuggestions opens the Suggest from Translated Names review.
    const [pendingScope, setPendingScope] = useState<"all" | "names" | "changed">("all")
    const namesRunRef = useRef<NamesRun | null>(null)
    const [stageReview, setStageReview] = useState<{ step: StageStep; suggestions: TermSuggestion[] } | null>(null)
    const [pausedStep, setPausedStep] = useState<StageStep | null>(null)
    const [nameReviewSuggestions, setNameReviewSuggestions] = useState<TermSuggestion[] | null>(null)
    const [modContext, setModContext] = useState<WH3ModContext>({
        source_game: "",
        character_name: "",
        background: "",
        source_language_override: null,
        target_language_override: null,
    })
    const [glossaryCount, setGlossaryCount] = useState<number>(0)
    const [activeProvider, setActiveProvider] = useState<string>("claude")
    const [preview, setPreview] = useState<WH3TranslationPreview | null>(null)
    const [pendingProvider, setPendingProvider] = useState<string>("")
    const [sortConfig, setSortConfig] = useState<{ key: SortField; direction: "asc" | "desc" | null }>({ key: "key", direction: null })
    const [columnWidths, setColumnWidths] = useState<Record<SortField, number>>(() => {
        try {
            const raw = window.localStorage.getItem(COLUMN_WIDTH_KEY)
            if (raw) {
                const parsed = JSON.parse(raw) as Partial<Record<SortField, number>>
                return { ...DEFAULT_COLUMN_WIDTHS, ...parsed }
            }
        } catch {
            /* fall through */
        }
        return DEFAULT_COLUMN_WIDTHS
    })

    // Persist column widths whenever they change (the shared table reports drags via onResizeColumn).
    useEffect(() => {
        try {
            window.localStorage.setItem(COLUMN_WIDTH_KEY, JSON.stringify(columnWidths))
        } catch {
            /* ignore quota errors */
        }
    }, [columnWidths])

    const onResizeColumn = useCallback((field: string, width: number) => {
        setColumnWidths((prev) => ({ ...prev, [field]: width }))
    }, [])

    const loadStrings = useCallback(async () => {
        if (!workshopId) return
        const rows = await fetchStrings(workshopId)
        setStrings(rows)
    }, [workshopId])

    // Reload counts and rows after accepting a glossary suggestion under an edited English renamed existing translations.
    const reloadAfterRename = useCallback(async () => {
        try {
            setProgress(await rescanMod(workshopId))
            await loadStrings()
        } catch {
            /* keep the current table */
        }
    }, [workshopId, loadStrings])

    useEffect(() => {
        let cancelled = false
        ;(async () => {
            try {
                const [allMods, summary, ctx] = await Promise.all([listTranslationMods(), rescanMod(workshopId), fetchModContext(workshopId)])
                if (cancelled) return
                setMod(allMods.find((m) => m.workshop_id === workshopId) ?? null)
                setProgress(summary)
                setModContext(ctx)
                await loadStrings()
            } catch (e) {
                setBanner({ type: "error", message: `Failed to load: ${(e as Error).message}` })
            } finally {
                if (!cancelled) setLoading(false)
            }
        })()
        return () => {
            cancelled = true
        }
    }, [workshopId, loadStrings])

    useEffect(() => {
        let cancelled = false
        ;(async () => {
            try {
                const dict = await loadGlossary(workshopId)
                if (!cancelled) setGlossaryCount(Object.keys(dict).length)
            } catch {
                /* leave at 0 */
            }
        })()
        return () => {
            cancelled = true
        }
    }, [workshopId, openModal])

    const onRowSave = useCallback(
        async (key: string, text: string) => {
            try {
                await saveString(workshopId, key, text)
                setStrings((prev) =>
                    prev.map((r) =>
                        r.key === key
                            ? { ...r, translation_text: text, provider: "manual", status: r.status === "untranslated" || r.status === "stale" ? "translated" : r.status, canonical_status: "pending" }
                            : r
                    )
                )
                setBanner({ type: "success", message: `Saved ${key}` })
            } catch (e) {
                setBanner({ type: "error", message: `Save failed: ${(e as Error).message}` })
            }
        },
        [workshopId]
    )

    // Optimistically reflect each completed batch's translations in the table while the run continues.
    const onBatchTranslated = useCallback((translations: Record<string, string>) => {
        setStrings((prev) =>
            prev.map((r) => (translations[r.key] !== undefined ? { ...r, translation_text: translations[r.key], provider: "claude", status: "translated", canonical_status: "pending" } : r))
        )
    }, [])

    const { state: batchState, startTranslation, continueAfterReview, cancel: cancelTranslation } = useIterativeTranslation("total_war_warhammer_3", workshopId, onBatchTranslated)
    const isTranslating = batchState.phase === "translating"

    // Clicking Translate previews the run (prompts + cost + batch plan) and opens the confirm modal. Confirming starts the iterative batch loop.
    const handleTranslateClick = useCallback(
        async (provider?: string) => {
            const p = provider || activeProvider
            setPendingProvider(p)
            setPendingScope("all")
            try {
                const pv = await previewTranslation(workshopId, p)
                if (pv.total_strings === 0) {
                    setBanner({ type: "success", message: pv.message || "All strings are already translated." })
                    return
                }
                setPreview(pv)
            } catch (e) {
                setBanner({ type: "error", message: `Preview failed: ${(e as Error).message}` })
            }
        },
        [workshopId, activeProvider]
    )

    // Translate only the name strings (unit/skill/building/location/...) first. They are then reviewed into the glossary so the bulk run reuses them.
    const handleTranslateNamesClick = useCallback(
        async (provider?: string) => {
            const p = provider || activeProvider
            setPendingProvider(p)
            setPendingScope("names")
            try {
                const pv = await previewTranslation(workshopId, p, "names")
                if (pv.total_strings === 0) {
                    setBanner({ type: "success", message: pv.message || "All names are already translated." })
                    return
                }
                setPreview(pv)
            } catch (e) {
                setBanner({ type: "error", message: `Names preview failed: ${(e as Error).message}` })
            }
        },
        [workshopId, activeProvider]
    )

    // Update Changed previews only the changed rows and revises their previous translations.
    const handleUpdateChangedClick = useCallback(
        async (provider?: string) => {
            const p = provider || activeProvider
            setPendingProvider(p)
            setPendingScope("changed")
            try {
                const pv = await previewTranslation(workshopId, p, "changed")
                if (pv.total_strings === 0) {
                    setBanner({ type: "success", message: pv.message || "No changed strings to update." })
                    return
                }
                setPreview(pv)
            } catch (e) {
                setBanner({ type: "error", message: `Update preview failed: ${(e as Error).message}` })
            }
        },
        [workshopId, activeProvider]
    )

    const onAcceptChanged = useCallback(async () => {
        try {
            const n = await acceptChanged(workshopId)
            setBanner({ type: "success", message: `Accepted ${n} changed string${n === 1 ? "" : "s"}.` })
            await reloadAfterRename()
        } catch (e) {
            setBanner({ type: "error", message: `Accept failed: ${(e as Error).message}` })
        }
    }, [workshopId, reloadAfterRename])

    const onConfirmTranslate = useCallback(() => {
        if (!preview) return
        const provider = pendingProvider || activeProvider
        const stages = pendingScope === "names" ? preview.stages : undefined
        setPreview(null)
        setPausedStep(null)
        if (stages?.length) {
            namesRunRef.current = { stages, index: 0, provider, translated: 0 }
            startTranslation(provider, stages[0].batch_plan)
        } else {
            namesRunRef.current = null
            startTranslation(provider, preview.batch_plan ?? [], pendingScope === "changed" ? "update" : "translate")
        }
    }, [preview, pendingProvider, pendingScope, activeProvider, startTranslation])

    /**
     * End the names run and report how many names it translated.
     *
     * @param stoppedBefore The stage the user stopped before, or undefined when every stage ran.
     */
    const finishNamesRun = useCallback((stoppedBefore?: string) => {
        const translated = namesRunRef.current?.translated ?? 0
        namesRunRef.current = null
        const names = `${translated} name${translated === 1 ? "" : "s"}`
        setBanner({ type: "success", message: stoppedBefore ? `Translated ${names}. Stopped before ${stoppedBefore}.` : `Translated ${names}.` })
    }, [])

    // Start the names run's next stage, or finish the run after the last one.
    const advanceNamesRun = useCallback(() => {
        const run = namesRunRef.current
        if (!run) return
        if (run.index + 1 >= run.stages.length) {
            finishNamesRun()
            return
        }
        run.index += 1
        startTranslation(run.provider, run.stages[run.index].batch_plan)
    }, [startTranslation, finishNamesRun])

    // Stop a translation run. A names run is dropped too, so its stages do not continue.
    const onCancelTranslate = useCallback(() => {
        namesRunRef.current = null
        cancelTranslation()
    }, [cancelTranslation])

    // When the iterative run finishes (or errors), refresh counts + rows and surface a result banner.
    useEffect(() => {
        // A names run does not pause for per-batch provider suggestions. Each stage's names are reviewed once the stage finishes.
        if (batchState.phase === "reviewing" && namesRunRef.current) {
            continueAfterReview()
            return
        }
        if (batchState.phase === "complete") {
            const total = batchState.totalTranslated
            const run = namesRunRef.current
            ;(async () => {
                try {
                    const summary = await rescanMod(workshopId)
                    setProgress(summary)
                    await loadStrings()
                } catch {
                    /* ignore refresh errors */
                }
                if (!run) {
                    setBanner({ type: "success", message: `Translated ${total} strings` })
                    return
                }
                run.translated += total
                let names: TermSuggestion[] = []
                try {
                    names = await loadNameSuggestions(workshopId, run.stages[run.index].categories)
                } catch {
                    /* skip this stage's review */
                }
                // A stage with nothing new to review moves straight on to the next one.
                if (names.length > 0) {
                    refreshSuggestions()
                    setStageReview({ step: stageStep(run), suggestions: names })
                } else {
                    advanceNamesRun()
                }
            })()
        } else if (batchState.phase === "error") {
            namesRunRef.current = null
            setBanner({ type: "error", message: batchState.message })
        }
    }, [batchState, workshopId, loadStrings, continueAfterReview, advanceNamesRun, refreshSuggestions])

    // Suggest glossary terms from names that are already translated, without translating anything.
    const onSuggestFromNames = useCallback(async () => {
        try {
            const names = await loadNameSuggestions(workshopId)
            if (names.length === 0) {
                setBanner({ type: "success", message: "No new name terms found." })
                return
            }
            refreshSuggestions()
            setNameReviewSuggestions(names)
        } catch (e) {
            setBanner({ type: "error", message: `Name suggestions failed: ${(e as Error).message}` })
        }
    }, [workshopId, refreshSuggestions])

    const onSyncChanges = useCallback(async () => {
        try {
            const result = await syncChanges(workshopId)
            const fileCount = Object.keys(result.per_file).length
            const keyCount = Object.values(result.per_file).reduce((a, b) => a + b, 0)
            const summary = await rescanMod(workshopId)
            setProgress(summary)
            await loadStrings()
            const orphanNote = result.removed_orphans > 0 ? `, removed ${result.removed_orphans} orphan${result.removed_orphans !== 1 ? "s" : ""}` : ""
            const packNote = result.pack_built ? ", rebuilt pack" : result.pack_error ? ` (pack rebuild failed: ${result.pack_error})` : ""
            setBanner({ type: "success", message: `Synced ${keyCount} keys across ${fileCount} files${orphanNote}${packNote}` })
        } catch (e) {
            setBanner({ type: "error", message: `Sync failed: ${(e as Error).message}` })
        }
    }, [workshopId, loadStrings])

    const onClearEnglish = useCallback(async () => {
        const ok = await confirm({ title: "Clear English", message: "Clear all translation text? An auto-snapshot is taken before clearing.", confirmLabel: "Clear", variant: "danger" })
        if (!ok) return
        try {
            const result = await clearTranslations(workshopId)
            const summary = await rescanMod(workshopId)
            setProgress(summary)
            await loadStrings()
            setBanner({ type: "success", message: `Cleared ${result.cleared} translations` })
        } catch (e) {
            setBanner({ type: "error", message: `Clear failed: ${(e as Error).message}` })
        }
    }, [workshopId, loadStrings, confirm])

    const onRestored = useCallback(async () => {
        const summary = await rescanMod(workshopId)
        setProgress(summary)
        await loadStrings()
        // Snapshots include the glossary, and the History dialog stays open after a restore, so refresh the count now.
        refreshGlossaryCount()
        setBanner({ type: "success", message: "Restored from snapshot" })
    }, [workshopId, loadStrings, refreshGlossaryCount])

    const saveSourceLanguage = useCallback(
        async (value: string) => {
            const next: WH3ModContext = { ...modContext, source_language_override: value === "" ? null : value }
            setModContext(next)
            try {
                await saveModContext(workshopId, next)
            } catch (e) {
                setBanner({ type: "error", message: `Language save failed: ${(e as Error).message}` })
            }
        },
        [workshopId, modContext]
    )

    const saveTargetLanguage = useCallback(
        async (value: string) => {
            const next: WH3ModContext = { ...modContext, target_language_override: value === "" ? null : value }
            setModContext(next)
            try {
                await saveModContext(workshopId, next)
            } catch (e) {
                setBanner({ type: "error", message: `Language save failed: ${(e as Error).message}` })
            }
        },
        [workshopId, modContext]
    )

    // The page owns the mod context. Saving merges the three fields into it before the full PUT, so a later language change keeps them.
    const saveContext = useCallback(
        async (fields: ContextFields) => {
            const next: WH3ModContext = { ...modContext, ...fields }
            await saveModContext(workshopId, next)
            setModContext(next)
        },
        [workshopId, modContext]
    )
    const hasContext = !!(modContext.source_game || modContext.character_name || modContext.background)

    // Save the translated-names prompt setting as soon as it is toggled, and put it back if the save fails.
    const onIncludeNamesChange = useCallback(
        async (checked: boolean) => {
            const next: WH3ModContext = { ...modContext, include_translated_names: checked }
            setModContext(next)
            try {
                await saveModContext(workshopId, next)
            } catch (e) {
                setModContext(modContext)
                setBanner({ type: "error", message: `Could not save the setting: ${(e as Error).message}` })
            }
        },
        [workshopId, modContext]
    )

    const onOpenFolder = useCallback(async () => {
        try {
            await openModFolder(workshopId)
        } catch (e) {
            setBanner({ type: "error", message: `Open folder failed: ${(e as Error).message}` })
        }
    }, [workshopId])

    const handleSort = useCallback((field: string) => {
        const f = field as SortField
        setSortConfig((prev) => {
            if (prev.key !== f) return { key: f, direction: "asc" }
            if (prev.direction === "asc") return { key: f, direction: "desc" }
            if (prev.direction === "desc") return { key: f, direction: null }
            return { key: f, direction: "asc" }
        })
    }, [])

    const filteredRows = useMemo(() => {
        let rows = strings
        if (filter !== "all") rows = rows.filter((r) => (r.canonical_status ?? "missing") === filter)
        const needle = search.trim().toLowerCase()
        if (needle) rows = rows.filter((r) => r.key.toLowerCase().includes(needle) || (r.parent_text ?? "").toLowerCase().includes(needle) || (r.translation_text ?? "").toLowerCase().includes(needle))
        return rows
    }, [strings, filter, search])

    const sortedRows = useMemo(() => {
        if (sortConfig.direction === null) return filteredRows
        const dir = sortConfig.direction === "asc" ? 1 : -1
        const key = sortConfig.key
        const val = (r: WH3DriftRow) => (key === "status" ? (r.canonical_status ?? "") : String(r[key] ?? ""))
        return [...filteredRows].sort((a, b) => val(a).localeCompare(val(b)) * dir)
    }, [filteredRows, sortConfig])

    const translateCount = useMemo(() => progress?.counts.untranslated ?? 0, [progress])
    const changedCount = useMemo(() => progress?.canonical_counts.changed ?? 0, [progress])
    // Counted from the table rows so each translated batch shows up right away. Done = synced + pending + untouched, total adds missing.
    const done = useMemo(() => strings.filter((r) => r.canonical_status === "synced" || r.canonical_status === "pending" || r.canonical_status === "untouched").length, [strings])
    const total = useMemo(() => done + strings.filter((r) => r.canonical_status === "missing" || r.canonical_status === "changed").length, [strings, done])

    const sourceLang = modContext.source_language_override ?? mod?.source_language ?? "Chinese"
    const targetLang = sourceLang === "English" ? (modContext.target_language_override ?? mod?.target_language ?? "Chinese") : "English"

    const columns = translationColumns<WH3DriftRow>(
        {
            status: { field: "status", render: (r) => <StatusBadge status={r.canonical_status ?? "missing"} /> },
            mode: { field: "provider", render: (r) => <span title={r.provider ?? ""}>{r.provider ?? ""}</span> },
            source: {
                field: "source_filename",
                render: (r) => (
                    <a
                        href="#"
                        title={r.source_filename}
                        onClick={(e) => {
                            e.preventDefault()
                            e.stopPropagation()
                            openSourceFile(workshopId, r.source_filename).catch(() => {})
                        }}
                    >
                        <HighlightText text={r.source_filename} query={search} />
                    </a>
                ),
            },
            key: { field: "key", render: (r) => <HighlightText text={r.key} query={search} /> },
            original: { field: "parent_text", render: (r) => (r.parent_text != null ? <HighlightText text={r.parent_text} query={search} /> : <em style={{ color: "var(--text-dim)" }}>orphan</em>) },
            translation: {
                field: "translation_text",
                render: (r) => (
                    <TranslationCell
                        value={r.translation_text ?? ""}
                        previous={r.previous_text}
                        synced={r.canonical_status === "synced"}
                        placeholder="(untranslated)"
                        highlight={search}
                        onSave={(text) => onRowSave(r.key, text)}
                    />
                ),
            },
        },
        { original: `Original (${sourceLang})`, translation: targetLang }
    )

    if (loading) return <LoadingState message="Loading mod details..." />

    const titleBadges = (
        <>
            <SteamLink href={`https://steamcommunity.com/sharedfiles/filedetails/?id=${workshopId}`} />
            <OpenFolderButton onClick={onOpenFolder} />
            {progress?.has_unsynced_changes && <PendingSyncPill />}
        </>
    )

    const languageControls = (
        <LanguageControls
            source={modContext.source_language_override ?? mod?.source_language ?? "Chinese"}
            target={modContext.target_language_override ?? mod?.target_language ?? "Chinese"}
            onSourceChange={saveSourceLanguage}
            onTargetChange={saveTargetLanguage}
        />
    )

    const syncState = progress?.has_unsynced_changes ? "sync" : (progress?.canonical_counts.synced ?? 0) > 0 ? "resync" : "disabled"

    const toolbar = (
        <TranslationToolbar
            glossary={{ count: glossaryCount, onClick: () => setOpenModal("glossary") }}
            suggestions={{ count: suggestions.length, onClick: () => setShowSuggestions(true) }}
            scan={{ scanning, onClick: scan }}
            apiResponses={{ onClick: () => setOpenModal("responses") }}
            context={{ label: "Mod Context", hasContext, onClick: () => setShowContext((v) => !v) }}
            history={{ onClick: () => setOpenModal("history") }}
            reset={{ onClick: () => setOpenModal("reset") }}
            clearEnglish={{ onClick: onClearEnglish }}
            extraActions={
                <>
                    <SplitButton
                        variant="outline"
                        label={`Update Changed (${changedCount})`}
                        onClick={() => handleUpdateChangedClick()}
                        disabled={isTranslating || changedCount === 0}
                        menuDisabled={isTranslating || changedCount === 0}
                        menuLabel="Update Changed menu"
                        items={[{ label: "Accept All Changed", onSelect: onAcceptChanged }]}
                    />
                    <SplitButton
                        variant="outline"
                        label="Translate Names"
                        onClick={() => handleTranslateNamesClick()}
                        disabled={isTranslating || translateCount === 0}
                        menuDisabled={isTranslating}
                        menuLabel="Translate Names menu"
                        items={[{ label: "Suggest from Translated Names", onSelect: onSuggestFromNames }]}
                    />
                </>
            }
            translate={
                <SplitButton
                    label={`Translate (${activeProvider.charAt(0).toUpperCase() + activeProvider.slice(1)})`}
                    onClick={() => handleTranslateClick()}
                    disabled={isTranslating || translateCount === 0}
                    menuDisabled={isTranslating}
                    menuLabel="Translate provider menu"
                    items={["claude"].map((p) => ({ label: p.charAt(0).toUpperCase() + p.slice(1), onSelect: () => setActiveProvider(p) }))}
                />
            }
            sync={<SyncButton state={syncState} confirmMessage={wh3SyncMessage} onSync={onSyncChanges} />}
        />
    )

    const modals = (
        <>
            {showSuggestions && (
                <GlossarySuggestionModal
                    gameId="total_war_warhammer_3"
                    onTranslationsChanged={reloadAfterRename}
                    modId={workshopId}
                    suggestions={suggestions}
                    onClose={() => setShowSuggestions(false)}
                    onUpdated={() => {
                        refreshSuggestions()
                        refreshGlossaryCount()
                    }}
                />
            )}
            {openModal === "glossary" && (
                <ModGlossaryModal workshopId={workshopId} onClose={() => setOpenModal(null)} onSuggestionsChanged={() => refreshSuggestions()} onTranslationsChanged={reloadAfterRename} />
            )}
            {openModal === "responses" && <ApiResponsesModal workshopId={workshopId} onClose={() => setOpenModal(null)} />}
            {(openModal === "history" || openModal === "reset") && (
                <HistoryModal workshopId={workshopId} onClose={() => setOpenModal(null)} defaultRestoreMode={openModal === "reset"} onRestored={onRestored} />
            )}
            {preview && (
                <TranslationConfirmModal
                    preview={preview}
                    title={pendingScope === "names" ? "Translate Names" : pendingScope === "changed" ? "Update Changed" : undefined}
                    onConfirm={onConfirmTranslate}
                    onCancel={() => setPreview(null)}
                />
            )}

            {/* Review of the names stage that just finished. Continue starts the next stage, and closing pauses the run. */}
            {stageReview && (
                <GlossarySuggestionModal
                    key={stageReview.step.index}
                    gameId="total_war_warhammer_3"
                    onTranslationsChanged={reloadAfterRename}
                    modId={workshopId}
                    suggestions={stageReview.suggestions}
                    subtitle={`Stage ${stageReview.step.index + 1} of ${stageReview.step.total}: ${stageReview.step.label}`}
                    continueLabel={stageReview.step.nextLabel ? `Continue to ${stageReview.step.nextLabel}` : "Finish"}
                    onClose={() => {
                        const step = stageReview.step
                        setStageReview(null)
                        if (step.nextLabel) setPausedStep(step)
                        else finishNamesRun()
                    }}
                    onUpdated={() => {
                        refreshSuggestions()
                        refreshGlossaryCount()
                    }}
                    onContinue={() => {
                        setStageReview(null)
                        advanceNamesRun()
                    }}
                />
            )}

            {/* Suggest from Translated Names: already-translated names the glossary does not cover yet. */}
            {nameReviewSuggestions && (
                <GlossarySuggestionModal
                    gameId="total_war_warhammer_3"
                    onTranslationsChanged={reloadAfterRename}
                    modId={workshopId}
                    suggestions={nameReviewSuggestions}
                    onClose={() => setNameReviewSuggestions(null)}
                    onUpdated={() => {
                        refreshSuggestions()
                        refreshGlossaryCount()
                    }}
                />
            )}

            {/* Shown when the iterative loop pauses for glossary suggestion review between batches. Closing pauses; the paused banner lets the user resume. */}
            {batchState.phase === "reviewing" && showReviewModal && !namesRunRef.current && (
                <GlossarySuggestionModal
                    gameId="total_war_warhammer_3"
                    onTranslationsChanged={reloadAfterRename}
                    modId={workshopId}
                    suggestions={batchState.suggestions}
                    onClose={() => setShowReviewModal(false)}
                    onUpdated={() => {
                        refreshSuggestions()
                        refreshGlossaryCount()
                    }}
                    batchProgress={{ current: batchState.batchIndex + 1, total: batchState.totalBatches }}
                    onContinue={() => {
                        setShowReviewModal(false)
                        continueAfterReview()
                    }}
                />
            )}
            {confirmDialog}
        </>
    )

    return (
        <TranslationPage<WH3DriftRow>
            title={mod?.display_name ?? workshopId}
            progressLabel={`${done} / ${total} total strings translated`}
            onBack={() => navigate(`/${slug}/dashboard`)}
            previewImage={mod?.preview_image_url ? `${API_BASE}${mod.preview_image_url}` : null}
            titleBadges={titleBadges}
            languageControls={languageControls}
            toolbar={toolbar}
            statusFilters={STATUS_FILTERS}
            activeFilter={filter}
            onFilterChange={(v) => setFilter(v as RowStatus | "all")}
            search={search}
            onSearchChange={setSearch}
            columns={columns}
            rows={sortedRows}
            getRowKey={(r) => `${r.source_filename}::${r.key}`}
            getRowStyle={(r) => canonicalRowStyle(r.canonical_status ?? "missing", { override: r.canonical_status === "pending" })}
            sortField={sortConfig.direction ? sortConfig.key : null}
            sortDirection={sortConfig.direction}
            onSort={handleSort}
            columnWidths={columnWidths}
            onResizeColumn={onResizeColumn}
            translating={batchState.phase === "translating" ? { batchIndex: batchState.batchIndex, totalBatches: batchState.totalBatches, streaming: batchState.streamingProgress } : null}
            onCancelTranslate={onCancelTranslate}
            extraBanners={
                <>
                    {batchState.phase === "reviewing" && !showReviewModal && !namesRunRef.current && (
                        <BatchReviewBanner
                            batchIndex={batchState.batchIndex}
                            totalBatches={batchState.totalBatches}
                            onReview={() => setShowReviewModal(true)}
                            onContinue={continueAfterReview}
                            onCancel={() => {
                                cancelTranslation()
                                setBanner({ type: "success", message: `Translation cancelled. ${batchState.batchIndex} of ${batchState.totalBatches} batches completed.` })
                            }}
                        />
                    )}
                    {pausedStep && (
                        <div className="glass-card static batch-review-banner">
                            <span>Names paused after {pausedStep.label}.</span>
                            <div className="batch-review-banner-actions">
                                <button
                                    type="button"
                                    className="btn btn-primary"
                                    onClick={() => {
                                        setPausedStep(null)
                                        advanceNamesRun()
                                    }}
                                >
                                    Continue to {pausedStep.nextLabel}
                                </button>
                                <button
                                    type="button"
                                    className="btn btn-outline"
                                    onClick={() => {
                                        setPausedStep(null)
                                        finishNamesRun(pausedStep.nextLabel ?? undefined)
                                    }}
                                >
                                    Stop
                                </button>
                            </div>
                        </div>
                    )}
                </>
            }
            banner={banner}
            onDismissBanner={() => setBanner(null)}
            panels={
                showContext && (
                    <ContextPanel
                        title="Mod Context"
                        description="This context is included in the translation prompt to help the AI understand the mod's setting and lore."
                        value={modContext}
                        onSave={saveContext}
                        extra={
                            <label
                                className="checkbox-row"
                                title="Names you translated but have not accepted into the Mod Glossary are sent as glossary terms. Glossary terms win when both have the same name."
                            >
                                <input type="checkbox" checked={!!modContext.include_translated_names} onChange={(e) => onIncludeNamesChange(e.target.checked)} />
                                Also send translated names that aren't in the glossary
                            </label>
                        }
                        placeholders={{
                            source_game: "e.g. Total War: Warhammer III",
                            character_name: "e.g. Grand Cathay",
                            background: "Describe the factions, units and lore this mod adds, and any naming conventions the translation should follow...",
                        }}
                    />
                )
            }
            modals={modals}
        />
    )
}

export default TranslationDetailsPage
