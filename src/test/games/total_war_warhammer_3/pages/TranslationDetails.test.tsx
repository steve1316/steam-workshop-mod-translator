import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import TranslationDetailsPage from "../../../../games/total_war_warhammer_3/pages/TranslationDetails"
import type { WH3DriftRow, WH3RescanSummary, WH3TranslationModSummary } from "../../../../shared_types"

const MOD: WH3TranslationModSummary = {
    workshop_id: "3315737452",
    display_name: "Zerooz Cathy",
    parent_workshop_ids: ["2901237965"],
    local_source_dir: "C:\\nope",
    source_language: "Chinese",
    target_language: "English",
    preview_image_url: "/games/total_war_warhammer_3/translation/mods/3315737452/preview",
}

const SUMMARY: WH3RescanSummary = {
    mod_id: MOD.workshop_id,
    counts: { translated: 5, untranslated: 3, stale: 1, orphan: 0 },
    canonical_counts: { synced: 5, untouched: 0, pending: 1, changed: 0, missing: 3, untranslatable: 0 },
    scanned_at: "2026-05-25T00:00:00Z",
    has_unsynced_changes: false,
    has_mod_context: false,
}

const STRINGS: WH3DriftRow[] = [
    {
        source_filename: "units.loc.tsv",
        key: "k1",
        parent_text: "原一",
        translation_text: "Original 1",
        status: "translated",
        provider: "manual",
        canonical_status: "synced",
        previous_text: "Original 1",
    },
    { source_filename: "units.loc.tsv", key: "k2", parent_text: "原二", translation_text: null, status: "untranslated", provider: null, canonical_status: "missing", previous_text: null },
    { source_filename: "units.loc.tsv", key: "k3", parent_text: "原三", translation_text: "Stale text", status: "stale", provider: "claude", canonical_status: "changed", previous_text: "Old Stale" },
]

const PREVIEW = {
    total_strings: 1,
    total_batches: 1,
    batch_size: 100,
    provider: "claude",
    previews: { Chinese: { system_prompt: "SYS PROMPT", user_messages: ["USER MSG"], strings_in_language: 1, batches: 1 } },
    estimates: { Chinese: { estimated_input_tokens: 10, estimated_output_tokens: 10, estimated_cost_usd: 0.01, model: "claude", note: "estimate" } },
    batch_plan: [{ source_lang: "Chinese", keys: ["k2"], size: 1 }],
}

function mockJson(body: unknown, status = 200) {
    return new Response(JSON.stringify(body), { status })
}

/**
 * Click the Sync button and confirm its dialog.
 *
 * @param label The Sync button's current label.
 */
async function syncAndConfirm(label: RegExp) {
    // Find elements outside act: an async act holds renders until it ends, so a find inside it never sees the loaded page.
    const button = await screen.findByRole("button", { name: label })
    await act(async () => {
        fireEvent.click(button)
    })
    const dialog = await screen.findByRole("dialog", { name: label })
    await act(async () => {
        fireEvent.click(within(dialog).getByRole("button", { name: /^(Sync|Re-sync)$/ }))
    })
}

function mockRouteFlow() {
    const spy = vi.spyOn(globalThis, "fetch")
    spy.mockImplementation(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString()
        if (url.endsWith("/translation/mods")) return mockJson([MOD])
        if (url.endsWith("/rescan")) return mockJson(SUMMARY)
        if (url.includes("/strings?status=")) return mockJson(STRINGS.filter((r) => url.includes(r.status)))
        if (url.endsWith("/strings")) return mockJson(STRINGS)
        if (url.endsWith("/mod-context")) return mockJson({ source_game: "", character_name: "", background: "", source_language_override: null, target_language_override: null })
        if (url.endsWith("/api-responses")) return mockJson([])
        if (url.endsWith("/snapshots")) return mockJson([])
        if (url.endsWith("/glossary")) return mockJson({})
        return mockJson({ status: "ok" })
    })
    return spy
}

const wrap = () => (
    <MemoryRouter initialEntries={[`/translation/${MOD.workshop_id}`]}>
        <Routes>
            <Route path="/translation/:workshopId" element={<TranslationDetailsPage />} />
        </Routes>
    </MemoryRouter>
)

beforeEach(() => {
    mockRouteFlow()
})

afterEach(() => {
    vi.restoreAllMocks()
})

