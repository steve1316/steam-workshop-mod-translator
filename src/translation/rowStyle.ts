import type { CSSProperties } from "react"
import type { RowStatus } from "../utils/stringFilters"

/**
 * Background tint for a translation table row, shared by both games.
 * @param status - The row's canonical status.
 * @param opts - `override` marks a row whose current translation differs from the synced/on-disk value (an unsynced edit).
 * @returns A style object, or undefined for rows needing no tint.
 */
export function canonicalRowStyle(status: RowStatus, opts: { override?: boolean } = {}): CSSProperties | undefined {
    if (status === "untranslatable") return { backgroundColor: "rgba(148, 163, 184, 0.1)" }
    if (status === "synced") return { backgroundColor: "rgba(52, 211, 153, 0.1)" }
    if (status === "changed") return { backgroundColor: "rgba(249, 115, 22, 0.1)" }
    if (opts.override) return { backgroundColor: "rgba(255, 220, 40, 0.15)" }
    return undefined
}
