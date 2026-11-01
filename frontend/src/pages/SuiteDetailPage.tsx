import { useMemo, type ReactNode } from 'react'
import { Layers, AlertTriangle } from 'lucide-react'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useSuiteDetail } from '@/hooks/useMetrics'
import { useSuiteTrend } from '@/hooks/useSuiteTrend'
import type { SuiteDetailRun, SuiteDetailSummary, SuiteDetailTestCase } from '@/types/analytics'
import type { TrendPoint } from '@/types/metrics'
import type { SuiteTrendPoint } from '@/hooks/useSuiteTrend'
import StackedColumnChartFrame from '@/components/charts/StackedColumnChartFrame'
import TimeSeriesChartFrame from '@/components/charts/TimeSeriesChartFrame'
import { readyState } from '@/components/charts/chartStateCore'
import {
  buildStackedColumnModel,
  STATUS_STACK_SERIES,
  utcDayLabel,
  type StackedColumnModel,
} from '@/components/charts/stackedColumnModel'
import { buildTimeSeriesModel, timeSeriesFromTrends, type TimeSeriesModel } from '@/components/charts/timeSeriesModel'
import SuiteDetailAdvanced from '@/components/reports/catalogue/SuiteDetailAdvanced'

/**
 * The suite's per-day counts as stacked columns. The days are zero-filled by
 * `suite_history_service`, so a day without a run is a MEASURED zero (a tick
 * on the baseline), not a gap.
 */
function runHistoryModel(points: readonly SuiteTrendPoint[]): StackedColumnModel {
  return buildStackedColumnModel({
    buckets: points.map((p) => ({
      key: p.date,
      label: utcDayLabel(p.date),
      values: { passed: p.passed_count, failed: p.failed_count, broken: p.broken_count, skipped: p.skipped_count },
    })),
    // Four statuses, four colours and decals, in the kit's one stack order (R2 F4).
    series: STATUS_STACK_SERIES,
    valueTitle: 'Executions',
    bucketTitle: 'Day (UTC)',
    xType: 'time',
  })
}

/**
 * The suite's pass rate PER DAY (owner decision OD-4), from the same per-day
 * counts as the run history. The rate is passed over what was evaluated —
 * skipped is outside it, as everywhere in the kit (`timeSeriesFromTrends`) —
 * and a day with nothing evaluated has no rate at all: a gap, never 0 %.
 */
function suitePassRateModel(points: readonly SuiteTrendPoint[]): TimeSeriesModel {
  const trend: TrendPoint[] = points.map((p) => {
    const evaluated = p.passed_count + p.failed_count + p.broken_count
    return {
      date: p.date,
      passed: p.passed_count,
      failed: p.failed_count,
      broken: p.broken_count,
      skipped: p.skipped_count,
      total: p.total_tests,
      pass_rate: evaluated > 0 ? (p.passed_count / evaluated) * 100 : 0,
    }
  })
  return buildTimeSeriesModel({ points: timeSeriesFromTrends(trend) })
}

/** What one point of the per-day pass rate is, stated under the chart's title (R2 F7). */
const PASS_RATE_POINT_NOTE = 'One point per day with runs · a day without runs is a gap, never 0%'

/**
 * Both charts' plot height: the VIZ-106 floor for a report chart (it was 220).
 */
export const SUITE_CHART_HEIGHT = 240

// ── The body's data ───────────────────────────────────────────────────────────

/**
 * One suite's reads for a window: the analytics roll-up (summary, per-test
 * rows, recent runs) and the per-day trend. The suite page's Charts tab reads
 * through here; SWR shares the requests with any other caller of the same
 * suite and window.
 */
function useSuiteBody(suiteName: string, days: number) {
  const { data, isLoading, error } = useSuiteDetail(suiteName || null, days)

  // Per-day trend (run_count + passed/failed/skipped) for the same time
  // window. Owned by the new ``suite_history_service`` so every page
  // shows the same numbers. Keyed on (suiteName, activeProjectId, days) so
  // the chart refreshes in lockstep with the days selector and the active
  // project, without driving state from an effect.
  const { points: trendPoints } = useSuiteTrend(suiteName || null, days)
  const trendHasRuns = trendPoints.some((p) => p.run_count > 0)

  const summary: Partial<SuiteDetailSummary> = data?.summary ?? {}
  const testCases: SuiteDetailTestCase[] = data?.test_cases ?? []
  const recentRuns: SuiteDetailRun[] = data?.recent_runs ?? []

  // Run-level aggregates (unique_tests / total_executions / recent_runs) are
  // populated even when the SDK shipped a TestNG/JUnit run without per-test
  // rows (e.g. JUnit XML with <testsuite tests=…> but no <testcase> elements,
  // or a live session whose Redis buffer evicted before persistence). The
  // page used to gate the *entire* view on ``testCases.length`` and fall
  // through to "No data for this suite" — hiding the real totals that every
  // other surface (/test-management, /reports/summary) shows. We now bail
  // only when there is genuinely nothing to summarise.
  const hasAnyData =
    (summary.unique_tests ?? 0) > 0
    || (summary.total_executions ?? 0) > 0
    || recentRuns.length > 0
    || trendHasRuns

  return { isLoading, error, summary, testCases, recentRuns, trendPoints, trendHasRuns, hasAnyData }
}

