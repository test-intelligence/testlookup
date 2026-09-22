/**
 * The report routes (VIZ-301): every route that gets the report chrome —
 * context header, filter bar, chips, filtered summary and metrics strip —
 * mounted ONCE by the layout (`ReportChromeSlot`), behind `viz_report_context`.
 *
 * `reportRoutes.ratchet.test.tsx` holds this list to `App.tsx` in both
 * directions: every entry is a real route, and every route in App.tsx is
 * either listed here or in `NOT_REPORT_ROUTES` with a reason — so a new page
 * cannot land without someone deciding whether it is a report.
 */
import { matchPath } from 'react-router-dom'
import { REPORT_WINDOW_OPTIONS } from '@/components/filters/filterOptions'

/** Paths exactly as `App.tsx` registers them, with a leading slash. */
export const REPORT_ROUTES = [
  '/overview',
  '/trends',
  '/coverage',
  '/coverage/suite',
  '/failures',
  '/defects',
  '/reports/summary',
  '/value-metrics',
  '/intelligence',
  '/runs/:runId/intelligence',
  '/release-gate',
  '/runs/compare',
  '/flaky-coach',
] as const

export type ReportRoute = (typeof REPORT_ROUTES)[number]

/**
 * Routes in `App.tsx` that are deliberately NOT reports, with the reason. The
 * ratchet fails on any App.tsx route in neither list.
 */
export const NOT_REPORT_ROUTES: Record<string, string> = {
  '/getting-started': 'onboarding',
  '/docs': 'documentation',
  '/docs/:docId': 'documentation',
  '/runs': 'a run list with its own filters, not an aggregate report',
  '/runs/:runId': 'one run: its scope is the run itself',
  '/runs/:runId/tests/:testId': 'one test execution',
  '/suites': 'suite management',
  '/suites/:suiteId': 'suite management',
  '/canonical-test-cases/:canonicalId': 'one canonical test',
  '/search': 'search',
  '/chat': 'Ask-AI chat',
  '/agents': 'agent operations',
  '/agents/run/:runId': 'agent operations',
  '/agents/workflows': 'agent configuration',
  '/deep-investigate': 'one investigation',
  '/deep-investigate/:runId': 'one investigation',
  '/release-gate/:runId':
    "one run's gate verdict (run-scoped); the story's list names only /release-gate — owner decision",
  '/quarantine': 'a work queue',
  '/reviews': 'a work queue',
  '/test-management': 'test management',
  '/live': 'live execution: always "now", no window or release filter',
  '/my-failures': 'a personal inbox',
  '/settings': 'settings',
  '/projects': 'management',
  '/releases': 'management',
  '/releases/:releaseId': 'management',
  '/activity': 'an audit log',
  '/users': 'management',
  '/policies': 'management',
  '/policies/:policyId': 'management',
  '/ownership': 'management',
}

/** Every `/settings/...` management route is settings, not a report. */
export const NOT_REPORT_PREFIXES = ['/settings/'] as const

/**
 * The window options of a report page whose set differs from the report
 * default (`REPORT_WINDOW_OPTIONS`: 1, 7, 14, 30, 90). The chrome snaps the
 * stored window to the SAME set its page does (`snapToAllowed`), so both
 * answer for one window. Kept in step with each page's own constant
 * (`SummaryReportPage.DAYS_OPTIONS`, `ValueMetricsPage.VALUE_OPTIONS`).
 */
export const REPORT_ROUTE_WINDOW_OPTIONS: Partial<Record<ReportRoute, readonly number[]>> = {
  '/reports/summary': [1, 7, 30, 90],
  '/value-metrics': [7, 30, 90, 365],
}

/** The window options the page on `route` offers. */
export function windowOptionsFor(route: ReportRoute): readonly number[] {
  return REPORT_ROUTE_WINDOW_OPTIONS[route] ?? REPORT_WINDOW_OPTIONS
}

/** The report route pattern `pathname` matches, or `null`. */
export function matchReportRoute(pathname: string): ReportRoute | null {
  for (const pattern of REPORT_ROUTES) {
    if (matchPath({ path: pattern, end: true }, pathname)) return pattern
  }
  return null
}
