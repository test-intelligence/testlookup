/**
 * Wording for the summary report's flaky KPI — BUG-007.
 *
 * A user read **"Flaky 0"** on the summary report beside a flaky test on Flaky
 * Coach and reasonably called it a contradiction. Both numbers were right under
 * their own rule and neither rule was on screen: Flaky Coach needs 3 runs in 30
 * days, this count needs 5 of a test's last 10 plus a failure ratio inside
 * 10-90%. Measured live on 2026-09-21, the two disagreed on 2 of 5 projects.
 *
 * Separate from `SummaryReportPage.tsx` because exporting helpers alongside a
 * component breaks React fast refresh (`react-refresh/only-export-components`)
 * — the same reason `components/layout/settingsRoutes.ts` sits beside its
 * component rather than inside it.
 */
import type { FlakyCountCriteria } from '@/types/summaryReport'

/**
 * Spell out every condition the count applied.
 *
 * The numbers come from the payload and are never written here as literals.
 * This count also feeds the release gate's flaky hard cap, so a sentence that
 * hardcoded "5 runs" would keep saying so after the gate moved — stating the
 * old rule confidently is worse than stating none.
 */
export function flakyCriteriaSentence(c?: FlakyCountCriteria): string | undefined {
  if (!c) return undefined
  const lo = Math.round(c.min_failure_ratio * 100)
  const hi = Math.round(c.max_failure_ratio * 100)
  return (
    `Counted over each test's last ${c.window_runs} runs. A test qualifies with ` +
    `at least ${c.min_runs} of them, a failure rate between ${lo}% and ${hi}%, ` +
    `and at least ${c.min_flips} pass/fail flips. Tests with less history are ` +
    `not judged either way — Flaky Coach applies a looser rule and may list ` +
    `flaky tests this figure excludes.`
  )
}

/**
 * Zero is the reading that misleads, so it is the one that gets the words.
 *
 * `fmtPct` is duplicated as a one-line local rather than imported, because
 * importing it from the page would recreate the fast-refresh problem this
 * module exists to avoid.
 */
export function flakySubtitle(
  ratePct: number | null | undefined,
  criteria: FlakyCountCriteria | undefined,
  count: number | null | undefined,
): string {
  if (count === 0 && criteria) {
    // "0% of total" restates the zero; it does not explain it.
    return `none met the ${criteria.min_runs}-run threshold`
  }
  const rate =
    ratePct == null || Number.isNaN(ratePct) ? '—' : `${ratePct.toFixed(1)}%`
  return `${rate} of total`
}
