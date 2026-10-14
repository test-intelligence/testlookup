/**
 * The deep-link of the "Compare to previous run" actions on /runs (per-row icon) and /runs/<id> (⋯ menu), and of
 * the Analysis tab's "vs previous run" card: /runs/compare?mode=manual&left=<previous>&right=<current>&suite=<name>.
 *
 * Which run is "previous" is the SERVER's rule since W03 (D42, `GET /runs/{id}/previous`): same project, primary
 * suite and branch, the newest run that finished before this one, any outcome. The client-side pick from a page of
 * loaded runs (same suite on ANY branch, by `created_at`) is gone, so every surface opens the same pair. The sibling
 * helper ``buildBisectHref`` in ``pages/RunsPage.tsx`` is separate on purpose: bisect starts from the last GREEN run.
 */

/** What the link needs of a run: its id and primary suite (a `TestRun`, or a `RunBrief` mapped to `id`). */
export interface CompareRunRef {
  id: string
  primary_suite_name?: string | null
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
  current: CompareRunRef,
  previous: CompareRunRef | null,
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
