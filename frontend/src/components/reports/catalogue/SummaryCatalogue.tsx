/**
 * The Summary report's catalogue sections (Wave 2.6, VIZ-408; plan 2.2
 * "Summary report").
 *
 * Mounted by `SummaryReportPage` in its own chunk, and only inside the page's
 * has-data branch (so "has this project ever had a run" is already yes). The
 * page mounts it once per place a section goes (`part`, a composition choice:
 * which sections render, never how they draw):
 *
 *   `headline`  the status donut beside the results-by-suite bars, then the
 *               trend (lazy): the three together, as before the parts split
 *   `suites`    the results-by-suite bars alone
 *   `status`    the status donut alone
 *   `trend`     the pass-rate trend alone (lazy, its own request)
 *
 * Since UX redesign P3 the page draws `suites` as its primary content and
 * `trend` inside a collapsed disclosure, and no `status` (the KPI row carries
 * its counts). The failures-by-test bars (`top-failing`) were deleted with
 * their last caller: they drew the top ten rows of the table under them.
 *
 * Two of the three charts are drawn from the `/reports/summary` payload the
 * page already holds, so they follow its window, release and Aggregation
 * toggle, and count its population: EACH UNIQUE TEST ONCE (the tiles' "per
 * unique test", F-067), not every execution. The trend is the one chart no
 * summary field answers; it is `/metrics/trends` — the day series Overview and
 * Trends draw — which counts EXECUTIONS in every run of the window whatever
 * the toggle says, and its caption says so, because two pass rates with
 * different denominators side by side are otherwise read as a contradiction.
 */
import { useMemo } from 'react'
import TimeSeriesChartFrame from '@/components/charts/TimeSeriesChartFrame'
import DonutChart from '@/components/charts/DonutChart'
import BarChart from '@/components/charts/BarChart'
import { hasChartData, readyState, type ChartState } from '@/components/charts/chartState'
import { buildTimeSeriesModel, timeSeriesFromTrends, type ReleaseInput } from '@/components/charts/timeSeriesModel'
import { dayWindow } from '@/components/charts/dayStrip.model'
import { useReleases } from '@/hooks/useReleases'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { utcDayIso } from '@/utils/calendarDay'
import type { Release } from '@/types/releases'
import type { SummaryReport, SummaryReportMode } from '@/types/summaryReport'
import LazySection from './LazySection'
import { useTrendsSeries } from './useTrendsSeries'
import { chartResponseFromStatusCounts, chartResponseFromSuiteRows } from './catalogueAdapters'
import {
  STATUS_TITLE,
  SUITES_TITLE,
  SUMMARY_BARS_HEIGHT,
  SUMMARY_DONUT_HEIGHT,
  SUMMARY_HEADLINE_CLASS,
  SUMMARY_HEADLINE_ROW_CLASS,
  SUMMARY_TREND_CHROME_PX,
  SUMMARY_TREND_HEIGHT,
  TREND_TITLE,
  statusTakeaway,
  suitesTakeaway,
  summaryTrendCaption,
} from './summaryCatalogueWords'

/** Which sections a mount renders: the three together (`headline`), or one of them. */
export type SummaryCataloguePart = 'headline' | 'suites' | 'status' | 'trend'

export interface SummaryCatalogueProps {
  part: SummaryCataloguePart
  /** The page's own `/reports/summary` payload, for the selected window, release and mode. */
  report: SummaryReport
  /** The page's window, days (one of 1, 7, 30, 90). */
  days: number
  /** The page's Aggregation toggle. */
  mode: SummaryReportMode
}

/** Why the trend has nothing to draw although the window had runs. */
const NO_EVALUATED_DAY = 'no day in this window has an evaluated execution (only skips)'

function releaseInputs(items: readonly Release[] | undefined): ReleaseInput[] {
  return (items ?? []).map((release) => ({
    id: release.id,
    name: release.name,
    date: release.released_at ?? release.planned_date,
  }))
}

