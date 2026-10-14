/**
 * Trends › Execution time (EXEC-04b; absorbs TRND-09, the VIZ-406 remainder):
 * how long the window's runs took, from ONE read of
 * `/api/v1/analytics/execution-metrics`, scoped like every other Trends
 * section — the active project, the page's window (clamped to 90 days), the
 * top-bar release and the page's suite filter.
 *
 *   1. The totals: Wall clock · Test time (summed) · Parallelism · Runs.
 *   2. Wall clock vs test time per day (`ExecutionTimeChartFrame`).
 *   3. Execution time by suite: p50 / p95 of both figures, each with its
 *      change against the previous window of the same length.
 *   4. Biggest regressions: suites (5-vs-20 run medians, same branch) and
 *      tests (window median vs the duration baseline), the rule in words.
 *   5. The 20 slowest tests by p95 (`SlowestTests`, its scale clamped at the
 *      p90 so one outlier no longer flattens the rest) and the duration
 *      distribution (`DurationHistogram` over the server's fixed buckets).
 *
 * `null` is "not reported", never 0 (the contract's rule), and a block the
 * server could not measure is that block's not-measured state with the
 * server's reason as plain text. Every name (suite, test, branch) is
 * untrusted ingested text and is rendered only as React text.
 *
 * All Projects asks nothing: execution time is a per-project read, and the
 * section says so.
 *
 * EXEC-06: "Export CSV" in the totals' header downloads the per-suite table
 * for the SAME scope the section reads (`executionMetricsService.
 * downloadExecutionMetricsCsv`, through the authed client; a failure is its
 * own toast).
 */
import { useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { clsx } from 'clsx'
import ChartFrame from '@/components/charts/ChartFrame'
import { hasChartData, readyState, type ChartState } from '@/components/charts/chartStateCore'
import { formatPlainValue } from '@/components/charts/chartText'
import DurationChartFrame from '@/components/charts/DurationChartFrame'
import DurationHistogram from '@/components/charts/DurationHistogram'
import { histogramToChartSeries } from '@/components/charts/durationBuckets'
import ExecutionTimeChartFrame from '@/components/charts/ExecutionTimeChartFrame'
import {
  executionTimeFromDays,
  formatMs,
  formatParallelism,
  NOT_REPORTED,
  PARALLELISM_LABEL,
  SUMMED_LABEL,
  WALL_CLOCK_LABEL,
} from '@/components/charts/executionTimeModel'
import { useExecutionMetrics } from '@/hooks/useExecutionMetrics'
import { executionMetricsService } from '@/services/executionMetricsService'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { normalizeScope, type ScopeValue } from '@/lib/scopeParams'
import { suiteHrefByName } from '@/routing/suiteHref'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type {
  ExecutionMetrics,
  ExecutionScope,
  ExecutionSuiteRow,
  SuiteRegressionItem,
  TestRegressionItem,
} from '@/types/executionMetrics'
import { formatNumber } from '@/utils/formatters'
import { clampCatalogueDays } from './catalogueScope'
import {
  changeText,
  changeTitle,
  changeTone,
  executionState,
  histogramFromServer,
  histogramTakeaway,
  percentileText,
  previousWindowWords,
  ratioText,
  serverHistogramCaption,
  slowestFromServer,
  slowestTitle,
  slowTestName,
  suiteRuleText,
  testRuleText,
} from './ExecutionTimeSection.model'

// ── Words ────────────────────────────────────────────────────────────────────

export const EXECUTION_TITLE = 'Execution time'
export const PER_DAY_TITLE = 'Wall clock vs test time per day'
export const SUITES_TITLE = 'Execution time by suite'
export const REGRESSIONS_TITLE = 'Biggest regressions'
export const HISTOGRAM_TITLE = 'Duration distribution'
/** The in-app guide's topic for these figures (`content/guide/charts.md`). */
export const EXECUTION_GUIDE_HREF = '/docs/charts#execution-time'

export const ALL_PROJECTS_REASON = 'execution time is measured per project: pick one project in the top bar to see it'
export const ONE_DAY_EXECUTION_REASON =
  'a one-day window has one day, which is not a trend; pick 7 days or more to see execution time per day'

/** The placeholder height the catalogue's `LazySection` holds for this section, px. */
export const EXECUTION_SECTION_MIN_HEIGHT = 1_400
const PER_DAY_HEIGHT = 240
const SIDE_HEIGHT = 260

const CARD = 'flex min-w-0 flex-col gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4'
/**
 * The table's own cells: the global `.th` is the muted token, under 4.5:1 on
 * the card in the lab theme (axe, every impact, runs over this section).
 */
const TH = 'px-3 py-2 text-xs font-medium uppercase tracking-wider text-[var(--color-text-secondary)]'
const TD = 'px-3 py-2 text-sm'
const MUTED = 'text-xs text-[var(--color-text-secondary)]'
/**
 * A change's hue is a DOT beside its words, never the words' colour: the
 * status hues are under 4.5:1 as small text on the card (axe, signal theme),
 * and the sign and the tooltip already say slower or faster in words.
 */
const TONE_DOT = {
  worse: 'var(--status-failed)',
  better: 'var(--status-passed)',
} as const

// ── Totals ───────────────────────────────────────────────────────────────────

function Stat({ label, value, detail, testId }: { label: string; value: string; detail: string; testId: string }) {
  return (
    <div data-execution-stat={testId} className="min-w-0 rounded-md border border-[var(--color-border)] px-3 py-2">
      <dt className="text-[11px] font-medium uppercase text-[var(--color-text-secondary)]">{label}</dt>
      <dd className="m-0 mt-0.5 text-lg font-semibold tabular-nums text-[var(--color-text)]">{value}</dd>
      <dd className={clsx('m-0', MUTED)}>{detail}</dd>
    </div>
  )
}

function ExecutionTotals({ data, windowDays, onExport }: { data: ExecutionMetrics; windowDays: number; onExport: () => void }) {
  const { totals } = data
  const runs = (n: number) => `${formatNumber(n)} ${n === 1 ? 'run' : 'runs'}`
  const suiteGrain = data.per_day.grain === 'suite'
  return (
    <section aria-labelledby="execution-totals-title" data-execution-totals="" className={CARD}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="execution-totals-title" className="m-0 text-sm font-semibold text-[var(--color-text)]">
          {EXECUTION_TITLE}
        </h3>
        <div className="flex flex-wrap items-center gap-3">
          <Link to={EXECUTION_GUIDE_HREF} className="text-xs text-[var(--color-text-secondary)] underline decoration-dotted underline-offset-2 hover:text-[var(--color-text)]">
            How to read these figures
          </Link>
          {/* EXEC-06: the per-suite table as CSV, for the scope on screen. */}
          <button type="button" className="btn-secondary px-3 py-1 text-xs" data-execution-export="" onClick={onExport}>
            Export CSV
          </button>
        </div>
      </div>
      <dl className="m-0 grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(10rem, 1fr))' }}>
        <Stat
          testId="wall"
          label={WALL_CLOCK_LABEL}
          value={totals.wall_clock_ms === null ? NOT_REPORTED : formatMs(totals.wall_clock_ms)}
          detail={
            totals.wall_clock_ms === null
              ? 'no run in the window reported one'
              : `over the ${runs(totals.wall_runs)} that reported one, of ${formatNumber(totals.runs)}`
          }
        />
        <Stat
          testId="summed"
          label={SUMMED_LABEL}
          value={totals.summed_ms === null ? NOT_REPORTED : formatMs(totals.summed_ms)}
          detail={totals.summed_ms === null ? 'no test reported a duration' : `every test's duration added up, ${runs(totals.summed_runs)}`}
        />
        <Stat
          testId="parallelism"
          label={PARALLELISM_LABEL}
          value={formatParallelism(totals.parallelism)}
          detail={totals.parallelism === null ? 'needs runs with both figures' : 'summed ÷ wall clock, over the runs with both'}
        />
        <Stat
          testId="runs"
          label="Runs"
          value={formatNumber(totals.runs)}
          detail={`last ${windowDays === 1 ? '24 hours' : `${windowDays} days`}${data.branch ? ` · branch ${data.branch}` : ''}`}
        />
      </dl>
      {suiteGrain ? (
        <p data-execution-grain="" className={clsx('m-0', MUTED)}>
          Counted over the selected suites&apos; own executions, not whole runs.
        </p>
      ) : null}
    </section>
  )
}

// ── Per suite ────────────────────────────────────────────────────────────────

function ChangeCell({ value, pct, previous }: { value: string; pct: number | null; previous: string }) {
  const tone = changeTone(pct)
  return (
    <td className={clsx(TD, 'text-right tabular-nums')}>
      <span className={value === NOT_REPORTED ? 'text-[var(--color-text-secondary)]' : 'text-[var(--color-text)]'}>{value}</span>
      <span
        data-change-tone={tone}
        title={changeTitle(pct, previous)}
        className="ml-1.5 inline-flex items-center gap-1 text-[11px] text-[var(--color-text-secondary)]"
      >
        {tone !== 'none' ? (
          <span aria-hidden="true" className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: TONE_DOT[tone] }} />
        ) : null}
        {changeText(pct)}
      </span>
    </td>
  )
}

