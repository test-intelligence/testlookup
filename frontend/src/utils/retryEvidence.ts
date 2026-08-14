/**
 * Retry evidence helpers.
 *
 * Roadmap Phase 1 (P1-B). `retry_count` and `is_flaky_run` are captured at
 * ingest and were never shown anywhere, so a test that only went green on its
 * third attempt rendered identically to one that passed first time. A retry is
 * evidence about stability, not a way to make the build green — surfacing it is
 * the point.
 *
 * Lives outside any page component so run detail, test detail and the history
 * timeline all agree on what "attempt" means.
 */

export interface RetryEvidence {
  /** Number of RETRIES (not attempts). Null when the producer never reported it. */
  retry_count?: number | null
  /** Some producers only flag the run as flaky without a count. */
  is_flaky_run?: boolean | null
}

/**
 * How many attempts a test needed to reach its final status.
 *
 * `retry_count` counts retries, so first-time-pass is 0 retries / 1 attempt.
 * A producer that sets only `is_flaky_run` still tells us at least one retry
 * happened, so that floors at 2 rather than silently reading as 1 — otherwise
 * the whole point (this test did not pass cleanly) is erased.
 */
export function retryAttempts(evidence: RetryEvidence): number {
  const retries = evidence.retry_count ?? 0
  if (retries > 0) return retries + 1
  return evidence.is_flaky_run ? 2 : 1
}

/** True when the test needed more than one attempt. */
export function wasRetried(evidence: RetryEvidence): boolean {
  return retryAttempts(evidence) > 1
}
