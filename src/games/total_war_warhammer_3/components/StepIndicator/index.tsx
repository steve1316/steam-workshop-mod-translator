import type { StepStatus } from "../../api"

/** Text shown for each pipeline step status. */
const LABELS: Record<StepStatus, string> = {
    queued: "Waiting...",
    running: "In-Progress...",
    done: "✓ Done",
    skipped: "Up to date",
    failed: "✕ Failed",
}

/** Props for `StepIndicator`. */
interface Props {
    /** Progress of the script inside the running `update` pipeline. */
    status: StepStatus
    /** Extra classes appended to the indicator (e.g. `btn-compact`). */
    className?: string
}

/**
 * Button-shaped status pill that stands in for a script's Run button while the `update` pipeline runs. Queued and running steps show a spinner.
 *
 * @param status Progress of the script inside the pipeline.
 * @param className Extra classes appended after the indicator classes.
 * @returns A `span` with `role="status"` describing the step.
 */
export default function StepIndicator({ status, className }: Props) {
    return (
        <span role="status" className={`btn btn-outline step-indicator step-indicator--${status}${className ? ` ${className}` : ""}`}>
            {(status === "queued" || status === "running") && <span className="step-indicator__spinner" aria-hidden="true" />}
            {LABELS[status]}
        </span>
    )
}
