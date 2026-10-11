import { useEffect, useState } from "react"
import { FaSteam, FaFolderOpen } from "react-icons/fa"
import WorkshopCard from "../../../../components/WorkshopCard"
import { API_BASE } from "../../../../config"
import { highlightMatch } from "../../../../utils/text"
import PublishWorkshopDialog from "../PublishWorkshopDialog"
import ScriptRunButton from "../ScriptRunButton"

/**
 * Format a unix timestamp as a relative "<label> ... ago" string.
 *
 * Sub-hour deltas collapse to `"<1h ago"`, deltas under a day stay in whole hours, and anything 24h+ switches to days with a single decimal
 * (trailing `.0` is dropped so whole-day values read cleanly).
 *
 * @param label Prefix such as `"Rebuilt"` or `"Published"`.
 * @param unix Unix timestamp (seconds) of the event.
 * @param nowMs Current epoch time in milliseconds, defaulted to `Date.now()` so tests can inject a fixed clock.
 * @returns A human-readable label such as `"Rebuilt 3h ago"`, `"Published 1.2 days ago"`, or `"Rebuilt <1h ago"` when the delta is sub-hour.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function formatAgo(label: string, unix: number, nowMs: number = Date.now()): string {
    const deltaSeconds = Math.max(0, nowMs / 1000 - unix)
    const hours = Math.floor(deltaSeconds / 3600)
    if (hours < 1) return `${label} <1h ago`
    if (hours < 24) return `${label} ${hours}h ago`
    const days = parseFloat((deltaSeconds / 86400).toFixed(1))
    return `${label} ${days} days ago`
}

/** A single compat pack card shown on the TW3 Dashboard. */
export interface PackEntry {
    /** Display name of the compat pack. */
    title: string
    /** Steam Workshop item ID. */
    workshopId: string
    /** Backend script id that rebuilds this pack. Omit for mods that have no rebuild pipeline. */
    scriptId?: string
    /** Optional note shown when multiple packs share a script. */
    sharedNote?: string
}

/** Props for PackCardComponent. */
interface PackCardProps {
    /** A single entry from the TW3 `PACKS` array. */
    pack: PackEntry
    /** Trimmed dashboard search text. The matching part of the title is highlighted. */
    searchQuery?: string
}

/**
 * TW3-specific dashboard card. Wraps `WorkshopCard` with the pack title +
 * workshopId badge, then fills the body slot with an optional sharedNote, a
 * Rebuild `ScriptRunButton`, and a Steam workshop link.
 *
 * @param pack The pack metadata to render.
 * @param searchQuery Trimmed search text to highlight in the title.
 * @returns The rendered TW3 pack card.
 */
const PackCardComponent = ({ pack, searchQuery = "" }: PackCardProps) => {
    const [publishOpen, setPublishOpen] = useState(false)
    const [lastModifiedUnix, setLastModifiedUnix] = useState<number | null>(null)
    const [publishedUnix, setPublishedUnix] = useState<number | null>(null)
    const previewImageUrl = `${API_BASE}/games/total_war_warhammer_3/packs/${pack.workshopId}/preview`
    const workshopUrl = `https://steamcommunity.com/sharedfiles/filedetails/?id=${pack.workshopId}`

    useEffect(() => {
        let cancelled = false
        fetch(`${API_BASE}/games/total_war_warhammer_3/packs/${pack.workshopId}/last_modified`)
            .then((res) => (res.ok ? res.json() : null))
            .then((body) => {
                if (cancelled || body == null) return
                const ts = body.last_modified_unix
                if (typeof ts === "number") setLastModifiedUnix(ts)
                if (typeof body.published_unix === "number") setPublishedUnix(body.published_unix)
            })
            .catch(() => {})
        return () => {
            cancelled = true
        }
    }, [pack.workshopId])

    const subtitle =
        lastModifiedUnix !== null || publishedUnix !== null ? (
            <>
                {lastModifiedUnix !== null && <span title="Last time the local pack files changed on disk">{formatAgo("Rebuilt", lastModifiedUnix)}</span>}
                {lastModifiedUnix !== null && publishedUnix !== null && <br />}
                {publishedUnix !== null && <span title="Last time this item was updated on the Steam Workshop">{formatAgo("Published", publishedUnix)}</span>}
            </>
        ) : undefined

    return (
        <>
            <WorkshopCard previewImageUrl={previewImageUrl} previewAlt={pack.title} title={highlightMatch(pack.title, searchQuery)} idBadge={pack.workshopId} subtitle={subtitle}>
                {pack.sharedNote && <p style={{ fontSize: "0.85em", color: "var(--text-dim)", margin: "0.5rem 0" }}>{pack.sharedNote}</p>}
                <div className="mod-actions" style={{ flexWrap: "nowrap", gap: "0.5rem" }}>
                    {pack.scriptId ? (
                        <ScriptRunButton scriptId={pack.scriptId} label="Rebuild" className="btn-compact" />
                    ) : (
                        <button type="button" className="btn btn-primary btn-compact" disabled title="No rebuild script associated with this mod">
                            Rebuild
                        </button>
                    )}
                    <button className="btn btn-outline btn-compact" onClick={() => setPublishOpen(true)} title="Push the local pack to the Steam Workshop">
                        Publish
                    </button>
                    <button
                        type="button"
                        className="btn btn-outline btn-icon"
                        onClick={() => {
                            fetch(`${API_BASE}/games/total_war_warhammer_3/packs/${pack.workshopId}/open`, { method: "POST" }).catch(() => {})
                        }}
                        aria-label="Open local pack folder"
                        title="Open local pack folder"
                    >
                        <FaFolderOpen size={18} />
                    </button>
                    <a href={workshopUrl} target="_blank" rel="noopener noreferrer" className="btn btn-outline btn-icon" aria-label="Open Steam workshop page" title="Open Steam workshop page">
                        <FaSteam size={20} />
                    </a>
                </div>
            </WorkshopCard>
            {publishOpen && <PublishWorkshopDialog workshopId={pack.workshopId} title={pack.title} onClose={() => setPublishOpen(false)} />}
        </>
    )
}

export default PackCardComponent
