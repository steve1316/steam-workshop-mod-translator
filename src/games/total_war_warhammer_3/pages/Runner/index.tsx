import { useEffect, useRef } from "react"
import { cancelRun, runStreamUrl, startRun } from "../../api"
import { kickPoll, useCurrentRun } from "../../hooks/useCurrentRun"
import { appendLine } from "../../hooks/useRunnerLog"
import ScriptCard, { type ScriptEntry } from "../../components/ScriptCard"
import RunnerLogTerminal from "../../components/RunnerLogTerminal"
import PageHeader from "../../../../ui/PageHeader"

/** The set of registered helper scripts shown on the Runner page. */
const SCRIPTS: ScriptEntry[] = [
    {
        id: "update_dynamic_rors",
        label: "Dynamic RoR (default)",
        description: "Regenerate the Dynamic RoR pack from the current set of installed mods. Default mode - run this most often.",
    },
    {
        id: "update_dynamic_rors_vanilla",
        label: "Dynamic RoR (--vanilla)",
        description: "Regenerate the Dynamic RoR pack with the --vanilla flag. Builds against the base game only, ignoring installed mods.",
    },
    {
        id: "update_double_unit_size",
        label: "2x Unit Size",
        description: "Regenerate the 2x Unit Size pack. Doubles unit sizes (and 4x for some unit categories) and rescales mass/HP.",
    },
    {
        id: "update_modified_attribute_mods",
        label: "Modified Attributes (3 packs)",
        description: "Regenerate the three Modified Attributes compat packs.",
    },
    {
        id: "process_main_units_tables",
        label: "Land Encounters",
        description: "Pre-process main_units and land_units tables for the Land Encounters workflow.",
    },
    {
        id: "glf_inner_join",
        label: "GLF Battle Mage",
        description: "Run the one-off TSV inner-join helper that copies combat attributes for the [GLF] Battle Mage mod.",
    },
    {
        id: "update_ttc_compat",
        label: "Tabletop Caps Compat",
        description:
            "Regenerate the Yet Another Tabletop Caps compat pack. Classifies every recruitable modded unit and writes the auto lists, with low-confidence picks listed in reports/ttc_review.md.",
    },
    {
        id: "update",
        label: "Update",
        description: "Top-level pipeline that runs every other update script in sequence.",
    },
]

/** Payload delivered on the SSE "done" event when a run finishes. */
interface DoneEvent {
    /** Process exit code, or null if the process was killed. */
    exit_code: number | null
    /** Total wall-clock seconds the run took, or null if unavailable. */
    duration_seconds: number | null
}

/**
 * TW3 Runner page. Renders a grid of `ScriptCard` instances and a persistent `RunnerLogTerminal` below. Starting a run appends a separator into the shared log; the active card swaps Run for Cancel and siblings disable. During the `update` pipeline, each script it rebuilds shows its progress instead. SSE lines append into the same shared log as data entries, with a closing separator on `done`.
 *
 * @returns The Runner page composition.
 */
export default function RunnerPage() {
    const run = useCurrentRun()
    const sourceRef = useRef<EventSource | null>(null)
    const activeScriptIdRef = useRef<string | null>(null)

    const labelFor = (scriptId: string): string => SCRIPTS.find((s) => s.id === scriptId)?.label ?? scriptId

    useEffect(() => {
        if (run.status !== "running") {
            sourceRef.current?.close()
            sourceRef.current = null
            return
        }
        if (sourceRef.current) return // already attached

        activeScriptIdRef.current = run.script_id
        const es = new EventSource(runStreamUrl())
        sourceRef.current = es

        es.onmessage = (evt) => {
            try {
                const payload = JSON.parse(evt.data) as { line: string; ts: string }
                appendLine({ kind: "data", line: payload.line, ts: payload.ts })
            } catch {
                /* ignore malformed lines */
            }
        }
        es.addEventListener("done", (evt) => {
            try {
                const info = JSON.parse((evt as MessageEvent).data) as DoneEvent
                const label = labelFor(activeScriptIdRef.current ?? "")
                const duration = info.duration_seconds != null ? `${info.duration_seconds.toFixed(0)}s` : "unknown time"
                appendLine({ kind: "separator", text: `--- ${label} exited with code ${info.exit_code} in ${duration} ---` })
            } catch {
                /* ignore */
            }
            es.close()
            sourceRef.current = null
            activeScriptIdRef.current = null
        })
        return () => {
            es.close()
            sourceRef.current = null
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [run.status, run.status === "running" ? run.run_id : null])

    const handleStart = async (scriptId: string) => {
        try {
            await startRun(scriptId)
            const label = labelFor(scriptId)
            const time = new Date().toLocaleTimeString()
            appendLine({ kind: "separator", text: `--- Starting ${label} at ${time} ---` })
        } catch (err) {
            console.error("Failed to start run", err)
        } finally {
            kickPoll()
        }
    }

    const handleCancel = async () => {
        try {
            await cancelRun()
        } catch (err) {
            console.error("Failed to cancel run", err)
        }
    }

    const runningScriptId = run.status === "running" ? run.script_id : null
    const steps = run.status === "running" ? run.steps : undefined

    return (
        <div className="page-fill">
            <PageHeader title="Runner" meta={<p>Run any of the helper_scripts/update_*.py scripts. Output accumulates in the terminal below across runs.</p>} />
            <div className="mod-grid">
                {SCRIPTS.map((s) => (
                    <ScriptCard
                        key={s.id}
                        script={s}
                        running={runningScriptId === s.id}
                        disabled={runningScriptId !== null && runningScriptId !== s.id}
                        step={steps?.[s.id]}
                        onRun={handleStart}
                        onCancel={handleCancel}
                    />
                ))}
            </div>
            <RunnerLogTerminal fill />
        </div>
    )
}
