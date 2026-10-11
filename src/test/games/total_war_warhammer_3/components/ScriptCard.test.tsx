import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"
import ScriptCard from "../../../../games/total_war_warhammer_3/components/ScriptCard"

const SCRIPT = { id: "update_dynamic_rors", label: "Dynamic RoR (default)", description: "Regenerate the Dynamic RoR pack from the current set of installed mods." }

describe("ScriptCard", () => {
    it("renders label, description, and an enabled Run button by default", () => {
        render(<ScriptCard script={SCRIPT} running={false} disabled={false} onRun={vi.fn()} onCancel={vi.fn()} />)
        expect(screen.getByRole("heading", { name: SCRIPT.label })).toBeInTheDocument()
        expect(screen.getByText(SCRIPT.description)).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /^run$/i })).toBeEnabled()
    })

    it("disables the Run button when disabled=true", () => {
        render(<ScriptCard script={SCRIPT} running={false} disabled={true} onRun={vi.fn()} onCancel={vi.fn()} />)
        expect(screen.getByRole("button", { name: /^run$/i })).toBeDisabled()
    })

    it("shows a Cancel button when running=true and clicking calls onCancel", async () => {
        const onCancel = vi.fn()
        render(<ScriptCard script={SCRIPT} running={true} disabled={false} onRun={vi.fn()} onCancel={onCancel} />)
        const cancelBtn = screen.getByRole("button", { name: /cancel/i })
        await userEvent.setup().click(cancelBtn)
        expect(onCancel).toHaveBeenCalledTimes(1)
    })

    it("calls onRun with the script id when Run is clicked", async () => {
        const onRun = vi.fn()
        render(<ScriptCard script={SCRIPT} running={false} disabled={false} onRun={onRun} onCancel={vi.fn()} />)
        await userEvent.setup().click(screen.getByRole("button", { name: /^run$/i }))
        expect(onRun).toHaveBeenCalledWith(SCRIPT.id)
    })

    it("replaces the Run button with a step indicator while the pipeline runs", () => {
        const { rerender } = render(<ScriptCard script={SCRIPT} running={false} disabled={true} step="running" onRun={vi.fn()} onCancel={vi.fn()} />)
        expect(screen.queryByRole("button", { name: /^run$/i })).not.toBeInTheDocument()
        expect(screen.getByRole("status")).toHaveTextContent("In-Progress...")
        rerender(<ScriptCard script={SCRIPT} running={false} disabled={true} step="done" onRun={vi.fn()} onCancel={vi.fn()} />)
        expect(screen.getByRole("status")).toHaveTextContent("Done")
    })
})
