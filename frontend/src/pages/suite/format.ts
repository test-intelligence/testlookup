/**
 * The suite pages' value formats (UX redesign P4: shared by the suite page's
 * Tests and Runs tabs and the former `/coverage/suite` body, so a duration or
 * a run date reads the same in every tab).
 */

/** An average duration: "—" when there is none (null, or 0: nothing timed). */
export function formatSuiteDuration(ms: number | null | undefined): string {
  if (!ms) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60_000).toFixed(1)}m`
}

/** A run's day, e.g. "Sep 18, 2026"; "—" when there is none. */
export function formatSuiteDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}
