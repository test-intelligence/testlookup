/**
 * Standardized SWR refresh intervals (P4-4).
 *
 * Four tiers matched to data-change frequency:
 *   REALTIME  — live execution dashboards, agent pipelines (5 s)
 *   ACTIVE    — test run lists, dashboard summaries (15 s)
 *   POLLING   — notifications, defects (30 s)
 *   BACKGROUND — trends, analytics, coverage (60 s)
 *
 * All hooks should import from here — never hardcode intervals.
 */
export const REFRESH_INTERVALS = {
  /** Live execution, agent pipeline stages */
  REALTIME: 5_000,
  /** Test runs, active dashboards */
  ACTIVE: 15_000,
  /** Notifications, defect lists */
  POLLING: 30_000,
  /** Trends, coverage, analytics (slower-changing data) */
  BACKGROUND: 60_000,
} as const

export type RefreshTier = keyof typeof REFRESH_INTERVALS
