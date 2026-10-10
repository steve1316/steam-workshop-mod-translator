import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { publishPack } from "../../../../games/total_war_warhammer_3/api"
import TranslationModCard from "../../../../games/total_war_warhammer_3/components/TranslationModCard"
import { syncChanges } from "../../../../games/total_war_warhammer_3/translationApi"
import type { WH3RescanSummary, WH3TranslationModSummary } from "../../../../shared_types"

vi.mock("../../../../games/total_war_warhammer_3/api", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../../../../games/total_war_warhammer_3/api")>()),
    publishPack: vi.fn(),
}))

vi.mock("../../../../games/total_war_warhammer_3/translationApi", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../../../../games/total_war_warhammer_3/translationApi")>()),
    syncChanges: vi.fn(),
}))

/** Minimal `EventSource` stand-in, since jsdom does not provide one. */
class FakeEventSource {
    onmessage: ((evt: MessageEvent) => void) | null = null
    onerror: (() => void) | null = null
    addEventListener() {}
    close() {}
}

const NEEDS_SYNC: WH3RescanSummary = {
    mod_id: "3315737452",
    counts: { translated: 5, untranslated: 0, stale: 0, orphan: 0 },
    canonical_counts: { synced: 5, untouched: 0, pending: 0, changed: 0, missing: 0, untranslatable: 0 },
    scanned_at: "2026-05-24T00:00:00Z",
    has_unsynced_changes: true,
    has_mod_context: false,
}

const MOD: WH3TranslationModSummary = {
    workshop_id: "3315737452",
    display_name: "Zerooz Cathy Alternative English Translation",
    parent_workshop_ids: ["2901237965"],
    local_source_dir: "C:\\does\\not\\matter",
    source_language: "Chinese",
    target_language: "English",
    preview_image_url: null,
}

const wrap = (ui: React.ReactNode) => <MemoryRouter>{ui}</MemoryRouter>

beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(JSON.stringify({ mod_id: "x", counts: { translated: 0, untranslated: 0, stale: 0, orphan: 0 }, scanned_at: "", has_unsynced_changes: false }), { status: 200 })
    )
})

afterEach(() => {
    vi.restoreAllMocks()
    vi.mocked(publishPack).mockReset()
    vi.mocked(syncChanges).mockReset()
    vi.unstubAllGlobals()
})