function SuitesTable({ data, windowDays }: { data: ExecutionMetrics; windowDays: number }) {
  const { suites } = data
  const previous = previousWindowWords(windowDays)
  return (
    <section aria-labelledby="execution-suites-title" data-execution-suites="" className={CARD}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="execution-suites-title" className="m-0 text-sm font-semibold text-[var(--color-text)]">
          {SUITES_TITLE}
        </h3>
        <span className={MUTED}>Change against the {previous}</span>
      </div>
      {suites.items.length === 0 ? (
        <p className={clsx('m-0', MUTED)}>No suite ran in this window.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className={clsx(TH, 'text-left')}>Suite</th>
                <th className={clsx(TH, 'text-right')}>Runs</th>
                <th className={clsx(TH, 'text-right')}>{WALL_CLOCK_LABEL} p50</th>
                <th className={clsx(TH, 'text-right')}>{WALL_CLOCK_LABEL} p95</th>
                <th className={clsx(TH, 'text-right')}>Test time p50</th>
                <th className={clsx(TH, 'text-right')}>Test time p95</th>
              </tr>
            </thead>
            <tbody>
              {suites.items.map((row: ExecutionSuiteRow) => (
                <tr key={row.suite_key} data-execution-suite={row.suite_key} className="border-t border-[var(--color-border)]">
                  <td className={clsx(TD, 'max-w-[240px] truncate')}>
                    <Link to={suiteHrefByName(row.suite_name)} title={row.suite_name} className="text-[var(--color-text)] hover:underline">
                      {row.suite_name}
                    </Link>
                  </td>
                  <td className={clsx(TD, 'text-right tabular-nums text-[var(--color-text-secondary)]')}>{formatNumber(row.current.runs)}</td>
                  <ChangeCell value={percentileText(row.current.wall, 'p50_ms')} pct={row.wall_p50_change_pct} previous={previous} />
                  <ChangeCell value={percentileText(row.current.wall, 'p95_ms')} pct={row.wall_p95_change_pct} previous={previous} />
                  <ChangeCell value={percentileText(row.current.summed, 'p50_ms')} pct={row.summed_p50_change_pct} previous={previous} />
                  <ChangeCell value={percentileText(row.current.summed, 'p95_ms')} pct={row.summed_p95_change_pct} previous={previous} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {suites.truncated ? (
        <p data-execution-suites-cut="" className={clsx('m-0', MUTED)}>
          Showing the {formatNumber(suites.items.length)} slowest of {formatNumber(suites.total)} suites.
        </p>
      ) : null}
    </section>
  )
}

// ── Regressions ──────────────────────────────────────────────────────────────

function SuiteRegression({ item }: { item: SuiteRegressionItem }) {
  const figure = item.metric === 'wall' ? 'wall clock' : 'test time (summed)'
  // The API may leave the ingested spelling out (`Optional[str]`): the suite key names it then.
  const name = item.suite_name || item.suite_key
  return (
    <li data-regression-suite={item.suite_key} className="text-sm text-[var(--color-text)]">
      <Link to={suiteHrefByName(name)} className="font-medium hover:underline">
        {name}
      </Link>
      {item.branch ? <span className="text-[var(--color-text-secondary)]"> on {item.branch}</span> : null}
      {': '}
      <strong className="font-semibold">{ratioText(item.ratio)} slower</strong> {figure}, a median of {formatMs(item.recent_median_ms)}{' '}
      over its last {formatNumber(item.recent_runs)} runs against {formatMs(item.baseline_median_ms)} over the{' '}
      {formatNumber(item.baseline_runs)} before.{' '}
      <span className="text-[var(--color-text-secondary)]">
        {item.was_slow_before ? 'Already this slow on the run before.' : 'New on the latest run.'}
      </span>
      {item.latest_run_id ? (
        <>
          {' '}
          <Link to={`/runs/${encodeURIComponent(item.latest_run_id)}`} className="text-xs underline decoration-dotted underline-offset-2">
            Latest run
          </Link>
        </>
      ) : null}
    </li>
  )
}

function TestRegression({ item }: { item: TestRegressionItem }) {
  return (
    <li data-regression-test={item.test_fingerprint} className="text-sm text-[var(--color-text)]">
      <span className="font-medium">{slowTestName(item)}</span>
      {': '}
      <strong className="font-semibold">{ratioText(item.ratio)}</strong> its baseline, a median of {formatMs(item.window_p50_ms)} over{' '}
      {formatNumber(item.window_n)} {item.window_n === 1 ? 'run' : 'runs'} against a {formatMs(item.baseline_mean_ms)} mean ({formatNumber(item.sample_count)}{' '}
      samples).
    </li>
  )
}

function RegressionBlock({
  heading,
  measured,
  reason,
  rule,
  empty,
  children,
}: {
  heading: string
  measured: boolean
  reason: string | null
  rule: string
  empty: string
  children: ReactNode[]
}) {
  return (
    <div className="min-w-0">
      <h4 className="m-0 text-xs font-semibold uppercase text-[var(--color-text-secondary)]">{heading}</h4>
      {!measured ? (
        <p data-regression-not-measured="" className="m-0 mt-1 text-sm text-[var(--color-text-secondary)]">
          Not measured: {reason ?? 'no history to compare'}.
        </p>
      ) : children.length === 0 ? (
        <p className="m-0 mt-1 text-sm text-[var(--color-text-secondary)]">{empty}</p>
      ) : (
        <ul className="m-0 mt-1 flex list-none flex-col gap-1.5 p-0">{children}</ul>
      )}
      <p className={clsx('m-0 mt-1', MUTED)}>{rule}</p>
    </div>
  )
}

function Regressions({ data }: { data: ExecutionMetrics }) {
  const { suites, tests, rule } = data.regressions
  return (
    <section aria-labelledby="execution-regressions-title" data-execution-regressions="" className={CARD}>
      <h3 id="execution-regressions-title" className="m-0 text-sm font-semibold text-[var(--color-text)]">
        {REGRESSIONS_TITLE}
      </h3>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <RegressionBlock
          heading="Suites"
          measured={suites.measured}
          reason={suites.reason}
          rule={suiteRuleText(rule)}
          empty={`No suite got slower by this rule (${formatNumber(suites.evaluated)} with enough history).`}
        >
          {suites.items.map((item) => (
            <SuiteRegression key={`${item.suite_key}\u001f${item.branch ?? ''}`} item={item} />
          ))}
        </RegressionBlock>
        <RegressionBlock
          heading="Tests"
          measured={tests.measured}
          reason={tests.reason}
          rule={testRuleText(rule)}
          empty={`No test got slower than its baseline (${formatNumber(tests.considered)} judged).`}
        >
          {tests.items.map((item) => (
            <TestRegression key={item.test_fingerprint} item={item} />
          ))}
        </RegressionBlock>
      </div>
    </section>
  )
}

// ── The section ──────────────────────────────────────────────────────────────

export interface ExecutionTimeSectionProps {
  /** The page's window, days. Clamped to 1..90 on the wire. */
  days: number
  /** The page's suite scope (`usePageSuiteFilter().suiteFilter`). */
  suiteFilter: ScopeValue
}

export default function ExecutionTimeSection({ days, suiteFilter }: ExecutionTimeSectionProps) {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const allProjects = activeProjectId === ALL_PROJECTS_ID
  const release = useReleaseScope()
  const windowDays = clampCatalogueDays(days)
  // A sorted, distinct list: the hook keys on it, so `[b, a]` and `[a, b]` are one read.
  const suiteNames = useMemo(() => normalizeScope(suiteFilter), [suiteFilter])
  // ONE scope for the read and the CSV export, so the file is the table on screen.
  const scope: ExecutionScope = {
    projectId: allProjects ? null : activeProjectId,
    days: windowDays,
    releaseIds: release ? [release] : undefined,
    suiteNames,
  }
  const read = useExecutionMetrics(scope)
  const exportCsv = () => void executionMetricsService.downloadExecutionMetricsCsv(scope)
  const retry = read.mutate ? () => void read.mutate() : undefined
  const top: ChartState<ExecutionMetrics> = allProjects
    ? { status: 'not-measured', reason: ALL_PROJECTS_REASON, meta: null }
    : executionState({ data: read.data, error: read.error, isLoading: read.isLoading, retry })
  const data = hasChartData(top) ? top.data : null

  const perDayModel = useMemo(() => (data ? executionTimeFromDays(data.per_day.points) : null), [data])
  const perDayState: ChartState<unknown> = !data
    ? top
    : windowDays < 2
      ? { status: 'not-measured', reason: ONE_DAY_EXECUTION_REASON, meta: null }
      : perDayModel && perDayModel.measured
        ? readyState(perDayModel)
        : { status: 'not-measured', reason: data.reason ?? 'no run in this scope in the window reported a time', meta: null }

  const slowestState = executionState({ data: read.data, error: read.error, isLoading: read.isLoading, retry }, (d) => d.slowest_tests)
  const slowest = useMemo(
    () => (data ? slowestFromServer(data.slowest_tests.items, data.slowest_tests.limit) : null),
    [data],
  )
  const histogramState = executionState({ data: read.data, error: read.error, isLoading: read.isLoading, retry }, (d) => d.duration_histogram)
  const histogram = useMemo(
    () => (data?.duration_histogram ? histogramFromServer(data.duration_histogram) : null),
    [data],
  )
  const histogramSeries = useMemo(() => (histogram ? histogramToChartSeries(histogram) : null), [histogram])
  const scopeLabel = `last ${windowDays} days`
  const loading = top.status === 'loading'

  return (
    <div data-catalogue-section="trends-execution" className="grid min-w-0 grid-cols-1 gap-3.5">
      {data ? <ExecutionTotals data={data} windowDays={windowDays} onExport={exportCsv} /> : null}
      <ExecutionTimeChartFrame
        title={PER_DAY_TITLE}
        headingLevel={3}
        height={PER_DAY_HEIGHT}
        state={perDayState}
        model={perDayModel}
        scopeLabel={scopeLabel}
        footer={
          data?.per_day.grain === 'suite' ? (
            <span data-catalogue-grain="">Counted per suite execution.</span>
          ) : data ? (
            <span data-catalogue-grain="">Counted per run.</span>
          ) : null
        }
      />
      {data ? <SuitesTable data={data} windowDays={windowDays} /> : null}
      {data ? <Regressions data={data} /> : null}
      {data || loading ? (
        // One column: a slow test's name is long (suite and test), and twenty of them need the width.
        <div className="grid min-w-0 grid-cols-1 items-start gap-3.5">
          <DurationChartFrame
            kind="slowest"
            title={slowestTitle(data?.slowest_tests.limit ?? 20, data?.slowest_tests.items.length ?? 20)}
            headingLevel={3}
            height={SIDE_HEIGHT}
            state={slowestState as ChartState<unknown>}
            slowest={slowest}
            scopeLabel={scopeLabel}
            footer={data ? <span data-catalogue-grain="">Counted per test execution.</span> : null}
          />
          <ChartFrame
            title={HISTOGRAM_TITLE}
            takeaway={data?.duration_histogram ? histogramTakeaway(data.duration_histogram) : undefined}
            headingLevel={3}
            height={SIDE_HEIGHT}
            state={histogramState as ChartState<unknown>}
            series={histogramSeries}
            chartType="Histogram"
            axes={{ x: 'Duration bucket', y: 'Executions' }}
            format={formatPlainValue}
            scopeLabel={scopeLabel}
            footer={data ? <span data-catalogue-grain="">Counted per test execution.</span> : null}
          >
            {histogram && data?.duration_histogram ? (
              <DurationHistogram
                model={histogram}
                title={HISTOGRAM_TITLE}
                height={SIDE_HEIGHT}
                caption={serverHistogramCaption(data.duration_histogram)}
              />
            ) : null}
          </ChartFrame>
        </div>
      ) : null}
    </div>
  )
}