describe("TranslationDetails (Plan 3 layout)", () => {
    it("renders the toolbar buttons", async () => {
        render(wrap())
        await waitFor(() => screen.getByRole("button", { name: /Mod Glossary/i }))
        expect(screen.getByRole("button", { name: /Scan for Terms/i })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /API Responses/i })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /Mod Context/i })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /History/i })).toBeInTheDocument()
    })

    it("renders the action row buttons including Translate provider label", async () => {
        render(wrap())
        await waitFor(() => screen.getByRole("button", { name: /Reset/i }))
        expect(screen.getByRole("button", { name: /Clear English/i })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /^Translate \(Claude\)/i })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /Sync Changes/i })).toBeInTheDocument()
    })

    it("renders the search input above the filter pills", async () => {
        render(wrap())
        await waitFor(() => screen.getByPlaceholderText(/search keys or text/i))
    })

    it("filters table client-side when the user types in search", async () => {
        render(wrap())
        await waitFor(() => screen.getByText("Original 1"))
        fireEvent.change(screen.getByPlaceholderText(/search keys or text/i), { target: { value: "Stale" } })
        await waitFor(() => expect(screen.queryByText("Original 1")).not.toBeInTheDocument())
        // The match is wrapped in a highlight, so the cell text is split across elements.
        const mark = screen.getAllByText("Stale", { selector: "mark.search-highlight" }).find((m) => m.parentElement?.textContent === "Stale text")
        expect(mark).toBeDefined()
    })

    it("shows the previous on-disk translation struck through on a pending (edited) row", async () => {
        render(wrap())
        const prev = await screen.findByText("Old Stale")
        expect(prev.className).toContain("prev-translation")
    })

    it("renders the Mode column with provider value per row", async () => {
        render(wrap())
        await waitFor(() => screen.getByText("Original 1"))
        const headers = screen.getAllByRole("columnheader")
        expect(headers.some((h) => /mode/i.test(h.textContent ?? ""))).toBe(true)
        expect(screen.getByText("manual")).toBeInTheDocument()
        expect(screen.getByText("claude")).toBeInTheDocument()
    })

    it("opens ModGlossaryModal when the toolbar button is clicked", async () => {
        render(wrap())
        await waitFor(() => screen.getByRole("button", { name: /Mod Glossary/i }))
        fireEvent.click(screen.getByRole("button", { name: /Mod Glossary/i }))
        await waitFor(() => expect(screen.getByRole("heading", { name: /Mod Glossary/i })).toBeInTheDocument())
    })

    it("opens HistoryModal in restore mode when Reset is clicked", async () => {
        render(wrap())
        await waitFor(() => screen.getByRole("button", { name: /Reset/i }))
        fireEvent.click(screen.getByRole("button", { name: /Reset/i }))
        await waitFor(() => expect(screen.getByRole("heading", { name: /Reset to snapshot/i })).toBeInTheDocument())
    })

    it("POSTs to /clear-translations after Clear English confirmation", async () => {
        const fetchSpy = mockRouteFlow()
        render(wrap())
        await waitFor(() => screen.getByRole("button", { name: /Clear English/i }))
        await act(async () => {
            fireEvent.click(screen.getByRole("button", { name: /Clear English/i }))
        })
        await act(async () => {
            fireEvent.click(within(await screen.findByRole("dialog", { name: "Clear English" })).getByRole("button", { name: "Clear" }))
        })
        await waitFor(() => {
            const calls = fetchSpy.mock.calls.map((c) => (typeof c[0] === "string" ? c[0] : (c[0] as URL).toString()))
            expect(calls.some((u) => u.includes("/clear-translations"))).toBe(true)
        })
    })

    it("confirms, then POSTs to /sync when Sync Changes is clicked", async () => {
        const fetchSpy = mockRouteFlow()
        const base = fetchSpy.getMockImplementation()!
        fetchSpy.mockImplementation(async (input, init) => (String(input).endsWith("/rescan") ? mockJson({ ...SUMMARY, has_unsynced_changes: true }) : base(input, init)))
        render(wrap())
        await syncAndConfirm(/^Sync Changes$/)
        await waitFor(() => {
            const calls = fetchSpy.mock.calls.map((c) => String(c[0]))
            expect(calls.some((u) => u.endsWith("/sync"))).toBe(true)
        })
    })

    it("renders the back-to-dashboard button styled as btn-outline", async () => {
        render(wrap())
        const back = await screen.findByRole("button", { name: /Back to Dashboard/i })
        expect(back).toHaveClass("btn", "btn-outline")
    })

    it("renders the 80x80 preview image when preview_image_url is set", async () => {
        render(wrap())
        const img = await screen.findByAltText(MOD.display_name)
        expect(img).toHaveAttribute("src", expect.stringContaining(MOD.preview_image_url!))
    })

    it("renders a Steam icon link pointing at the translation mod's workshop page", async () => {
        render(wrap())
        const link = await screen.findByTitle("Open on Steam Workshop")
        expect(link).toHaveAttribute("href", `https://steamcommunity.com/sharedfiles/filedetails/?id=${MOD.workshop_id}`)
        expect(link).toHaveAttribute("target", "_blank")
    })

    it("renders an Open Folder button that POSTs to /open-folder when clicked", async () => {
        const fetchSpy = mockRouteFlow()
        render(wrap())
        const folder = await screen.findByTitle("Open local folder")
        await act(async () => {
            fireEvent.click(folder)
        })
        await waitFor(() => {
            const calls = fetchSpy.mock.calls.map((c) => (typeof c[0] === "string" ? c[0] : (c[0] as URL).toString()))
            expect(calls.some((u) => u.includes("/open-folder"))).toBe(true)
        })
    })

    it("renders the Changes pending sync badge when has_unsynced_changes is true", async () => {
        const spy = vi.spyOn(globalThis, "fetch")
        spy.mockImplementation(async (input: RequestInfo | URL) => {
            const url = typeof input === "string" ? input : input.toString()
            if (url.endsWith("/translation/mods")) return mockJson([MOD])
            if (url.endsWith("/rescan")) return mockJson({ ...SUMMARY, has_unsynced_changes: true })
            if (url.endsWith("/strings")) return mockJson(STRINGS)
            if (url.endsWith("/mod-context")) return mockJson({ source_game: "", character_name: "", background: "", source_language_override: null, target_language_override: null })
            return mockJson({ status: "ok" })
        })
        render(wrap())
        await waitFor(() => expect(screen.getByText(/Changes pending sync/i)).toBeInTheDocument())
    })

    it("renders the Source Language dropdown with the registry default selected", async () => {
        render(wrap())
        const select = (await screen.findByLabelText(/Source Language/i)) as HTMLSelectElement
        expect(select.value).toBe("Chinese")
    })

    it("PUTs to /mod-context when the Source Language dropdown changes", async () => {
        const fetchSpy = mockRouteFlow()
        render(wrap())
        const select = await screen.findByLabelText(/Source Language/i)
        await act(async () => {
            fireEvent.change(select, { target: { value: "Japanese" } })
        })
        await waitFor(() => {
            const putCalls = fetchSpy.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "PUT")
            const modContextPut = putCalls.find((c) => (typeof c[0] === "string" ? c[0] : (c[0] as URL).toString()).includes("/mod-context"))
            expect(modContextPut).toBeDefined()
            const body = JSON.parse((modContextPut![1] as RequestInit).body as string)
            expect(body.source_language_override).toBe("Japanese")
        })
    })

    it("renders the inline 'X / Y total strings translated' counter", async () => {
        render(wrap())
        // Counted from the table rows: synced k1 + pending k3 are done, missing k2 is not.
        await waitFor(() => expect(screen.getByText(/2\s*\/\s*3\s*total strings translated/i)).toBeInTheDocument())
    })

    it("updates the counter after each batch while the translation is still running", async () => {
        const rows: WH3DriftRow[] = [
            ...STRINGS,
            { source_filename: "units.loc.tsv", key: "k4", parent_text: "原四", translation_text: null, status: "untranslated", provider: null, canonical_status: "missing", previous_text: null },
        ]
        const twoBatches = {
            ...PREVIEW,
            total_strings: 2,
            total_batches: 2,
            batch_plan: [
                { source_lang: "Chinese", keys: ["k2"], size: 1 },
                { source_lang: "Chinese", keys: ["k4"], size: 1 },
            ],
        }
        const spy = vi.spyOn(globalThis, "fetch")
        spy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
            const url = typeof input === "string" ? input : input.toString()
            if (url.endsWith("/translate/preview")) return mockJson(twoBatches)
            if (url.endsWith("/translate/batch")) {
                // The first batch returns and the second never does, so the run stays in progress.
                const keys: string[] = JSON.parse(String(init?.body ?? "{}")).keys ?? []
                return keys.includes("k2") ? mockJson({ status: "success", translated: 1, translations: { k2: "EN k2" }, suggestions: [] }) : new Promise<Response>(() => undefined)
            }
            if (url.endsWith("/translation/mods")) return mockJson([MOD])
            if (url.endsWith("/rescan")) return mockJson(SUMMARY)
            if (url.endsWith("/strings")) return mockJson(rows)
            if (url.endsWith("/mod-context")) return mockJson({ source_game: "", character_name: "", background: "", source_language_override: null, target_language_override: null })
            return mockJson({ status: "ok" })
        })

        render(wrap())
        await waitFor(() => expect(screen.getByText(/2\s*\/\s*4\s*total strings translated/i)).toBeInTheDocument())
        fireEvent.click(await screen.findByRole("button", { name: /^Translate \(Claude\)/i }))
        fireEvent.click(await screen.findByRole("button", { name: /^Translate \d+ strings?$/i }))
        await waitFor(() => expect(screen.getByText(/3\s*\/\s*4\s*total strings translated/i)).toBeInTheDocument())
        expect(screen.getByText(/Translating batch/i)).toBeInTheDocument()
    })

    it("no longer renders the standalone progress-bar glass-card", async () => {
        const { container } = render(wrap())
        await screen.findByText(/Back to Dashboard/i)
        expect(container.querySelector(".translation-progress-bar")).toBeNull()
    })

    it("renders the action row split into three .mod-actions-group blocks", async () => {
        const { container } = render(wrap())
        await screen.findByRole("button", { name: /Back to Dashboard/i })
        const groups = container.querySelectorAll(".mod-actions .mod-actions-group")
        expect(groups.length).toBe(3)
    })

    it("shows the glossary count on the Mod Glossary button", async () => {
        const spy = vi.spyOn(globalThis, "fetch")
        spy.mockImplementation(async (input: RequestInfo | URL) => {
            const url = typeof input === "string" ? input : input.toString()
            if (url.endsWith("/translation/mods")) return mockJson([MOD])
            if (url.endsWith("/rescan")) return mockJson(SUMMARY)
            if (url.endsWith("/strings")) return mockJson(STRINGS)
            if (url.endsWith("/mod-context")) return mockJson({ source_game: "", character_name: "", background: "", source_language_override: null, target_language_override: null })
            if (url.endsWith("/glossary")) return mockJson({ Phoenix: { source: "x", category: "factions" }, Sky: { source: "y", category: "lore" } })
            return mockJson({ status: "ok" })
        })
        render(wrap())
        await waitFor(() => expect(screen.getByRole("button", { name: /Mod Glossary \(2\)/i })).toBeInTheDocument())
    })

    it("shows a green-dot indicator on Mod Context when has_mod_context is true", async () => {
        const spy = vi.spyOn(globalThis, "fetch")
        spy.mockImplementation(async (input: RequestInfo | URL) => {
            const url = typeof input === "string" ? input : input.toString()
            if (url.endsWith("/translation/mods")) return mockJson([MOD])
            if (url.endsWith("/rescan")) return mockJson({ ...SUMMARY, has_mod_context: true })
            if (url.endsWith("/strings")) return mockJson(STRINGS)
            if (url.endsWith("/mod-context")) return mockJson({ source_game: "", character_name: "Zerooz", background: "", source_language_override: null, target_language_override: null })
            return mockJson({ status: "ok" })
        })
        const { container } = render(wrap())
        await waitFor(() => expect(container.querySelector(".btn-dot")).not.toBeNull())
    })

    it("renders the Translate button as a split-dropdown with provider label", async () => {
        render(wrap())
        const main = await screen.findByRole("button", { name: /^Translate \(Claude\)/i })
        expect(main).toBeInTheDocument()
        // Chevron sibling exists.
        const chevron = screen.getByRole("button", { name: /Translate provider menu/i })
        expect(chevron).toBeInTheDocument()
    })

    it("opens the provider dropdown when the chevron is clicked", async () => {
        render(wrap())
        await screen.findByRole("button", { name: /^Translate \(Claude\)/i })
        const chevron = screen.getByRole("button", { name: /Translate provider menu/i })
        fireEvent.click(chevron)
        await waitFor(() => expect(screen.getByRole("menuitem", { name: /Claude/i })).toBeInTheDocument())
    })

    it("labels Sync 'Sync Changes' while there are unsynced changes", async () => {
        const spy = mockRouteFlow()
        const base = spy.getMockImplementation()!
        spy.mockImplementation(async (input, init) => (String(input).endsWith("/rescan") ? mockJson({ ...SUMMARY, has_unsynced_changes: true }) : base(input, init)))
        render(wrap())
        expect(await screen.findByRole("button", { name: /^Sync Changes$/ })).toBeEnabled()
    })

    it("labels Sync 'Re-sync Changes' when nothing is pending but rows were synced before", async () => {
        render(wrap())
        expect(await screen.findByRole("button", { name: /^Re-sync Changes$/ })).toBeEnabled()
    })

    it("disables Sync when nothing is pending and nothing was ever synced", async () => {
        const spy = mockRouteFlow()
        const base = spy.getMockImplementation()!
        spy.mockImplementation(async (input, init) => (String(input).endsWith("/rescan") ? mockJson({ ...SUMMARY, canonical_counts: { ...SUMMARY.canonical_counts, synced: 0 } }) : base(input, init)))
        render(wrap())
        await screen.findByRole("button", { name: /Back to Dashboard/ })
        await waitFor(() => expect(screen.getByRole("button", { name: /^Sync Changes$/ })).toBeDisabled())
    })

    it("wraps the search input and filter pills in a single glass-card", async () => {
        render(wrap())
        await screen.findByRole("button", { name: /Back to Dashboard/i })
        const searchInput = screen.getByPlaceholderText(/search keys or text/i)
        const allFilterPill = screen.getByRole("button", { name: /^All$/i })
        // Walk up from each element to find the nearest .glass-card ancestor; they must be the same node.
        const searchCard = searchInput.closest(".glass-card")
        const filterCard = allFilterPill.closest(".glass-card")
        expect(searchCard).not.toBeNull()
        expect(filterCard).not.toBeNull()
        expect(searchCard).toBe(filterCard)
    })

    it("renders a x clear button inside the search input when text is present", async () => {
        render(wrap())
        const input = await screen.findByPlaceholderText(/search keys or text/i)
        fireEvent.change(input, { target: { value: "abc" } })
        expect(screen.getByTitle("Clear search")).toBeInTheDocument()
    })

    it("clears the search input when the x is clicked", async () => {
        render(wrap())
        const input = (await screen.findByPlaceholderText(/search keys or text/i)) as HTMLInputElement
        fireEvent.change(input, { target: { value: "abc" } })
        fireEvent.click(screen.getByTitle("Clear search"))
        expect(input.value).toBe("")
    })

    it("active filter pill renders as btn-primary; inactive as btn-outline", async () => {
        render(wrap())
        const allPill = await screen.findByRole("button", { name: /^All$/i })
        const missing = screen.getByRole("button", { name: /^Missing$/i })
        expect(allPill).toHaveClass("btn-primary")
        expect(missing).toHaveClass("btn-outline")
        fireEvent.click(missing)
        await waitFor(() => expect(screen.getByRole("button", { name: /^Missing$/i })).toHaveClass("btn-primary"))
    })

    it("renders the canonical status filter pills (All / Missing / Untouched / Pending / Synced)", async () => {
        render(wrap())
        await screen.findByRole("button", { name: /^All$/i })
        for (const label of ["All", "Missing", "Untouched", "Pending", "Synced"]) {
            expect(screen.getByRole("button", { name: new RegExp(`^${label}$`, "i") })).toBeInTheDocument()
        }
    })

    it("previews then shows the in-progress banner after confirming the translation", async () => {
        // Translate now previews first (opens the confirm modal); confirming starts the iterative batch loop. Hold /translate/batch open to observe the banner.
        let resolveBatch: (v: Response) => void = () => undefined
        const batchPromise = new Promise<Response>((r) => {
            resolveBatch = r
        })
        const spy = vi.spyOn(globalThis, "fetch")
        spy.mockImplementation(async (input: RequestInfo | URL) => {
            const url = typeof input === "string" ? input : input.toString()
            if (url.endsWith("/translate/preview")) return mockJson(PREVIEW)
            if (url.endsWith("/translate/batch")) return batchPromise
            if (url.endsWith("/translation/mods")) return mockJson([MOD])
            if (url.endsWith("/rescan")) return mockJson(SUMMARY)
            if (url.endsWith("/strings")) return mockJson(STRINGS)
            if (url.endsWith("/mod-context")) return mockJson({ source_game: "", character_name: "", background: "", source_language_override: null, target_language_override: null })
            return mockJson({ status: "ok" })
        })

        render(wrap())
        fireEvent.click(await screen.findByRole("button", { name: /^Translate \(Claude\)/i }))
        // Confirm modal appears with the "Translate N strings" action.
        fireEvent.click(await screen.findByRole("button", { name: /^Translate \d+ strings?$/i }))
        await waitFor(() => expect(screen.getByText(/Translating batch/i)).toBeInTheDocument())
        expect(screen.getByRole("button", { name: /^Cancel$/i })).toBeInTheDocument()
        resolveBatch(mockJson({ status: "success", translated: 1, translations: { k2: "x" }, suggestions: [] }))
    })

    it("wraps the table inside .string-table-container glass-card", async () => {
        const { container } = render(wrap())
        await screen.findByRole("button", { name: /Back to Dashboard/i })
        expect(container.querySelector(".string-table-container")).not.toBeNull()
        expect(container.querySelector(".string-table-container table")).not.toBeNull()
    })

    it("renders Status column with canonical SYNCED / MISSING / PENDING badge text", async () => {
        render(wrap())
        await screen.findByText("Original 1")
        expect(screen.getByText("SYNCED")).toBeInTheDocument()
        expect(screen.getByText("MISSING")).toBeInTheDocument()
        expect(screen.getByText("PENDING")).toBeInTheDocument()
    })

    it("clicking a column header cycles sort direction null -> asc -> desc -> null", async () => {
        render(wrap())
        await screen.findByText("Original 1")
        const keyHeader = screen.getByRole("columnheader", { name: /^Key/i })
        // Initial: no sort indicator on Key.
        expect(keyHeader.querySelector(".sort-icon.active")).toBeNull()
        fireEvent.click(keyHeader)
        expect(keyHeader.querySelector(".sort-icon.active")).not.toBeNull()
        fireEvent.click(keyHeader)
        expect(keyHeader.querySelector(".sort-icon.active")).not.toBeNull()
        fireEvent.click(keyHeader)
        expect(keyHeader.querySelector(".sort-icon.active")).toBeNull()
    })

    it("renders a .resizer handle on each sortable column header", async () => {
        const { container } = render(wrap())
        await screen.findByRole("button", { name: /Back to Dashboard/i })
        const resizers = container.querySelectorAll(".sortable-th .resizer")
        // 6 columns -> 6 resizers.
        expect(resizers.length).toBe(6)
    })

    it("reads initial column widths from localStorage when present", async () => {
        window.localStorage.setItem("wh3-translation-column-widths", JSON.stringify({ status: 200, provider: 110, source_filename: 220, key: 250, parent_text: 300, translation_text: 320 }))
        try {
            const { container } = render(wrap())
            await screen.findByRole("button", { name: /Back to Dashboard/i })
            const statusHeader = container.querySelector('th[data-field="status"]') as HTMLElement
            expect(statusHeader.style.width).toBe("200px")
        } finally {
            window.localStorage.removeItem("wh3-translation-column-widths")
        }
    })

    it("Cancel during a run stops the loop and hides the banner", async () => {
        const spy = vi.spyOn(globalThis, "fetch")
        spy.mockImplementation(async (input: RequestInfo | URL) => {
            const url = typeof input === "string" ? input : input.toString()
            if (url.endsWith("/translate/preview")) return mockJson(PREVIEW)
            // Hold the batch open so the banner stays up until the user cancels.
            if (url.endsWith("/translate/batch")) return new Promise<Response>(() => undefined)
            if (url.endsWith("/translation/mods")) return mockJson([MOD])
            if (url.endsWith("/rescan")) return mockJson(SUMMARY)
            if (url.endsWith("/strings")) return mockJson(STRINGS)
            if (url.endsWith("/mod-context")) return mockJson({ source_game: "", character_name: "", background: "", source_language_override: null, target_language_override: null })
            return mockJson({ status: "ok" })
        })

        render(wrap())
        fireEvent.click(await screen.findByRole("button", { name: /^Translate \(Claude\)/i }))
        fireEvent.click(await screen.findByRole("button", { name: /^Translate \d+ strings?$/i }))
        await waitFor(() => expect(screen.getByText(/Translating batch/i)).toBeInTheDocument())
        fireEvent.click(screen.getByRole("button", { name: /^Cancel$/i }))
        await waitFor(() => expect(screen.queryByText(/Translating batch/i)).not.toBeInTheDocument())
    })

    it("appends a rebuilt-pack note to the sync banner when pack_built is true", async () => {
        const spy = vi.spyOn(globalThis, "fetch")
        spy.mockImplementation(async (input: RequestInfo | URL) => {
            const url = typeof input === "string" ? input : input.toString()
            if (url.endsWith("/translation/mods")) return mockJson([MOD])
            if (url.endsWith("/rescan")) return mockJson(SUMMARY)
            if (url.includes("/strings?status=")) return mockJson(STRINGS.filter((r) => url.includes(r.status)))
            if (url.endsWith("/strings")) return mockJson(STRINGS)
            if (url.endsWith("/sync")) return mockJson({ per_file: { "/abs/units.loc.tsv": 3 }, removed_orphans: 0, pack_built: true, pack_error: null })
            if (url.endsWith("/mod-context")) return mockJson({ source_game: "", character_name: "", background: "", source_language_override: null, target_language_override: null })
            return mockJson({ status: "ok" })
        })

        render(wrap())
        await syncAndConfirm(/Sync Changes/i)
        await waitFor(() => expect(screen.getByText(/, rebuilt pack/i)).toBeInTheDocument())
    })

    it("appends a pack-rebuild-failed warning to the sync banner when pack_error is set", async () => {
        const spy = vi.spyOn(globalThis, "fetch")
        spy.mockImplementation(async (input: RequestInfo | URL) => {
            const url = typeof input === "string" ? input : input.toString()
            if (url.endsWith("/translation/mods")) return mockJson([MOD])
            if (url.endsWith("/rescan")) return mockJson(SUMMARY)
            if (url.includes("/strings?status=")) return mockJson(STRINGS.filter((r) => url.includes(r.status)))
            if (url.endsWith("/strings")) return mockJson(STRINGS)
            if (url.endsWith("/sync")) return mockJson({ per_file: { "/abs/units.loc.tsv": 3 }, removed_orphans: 0, pack_built: false, pack_error: "no .pack found in X" })
            if (url.endsWith("/mod-context")) return mockJson({ source_game: "", character_name: "", background: "", source_language_override: null, target_language_override: null })
            return mockJson({ status: "ok" })
        })

        render(wrap())
        await syncAndConfirm(/Sync Changes/i)
        await waitFor(() => expect(screen.getByText(/pack rebuild failed: no \.pack found in X/i)).toBeInTheDocument())
    })

    it("scans for terms, then offers the new suggestions for review", async () => {
        const SUGGESTION = { english: "Cathay", source: "震旦", source_lang: "Chinese", category: "faction", reason: "place" }
        const pending: unknown[][] = [[], [SUGGESTION]]
        const spy = mockRouteFlow()
        const base = spy.getMockImplementation()!
        spy.mockImplementation(async (input, init) => {
            const url = String(input)
            if (url.endsWith("/glossary/suggestions/scan")) return mockJson({ status: "success", new: 1 })
            if (url.endsWith("/glossary/suggestions")) return mockJson(pending.length > 1 ? pending.shift() : pending[0])
            return base(input, init)
        })
        render(wrap())
        const scanButton = await screen.findByRole("button", { name: "Scan for Terms" })
        await act(async () => {
            fireEvent.click(scanButton)
        })
        expect(await screen.findByText("Found 1 new glossary term suggestion(s).")).toBeInTheDocument()
        const suggestionsButton = await screen.findByRole("button", { name: /Suggestions/ })
        await act(async () => {
            fireEvent.click(suggestionsButton)
        })
        expect(await screen.findByRole("dialog", { name: "Suggested Glossary Terms" })).toHaveTextContent("Cathay")
    })

    it("reloads the strings after accepting a suggestion under an edited English renames translations", async () => {
        const SUGGESTION = { english: "Nangao", source: "南皋", source_lang: "Chinese", category: "location", reason: "place" }
        let rows = STRINGS
        const spy = mockRouteFlow()
        const base = spy.getMockImplementation()!
        spy.mockImplementation(async (input, init) => {
            const url = String(input)
            if (url.endsWith("/glossary/suggestions")) return mockJson([SUGGESTION])
            if (url.endsWith("/glossary/suggestions/accept")) {
                rows = STRINGS.map((r) => (r.key === "k1" ? { ...r, translation_text: "Nangau Gate", canonical_status: "pending" } : r))
                return mockJson({ status: "success", accepted: 1, replaced: 1 })
            }
            if (url.endsWith("/strings")) return mockJson(rows)
            return base(input, init)
        })
        render(wrap())
        const suggestionsButton = await screen.findByRole("button", { name: /Suggestions/ })
        await act(async () => {
            fireEvent.click(suggestionsButton)
        })
        const dialog = await screen.findByRole("dialog", { name: "Suggested Glossary Terms" })
        fireEvent.click(within(dialog).getByRole("button", { name: "Edit Nangao" }))
        const box = within(dialog).getByRole("textbox", { name: "English for 南皋" })
        fireEvent.change(box, { target: { value: "Nangau" } })
        fireEvent.keyDown(box, { key: "Enter" })
        await act(async () => {
            fireEvent.click(within(dialog).getByRole("button", { name: /^Accept$/ }))
        })

        expect(await within(dialog).findByText("Nangau: updated 1 translation.")).toBeInTheDocument()
        expect(await screen.findByText("Nangau Gate")).toBeInTheDocument()
    })

    it("keeps saved Mod Context when the source language changes afterwards", async () => {
        const spy = mockRouteFlow()
        render(wrap())
        const contextButton = await screen.findByRole("button", { name: /^Mod Context$/ })
        await act(async () => {
            fireEvent.click(contextButton)
        })
        fireEvent.change(await screen.findByLabelText("Background"), { target: { value: "Cathay lore" } })
        await act(async () => {
            fireEvent.click(screen.getByRole("button", { name: "Save Context" }))
        })
        fireEvent.change(screen.getByLabelText(/Source Language/), { target: { value: "Korean" } })
        await waitFor(() => {
            const puts = spy.mock.calls.filter(([u, i]) => String(u).endsWith("/mod-context") && (i as RequestInit | undefined)?.method === "PUT")
            expect(puts).toHaveLength(2)
            expect(JSON.parse(String((puts[1][1] as RequestInit).body))).toMatchObject({ background: "Cathay lore", source_language_override: "Korean" })
        })
    })

    it("refreshes the Mod Glossary count when the glossary dialog closes", async () => {
        let glossary: Record<string, unknown> = { Phoenix: { source: "x", category: "factions" }, Sky: { source: "y", category: "lore" } }
        const spy = vi.mocked(globalThis.fetch)
        const base = spy.getMockImplementation()!
        spy.mockImplementation(async (input, init) => (String(input).endsWith("/glossary") ? mockJson(glossary) : base(input, init)))
        render(wrap())
        fireEvent.click(await screen.findByRole("button", { name: /Mod Glossary \(2\)/i }))
        const dialog = await screen.findByRole("dialog", { name: "Mod Glossary" })
        glossary = {}
        fireEvent.click(within(dialog).getByRole("button", { name: "Close" }))
        expect(await screen.findByRole("button", { name: /Mod Glossary \(0\)/i })).toBeInTheDocument()
    })

    it("refreshes the Mod Glossary count after a History restore", async () => {
        let glossary: Record<string, unknown> = {}
        const spy = vi.mocked(globalThis.fetch)
        const base = spy.getMockImplementation()!
        spy.mockImplementation(async (input, init) => {
            const url = String(input)
            if (url.endsWith("/glossary")) return mockJson(glossary)
            if (url.endsWith("/snapshots")) return mockJson([{ ulid: "01J", created_at: "2026-09-27T01:00:00Z", label: "Before deleting all glossary terms", kind: "auto" }])
            if (url.endsWith("/snapshots/01J/restore")) {
                glossary = { Phoenix: { source: "x", category: "factions" }, Sky: { source: "y", category: "lore" } }
                return mockJson({ status: "ok" })
            }
            return base(input, init)
        })
        render(wrap())
        await screen.findByRole("button", { name: /Mod Glossary \(0\)/i })
        fireEvent.click(screen.getByRole("button", { name: /History/i }))
        fireEvent.click(await screen.findByRole("button", { name: "Restore" }))
        const confirmDialog = await screen.findByRole("dialog", { name: "Restore snapshot" })
        fireEvent.click(within(confirmDialog).getByRole("button", { name: "Restore" }))
        expect(await screen.findByRole("button", { name: /Mod Glossary \(2\)/i })).toBeInTheDocument()
    })
})

