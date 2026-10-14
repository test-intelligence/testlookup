/**
 * EXEC-05: wording for a run's duration regressions.
 */

const HISTORY = /^not enough history \((\d+) of (\d+)\)$/

/** The backend's reason, as a sentence: "not enough history (4 of 10)" -> "Not enough history yet (4 of 10 runs)". */
export function describeDurationReason(reason: string | null | undefined): string {
  if (!reason) return 'Not evaluated for this run yet.'
  const history = HISTORY.exec(reason)
  if (history) return `Not enough history yet (${history[1]} of ${history[2]} runs)`
  return reason.charAt(0).toUpperCase() + reason.slice(1)
}
