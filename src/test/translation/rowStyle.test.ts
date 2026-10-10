import { describe, expect, it } from "vitest"
import { canonicalRowStyle } from "../../translation/rowStyle"

describe("canonicalRowStyle", () => {
    it("tints untranslatable grey, synced green, override yellow", () => {
        expect(canonicalRowStyle("untranslatable")).toEqual({ backgroundColor: "rgba(148, 163, 184, 0.1)" })
        expect(canonicalRowStyle("synced")).toEqual({ backgroundColor: "rgba(52, 211, 153, 0.1)" })
        expect(canonicalRowStyle("pending", { override: true })).toEqual({ backgroundColor: "rgba(255, 220, 40, 0.15)" })
    })

    it("tints changed rows orange", () => {
        expect(canonicalRowStyle("changed")).toEqual({ backgroundColor: "rgba(249, 115, 22, 0.1)" })
    })

    it("returns undefined for plain missing/untouched rows", () => {
        expect(canonicalRowStyle("missing")).toBeUndefined()
        expect(canonicalRowStyle("untouched")).toBeUndefined()
    })
})