describe("TranslationDetails Translate Names", () => {
    const DRAGON = { english: "Dragon Guard", source: "龙卫", source_lang: "Chinese", category: "unit", reason: "Translated unit name" }
    const ROAR = { english: "Dragon Roar", source: "龙吼", source_lang: "Chinese", category: "skill", reason: "Translated skill name" }
    const STAGED_PREVIEW = {
        ...PREVIEW,
        total_strings: 2,
        total_batches: 2,
        batch_plan: [
            { source_lang: "Chinese", keys: ["n1"], size: 1 },
            { source_lang: "Chinese", keys: ["n2"], size: 1 },
        ],
        stages: [
            { id: "units", label: "Units & Lords", categories: ["unit"], total_strings: 1, batch_plan: [{ source_lang: "Chinese", keys: ["n1"], size: 1 }] },
            { id: "skills", label: "Skills & Abilities", categories: ["skill"], total_strings: 1, batch_plan: [{ source_lang: "Chinese", keys: ["n2"], size: 1 }] },
        ],
    }

    /**
     * Route the names flow: the staged preview, batches that echo their keys, and name suggestions keyed by category.
     *
     * @param byCategory Suggestions returned for each requested category, and under "all" when no category is sent.
     * @returns The keys of every batch sent and the body of every name-suggestions request.
     */
    function mockNamesFlow(byCategory: Record<string, unknown[]>) {
        const batches: string[][] = []
        const suggestionBodies: Array<{ mod_id: string; categories?: string[] }> = []
        const spy = vi.mocked(globalThis.fetch)
        const base = spy.getMockImplementation()!
        spy.mockImplementation(async (input, init) => {
            const url = String(input)
            if (url.endsWith("/translate/preview")) return mockJson(STAGED_PREVIEW)
            if (url.endsWith("/translate/batch")) {
                const keys = JSON.parse(String(init?.body)).keys as string[]
                batches.push(keys)
                return mockJson({ status: "success", translated: keys.length, translations: Object.fromEntries(keys.map((k) => [k, `EN ${k}`])), suggestions: [] })
            }
            if (url.endsWith("/translate/name-suggestions")) {
                const body = JSON.parse(String(init?.body))
                suggestionBodies.push(body)
                return mockJson({ suggestions: byCategory[body.categories?.[0] ?? "all"] ?? [] })
            }
            return base(input, init)
        })
        return { batches, suggestionBodies }
    }

    /** Click Translate Names and confirm the run. */
    async function startNamesRun() {
        fireEvent.click(await screen.findByRole("button", { name: "Translate Names" }))
        fireEvent.click(await screen.findByRole("button", { name: /^Translate \d+ strings?$/i }))
    }

    it("translates one stage at a time and reviews each stage's names before the next", async () => {
        const { batches, suggestionBodies } = mockNamesFlow({ unit: [DRAGON], skill: [ROAR] })
        render(wrap())
        await startNamesRun()

        const first = await screen.findByRole("dialog", { name: "Suggested Glossary Terms" })
        expect(first).toHaveTextContent("Stage 1 of 2: Units & Lords")
        expect(first).toHaveTextContent("Dragon Guard")
        expect(batches).toEqual([["n1"]])
        expect(suggestionBodies[0].categories).toEqual(["unit"])

        fireEvent.click(within(first).getByRole("button", { name: "Continue to Skills & Abilities" }))
        await waitFor(() => expect(screen.getByRole("dialog", { name: "Suggested Glossary Terms" })).toHaveTextContent("Dragon Roar"))
        const second = screen.getByRole("dialog", { name: "Suggested Glossary Terms" })
        expect(second).toHaveTextContent("Stage 2 of 2: Skills & Abilities")
        expect(batches).toEqual([["n1"], ["n2"]])
        expect(suggestionBodies[1].categories).toEqual(["skill"])

        fireEvent.click(within(second).getByRole("button", { name: "Finish" }))
        expect(await screen.findByText("Translated 2 names.")).toBeInTheDocument()
        expect(screen.queryByRole("dialog", { name: "Suggested Glossary Terms" })).not.toBeInTheDocument()
    })

    it("pauses when a stage review is closed, and the banner resumes the next stage", async () => {
        const { batches } = mockNamesFlow({ unit: [DRAGON], skill: [ROAR] })
        render(wrap())
        await startNamesRun()
        const first = await screen.findByRole("dialog", { name: "Suggested Glossary Terms" })
        fireEvent.click(within(first).getByRole("button", { name: "Close" }))

        expect(await screen.findByText("Names paused after Units & Lords.")).toBeInTheDocument()
        expect(batches).toEqual([["n1"]])
        fireEvent.click(screen.getByRole("button", { name: "Continue to Skills & Abilities" }))
        await waitFor(() => expect(batches).toEqual([["n1"], ["n2"]]))
    })

    it("stops a paused names run from the banner", async () => {
        const { batches } = mockNamesFlow({ unit: [DRAGON], skill: [ROAR] })
        render(wrap())
        await startNamesRun()
        const first = await screen.findByRole("dialog", { name: "Suggested Glossary Terms" })
        fireEvent.click(within(first).getByRole("button", { name: "Close" }))
        fireEvent.click(await screen.findByRole("button", { name: "Stop" }))

        expect(await screen.findByText("Translated 1 name. Stopped before Skills & Abilities.")).toBeInTheDocument()
        expect(screen.queryByText("Names paused after Units & Lords.")).not.toBeInTheDocument()
        expect(batches).toEqual([["n1"]])
    })

    it("moves straight to the next stage when a stage has no new names", async () => {
        const { batches } = mockNamesFlow({ unit: [], skill: [ROAR] })
        render(wrap())
        await startNamesRun()

        const review = await screen.findByRole("dialog", { name: "Suggested Glossary Terms" })
        expect(review).toHaveTextContent("Stage 2 of 2: Skills & Abilities")
        expect(batches).toEqual([["n1"], ["n2"]])
    })

    it("suggests glossary terms from already translated names without translating anything", async () => {
        const { batches, suggestionBodies } = mockNamesFlow({ all: [DRAGON] })
        render(wrap())
        fireEvent.click(await screen.findByRole("button", { name: "Translate Names menu" }))
        fireEvent.click(screen.getByRole("menuitem", { name: "Suggest from Translated Names" }))

        expect(await screen.findByRole("dialog", { name: "Suggested Glossary Terms" })).toHaveTextContent("Dragon Guard")
        expect(suggestionBodies).toEqual([{ mod_id: MOD.workshop_id }])
        expect(batches).toEqual([])
    })

    it("says so when there are no new translated names to suggest", async () => {
        mockNamesFlow({ all: [] })
        render(wrap())
        fireEvent.click(await screen.findByRole("button", { name: "Translate Names menu" }))
        fireEvent.click(screen.getByRole("menuitem", { name: "Suggest from Translated Names" }))

        expect(await screen.findByText("No new name terms found.")).toBeInTheDocument()
        expect(screen.queryByRole("dialog", { name: "Suggested Glossary Terms" })).not.toBeInTheDocument()
    })

    it("saves the translated-names prompt setting from the Mod Context panel", async () => {
        const spy = vi.mocked(globalThis.fetch)
        render(wrap())
        const contextButton = await screen.findByRole("button", { name: /^Mod Context$/ })
        await act(async () => {
            fireEvent.click(contextButton)
        })
        const checkbox = screen.getByRole("checkbox", { name: "Also send translated names that aren't in the glossary" })
        expect(checkbox).not.toBeChecked()
        await act(async () => {
            fireEvent.click(checkbox)
        })

        expect(checkbox).toBeChecked()
        const put = spy.mock.calls.find(([u, i]) => String(u).endsWith("/mod-context") && (i as RequestInit | undefined)?.method === "PUT")
        expect(JSON.parse(String((put![1] as RequestInit).body))).toMatchObject({ include_translated_names: true, background: "" })
    })
})
