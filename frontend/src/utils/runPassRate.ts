/**
 * Whether a run HAS a pass rate yet (owner decision OD-18), in one place.
 *
 * The API reports `pass_rate: 0` for a run that is still running, or one that
 * ran no tests. That 0 is not a measurement: drawn, it is a red 0 % run
 * nobody had. /runs (the "Avg pass rate" sparkline and average) and the
 * Intelligence Hub (the per-run meter) read the same run; with two copies of
 * this rule one of them called it "—" and the other "0 % bad" (R1 F5).
 *
 * Lives here, not in a page, so both pages import it without one lazy page
 * pulling the other into its chunk.
 */

export interface RunPassRateInput {
  status?: string | null
  total_tests?: number | null
  pass_rate?: number | null
}

/** A run that has not finished: running, in progress, pending or queued. */
export function isRunInProgress(status: string | null | undefined): boolean {
  return /running|in[_-]?progress|pending|queued/i.test(status ?? '')
}

/** The run's pass rate, or `null` when it has none yet: in flight, no tests, or no finite rate. */
export function measuredRunPassRate(run: RunPassRateInput): number | null {
  if (isRunInProgress(run.status) || !((run.total_tests ?? 0) > 0) || run.pass_rate == null) return null
  const rate = Number(run.pass_rate)
  return Number.isFinite(rate) ? rate : null
}
