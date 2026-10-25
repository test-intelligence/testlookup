/**
 * W13 TCL-03: the copy of the Cases tab's health banner and of the Insights'
 * component lines, over the SERVER's library health
 * (`GET /test-management/health`). The page computes none of these numbers:
 * the 200-row roll and its synthesis are gone; this module only words them.
 *
 * Absence is not health: no project, a read in flight, a failed read and an
 * unmeasured library each say what they are, never `0`.
 */
import type { BannerState } from '@/components/ui/StatusBanner'
import type { LibraryHealth, LibraryHealthComponent } from '@/types/test-library'

/** = the server's `HEALTH_NEEDS_PROJECT_SENTENCE` (All Projects asks nothing). */
export const HEALTH_NEEDS_PROJECT_TEXT = 'Choose a project: library health is per project.'
export const HEALTH_LOAD_ERROR_TEXT = 'Could not load library health.'
/** The banner's title while the library holds no authored case (the server's `measured: false`). */
export const LIBRARY_EMPTY_TITLE = 'No authored test cases yet'
export const HEALTH_LOADING_TITLE = 'Library health'
export const NOT_MEASURED_TEXT = 'not measured'
/** = the server's `STRATEGY_GAP_TITLES` for the two in-place lists. */
export const FLAKY_ACTIVE_TITLE = 'Flaky and active'
export const NO_OWNER_TITLE = 'No owner'

const TAG_STATE: Record<string, BannerState> = { Healthy: 'ok', 'Needs attention': 'warn', 'At risk': 'fail' }

export interface HealthBanner {
  state: BannerState
  /** The tag in the pill ("AT RISK", not the state's "Failing"); none while pending. */
  pillLabel?: string
  title: string
  /**
   * The answer to read the four facts from, or null (no facts: never zeros).
   * The page words the facts itself (`label: authoredTotal === 1 ? 'Case' :
   * 'Cases'`, pinned in its source by `TestManagementPage.refresh.test.ts`).
   */
  factsFrom: LibraryHealth | null
}

export interface HealthReadState {
  projectId: string | null
  health: LibraryHealth | undefined
  error?: unknown
}

/** One component by key (`automation`, `stale_drafts`, `review_freshness`, `retired_in_open_plans`). */
export function healthComponent(health: LibraryHealth | undefined, key: string): LibraryHealthComponent | undefined {
  return health?.components.find((c) => c.key === key)
}

/** The automated share as the banner and the Automated stat print it ("33%"), or `not measured`. */
export function automatedPctText(health: LibraryHealth | undefined): string {
  const c = healthComponent(health, 'automation')
  return c?.measured && c.value != null ? `${Math.round(c.value)}%` : NOT_MEASURED_TEXT
}

/**
 * The banner: `pending` with no facts while there is no answer (no project,
 * loading, failed) or nothing to grade; else the server's tag and score, and
 * four facts read straight from the answer.
 */
export function bannerFor({ projectId, health, error }: HealthReadState): HealthBanner {
  if (!projectId) return { state: 'pending', title: HEALTH_NEEDS_PROJECT_TEXT, factsFrom: null }
  if (!health) {
    return { state: 'pending', title: error ? HEALTH_LOAD_ERROR_TEXT : HEALTH_LOADING_TITLE, factsFrom: null }
  }
  if (!health.measured || health.score == null || !health.tag) {
    return { state: 'pending', title: LIBRARY_EMPTY_TITLE, factsFrom: null }
  }
  return {
    state: TAG_STATE[health.tag] ?? 'pending',
    pillLabel: health.tag.toUpperCase(),
    title: `Library health ${health.score}/100`,
    factsFrom: health,
  }
}

/**
 * One Insights line per component: its label, its rounded value and the
 * server's sentence with both numbers ("Automated 38 · 13 of 34 cases
 * automated (target 60 %)"); an unmeasured one says so.
 */
export function componentLine(c: LibraryHealthComponent): string {
  if (!c.measured || c.value == null) return `${c.label} · ${NOT_MEASURED_TEXT}`
  return `${c.label} ${Math.round(c.value)} · ${c.detail}`
}
