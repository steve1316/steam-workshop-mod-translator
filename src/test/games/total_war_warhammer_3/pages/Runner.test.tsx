import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import RunnerPage from "../../../../games/total_war_warhammer_3/pages/Runner"
import { _resetUseRunnerLogForTests } from "../../../../games/total_war_warhammer_3/hooks/useRunnerLog"
import { _resetUseCurrentRunForTests } from "../../../../games/total_war_warhammer_3/hooks/useCurrentRun"

class FakeEventSource {
    static instances: FakeEventSource[] = []
    url: string
    onmessage: ((evt: MessageEvent) => void) | null = null
    onerror: ((evt: Event) => void) | null = null
    listeners: Record<string, ((evt: MessageEvent) => void)[]> = {}
    closed = false

    constructor(url: string) {
        this.url = url
        FakeEventSource.instances.push(this)
    }

    addEventListener(name: string, handler: (evt: MessageEvent) => void) {
        ;(this.listeners[name] ??= []).push(handler)
    }

    emit(data: string) {
        this.onmessage?.(new MessageEvent("message", { data }))
    }

    emitDone(data: string) {
        for (const h of this.listeners["done"] ?? []) {
            h(new MessageEvent("done", { data }))
        }
    }

    close() {
        this.closed = true
    }
}

beforeEach(() => {
    FakeEventSource.instances = []
    ;(globalThis as unknown as { EventSource: typeof FakeEventSource }).EventSource = FakeEventSource
    _resetUseRunnerLogForTests()
    _resetUseCurrentRunForTests()
    vi.spyOn(globalThis, "fetch").mockImplementation((url: unknown) => {
        const urlStr = String(url)
        if (urlStr.includes("/translation/mods")) {
            return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }))
        }
        return Promise.resolve(new Response(JSON.stringify({ status: "idle" }), { status: 200 }))
    })
})

afterEach(() => vi.restoreAllMocks())

const wrap = (ui: React.ReactNode) => <MemoryRouter>{ui}</MemoryRouter>

