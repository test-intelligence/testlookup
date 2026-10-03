/**
 * The chart states themselves (VIZ-107): the types, and the three checks a
 * frame or a page makes on a state it was handed. See `chartState.ts` for
 * what each state means.
 *
 * A leaf module, apart from `chartState.ts` on purpose: `ChartFrame`, the
 * chart components and the pages that hand a frame a `readyState(...)` need
 * only these, while resolving a state from a request (validation, error
 * classification) is code only the catalogue sections run. In one module, the
 * resolver became live once the sections used it and rolldown placed it in the
 * frame chunk every flag-off chart page loads (Wave 2.6 R1-3). `chartState`
 * re-exports everything here, so no caller changes.
 */
import type { AnyChartSeries, ChartSeries, EnvelopeMeta } from '@/lib/viz/contracts'

/**
 * The canonical analytics response a chart reads: the C2 envelope plus a C3
 * series. `S` defaults to the four kinds every pre-Wave-3 reader handles
 * (`ChartSeries`), so a reader whose `switch (series.kind)` has no `points`
 * branch can never be handed one; a Wave-3 source names its own kind (a
 * `ChartResponse<MatrixChart>`, a `ChartResponse<PointsChart>`).
 */
export interface ChartResponse<S extends AnyChartSeries = ChartSeries> {
  meta: EnvelopeMeta | null
  series: S
}

/** A response of ANY C3 kind, `points` included (what `validateAnyChartResponse` accepts). */
export type AnyChartResponse = ChartResponse<AnyChartSeries>

export type ChartErrorKind =
  /** 5xx or an unexpected status: a server fault, never the user's. */
  | 'server'
  /** No response at all (offline, timeout, CORS). */
  | 'network'
  /** 422: a filter value the server rejected; `param` names it when the server says which. */
  | 'invalid-param'
  /** The response arrived but failed the contract validator: never half-drawn. */
  | 'invalid-payload'
  /** 429: wait `retryAfterSeconds` (when the server says). */
  | 'rate-limited'
  /** A lazy chunk from the previous deploy is gone. */
  | 'stale-build'
  /** 404 on the chart's endpoint. */
  | 'not-found'
  /** 401 AFTER the token refresh retried the request: the session is gone. */
  | 'session-expired'

export interface ChartError {
  kind: ChartErrorKind
  /** A sentence for the reader. Never blames the user for a server fault. */
  message: string
  requestId: string | null
  status: number | null
  param?: string | null
  retryAfterSeconds?: number | null
}

export type ChartState<T = unknown> =
  | { status: 'loading' }
  | { status: 'never-had-data' }
  | { status: 'filtered-empty'; meta: EnvelopeMeta | null }
  | { status: 'not-measured'; reason: string; meta: EnvelopeMeta | null }
  | {
      status: 'error'
      error: ChartError
      retry?: () => void
      /** A new request for this chart is in flight (e.g. after Retry). */
      revalidating?: boolean
    }
  | { status: 'forbidden'; requestId: string | null }
  | { status: 'truncated'; data: T; meta: EnvelopeMeta; shown: number; total: number; revalidating: boolean }
  | { status: 'ready'; data: T; meta: EnvelopeMeta | null; revalidating: boolean }

export type ChartStatus = ChartState['status']

export const CHART_STATUSES: readonly ChartStatus[] = [
  'loading',
  'never-had-data',
  'filtered-empty',
  'not-measured',
  'error',
  'forbidden',
  'truncated',
  'ready',
]

/** The states that draw the chart (the child renderer is mounted only for these). */
export function hasChartData<T>(state: ChartState<T>): state is Extract<ChartState<T>, { data: T }> {
  return state.status === 'ready' || state.status === 'truncated'
}

/**
 * The drawn state for a page that owns its own loading, empty and error
 * branches and mounts a kit frame only once it has something to draw (VIZ-104,
 * Wave 2.5 K0). `meta: null` because the data came through an existing
 * endpoint, not a `chart-data` envelope: an export then stamps "Scope
 * unavailable", which is true, rather than a scope nobody measured.
 */
export function readyState<T>(data: T): Extract<ChartState<T>, { status: 'ready' }> {
  return { status: 'ready', data, meta: null, revalidating: false }
}

/**
 * Whether a series has anything to plot, tabulate or export: at least one
 * point, cell or node. A point whose value is `null` COUNTS — it is a gap the
 * table states as "—" — so only a series with no positions at all is empty.
 * A drawn state can still carry such a series: the duration histogram with
 * nothing timed is `ready` and says so inside its own body.
 */
export function seriesHasPoints(series: AnyChartSeries): boolean {
  switch (series.kind) {
    case 'series':
      return series.series.some((line) => line.points.length > 0)
    case 'matrix':
      return series.cells.length > 0
    case 'tree':
    case 'graph':
      return series.nodes.length > 0
    case 'points':
      return series.points.length > 0
  }
}
