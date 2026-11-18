/**
 * Whether a run HAS a pass rate yet (owner decision OD-18), in one place.
 *
 * The API reports `pass_rate: 0` for a run that is still running, or one that
 * EXECUTED no test (none reported, or every result skipped: D-0005). That 0 is
 * not a measurement: drawn, it is a red 0 % run
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
  /** The executed outcomes (`core/pass_rate.py`: passed + failed + broken). Absent on older payloads. */
  passed_tests?: number | null
  failed_tests?: number | null
  broken_tests?: number | null
}

/** Did the run execute a test? `null` when the payload carries no counts (older payloads). */
function executedAny(run: RunPassRateInput): boolean | null {
  if (typeof run.passed_tests !== 'number' || typeof run.failed_tests !== 'number') return null
  return run.passed_tests + run.failed_tests + (run.broken_tests ?? 0) > 0
}

/** A run that has not finished: running, in progress, pending or queued. */
export function isRunInProgress(status: string | null | undefined): boolean {
  return /running|in[_-]?progress|pending|queued/i.test(status ?? '')
}

/**
 * The run's pass rate, or `null` when it has none: in flight, no tests, nothing executed
 * (every result skipped, D-0005), or no finite rate. No rate without evidence.
 */
export function measuredRunPassRate(run: RunPassRateInput): number | null {
  if (isRunInProgress(run.status) || !((run.total_tests ?? 0) > 0) || run.pass_rate == null) return null
  if (executedAny(run) === false) return null
  const rate = Number(run.pass_rate)
  return Number.isFinite(rate) ? rate : null
}

/** Why a run shows no pass rate, in words for a tooltip (`null` when it has one). */
export function noPassRateReason(run: RunPassRateInput): string | null {
  if (measuredRunPassRate(run) !== null) return null
  if (isRunInProgress(run.status)) return 'No pass rate: the run is still in progress'
  if ((run.total_tests ?? 0) > 0 && executedAny(run) === false) {
    return 'No pass rate: no test was executed (every result was skipped)'
  }
  return 'No pass rate: the run reported no tests'
}
