/**
 * Overview's catalogue sections (Wave 2.6, VIZ-408; plan 2.2 "Overview").
 *
 * Mounted by `OverviewPage` on every visit since Phase D, S1 (no flag), and
 * loaded as its own chunk. It takes the row that held the
 * Execution-trend card (owner decision OD-4: the pass-rate trend REPLACES it)
 * and adds a lazy row under it:
 *
 *   headline row  Pass rate trend (release markers) | Status breakdown donut
 *   lazy row      Top failing tests                 | Failure categories
 *
 * Where the numbers come from, and why:
 *
 *   - The trend and the donut are drawn from the day series the page ALREADY
 *     holds (`/metrics/trends`), passed in as `window`: zero requests, and the
 *     donut's totals are the sums of the same points the KPI sparklines and
 *     the flag-off foot strip read, so the chart and the numbers around it
 *     cannot disagree (OD-5, ARCH D7). `unknown` is the residual of each day's
 *     `total`, and is left out, not zeroed, when a day carries none.
 *   - Top failing is the one section no page payload answers. It goes through
 *     the chart pipeline's parts (`chartGet`: toast-free, abortable, inside
 *     the 4-in-flight cap), with the page's window clamped to 90 days by
 *     `useCatalogueParams`, and only once the reader is near it
 *     (`LazySection`).
 *   - Failure categories is the page's OWN `/analytics/failure-categories`
 *     read (the infra KPI's `useFailureCategories`), handed in as
 *     `categories` and adapted here. Asking the chart pipeline instead would
 *     send the same URL a second time under a second SWR key, and even the
 *     page's hook called from this lazy section would revalidate, because the
 *     section mounts after SWR's dedupe window (the `/releases` lesson).
 *   - "Has this project ever had a run?" is the page's own answer
 *     (`everHadRun`), never derived from a chart's filtered payload: a release
 *     filter that matches nothing must not read as a project with no runs.
 */
import { useMemo } from 'react'
import TimeSeriesChartFrame from '@/components/charts/TimeSeriesChartFrame'
import DonutChart from '@/components/charts/DonutChart'
import BarChart, { BreakdownChart } from '@/components/charts/BarChart'
import {
  CHART_RESPONSE_ACCESSORS,
  ChartPayloadError,
  readyState,
  resolveChartState,
  validateChartResponse,
  type ChartResponse,
  type ChartState,
} from '@/components/charts/chartState'
import { chartResponseFromFailureCategories, chartResponseFromTopFailing } from '@/components/charts/BarChart.model'
import { buildTimeSeriesModel, timeSeriesFromTrends, type ReleaseInput } from '@/components/charts/timeSeriesModel'
import { CATALOG_URLS, type CatalogParams } from '@/components/charts/chartCatalogSources'
import { useChartData, type ChartFetcher, type ChartKey } from '@/hooks/useChartData'
import { chartGet } from '@/services/chartApi'
import { useReleases } from '@/hooks/useReleases'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type { ScopeValue } from '@/lib/scopeParams'
import type { TrendPoint } from '@/types/metrics'
import type { Release } from '@/types/releases'
import LazySection from './LazySection'
import { useCatalogueParams } from './catalogueScope'
import { chartResponseFromStatusCounts, statusCountsFromTrendPoints } from './catalogueAdapters'

/** One UTC day of the page's window, and its `/metrics/trends` point (`null`: no runs that day). */
export interface OverviewTrendDay {
  iso: string
  point: TrendPoint | null
}

/** The page's own `useFailureCategories` read: its SWR entry, as the page holds it. */
export interface FailureCategoriesRead {
  /** The raw `/analytics/failure-categories` payload (`undefined` until it answers). */
  data: unknown
  error: unknown
  isValidating: boolean
  /** Asks the page's read again (the frame's Retry). */
  retry: () => void
}

