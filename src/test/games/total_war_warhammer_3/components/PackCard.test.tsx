import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../../../games/total_war_warhammer_3/components/ScriptRunButton", () => ({
    default: ({ label }: { label: string }) => <button>{label}</button>,
}))

import PackCard, { formatAgo } from "../../../../games/total_war_warhammer_3/components/PackCard"

const BASE_PACK = {
    title: "Nanu's Dynamic RoR Compat",
    workshopId: "3513364573",
    scriptId: "update_dynamic_rors",
}

const wrap = (ui: React.ReactNode) => <MemoryRouter>{ui}</MemoryRouter>

beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ last_modified_unix: null }), { status: 200 }))
})

afterEach(() => {
    vi.restoreAllMocks()
})

describe("PackCard", () => {
    it("uses the shared compact and icon button classes in the action row", async () => {
        render(wrap(<PackCard pack={BASE_PACK} />))
        expect(await screen.findByRole("button", { name: /^Publish$/ })).toHaveClass("btn-compact")
        expect(screen.getByRole("button", { name: /Open local pack folder/ })).toHaveClass("btn-icon")
        expect(screen.getByRole("link", { name: /Open Steam workshop page/ })).toHaveClass("btn-icon")
    })

    it("renders the pack title and the workshopId in the badge", () => {
        render(wrap(<PackCard pack={BASE_PACK} />))
        expect(screen.getByRole("heading", { name: BASE_PACK.title, level: 3 })).toBeInTheDocument()
        expect(screen.getByText(BASE_PACK.workshopId)).toBeInTheDocument()
    })

    it("renders the sharedNote when present", () => {
        render(wrap(<PackCard pack={{ ...BASE_PACK, sharedNote: "Rebuilds with the other modified-attribute packs." }} />))
        expect(screen.getByText(/Rebuilds with the other modified-attribute packs/)).toBeInTheDocument()
    })

    it("does not render the sharedNote when absent", () => {
        render(wrap(<PackCard pack={BASE_PACK} />))
        expect(screen.queryByText(/Rebuilds with/)).not.toBeInTheDocument()
    })

    it("renders a Rebuild ScriptRunButton", () => {
        render(wrap(<PackCard pack={BASE_PACK} />))
        expect(screen.getByRole("button", { name: /Rebuild/i })).toBeInTheDocument()
    })

    it("renders a Steam workshop link with the correct URL", () => {
        render(wrap(<PackCard pack={BASE_PACK} />))
        const link = screen.getByRole("link", { name: /steam/i })
        expect(link).toHaveAttribute("href", `https://steamcommunity.com/sharedfiles/filedetails/?id=${BASE_PACK.workshopId}`)
    })

    it("points the preview image at the backend preview route", () => {
        render(wrap(<PackCard pack={BASE_PACK} />))
        const img = screen.getByAltText(BASE_PACK.title) as HTMLImageElement
        expect(img.src).toContain(`/api/games/total_war_warhammer_3/packs/${BASE_PACK.workshopId}/preview`)
    })

    it("renders a 'Rebuilt Nh ago' subtitle from the last_modified endpoint", async () => {
        const nowSeconds = Math.floor(Date.now() / 1000)
        const fiveHoursAgo = nowSeconds - 5 * 3600
        vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ last_modified_unix: fiveHoursAgo }), { status: 200 }))
        render(wrap(<PackCard pack={BASE_PACK} />))
        await waitFor(() => expect(screen.getByText("Rebuilt 5h ago")).toBeInTheDocument())
    })

    it("renders a 'Published Nh ago' line from published_unix", async () => {
        const nowSeconds = Math.floor(Date.now() / 1000)
        vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ last_modified_unix: nowSeconds - 3600, published_unix: nowSeconds - 3 * 3600 }), { status: 200 }))
        render(wrap(<PackCard pack={BASE_PACK} />))
        await waitFor(() => expect(screen.getByText("Published 3h ago")).toBeInTheDocument())
        expect(screen.getByText("Rebuilt 1h ago")).toBeInTheDocument()
    })

    it("omits the subtitle when the endpoint returns null", async () => {
        render(wrap(<PackCard pack={BASE_PACK} />))
        await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled())
        expect(screen.queryByText(/Rebuilt |Published /)).not.toBeInTheDocument()
    })

    it("omits the subtitle when the endpoint returns 404", async () => {
        vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 404 }))
        render(wrap(<PackCard pack={BASE_PACK} />))
        await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled())
        expect(screen.queryByText(/Rebuilt |Published /)).not.toBeInTheDocument()
    })
})

describe("formatAgo", () => {
    it("formats a delta of multiple hours as 'Updated Nh ago'", () => {
        const now = 1_750_000_000_000
        expect(formatAgo("Updated", now / 1000 - 5 * 3600, now)).toBe("Updated 5h ago")
        expect(formatAgo("Updated", now / 1000 - 23 * 3600, now)).toBe("Updated 23h ago")
    })

    it("switches to day format once the delta hits 24 hours", () => {
        const now = 1_750_000_000_000
        expect(formatAgo("Updated", now / 1000 - 24 * 3600, now)).toBe("Updated 1 days ago")
        expect(formatAgo("Updated", now / 1000 - Math.round(1.2 * 86400), now)).toBe("Updated 1.2 days ago")
        expect(formatAgo("Updated", now / 1000 - 25 * 86400, now)).toBe("Updated 25 days ago")
        expect(formatAgo("Updated", now / 1000 - 3021 * 86400, now)).toBe("Updated 3021 days ago")
    })

    it("renders sub-hour deltas as 'Updated <1h ago'", () => {
        const now = 1_750_000_000_000
        expect(formatAgo("Updated", now / 1000 - 60, now)).toBe("Updated <1h ago")
    })

    it("clamps negative deltas (clock skew) to '<1h ago'", () => {
        const now = 1_750_000_000_000
        expect(formatAgo("Updated", now / 1000 + 3600, now)).toBe("Updated <1h ago")
    })
})
