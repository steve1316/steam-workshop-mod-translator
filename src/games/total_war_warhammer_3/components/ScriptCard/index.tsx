import type { StepStatus } from "../../api"
import StepIndicator from "../StepIndicator"

/** Shape of a single helper-script entry rendered as a `ScriptCard`. */
export interface ScriptEntry {
    /** Backend `SCRIPT_REGISTRY` key. */
    id: string
    /** Display label rendered as the card heading. */
    label: string
    /** One-paragraph explanation of what the script does. */
    description: string
}

/** Props for `ScriptCard`. */
interface Props {
    /** Script metadata. */
    script: ScriptEntry
    /** True when this script is the currently-running run. */
    running: boolean
    /** True when another script is running and this one should be disabled. */
    disabled: boolean
    /** Progress of this script inside a running `update` pipeline. Replaces the Run button when set. */
    step?: StepStatus
    /** Called with the script id when the user clicks Run. */
    onRun: (scriptId: string) => void
    /** Called when the user clicks Cancel on the currently-running card. */
    onCancel: () => void
}

/**
 * One glass card on the Runner page representing a single helper script. Renders the label as a heading, the description as a paragraph, and a footer button that toggles between Run / Cancel / Run (disabled) depending on the run state. While the `update` pipeline runs, the button becomes a `StepIndicator` showing this script's progress.
 *
 * @param script Script metadata.
 * @param running Whether this script is the currently-running run.
 * @param disabled Whether the Run button should be disabled (another script is running).
 * @param step Progress of this script inside a running `update` pipeline, if any.
 * @param onRun Called with the script id when the user clicks Run.
 * @param onCancel Called when the user clicks Cancel.
 * @returns A `glass-card` element.
 */
export default function ScriptCard({ script, running, disabled, step, onRun, onCancel }: Props) {
    return (
        <div className="glass-card script-card">
            <h3 className="script-card-title">{script.label}</h3>
            <p className="script-card-description">{script.description}</p>
            {running ? (
                <button className="btn btn-outline" onClick={onCancel}>
                    Cancel
                </button>
            ) : step ? (
                <StepIndicator status={step} />
            ) : (
                <button className="btn btn-primary" onClick={() => onRun(script.id)} disabled={disabled}>
                    Run
                </button>
            )}
        </div>
    )
}