export default function SummaryCatalogue({ part, report, days, mode }: SummaryCatalogueProps) {
  const donut = (
    <div data-catalogue-section="summary-donut" className="min-w-0">
      <StatusDonut report={report} mode={mode} />
    </div>
  )
  const bars = (
    <div data-catalogue-section="summary-suites" className="min-w-0">
      <ResultsBySuite report={report} mode={mode} />
    </div>
  )
  const trend = (
    <LazySection minHeight={SUMMARY_TREND_HEIGHT + SUMMARY_TREND_CHROME_PX} label="summary-trend">
      <div data-catalogue-section="summary-trend" className="min-w-0">
        <PassRateTrend days={days} />
      </div>
    </LazySection>
  )
  // One section alone. The bars and the donut keep the headline's box (its
  // bottom margin spaces what follows them as before); the trend alone has no
  // margin of its own (the page puts it in a disclosure).
  if (part === 'suites') return <div className={SUMMARY_HEADLINE_CLASS}>{bars}</div>
  if (part === 'status') return <div className={SUMMARY_HEADLINE_CLASS}>{donut}</div>
  if (part === 'trend') return <div className="min-w-0">{trend}</div>
  return (
    <div className={SUMMARY_HEADLINE_CLASS}>
      <div className={SUMMARY_HEADLINE_ROW_CLASS}>
        {donut}
        {bars}
      </div>
      {trend}
    </div>
  )
}

// ── From the report payload ──────────────────────────────────────────────────

function StatusDonut({ report, mode }: { report: SummaryReport; mode: SummaryReportMode }) {
  const { totals } = report
  // The report's own totals for the SELECTED mode: the tiles and counts beside
  // it read the same object, so the ring cannot disagree with them. No
  // `unknown`: the summary does not count it (and no `total` is passed, so the
  // adapter leaves the slice out rather than inventing a remainder).
  const state = useMemo(
    () =>
      readyState(
        chartResponseFromStatusCounts({
          passed: totals.passed,
          failed: totals.failed,
          broken: totals.broken,
          skipped: totals.skipped,
        }),
      ),
    [totals.passed, totals.failed, totals.broken, totals.skipped],
  )
  return (
    <DonutChart
      title={STATUS_TITLE}
      takeaway={statusTakeaway(report, mode)}
      headingLevel={2}
      height={SUMMARY_DONUT_HEIGHT}
      state={state}
      centreCaption="tests"
      axisLabel="Tests"
    />
  )
}

function ResultsBySuite({ report, mode }: { report: SummaryReport; mode: SummaryReportMode }) {
  const state = useMemo(() => readyState(chartResponseFromSuiteRows(report.suites)), [report.suites])
  return (
    <BarChart
      title={SUITES_TITLE}
      takeaway={suitesTakeaway(report, mode)}
      headingLevel={2}
      variant="stacked"
      height={SUMMARY_BARS_HEIGHT}
      state={state}
      dimension="Suite"
      valueAxisLabel="Tests"
    />
  )
}

// ── The one new request ──────────────────────────────────────────────────────

function PassRateTrend({ days }: { days: number }) {
  // The page's release scope (the report's own), not the suite filter: the
  // summary ignores suites, so its trend does too. `useTrendsSeries` clamps the
  // window to 90 days and asks nothing before the project resolves.
  const releaseScope = useReleaseScope()
  const trend = useTrendsSeries(days, releaseScope)
  const { data: releaseData } = useReleases(undefined, { cached: true })
  const releases = useMemo(() => releaseInputs(releaseData?.items), [releaseData])

  const { model, state } = useMemo(() => {
    if (!hasChartData(trend)) return { model: null, state: trend as ChartState<unknown> }
    const window = dayWindow(days, utcDayIso())
    const built = buildTimeSeriesModel({
      points: timeSeriesFromTrends(
        trend.data.data.map((point) => ({ ...point, date: point.date.slice(0, 10) })),
        { from: window[0], to: window[window.length - 1] },
      ),
      releases,
    })
    const measured = built.points.some((point) => point.rate !== null)
    const frameState: ChartState<unknown> = measured
      ? { ...trend, data: built }
      : { status: 'not-measured', reason: NO_EVALUATED_DAY, meta: null }
    return { model: built, state: frameState }
  }, [trend, days, releases])

  return (
    <TimeSeriesChartFrame
      title={TREND_TITLE}
      takeaway={summaryTrendCaption(days)}
      headingLevel={2}
      height={SUMMARY_TREND_HEIGHT}
      model={model}
      state={state}
    />
  )
}