type SuiteBody = ReturnType<typeof useSuiteBody>

/** Loading, a failed read, or a window with nothing in it; otherwise `children`. */
function SuiteBodyState({ body, days, children }: { body: SuiteBody; days: number; children: ReactNode }) {
  if (body.isLoading) {
    return <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
  }
  if (body.error) {
    return (
      <EmptyState
        icon={<AlertTriangle className="h-8 w-8 text-[var(--status-failed)]" />}
        title="Could not load suite details"
        description="Check the console for errors or try again"
      />
    )
  }
  if (!body.hasAnyData) {
    return (
      <EmptyState
        icon={<Layers className="h-8 w-8" />}
        title="No data for this suite"
        description={`No test executions found in the last ${days} days`}
      />
    )
  }
  return <>{children}</>
}

// ── Sections ──────────────────────────────────────────────────────────────────

/**
 * The run history and the per-day pass rate. `primary`: the suite page's
 * Charts tab marks the run history as the tab's primary content (the page
 * template's `data-primary`).
 */
function SuiteTrendCharts({
  days,
  trendPoints,
  primary = false,
}: {
  days: number
  trendPoints: readonly SuiteTrendPoint[]
  primary?: boolean
}) {
  // Both charts read the one per-day series, so their days always line up.
  const historyModel = useMemo(() => runHistoryModel(trendPoints), [trendPoints])
  const passRateModel = useMemo(() => suitePassRateModel(trendPoints), [trendPoints])

  // Run history — per-day executions of this suite by status, on the chart
  // kit (VIZ-104). The caller hides it when no day in the window had a run,
  // so an empty suite does not show a row of zero columns. The run-by-run
  // pass rates stay in the suite page's Runs tab.
  const history = (
    <StackedColumnChartFrame
      title={`Run history — last ${days} days`}
      takeaway={`${trendPoints.reduce((a, p) => a + p.run_count, 0)} runs · ${trendPoints.reduce((a, p) => a + p.total_tests, 0)} executions`}
      headingLevel={3}
      state={readyState(trendPoints)}
      model={historyModel}
      height={SUITE_CHART_HEIGHT}
      bucketNoun="day"
    />
  )

  return (
    <>
      {primary ? <div data-primary="">{history}</div> : history}

      {/* Pass rate PER DAY (OD-4): the kit's time series over the same
          days, with its gap semantics — a day nobody ran is a gap.

          The common cadence (nightly on weekdays, every other day) leaves
          no two adjacent days with a rate, so the chart is a row of dots
          under a legend that shows a line (R2 F7). The dots stay apart:
          joining them across a day without runs would draw a trend
          through a day nobody measured, and Trends does not either (the
          kit keeps `connectNulls` off, and a no-run day and a skips-only
          day are the same `null` to it). The takeaway says what a point
          is instead, where a sighted reader sees it.

          The catalogue's additions (VIZ-408, plan 2.2): the trend overlays
          and the local zoom. No "apply as window": it was left out while
          the chart's page kept its own `?days` window; the suite page that
          hosts it now reads the global window, and the chart still offers
          none. No `rateTarget` either: the page has no target of its own
          to draw. */}
      <div data-catalogue-section="suite-pass-rate">
        <TimeSeriesChartFrame
          title={`Pass rate trend — last ${days} days`}
          takeaway={PASS_RATE_POINT_NOTE}
          headingLevel={3}
          state={readyState(trendPoints)}
          model={passRateModel}
          height={SUITE_CHART_HEIGHT}
          trendAnalysis
          zoom
        />
      </div>
    </>
  )
}

// ── The suite page's Charts tab ───────────────────────────────────────────────

/**
 * The suite page's Charts tab (UX redesign P4): the run history, the per-day
 * pass rate and the Wave 3 sections (test x run heatmap, test scatter). The
 * page's own header carries the KPIs, its Tests tab the per-test rows and its
 * Runs tab the recent runs, so none of them is repeated here.
 */
export function SuiteChartsPanel({ suiteName, days, projectId }: { suiteName: string; days: number; projectId?: string | null }) {
  const body = useSuiteBody(suiteName, days)
  return (
    <SuiteBodyState body={body} days={days}>
      <div className="space-y-4" data-suite-charts="">
        {body.trendHasRuns && <SuiteTrendCharts days={days} trendPoints={body.trendPoints} primary />}
        {/* EXEC-04b: the composite's last section, "Suite execution time", reads the suite's own project. */}
        <SuiteDetailAdvanced days={days} suiteName={suiteName} projectId={projectId} />
      </div>
    </SuiteBodyState>
  )
}
