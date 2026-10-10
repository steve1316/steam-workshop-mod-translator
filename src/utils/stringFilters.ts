import type { LocString } from "../shared_types"

export type RowStatus = "synced" | "untouched" | "pending" | "changed" | "missing" | "untranslatable"

export type FilterTab = "all" | "missing" | "untouched" | "pending" | "synced"

export type SortField = "is_translated" | "translated_by" | "key" | "source_file" | "source" | "english"

export type SortDirection = "asc" | "desc" | null

export type SortConfig = { key: SortField; direction: SortDirection }

/**
 * Determine the display status for a string table row.
 * @param s - The localization string to evaluate.
 * @returns The row status used for badge rendering.
 */
export function getRowStatus(s: LocString): RowStatus {
    if (s.untranslatable_reason) return "untranslatable"
    if (s.is_synced) return "synced"
    if (s.is_untouched) return "untouched"
    if (s.is_translated || !s.source.trim()) return "pending"
    return "missing"
}

/**
 * Determine the background style for a string table row.
 * @param s - The localization string to evaluate.
 * @returns A CSS style object with backgroundColor, or undefined for default rows.
 */
export function getRowStyle(s: LocString): React.CSSProperties | undefined {
    if (s.untranslatable_reason) return { backgroundColor: "rgba(148, 163, 184, 0.1)" }
    if (s.is_synced) return { backgroundColor: "rgba(52, 211, 153, 0.1)" }
    const hasOverride = !s.is_synced && s.english !== s.original_english
    if (hasOverride) return { backgroundColor: "rgba(255, 220, 40, 0.15)" }
    return undefined
}

/**
 * Filter strings by status tab and free-text search.
 * Rows with empty source text are always hidden.
 * @param strings - All localization strings.
 * @param filter - The active filter tab.
 * @param search - Free-text search query.
 * @returns Filtered array of strings.
 */
export function filterStrings(strings: LocString[], filter: FilterTab, search: string): LocString[] {
    const q = search.toLowerCase()
    return strings.filter((s) => {
        if (!s.source.trim()) return false

        const isDone = s.is_translated
        const isPending = isDone && !s.is_synced
        const isUntouched = !!s.is_untouched
        const isUntranslatable = !!s.untranslatable_reason
        const matchesFilter =
            filter === "all" ||
            (filter === "missing" && !isDone && !isUntranslatable) ||
            (filter === "untouched" && isUntouched) ||
            (filter === "pending" && isPending && !isUntouched) ||
            (filter === "synced" && s.is_synced)

        if (!matchesFilter) return false

        if (!q) return true
        return (
            s.key.toLowerCase().includes(q) ||
            s.source_file.toLowerCase().includes(q) ||
            s.source.toLowerCase().includes(q) ||
            s.english.toLowerCase().includes(q)
        )
    })
}

/**
 * Sort strings by the given column and direction.
 * Returns a new sorted array (does not mutate).
 * @param strings - Array of strings to sort.
 * @param config - Sort column and direction.
 * @returns Sorted copy of the array. Returns a shallow copy if direction is null.
 */
export function sortStrings(strings: LocString[], config: SortConfig): LocString[] {
    const result = [...strings]
    if (!config.direction) return result

    result.sort((a, b) => {
        const aValue = a[config.key]
        const bValue = b[config.key]
        if (aValue === bValue) return 0
        const comparison = aValue < bValue ? -1 : 1
        return config.direction === "asc" ? comparison : -comparison
    })

    return result
}