export interface OverviewCatalogueProps {
  /** The page's window, days (already snapped to the page's options). */
  days: number
  /** The page's own day series, one entry per UTC day of the window, oldest first. */
  window: readonly OverviewTrendDay[]
  /** The page's `useTrendData` is still loading. */
  trendsLoading: boolean
  /** The page's failure-categories read, drawn as it is (no second request). */
  categories: FailureCategoriesRead
  /** The page's suite scope, for the section that asks the server (top failing). */
  suiteFilter: ScopeValue
  /** A suite or release filter narrows the data: an empty window is then the filter's doing. */
  filtersApplied: boolean
  /** The page's unfiltered existence probe: `null` while it has not answered. */
  everHadRun: boolean | null
}

/** The headline row's plot height: the Execution-trend card's, which the trend replaces. */
export const OVERVIEW_HEADLINE_HEIGHT = 260
/** The lazy row's plot height. */
export const OVERVIEW_BREAKDOWN_HEIGHT = 280
/**
 * A frame's title, takeaway, toolbar and footer around its plot, px: what a
 * lazy placeholder adds to the plot height so that nothing below it moves
 * when the section arrives. An estimate (it varies with the footer), kept on
 * the generous side: a placeholder a little too tall shifts nothing upward.
 */
const FRAME_CHROME_PX = 110

const windowWords = (days: number) => (days === 1 ? 'the last 24 hours' : `the last ${days} days`)

/** Why the trend has nothing to draw although the window had runs. */
const NO_EVALUATED_DAY = 'no day in this window has an evaluated execution (only skips)'

/** A release as a marker: released, else planned; a release with neither is not drawn (`timeSeriesModel`). */
function releaseInputs(items: readonly Release[] | undefined): ReleaseInput[] {
  return (items ?? []).map((release) => ({
    id: release.id,
    name: release.name,
    date: release.released_at ?? release.planned_date,
  }))
}

/**
 * The state for a section drawn from the page's day series when the window
 * holds no day with runs. `null` when it does (the caller draws the data).
 *
 * The project never had a run: the first-run state. The probe has not
 * answered: loading (never a guess). A filter narrowed it: the frame's
 * filter words. Otherwise the window itself is empty, which is said as a fact
 * about the window, not blamed on filters nobody set.
 */
function emptyWindowState(
  hasDays: boolean,
  everHadRun: boolean | null,
  filtersApplied: boolean,
  days: number,
): ChartState<unknown> | null {
  if (hasDays) return null
  if (everHadRun === false) return { status: 'never-had-data' }
  if (everHadRun === null) return { status: 'loading' }
  if (filtersApplied) return { status: 'filtered-empty', meta: null }
  return { status: 'not-measured', reason: `No executions in ${windowWords(days)}.`, meta: null }
}

export default function OverviewCatalogue({
  days,
  window,
  trendsLoading,
  categories,
  suiteFilter,
  filtersApplied,
  everHadRun,
}: OverviewCatalogueProps) {
  // The days that HAD runs, dated by their UTC day (a payload date may carry a time).
  const points = useMemo(
    () => window.flatMap(({ iso, point }) => (point ? [{ ...point, date: iso }] : [])),
    [window],
  )

  return (
    <>
      <div className="grid grid-cols-1 xl:[grid-template-columns:minmax(0,1.6fr)_minmax(0,1fr)] gap-4">
        <div data-catalogue-section="overview-trend" className="min-w-0">
          <PassRateTrend
            days={days}
            window={window}
            points={points}
            loading={trendsLoading}
            filtersApplied={filtersApplied}
            everHadRun={everHadRun}
          />
        </div>
        <div data-catalogue-section="overview-donut" className="min-w-0">
          <StatusDonut
            days={days}
            points={points}
            loading={trendsLoading}
            filtersApplied={filtersApplied}
            everHadRun={everHadRun}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="min-w-0">
          <LazySection minHeight={OVERVIEW_BREAKDOWN_HEIGHT + FRAME_CHROME_PX} label="overview-top-failing">
            <div data-catalogue-section="overview-top-failing">
              <TopFailing days={days} suiteFilter={suiteFilter} everHadRun={everHadRun} />
            </div>
          </LazySection>
        </div>
        <div className="min-w-0">
          <LazySection minHeight={OVERVIEW_BREAKDOWN_HEIGHT + FRAME_CHROME_PX} label="overview-categories">
            <div data-catalogue-section="overview-categories">
              <FailureCategories days={days} categories={categories} everHadRun={everHadRun} />
            </div>
          </LazySection>
        </div>
      </div>
    </>
  )
}

