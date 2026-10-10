import type { RowStatus } from "../utils/stringFilters"

/** A status badge's display label and the CSS class (from index.css) that colors it. */
export interface StatusChip {
    /** Uppercase badge text shown in the Status column. */
    label: string
    /** CSS class name applied alongside `status-badge`. */
    className: string
}

/** Canonical status -> Chrono Ark's badge label + class. Shared by both games' chips. */
const CHIPS: Record<RowStatus, StatusChip> = {
    synced: { label: "SYNCED", className: "status-synced" },
    untouched: { label: "UNTOUCHED", className: "status-untouched" },
    pending: { label: "PENDING", className: "status-translated" },
    changed: { label: "CHANGED", className: "status-changed" },
    missing: { label: "MISSING", className: "status-missing" },
    untranslatable: { label: "N/A", className: "status-untranslatable" },
}

/**
 * Map a canonical row status to its badge label and CSS class.
 * @param status - The canonical row status.
 * @returns The chip's display label and CSS class name.
 */
export function statusToChip(status: RowStatus): StatusChip {
    return CHIPS[status]
}
