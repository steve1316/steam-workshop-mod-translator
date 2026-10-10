import { statusToChip } from "../../translation/statusChip"

describe("statusToChip", () => {
    it("maps each canonical status to Chrono Ark's badge label and CSS class", () => {
        expect(statusToChip("synced")).toEqual({ label: "SYNCED", className: "status-synced" })
        expect(statusToChip("untouched")).toEqual({ label: "UNTOUCHED", className: "status-untouched" })
        expect(statusToChip("pending")).toEqual({ label: "PENDING", className: "status-translated" })
        expect(statusToChip("changed")).toEqual({ label: "CHANGED", className: "status-changed" })
        expect(statusToChip("missing")).toEqual({ label: "MISSING", className: "status-missing" })
        expect(statusToChip("untranslatable")).toEqual({ label: "N/A", className: "status-untranslatable" })
    })
})