// ── Headline row: drawn from the page's day series ───────────────────────────

function PassRateTrend({
  days,
  window,
  points,
  loading,
  filtersApplied,
  everHadRun,
}: {
  days: number
  window: readonly OverviewTrendDay[]
  points: readonly TrendPoint[]
  loading: boolean
  filtersApplied: boolean
  everHadRun: boolean | null
}) {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  // Already fetched by the top bar's release picker (and the chrome), so this
  // is an SWR cache read, not a new request. In All Projects the list spans
  // every project, and one project's release is not a boundary in another's
  // trend, so no markers are drawn there.
  const { data: releaseData } = useReleases(undefined, { cached: true })
  const allProjects = activeProjectId === ALL_PROJECTS_ID
  const releases = useMemo(() => (allProjects ? [] : releaseInputs(releaseData?.items)), [allProjects, releaseData])

  const { model, state } = useMemo(() => {
    if (loading) return { model: null, state: { status: 'loading' } as ChartState<unknown> }
    const empty = emptyWindowState(points.length > 0, everHadRun, filtersApplied, days)
    if (empty) return { model: null, state: empty }
    // Every day of the window is a bucket, so a quiet week keeps its width
    // instead of the line bridging it.
    const built = buildTimeSeriesModel({
      points: timeSeriesFromTrends([...points], { from: window[0]?.iso, to: window[window.length - 1]?.iso }),
      releases,
    })
    const measured = built.points.some((point) => point.rate !== null)
    const frameState: ChartState<unknown> = measured
      ? readyState(built)
      : { status: 'not-measured', reason: NO_EVALUATED_DAY, meta: null }
    return { model: built, state: frameState }
  }, [loading, points, everHadRun, filtersApplied, days, window, releases])

  return (
    <TimeSeriesChartFrame
      title="Pass rate trend"
      takeaway={`Pass rate and executions per day over ${windowWords(days)}, with release markers`}
      headingLevel={3}
      height={OVERVIEW_HEADLINE_HEIGHT}
      model={model}
      state={state}
    />
  )
}

function StatusDonut({
  days,
  points,
  loading,
  filtersApplied,
  everHadRun,
}: {
  days: number
  points: readonly TrendPoint[]
  loading: boolean
  filtersApplied: boolean
  everHadRun: boolean | null
}) {
  const state = useMemo<ChartState<ChartResponse>>(() => {
    if (loading) return { status: 'loading' }
    const empty = emptyWindowState(points.length > 0, everHadRun, filtersApplied, days)
    if (empty) return empty as ChartState<ChartResponse>
    // The window's totals: the sum of the same points the KPI sparklines and
    // the flag-off trend foot strip add up. A status any day did not report
    // is `null` (not measured), never a smaller real number.
    return readyState(chartResponseFromStatusCounts(statusCountsFromTrendPoints(points)))
  }, [loading, points, everHadRun, filtersApplied, days])

  return (
    <DonutChart
      title="Status breakdown"
      takeaway={`Executions by status over ${windowWords(days)}`}
      headingLevel={3}
      height={OVERVIEW_HEADLINE_HEIGHT}
      state={state}
      centreCaption="executions"
      axisLabel="Executions"
    />
  )
}

// ── Lazy row: top failing (its own request) and failure categories (the page's) ──

