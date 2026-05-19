/**
 * Helpers for the "Compare with previous run" CTAs on /runs and
 * /runs/<id>. Both surfaces share the same deep-link target —
 * /runs/compare?mode=manual&left=<previous>&right=<current>&suite=<name>
 * — so the URL construction lives here once instead of being inlined
 * twice. The sibling helper ``buildBisectHref`` in ``pages/RunsPage.tsx``
 * follows the same shape; we considered merging both but kept them
 * separate because the inputs differ (bisect picks the last GREEN run,
 * this picks the chronologically immediately preceding run regardless
 * of status).
 */
import type { TestRun } from '@/types/runs'

/**
 * Pick the previous run of the same test suite, sorted chronologically.
 *
 * @param current     - The run the user is currently looking at.
 * @param candidates  - Pool of runs to search (typically what's already
 *                       loaded on the page, scoped to the active project).
 * @returns The run that was created immediately BEFORE ``current`` and
 *          carries the same ``primary_suite_name``, or ``null`` if no
 *          such run exists in the candidate pool.
 *
 * Notes
 * -----
 * * "Same suite" means same ``primary_suite_name``. We don't fall back
 *   to the multi-valued ``suite_names`` array because a partial overlap
 *   produces ambiguous diffs ("X passed, Y failed — but which subset
 *   did each cover?"). Returning ``null`` and letting the caller toast
 *   a clear "no previous run of this suite" is friendlier than a
 *   misleading compare.
 * * Runs that share the exact same ``created_at`` timestamp tie-break
 *   by ``id`` so the result is deterministic across renders. Same-
 *   timestamp runs are vanishingly rare in production but the
 *   determinism matters for unit tests.
 */
export function findPreviousRunOfSuite(
  current: Pick<TestRun, 'id' | 'created_at' | 'primary_suite_name'>,
  candidates: Pick<TestRun, 'id' | 'created_at' | 'primary_suite_name'>[],
): Pick<TestRun, 'id' | 'created_at' | 'primary_suite_name'> | null {
  const suite = current.primary_suite_name
  if (!suite) return null
  const currentTs = new Date(current.created_at).getTime()
  if (Number.isNaN(currentTs)) return null
  const sameSuite = candidates.filter(
    r => r.id !== current.id && r.primary_suite_name === suite,
  )
  const olderThanCurrent = sameSuite.filter(r => {
    const ts = new Date(r.created_at).getTime()
    return !Number.isNaN(ts) && ts < currentTs
  })
  olderThanCurrent.sort((a, b) => {
    const dt = new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    if (dt !== 0) return dt
    return a.id < b.id ? -1 : 1
  })
  return olderThanCurrent[0] ?? null
}

/**
 * Build the deep-link to /runs/compare that puts ``previous`` on the
 * left and ``current`` on the right, with the suite filter pre-populated.
 *
 * Returns ``null`` when there is no previous run (call sites should
 * toast a friendly "no previous run" instead of navigating to a
 * half-populated compare page).
 */
export function buildCompareWithPreviousHref(
  current: Pick<TestRun, 'id' | 'primary_suite_name'>,
  previous: Pick<TestRun, 'id' | 'primary_suite_name'> | null,
): string | null {
  if (!previous) return null
  const params = new URLSearchParams()
  params.set('mode', 'manual')
  // Left = baseline (older), right = the run the user is investigating.
  // Same orientation as the bisect CTA — the compare page treats LEFT
  // as the reference; swapping would flip every delta classification.
  params.set('left', previous.id)
  params.set('right', current.id)
  const suite = current.primary_suite_name || previous.primary_suite_name || ''
  if (suite) params.set('suite', suite)
  return `/runs/compare?${params.toString()}`
}
