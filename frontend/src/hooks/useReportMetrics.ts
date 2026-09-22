/**
 * The ONE request behind a report's context chrome (VIZ-301/302/305).
 *
 * `GET /api/v1/metrics/summary?include=report_metrics` with the report scope.
 * The same response feeds the metrics strip (its KPI fields and the
 * `report_metrics` block, C6), the context header and the filtered-dataset
 * summary (its `meta`, C2) — no second request from the chrome.
 *
 * `include=report_metrics` opts in to the strip's block: the server computes
 * it (two to three more queries) only when asked, so the page's own summary
 * call — which does not ask — pays nothing for the chrome.
 *
 * Why the chrome does NOT share the page's SWR entry (`useDashboardSummary`,
 * key `['metrics-summary', project, days, suite, release]`):
 *
 *   - the page's request does not send `include=report_metrics`, so its
 *     response is a SUBSET of this one (no block). Sharing a key would let
 *     whichever fetcher ran last decide whether the strip has figures;
 *   - the page polls (`REFRESH_INTERVALS.POLLING`) and toasts on failure; the
 *     chrome must never toast (`suppressToast`) and renders its own reason;
 *   - only Overview and Trends call `useDashboardSummary` at all.
 *
 * So on those two routes two summary requests remain, deliberately; the
 * server's 60 s summary cache answers the second from the same rows.
 *
 * The WINDOW sent is never the raw stored one: `/metrics/summary` rejects 0
 * ("All time") and anything over 90 with a 422. `ReportChrome` passes
 * `summaryWindow(stored, routeOptions).days` (the page's own snap, capped at
 * 90) and this hook clamps once more (`clampSummaryDays`), so no caller can
 * send a window the endpoint refuses.
 *
 * `meta` is validated with the shared contract guard before anything reads
 * it. A payload whose `meta` fails validation (or an older cached payload with
 * none) yields `meta: null` plus the reasons in `metaErrors`; the chrome then
 * says "context unavailable" rather than rendering fields it cannot trust.
 *
 * Wire shape (C1): no parameter for none, a scalar for one (byte-identical to
 * the legacy call), a repeated key for several. A release filter is never
 * sent in All-Projects mode (`release_requires_project`).
 *
 * The SWR key carries `scopeKey` strings (sorted, joined), never the arrays: a
 * fresh array per render would be a fresh key and a refetch per render.
 */
import useSWR from 'swr'
import { REPORT_WINDOW_OPTIONS } from '@/components/filters/filterOptions'
import { scopeKey, scopeParam } from '@/lib/scopeParams'
import { validateContract, type EnvelopeMeta } from '@/lib/viz/contracts'
import { getData } from '@/services/http'
import { scopedFetch } from '@/services/scopeAbort'
import { DEFAULT_TIME_WINDOW_DAYS, snapToAllowed } from '@/store/timeWindowStore'
import type { DashboardSummary } from '@/types/analytics'

export const REPORT_METRICS_ENDPOINT = '/api/v1/metrics/summary'
/** The dashboard summary's server cache TTL; SWR dedupes identical requests inside it. */
export const REPORT_METRICS_DEDUPE_MS = 60_000
/** The opt-in for the C6 block: without it the server does not compute `report_metrics`. */
export const REPORT_METRICS_INCLUDE = 'report_metrics'
/** `/metrics/summary` accepts `days` in 1..90 (`METRICS_SCOPE.max_days`). */
export const SUMMARY_MAX_WINDOW_DAYS = 90

export interface ReportMetricsScope {
  projectId: string | null
  allProjects: boolean
  releaseIds: readonly string[]
  suiteNames: readonly string[]
  windowDays: number
}

/** The KPI fields (`avg_pass_rate_7d.basis` included) plus the unvalidated
 *  envelope and the unvalidated strip block (C6, read by `metricsModel`
 *  through the shared guard). */
export type ReportSummary = DashboardSummary & { meta?: unknown; report_metrics?: unknown }

export interface ReportMetricsResult {
  summary: ReportSummary | null
  meta: EnvelopeMeta | null
  /** Why `meta` is null although a response arrived (contract errors, or "absent"). */
  metaErrors: string[]
  isLoading: boolean
  error: unknown
  /** A sentence for the chrome when the request failed (never a toast). */
  errorReason: string | null
}

/** The window a report route's page shows, and the one the chrome sends. */
export interface SummaryWindow {
  /** The stored window snapped to the page's own options: what its control shows. */
  pageDays: number
  /** What `/metrics/summary` is asked for: `pageDays`, capped at 90. */
  days: number
  /** Set only when `days` differs from `pageDays`: "max for summary". */
  note?: string
}

