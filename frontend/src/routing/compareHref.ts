/**
 * TRND-02: where Trends' "Compare runs" goes.
 *
 * With one suite selected, the latest-two comparison of that suite
 * (`/runs/compare?mode=latest&suite=<suite>`, `RunComparePage`'s latest mode);
 * with none, the manual form (`/runs/compare`). Latest mode needs ONE project
 * (`useLatestSuiteCompare(latestSuiteName, selectedProjectId)`), so in All
 * Projects there is no href and the reason says what to do instead.
 *
 * A suite name is CI text: it is percent-encoded into the query, never
 * concatenated raw (`a&b #c` must arrive as one `suite` value).
 */

export const COMPARE_RUNS_PATH = '/runs/compare'

/** Why "Compare runs" is off in All Projects (the ⋯ item's label carries it). */
export const COMPARE_PICK_PROJECT_REASON = 'pick a project'

/** The latest two runs of `suite`, compared. */
export function latestTwoHref(suite: string): string {
  return `${COMPARE_RUNS_PATH}?mode=latest&suite=${encodeURIComponent(suite)}`
}

/**
 * The ⋯ item's target: the latest-two form for the page's one suite, the
 * manual form otherwise; `href: null` (with the reason) in All Projects.
 */
export function compareRunsHref({
  suite,
  allProjects,
}: {
  suite: string | null
  allProjects: boolean
}): { href: string | null; reason: string | null } {
  if (allProjects) return { href: null, reason: COMPARE_PICK_PROJECT_REASON }
  const name = typeof suite === 'string' ? suite.trim() : ''
  return { href: name ? latestTwoHref(name) : COMPARE_RUNS_PATH, reason: null }
}