function TopFailing({ days, suiteFilter, everHadRun }: { days: number; suiteFilter: ScopeValue; everHadRun: boolean | null }) {
  // No `limit`: the endpoint's default (15) is more than the 10 bars drawn, so
  // a tie at the 10th bar can be admitted and said (`topN`), not cut blind.
  const params = useCatalogueParams(days, suiteFilter)
  const state = useTopFailing(params, everHadRun)
  return (
    <BarChart
      title="Top failing tests"
      takeaway={`Tests with the most failures over ${windowWords(days)}`}
      headingLevel={3}
      variant="ranked"
      topN={10}
      height={OVERVIEW_BREAKDOWN_HEIGHT}
      state={state}
      dimension="Test"
      valueAxisLabel="Failures"
    />
  )
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/**
 * Two different tests can share a name (one name in two suites): drawn as
 * they come, they are two bars with the same label, and nothing on the chart
 * tells them apart (R2-2). So a name that appears more than once is drawn with
 * its suite, "login times out (Auth)", as Summary's failing-test bars draw
 * every name; a name used once stays as it is, so the common case keeps its
 * short label. A twin with no suite keeps its bare name (nothing is invented).
 *
 * Names are counted in a `Map`, never as object keys: an ingested test can be
 * called `constructor` or `__proto__`. The payload is copied, never changed in
 * place.
 */
function nameTwinsBySuite(payload: unknown): unknown {
  if (!isRecord(payload) || !Array.isArray(payload.items)) return payload
  const items: unknown[] = payload.items
  const seen = new Map<string, number>()
  for (const item of items) {
    if (!isRecord(item)) continue
    const name = String(item.test_name ?? '')
    seen.set(name, (seen.get(name) ?? 0) + 1)
  }
  return {
    ...payload,
    items: items.map((item) => {
      if (!isRecord(item)) return item
      const name = String(item.test_name ?? '')
      const suite = typeof item.suite_name === 'string' ? item.suite_name.trim() : ''
      return (seen.get(name) ?? 0) > 1 && suite ? { ...item, test_name: `${name} (${suite})` } : item
    }),
  }
}

/**
 * Top failing through the chart pipeline's own parts (`chartGet`: toast-free,
 * abortable, inside the 4-in-flight cap; the legacy adapter; the contract
 * check), with twins named by suite before the adapter turns names into
 * labels. Its own SWR key: these labels are not the shared adapter's.
 */
function useTopFailing(params: CatalogParams | null, everHadRun: boolean | null): ChartState<ChartResponse> {
  const key: ChartKey | null = params === null ? null : (['overview-top-failing', params] as const)
  const fetcher: ChartFetcher<ChartKey> = async (_key, { signal }) => {
    const result = await chartGet(CATALOG_URLS['top-failing'], { params: params ?? {}, signal })
    return { data: chartResponseFromTopFailing(nameTwinsBySuite(result.data)), requestId: result.requestId }
  }
  return useChartData<ChartResponse, ChartKey>(key, fetcher, { validate: validateChartResponse, everHadData: everHadRun })
}

/**
 * The page's failure-categories read as a frame state: the same adapter and
 * contract check the chart pipeline applies (`chartCatalogSources`), then the
 * same resolution (`resolveChartState`), so a failed or malformed answer is
 * the frame's error with Retry, and an empty one follows the page's
 * `everHadRun`, exactly as if the pipeline had asked.
 */
function categoriesState(read: FailureCategoriesRead, everHadRun: boolean | null): ChartState<ChartResponse> {
  let data: ChartResponse | undefined
  let error = read.error
  if (read.data !== undefined) {
    const checked = validateChartResponse(chartResponseFromFailureCategories(read.data))
    if (checked.ok) data = checked.value
    else if (error === undefined || error === null) error = new ChartPayloadError(checked.errors, null)
  }
  return resolveChartState({
    data,
    error,
    isValidating: read.isValidating,
    everHadData: everHadRun,
    accessors: CHART_RESPONSE_ACCESSORS,
    retry: read.retry,
  })
}

function FailureCategories({
  days,
  categories,
  everHadRun,
}: {
  days: number
  categories: FailureCategoriesRead
  everHadRun: boolean | null
}) {
  const { data, error, isValidating, retry } = categories
  const state = useMemo(
    () => categoriesState({ data, error, isValidating, retry }, everHadRun),
    [data, error, isValidating, retry, everHadRun],
  )
  return (
    <BreakdownChart
      title="Failure categories"
      takeaway={`AI-classified failures by category over ${windowWords(days)}`}
      headingLevel={3}
      height={OVERVIEW_BREAKDOWN_HEIGHT}
      state={state}
      dimension="Category"
      valueAxisLabel="Failures"
      centreCaption="failures"
    />
  )
}