describe("Runner page", () => {
    it("renders 8 script cards in idle state", async () => {
        render(wrap(<RunnerPage />))
        await waitFor(() => expect(screen.getAllByRole("button", { name: /^run$/i })).toHaveLength(8))
    })

    it("renders descriptions on each card", async () => {
        render(wrap(<RunnerPage />))
        await waitFor(() => expect(screen.getAllByText(/Regenerate the Dynamic RoR pack/i).length).toBeGreaterThan(0))
    })

    it("starts a run, disables sibling cards, and shows Cancel on the active card", async () => {
        const fetchMock = vi.mocked(globalThis.fetch)
        fetchMock.mockImplementationOnce((url: unknown) => {
            const urlStr = String(url)
            if (urlStr.includes("/translation/mods")) return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }))
            return Promise.resolve(new Response(JSON.stringify({ status: "idle" }), { status: 200 }))
        })
        fetchMock.mockImplementationOnce((_url: unknown) => Promise.resolve(new Response(JSON.stringify({ run_id: "x", script_id: "update", started_at: new Date().toISOString() }), { status: 200 })))
        fetchMock.mockImplementation((url: unknown) => {
            const urlStr = String(url)
            if (urlStr.includes("/translation/mods")) return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }))
            return Promise.resolve(new Response(JSON.stringify({ status: "running", run_id: "x", script_id: "update", started_at: new Date().toISOString(), lines_emitted: 0 }), { status: 200 }))
        })
        render(wrap(<RunnerPage />))
        await waitFor(() => expect(screen.getAllByRole("button", { name: /^run$/i })).toHaveLength(8))
        const runButtons = screen.getAllByRole("button", { name: /^run$/i })
        await userEvent.setup().click(runButtons[runButtons.length - 1])
        await waitFor(() => expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument())
        const remainingRunButtons = screen.getAllByRole("button", { name: /^run$/i })
        expect(remainingRunButtons).toHaveLength(7)
        for (const btn of remainingRunButtons) expect(btn).toBeDisabled()
        expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/run/update"), expect.objectContaining({ method: "POST" }))
    })

    it("appends SSE data lines into the terminal", async () => {
        vi.mocked(globalThis.fetch).mockImplementation((url: unknown) => {
            const urlStr = String(url)
            if (urlStr.includes("/translation/mods")) return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }))
            return Promise.resolve(new Response(JSON.stringify({ status: "running", run_id: "x", script_id: "update", started_at: new Date().toISOString(), lines_emitted: 0 }), { status: 200 }))
        })
        render(wrap(<RunnerPage />))
        await waitFor(() => expect(FakeEventSource.instances.length).toBe(1))
        FakeEventSource.instances[0].emit(JSON.stringify({ line: "hello world", ts: "2026-05-12T00:00:00Z" }))
        await waitFor(() => expect(screen.getByText("hello world")).toBeInTheDocument())
    })

    it("preserves terminal output after a run ends", async () => {
        vi.mocked(globalThis.fetch).mockImplementation((url: unknown) => {
            const urlStr = String(url)
            if (urlStr.includes("/translation/mods")) return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }))
            return Promise.resolve(new Response(JSON.stringify({ status: "running", run_id: "x", script_id: "update", started_at: new Date().toISOString(), lines_emitted: 0 }), { status: 200 }))
        })
        render(wrap(<RunnerPage />))
        await waitFor(() => expect(FakeEventSource.instances.length).toBe(1))
        FakeEventSource.instances[0].emit(JSON.stringify({ line: "prior run line", ts: "2026-05-12T00:00:00Z" }))
        FakeEventSource.instances[0].emitDone(JSON.stringify({ exit_code: 0, duration_seconds: 3 }))
        await waitFor(() => expect(screen.getByText("prior run line")).toBeInTheDocument())
        expect(screen.getByText(/exited with code 0 in 3s/i)).toBeInTheDocument()
    })

    it("calls DELETE /run on cancel", async () => {
        const fetchMock = vi.mocked(globalThis.fetch)
        fetchMock.mockImplementation((url: unknown) => {
            const urlStr = String(url)
            if (urlStr.includes("/translation/mods")) return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }))
            return Promise.resolve(new Response(JSON.stringify({ status: "running", run_id: "x", script_id: "update", started_at: new Date().toISOString(), lines_emitted: 0 }), { status: 200 }))
        })
        render(wrap(<RunnerPage />))
        await waitFor(() => screen.getByRole("button", { name: /cancel/i }))
        await userEvent.setup().click(screen.getByRole("button", { name: /cancel/i }))
        await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/run"), expect.objectContaining({ method: "DELETE" })))
    })

    it("re-attaches the SSE stream when mounted while a run is already in progress", async () => {
        vi.mocked(globalThis.fetch).mockImplementation((url: unknown) => {
            const urlStr = String(url)
            if (urlStr.includes("/translation/mods")) return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }))
            return Promise.resolve(new Response(JSON.stringify({ status: "running", run_id: "x", script_id: "update", started_at: new Date().toISOString(), lines_emitted: 5 }), { status: 200 }))
        })
        render(wrap(<RunnerPage />))
        await waitFor(() => expect(FakeEventSource.instances.length).toBe(1))
    })

    it("fills the viewport under a shared page header and renders each script as a script card", async () => {
        const { container } = render(wrap(<RunnerPage />))
        await waitFor(() => expect(screen.getAllByRole("button", { name: /^run$/i })).toHaveLength(8))
        const page = container.querySelector(".page-fill")
        expect(page).not.toBeNull()
        expect(page).toContainElement(screen.getByRole("heading", { level: 1, name: "Runner" }))
        expect(container.querySelectorAll(".script-card")).toHaveLength(8)
    })

    it("offers a Tabletop Caps compat card that starts update_ttc_compat", async () => {
        const fetchMock = vi.mocked(globalThis.fetch)
        render(wrap(<RunnerPage />))
        const card = (await screen.findByRole("heading", { name: "Tabletop Caps Compat" })).closest(".script-card") as HTMLElement
        expect(card).toHaveTextContent("Yet Another Tabletop Caps")
        await userEvent.setup().click(within(card).getByRole("button", { name: /^run$/i }))
        await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/run/update_ttc_compat"), expect.objectContaining({ method: "POST" })))
    })

    it("shows each pipeline step's progress on its card while update runs and returns to Run afterwards", async () => {
        const steps = { update_dynamic_rors: "done", update_dynamic_rors_vanilla: "running", process_main_units_tables: "skipped", update_ttc_compat: "queued" }
        let state: object = { status: "running", run_id: "x", script_id: "update", started_at: new Date().toISOString(), lines_emitted: 0, steps }
        vi.mocked(globalThis.fetch).mockImplementation(() => Promise.resolve(new Response(JSON.stringify(state), { status: 200 })))
        render(wrap(<RunnerPage />))
        const cardFor = async (name: string) => (await screen.findByRole("heading", { name })).closest(".script-card") as HTMLElement
        await waitFor(async () => expect(within(await cardFor("Dynamic RoR (default)")).getByRole("status")).toHaveTextContent("Done"))
        expect(within(await cardFor("Dynamic RoR (--vanilla)")).getByRole("status")).toHaveTextContent("In-Progress...")
        expect(within(await cardFor("Land Encounters")).getByRole("status")).toHaveTextContent("Up to date")
        expect(within(await cardFor("Tabletop Caps Compat")).getByRole("status")).toHaveTextContent("Waiting...")
        expect(within(await cardFor("GLF Battle Mage")).getByRole("button", { name: /^run$/i })).toBeDisabled()
        expect(within(await cardFor("Update")).getByRole("button", { name: /cancel/i })).toBeInTheDocument()

        // The shared poll is already running, so the idle state lands on its next tick.
        state = { status: "idle" }
        await waitFor(() => expect(screen.getAllByRole("button", { name: /^run$/i })).toHaveLength(8), { timeout: 3000 })
        expect(screen.queryByRole("status")).not.toBeInTheDocument()
    })
})