describe("TranslationModCard", () => {
    it("renders the display name and workshop id badge", () => {
        render(wrap(<TranslationModCard mod={MOD} progress={null} onRescan={vi.fn()} />))
        expect(screen.getByRole("heading", { name: MOD.display_name, level: 3 })).toBeInTheDocument()
        expect(screen.getByText(MOD.workshop_id)).toBeInTheDocument()
    })

    it("does NOT render the source -> target language pill", () => {
        render(wrap(<TranslationModCard mod={MOD} progress={null} onRescan={vi.fn()} />))
        expect(screen.queryByText(/Chinese\s*->\s*English/)).not.toBeInTheDocument()
    })

    it("renders a single parent link when one parent", () => {
        render(wrap(<TranslationModCard mod={MOD} progress={null} onRescan={vi.fn()} />))
        const link = screen.getByRole("link", { name: /parent/i })
        expect(link).toHaveAttribute("href", "https://steamcommunity.com/sharedfiles/filedetails/?id=2901237965")
        expect(link).toHaveAttribute("target", "_blank")
    })

    it("Open mod page button links to the translation mod itself, not the parent", () => {
        render(wrap(<TranslationModCard mod={MOD} progress={null} onRescan={vi.fn()} />))
        const steamButton = screen.getByTitle("Open mod page")
        expect(steamButton).toHaveAttribute("href", `https://steamcommunity.com/sharedfiles/filedetails/?id=${MOD.workshop_id}`)
    })

    it("collapses parent links when there are multiple", () => {
        const collection: WH3TranslationModSummary = { ...MOD, parent_workshop_ids: ["a", "b", "c", "d", "e"] }
        render(wrap(<TranslationModCard mod={collection} progress={null} onRescan={vi.fn()} />))
        expect(screen.getByText(/Translates 5 mods/i)).toBeInTheDocument()
    })

    it("shows 'Not yet scanned' when progress is null", () => {
        render(wrap(<TranslationModCard mod={MOD} progress={null} onRescan={vi.fn()} />))
        expect(screen.getByText(/Not yet scanned/i)).toBeInTheDocument()
    })

    it("renders Remaining and Format stats", () => {
        const progress: WH3RescanSummary = {
            mod_id: MOD.workshop_id,
            counts: { translated: 12, untranslated: 5, stale: 3, orphan: 1 },
            canonical_counts: { synced: 12, untouched: 2, pending: 3, changed: 0, missing: 5, untranslatable: 4 },
            scanned_at: "2026-05-24T00:00:00Z",
            has_unsynced_changes: false,
            has_mod_context: false,
        }
        const { container } = render(wrap(<TranslationModCard mod={MOD} progress={progress} onRescan={vi.fn()} />))
        // Counts come from the table's statuses: done = synced + pending + untouched, total adds missing (untranslatable excluded).
        expect(screen.getByText(/17\s*\/\s*22\s*strings/)).toBeInTheDocument()
        const titles = [...container.querySelectorAll(".progress-bar-bg [title]")].map((el) => el.getAttribute("title"))
        expect(titles).toEqual(["15 translated by you", "2 untouched (pre-existing English)"])
        // Chrono-Ark-style stat boxes.
        expect(screen.getByText("Remaining")).toBeInTheDocument()
        const remainingStat = screen.getByText("Remaining").previousElementSibling
        expect(remainingStat).toHaveTextContent("5")
        expect(screen.getByText("Format")).toBeInTheDocument()
        const formatStat = screen.getByText("Format").previousElementSibling
        expect(formatStat).toHaveTextContent("LOC")
    })

    it("renders the View and Publish buttons (not 'Translate ->')", () => {
        render(wrap(<TranslationModCard mod={MOD} progress={null} onRescan={vi.fn()} />))
        expect(screen.getByRole("button", { name: "View" })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: "Publish" })).toBeInTheDocument()
        expect(screen.queryByText(/Translate ->/)).not.toBeInTheDocument()
    })

    it("publishes without syncing when there are no unsynced changes", async () => {
        vi.stubGlobal("EventSource", FakeEventSource)
        vi.mocked(publishPack).mockResolvedValue({ publish_id: "p", workshop_id: MOD.workshop_id, started_at: "" })
        render(wrap(<TranslationModCard mod={MOD} progress={{ ...NEEDS_SYNC, has_unsynced_changes: false }} onRescan={vi.fn()} />))
        fireEvent.click(screen.getByRole("button", { name: "Publish" }))
        expect(screen.queryByText(/will be synced/)).not.toBeInTheDocument()
        fireEvent.click(screen.getAllByRole("button", { name: "Publish" }).at(-1)!)
        await waitFor(() => expect(publishPack).toHaveBeenCalledWith(MOD.workshop_id, ""))
        expect(syncChanges).not.toHaveBeenCalled()
    })

    it("syncs before publishing when the mod needs a sync", async () => {
        vi.stubGlobal("EventSource", FakeEventSource)
        vi.mocked(syncChanges).mockResolvedValue({ per_file: { a: 1 }, removed_orphans: 0, pack_built: true, pack_error: null })
        vi.mocked(publishPack).mockResolvedValue({ publish_id: "p", workshop_id: MOD.workshop_id, started_at: "" })
        const onRescan = vi.fn()
        render(wrap(<TranslationModCard mod={MOD} progress={NEEDS_SYNC} onRescan={onRescan} />))
        fireEvent.click(screen.getByRole("button", { name: "Publish" }))
        expect(screen.getByText(/will be synced and the pack rebuilt/)).toBeInTheDocument()
        fireEvent.click(screen.getAllByRole("button", { name: "Publish" }).at(-1)!)
        await waitFor(() => expect(publishPack).toHaveBeenCalledWith(MOD.workshop_id, ""))
        expect(syncChanges).toHaveBeenCalledWith(MOD.workshop_id)
        expect(vi.mocked(syncChanges).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(publishPack).mock.invocationCallOrder[0])
        expect(onRescan).toHaveBeenCalledWith(MOD.workshop_id)
    })

    it("stops the publish when the pack rebuild during sync fails", async () => {
        vi.mocked(syncChanges).mockResolvedValue({ per_file: { a: 1 }, removed_orphans: 0, pack_built: false, pack_error: "RPFM not configured" })
        render(wrap(<TranslationModCard mod={MOD} progress={NEEDS_SYNC} onRescan={vi.fn()} />))
        fireEvent.click(screen.getByRole("button", { name: "Publish" }))
        fireEvent.click(screen.getAllByRole("button", { name: "Publish" }).at(-1)!)
        expect(await screen.findByText(/pack rebuild failed so the publish was stopped: RPFM not configured/)).toBeInTheDocument()
        expect(publishPack).not.toHaveBeenCalled()
    })

    it("renders the rescan icon button and fires onRescan when clicked", () => {
        const onRescan = vi.fn()
        render(wrap(<TranslationModCard mod={MOD} progress={null} onRescan={onRescan} />))
        const sync = screen.getByTitle("Rescan workshop folder")
        fireEvent.click(sync)
        expect(onRescan).toHaveBeenCalledWith(MOD.workshop_id)
    })

    it("shows the Needs Sync badge when progress.has_unsynced_changes is true", () => {
        const progress: WH3RescanSummary = {
            mod_id: MOD.workshop_id,
            counts: { translated: 5, untranslated: 0, stale: 0, orphan: 0 },
            canonical_counts: { synced: 5, untouched: 0, pending: 0, changed: 0, missing: 0, untranslatable: 0 },
            scanned_at: "2026-05-24T00:00:00Z",
            has_unsynced_changes: true,
            has_mod_context: false,
        }
        render(wrap(<TranslationModCard mod={MOD} progress={progress} onRescan={vi.fn()} />))
        expect(screen.getByText("Needs Sync")).toBeInTheDocument()
    })

    it("omits the Needs Sync badge when has_unsynced_changes is false", () => {
        const progress: WH3RescanSummary = {
            mod_id: MOD.workshop_id,
            counts: { translated: 5, untranslated: 0, stale: 0, orphan: 0 },
            canonical_counts: { synced: 5, untouched: 0, pending: 0, changed: 0, missing: 0, untranslatable: 0 },
            scanned_at: "2026-05-24T00:00:00Z",
            has_unsynced_changes: false,
            has_mod_context: false,
        }
        render(wrap(<TranslationModCard mod={MOD} progress={progress} onRescan={vi.fn()} />))
        expect(screen.queryByText("Needs Sync")).not.toBeInTheDocument()
    })

    it("renders the preview image when preview_image_url is set", () => {
        const withPreview: WH3TranslationModSummary = { ...MOD, preview_image_url: "/games/total_war_warhammer_3/translation/mods/3315737452/preview" }
        const { container } = render(wrap(<TranslationModCard mod={withPreview} progress={null} onRescan={vi.fn()} />))
        const img = container.querySelector(".mod-preview img")
        expect(img).not.toBeNull()
        expect(img).toHaveAttribute("src", expect.stringContaining("/games/total_war_warhammer_3/translation/mods/3315737452/preview"))
    })

    it("remembers the card before opening it so the dashboard can scroll back to it", () => {
        render(wrap(<TranslationModCard mod={MOD} progress={null} onRescan={vi.fn()} />))
        fireEvent.click(screen.getByRole("button", { name: "View" }))
        expect(sessionStorage.getItem("lastViewedMod")).toBe(MOD.workshop_id)
        sessionStorage.clear()
    })
})