/** `days` inside what `/metrics/summary` accepts (1..90): never a 422. */
export function clampSummaryDays(days: number): number {
  if (!Number.isFinite(days)) return SUMMARY_MAX_WINDOW_DAYS
  return Math.min(SUMMARY_MAX_WINDOW_DAYS, Math.max(1, Math.round(days)))
}

/**
 * The window for the chrome on a report route, from the STORED one.
 *
 * First the same snap the route's page applies to the same stored value
 * (`snapToAllowed` against the page's options), so the chrome and the page
 * answer for the same window: a stored 0 ("All time", set on /runs) is 24
 * hours on Overview, exactly as Overview itself shows it. Then the endpoint's
 * cap: a page that offers a year (Value metrics) gets 90 days from the
 * summary, and the header says so ("max for summary") rather than implying a
 * year.
 */
export function summaryWindow(stored: number, options: readonly number[] = REPORT_WINDOW_OPTIONS): SummaryWindow {
  const pageDays = snapToAllowed(Number.isFinite(stored) ? stored : DEFAULT_TIME_WINDOW_DAYS, options)
  const days = clampSummaryDays(pageDays)
  return days === pageDays ? { pageDays, days } : { pageDays, days, note: 'max for summary' }
}

/** What the request is made with: the release axis is dropped in All-Projects mode. */
function effective(scope: ReportMetricsScope) {
  return {
    projectId: scope.allProjects ? null : scope.projectId,
    releaseIds: scope.allProjects ? [] : scope.releaseIds,
    suiteNames: scope.suiteNames,
    days: clampSummaryDays(scope.windowDays),
  }
}

/** The query parameters, exactly as sent. */
export function reportMetricsParams(scope: ReportMetricsScope): Record<string, string | number | string[]> {
  const e = effective(scope)
  return {
    ...(e.projectId ? { project_id: e.projectId } : {}),
    days: e.days,
    ...scopeParam('suite_name', e.suiteNames),
    ...scopeParam('release_id', e.releaseIds),
    include: REPORT_METRICS_INCLUDE,
  }
}

/** A stable, primitive-only SWR key. `null` → no request. */
export function reportMetricsKey(scope: ReportMetricsScope, enabled = true): string[] | null {
  if (!enabled) return null
  const e = effective(scope)
  return ['report-metrics', e.projectId ?? '*', scopeKey(e.releaseIds) ?? '', scopeKey(e.suiteNames) ?? '', String(e.days)]
}

/** Validate `meta` with the C2 guard. */
export function readMeta(payload: unknown): { meta: EnvelopeMeta | null; errors: string[] } {
  if (payload === null || typeof payload !== 'object' || !('meta' in payload)) {
    return { meta: null, errors: ['absent: the response carries no meta'] }
  }
  const result = validateContract('envelope', (payload as { meta: unknown }).meta)
  return result.ok ? { meta: result.value, errors: [] } : { meta: null, errors: result.errors }
}

/** "the request failed (HTTP 422)" — for the chrome's own inline reason. */
export function describeRequestError(error: unknown): string {
  const status = (error as { response?: { status?: unknown } } | null)?.response?.status
  return typeof status === 'number'
    ? `the metrics request failed (HTTP ${status})`
    : 'the metrics request failed (no response from the server)'
}

export function useReportMetrics(scope: ReportMetricsScope, { enabled = true }: { enabled?: boolean } = {}): ReportMetricsResult {
  const key = reportMetricsKey(scope, enabled)
  const { data, error, isLoading } = useSWR<ReportSummary>(
    key,
    // The chrome renders its own failure ("—" + reason): never the global toast.
    // `scopedFetch`: built from the SETTLED scope like the page's own reads, so
    // a request for a scope the user has since left is aborted with theirs.
    () =>
      scopedFetch(() =>
        getData<ReportSummary>(REPORT_METRICS_ENDPOINT, { params: reportMetricsParams(scope), suppressToast: true }),
      ),
    { dedupingInterval: REPORT_METRICS_DEDUPE_MS, revalidateOnFocus: false, keepPreviousData: true },
  )
  const { meta, errors } = data ? readMeta(data) : { meta: null, errors: [] }
  return {
    summary: data ?? null,
    meta,
    metaErrors: errors,
    isLoading,
    error,
    errorReason: error && !data ? describeRequestError(error) : null,
  }
}
