/**
 * Outcome arithmetic for a test run.
 *
 * Lives here rather than in the page so its regression guard can import the
 * real thing — a test that re-declares this logic would pass while the card
 * computed something else entirely — and so exporting it does not break the
 * page's fast refresh.
 *
 * Two rules, both of which the run-intelligence card previously broke:
 *
 *  - **A failure is FAILED *or* BROKEN.** That is the canonical set the rest
 *    of the product uses (`flaky_signals._FAILED_STATUSES`,
 *    `metrics_service._evaluated`, `/coverage`). Counting FAILED only
 *    rendered a run of 4 failed + 1 broken as "4 passed · 4 failed ·
 *    1 skipped" against a total of 10 — one test absent from its own result
 *    distribution.
 *  - **Skips belong in neither numerator nor denominator**, because a skipped
 *    test was never evaluated. Dividing by `total` produced 40.0% for a run
 *    whose own payload — and the `/runs` page — said 44.4%.
 *
 * `pass_rate` from the payload wins when present, so the two surfaces cannot
 * drift apart again; the computed form is the fallback.
 */
export interface RunOutcomeCounts {
  total_tests?: number | null
  passed_tests?: number | null
  failed_tests?: number | null
  broken_tests?: number | null
  skipped_tests?: number | null
  unknown_tests?: number | null
  pass_rate?: number | null
}

export interface RunOutcome {
  passed: number
  broken: number
  skipped: number
  /** FAILED + BROKEN — the canonical failure set. */
  failed: number
  total: number
  /** passed + failed; skips excluded. */
  evaluated: number
  passRate: number
}

export function computeRunOutcome(run: RunOutcomeCounts): RunOutcome {
  const passed = run.passed_tests ?? 0
  const broken = run.broken_tests ?? 0
  const skipped = run.skipped_tests ?? 0
  const failed = (run.failed_tests ?? 0) + broken
  const total = run.total_tests ?? (passed + failed + skipped + (run.unknown_tests ?? 0))
  const evaluated = passed + failed
  const passRate = run.pass_rate ?? (evaluated > 0 ? (passed / evaluated) * 100 : 0)
  return { passed, broken, skipped, failed, total, evaluated, passRate }
}
