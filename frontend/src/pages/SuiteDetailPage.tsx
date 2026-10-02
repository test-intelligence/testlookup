import { useMemo } from 'react'
import { Link, useSearchParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Layers, CheckCircle2, XCircle, SkipForward,
  Clock, Activity, AlertTriangle, Calendar, FolderTree,
} from 'lucide-react'
import { clsx } from 'clsx'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useSuiteDetail } from '@/hooks/useMetrics'
import { useSuites } from '@/hooks/useSuites'
import { useSuiteTrend } from '@/hooks/useSuiteTrend'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import type { SuiteDetailSummary } from '@/types/analytics'
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
import { useCatalogueRolloutStatus } from '@/components/reports/catalogue/useCatalogueRollout'

const PERIODS = [
  { label: '1d',  days: 1 },
  { label: '7d',  days: 7 },
  { label: '14d', days: 14 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
] as const
const PERIOD_DAYS = PERIODS.map(p => p.days) as readonly number[]

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
 * Unconditional, flag on or off — the one change to this page's existing
 * baselines this wave makes on purpose.
 */
export const SUITE_CHART_HEIGHT = 240

/**
 * What the catalogue adds to the pass-rate frame when it is on (VIZ-408,
 * plan 2.2): the trend overlays and the local zoom. No "apply as window": this
 * page's window is its own `?days`, not the global one. No `rateTarget`
 * either: the page has no target of its own to draw. Off, the frame gets
 * NEITHER prop — not `false` — and is exactly the Wave 2.5 frame.
 */
const CATALOGUE_PASS_RATE_PROPS = { trendAnalysis: true, zoom: true } as const

/**
 * The pass-rate slot's height while the flag lookup is in flight, px: the
 * flag-off frame as drawn (header and takeaway above a 240 px plot, the legend
 * and the frame's padding), measured on the hermetic Suite detail page at 1280:
 * 361 (450 at 375, where the takeaway wraps). The flag-on frame is taller by
 * its overlay row and brush; holding the flag-off height means a flag-off
 * page (every project today) barely moves when the answer lands.
 */
export const SUITE_PASS_RATE_PENDING_HEIGHT = 360

/**
 * The pass-rate slot until the catalogue flag answers (R1-6, the Overview's
 * `TrendSlotPending` and Trends' D12 rule): neither frame yet, so the frame
 * mounts ONCE, in its final parent, instead of mounting bare and remounting
 * inside the catalogue section when a flag-on answer comes back after the
 * suite data.
 */
function PassRatePending() {
  return (
    <div
      className="card flex items-center justify-center"
      aria-busy="true"
      data-suite-pass-rate-pending=""
      style={{ minHeight: SUITE_PASS_RATE_PENDING_HEIGHT }}
    >
      <LoadingSpinner />
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function PassRateBar({ rate }: { rate: number }) {
  const color = rate >= 90 ? 'var(--status-passed)' : rate >= 70 ? 'var(--status-broken)' : 'var(--status-failed)'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-[var(--color-bg-hover)] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${Math.min(rate, 100)}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-xs font-mono font-semibold w-12 text-right" style={{ color }}>
        {Number(rate).toFixed(1)}%
      </span>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const s = (status || '').toUpperCase()
  const cls =
    s === 'PASSED'  ? 'bg-[var(--status-passed-bg)] text-[var(--status-passed)] ring-[var(--status-passed-bd)]' :
    s === 'FAILED'  ? 'bg-[var(--status-failed-bg)] text-[var(--status-failed)] ring-[var(--status-failed-bd)]' :
    s === 'BROKEN'  ? 'bg-[var(--status-broken-bg)] text-[var(--status-broken)] ring-[var(--status-broken-bd)]' :
    s === 'SKIPPED' ? 'bg-[var(--status-skipped-bg)] text-[var(--status-skipped)] ring-[var(--status-skipped-bd)]' :
                      'bg-[var(--color-bg-card)]/10 text-[var(--color-text-muted)] ring-[var(--color-border)]/20'
  return (
    <span className={clsx('inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ring-1 ring-inset', cls)}>
      {s}
    </span>
  )
}

function fmt(ms: number | null | undefined) {
  if (!ms) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60_000).toFixed(1)}m`
}

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function SuiteDetailPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const project       = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  // The one seam (K1), with "not known yet" kept apart: `undefined` while the
  // lookup is in flight (the pass-rate slot holds its place), then the
  // answer (`false` on failure), so the page is the Wave 2.5 page unless the
  // flag is known to be on.
  const catalogueStatus = useCatalogueRolloutStatus()
  const catalogue = catalogueStatus === true

  const suiteName  = searchParams.get('name') ?? ''
  // Precedence: explicit URL ``?days=`` (deep link) > shared global
  // preference > snapped default. Picking a period in the UI also
  // rewrites the URL so the chip *and* the data refresh together —
  // previously the URL pinned ``days``, so clicking 7d updated the
  // store but ``days`` (derived) stayed on the URL value and the
  // chart never refreshed. (Bug reported 2026-05-19 on
  // /coverage/suite?name=…&days=90.)
  const storedDays = useTimeWindowStore(s => s.days)
  const setStoredDays = useTimeWindowStore(s => s.setDays)
  const urlDays = Number(searchParams.get('days'))
  const days = PERIOD_DAYS.includes(urlDays)
    ? urlDays
    : snapToAllowed(storedDays, PERIOD_DAYS)
  const setDays = (next: number) => {
    setStoredDays(next)
    // Rewrite ?days= in the URL so the derived ``days`` value above
    // re-reads the new selection on the next render. ``replace`` so
    // the back button doesn't accumulate one entry per click.
    const params = new URLSearchParams(searchParams)
    params.set('days', String(next))
    setSearchParams(params, { replace: true })
  }

  const { data, isLoading, error } = useSuiteDetail(suiteName || null, days)

  // Per-day trend (run_count + passed/failed/skipped) for the same time
  // window. Owned by the new ``suite_history_service`` so every page
  // shows the same numbers. Keyed on (suiteName, activeProjectId, days) so
  // the chart refreshes in lockstep with the days selector and the active
  // project, without driving state from an effect.
  const { points: trendPoints } = useSuiteTrend(suiteName || null, days)
  // Both charts read the one per-day series, so their days always line up.
  const historyModel = useMemo(() => runHistoryModel(trendPoints), [trendPoints])
  const passRateModel = useMemo(() => suitePassRateModel(trendPoints), [trendPoints])
  const trendHasRuns = trendPoints.some((p) => p.run_count > 0)
  // The reverse-direction link to the catalog needs a TestSuite *id*,
  // but the analytics page only knows the name (from the URL). Use the
  // already-cached ``useSuites`` SWR entry to resolve it. The lookup is
  // cheap (O(N) over a few dozen suites at most) and the fetch is
  // shared with the rest of the app; we don't pay for it again here.
  const { data: allSuites } = useSuites()
  const catalogSuite = allSuites?.items.find(
    (s) =>
      s.name === suiteName &&
      (activeProjectId === ALL_PROJECTS_ID || s.project_id === activeProjectId),
  )

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<Layers className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar"
      />
    )
  }

  if (!suiteName) {
    return (
      <EmptyState
        icon={<Layers className="h-10 w-10" />}
        title="No suite specified"
        description="Navigate here from the Coverage tab"
      />
    )
  }

  const summary: Partial<SuiteDetailSummary> = data?.summary ?? {}
  const testCases: TestCaseRow[] = data?.test_cases  ?? []
  const recentRuns: RunRow[]     = data?.recent_runs ?? []

  // Run-level aggregates (unique_tests / total_executions / recent_runs) are
  // populated even when the SDK shipped a TestNG/JUnit run without per-test
  // rows (e.g. JUnit XML with <testsuite tests=…> but no <testcase> elements,
  // or a live session whose Redis buffer evicted before persistence). The
  // page used to gate the *entire* view on ``testCases.length`` and fall
  // through to "No data for this suite" — hiding the real totals that every
  // other surface (/test-management, /reports/summary) shows. We now bail
  // only when there is genuinely nothing to summarise, and let the per-test
  // table render its own inline explanation when only the per-test detail
  // is missing.
  const hasAnyData =
    (summary.unique_tests ?? 0) > 0
    || (summary.total_executions ?? 0) > 0
    || recentRuns.length > 0
    || trendHasRuns

  const periodSelector = (
    <div className="flex items-center gap-1 bg-[var(--color-bg-secondary)] rounded-lg p-1">
      {PERIODS.map(({ label, days: d }) => (
        <button
          key={d}
          onClick={() => setDays(d)}
          className={clsx(
            'px-3 py-1 rounded-md text-sm font-medium transition-colors',
            days === d ? 'bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-btn-primary-text)]',
          )}
        >
          {label}
        </button>
      ))}
    </div>
  )

  // Pivot back to the catalog ("what tests live in this suite") from the
  // analytics view ("how have those tests performed"). Only rendered when
  // we can resolve the catalog suite id — analytics pages can be reached
  // for suites that exist as a free-text aggregate on test_runs but
  // haven't been materialised into a TestSuite row yet (live-stream gap
  // fallback). For those we hide the link rather than navigating to a
  // 404'd ``/suites/null`` route.
  const headerActions = (
    // `flex-wrap`: below ~480 px the link and five period buttons do not fit
    // one row (VIZ-106); at desktop widths they do, and nothing moves.
    <div className="flex flex-wrap items-center gap-2">
      {catalogSuite && (
        <Link
          to={`/suites/${catalogSuite.id}`}
          className="inline-flex items-center gap-1 rounded px-3 py-1.5 text-sm text-[var(--color-text-muted)] ring-1 ring-[var(--color-border)] hover:bg-[var(--color-bg-secondary)] hover:text-[var(--color-text)]"
        >
          <FolderTree className="h-4 w-4" /> Open catalog
        </Link>
      )}
      {periodSelector}
    </div>
  )

  return (
    <div className="space-y-6">
      <button
        onClick={() => navigate(`/coverage?days=${days}`)}
        className="flex items-center gap-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors text-sm"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back to Coverage
      </button>
      <PageHeader
        title={suiteName}
        actions={headerActions}
      />

      {isLoading ? (
        <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
      ) : error ? (
        <EmptyState
          icon={<AlertTriangle className="h-8 w-8 text-[var(--status-failed)]" />}
          title="Failed to load suite details"
          description="Check the console for errors or try again"
        />
      ) : !hasAnyData ? (
        <EmptyState
          icon={<Layers className="h-8 w-8" />}
          title="No data for this suite"
          description={`No test executions found in the last ${days} days`}
        />
      ) : (
        <>
          {/* KPI Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            {[
              {
                label: 'Unique Tests', value: summary.unique_tests ?? 0,
                color: 'text-[var(--color-text)]', icon: <Layers className="h-4 w-4" />,
              },
              {
                label: 'Total Executions', value: summary.total_executions ?? 0,
                color: 'text-[var(--color-text-secondary)]', icon: <Activity className="h-4 w-4" />,
              },
              {
                label: 'Passed', value: summary.passed ?? 0,
                color: 'text-[var(--status-passed)]', icon: <CheckCircle2 className="h-4 w-4" />,
              },
              {
                label: 'Failed', value: summary.failed ?? 0,
                color: 'text-[var(--status-failed)]', icon: <XCircle className="h-4 w-4" />,
              },
              {
                label: 'Pass Rate',
                value: `${Number(summary.pass_rate ?? 0).toFixed(1)}%`,
                color: Number(summary.pass_rate ?? 0) >= 90 ? 'text-[var(--status-passed)]'
                     : Number(summary.pass_rate ?? 0) >= 70 ? 'text-[var(--status-broken)]' : 'text-[var(--status-failed)]',
                icon: <SkipForward className="h-4 w-4" />,
              },
              {
                label: 'Avg Duration', value: fmt(summary.avg_duration_ms),
                color: 'text-[var(--color-accent)]', icon: <Clock className="h-4 w-4" />,
              },
            ].map(({ label, value, color, icon }) => (
              <div key={label} className="card py-3">
                <div className="flex items-center gap-1.5 text-[var(--color-text-muted)] mb-1">
                  {icon}
                  <p className="text-xs uppercase tracking-wider">{label}</p>
                </div>
                {/* The metric value on the stat token (VIZ-106): 24 px like the
                    `text-2xl` it replaces, with the same 4:3 line height, so no
                    pixel moves — and presentation mode raises it with the rest. */}
                <p className={clsx('text-[length:var(--text-stat-lg)] leading-[calc(2/1.5)] font-bold tabular-nums', color)}>{value}</p>
              </div>
            ))}
          </div>

          {/* Run history — per-day executions of this suite by status, on the
              chart kit (VIZ-104). Hidden when no day in the window had a run,
              so an empty suite does not show a row of zero columns. The run-
              by-run pass rates stay in the "Recent runs" table below. */}
          {trendHasRuns && (
            <StackedColumnChartFrame
              title={`Run history — last ${days} days`}
              takeaway={`${trendPoints.reduce((a, p) => a + p.run_count, 0)} runs · ${trendPoints.reduce((a, p) => a + p.total_tests, 0)} executions`}
              headingLevel={3}
              state={readyState(trendPoints)}
              model={historyModel}
              height={SUITE_CHART_HEIGHT}
              bucketNoun="day"
            />
          )}

          {/* Pass rate PER DAY (OD-4): the kit's time series over the same
              days, with its gap semantics — a day nobody ran is a gap.

              The common cadence (nightly on weekdays, every other day) leaves
              no two adjacent days with a rate, so the chart is a row of dots
              under a legend that shows a line (R2 F7). The dots stay apart:
              joining them across a day without runs would draw a trend
              through a day nobody measured, and Trends does not either (the
              kit keeps `connectNulls` off, and a no-run day and a skips-only
              day are the same `null` to it). The takeaway says what a point
              is instead, where a sighted reader sees it. */}
          {trendHasRuns && (catalogueStatus === undefined ? (
            <PassRatePending />
          ) : catalogue ? (
            // Flag on (VIZ-408): the same frame and data, plus the overlays and
            // the zoom. The wrapper exists only here, so the flag-off DOM has
            // no catalogue section at all.
            <div data-catalogue-section="suite-pass-rate">
              <TimeSeriesChartFrame
                title={`Pass rate trend — last ${days} days`}
                takeaway={PASS_RATE_POINT_NOTE}
                headingLevel={3}
                state={readyState(trendPoints)}
                model={passRateModel}
                height={SUITE_CHART_HEIGHT}
                {...CATALOGUE_PASS_RATE_PROPS}
              />
            </div>
          ) : (
            <TimeSeriesChartFrame
              title={`Pass rate trend — last ${days} days`}
              takeaway={PASS_RATE_POINT_NOTE}
              headingLevel={3}
              state={readyState(trendPoints)}
              model={passRateModel}
              height={SUITE_CHART_HEIGHT}
            />
          ))}

          {/* Test Cases Table */}
          <div className="card">
            <h3 className="text-sm font-semibold text-[var(--color-text)] mb-4">
              Test Cases ({testCases.length})
            </h3>
            {testCases.length === 0 ? (
              <div className="rounded border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] px-4 py-3 text-sm text-[var(--status-broken)]">
                <p className="font-medium">
                  {summary.total_executions ?? 0} test{(summary.total_executions ?? 0) === 1 ? '' : 's'} reported by the run, but per-test rows are missing.
                </p>
                <p className="mt-1 text-xs text-[var(--status-broken)]/80">
                  This happens when the SDK doesn&apos;t emit <code className="font-mono">test_result</code> events,
                  the upload was a run-level summary (e.g. JUnit XML with no <code className="font-mono">&lt;testcase&gt;</code> elements),
                  or the live buffer evicted before persistence. Re-run the suite to populate detail rows.
                </p>
              </div>
            ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th text-left">Test Name</th>
                    <th className="th text-left">Class</th>
                    <th className="th text-right">Runs</th>
                    <th className="th text-right text-[var(--status-passed)]">Passed</th>
                    <th className="th text-right text-[var(--status-failed)]">Failed</th>
                    <th className="th text-right text-[var(--status-skipped)]">Skipped</th>
                    <th className="th min-w-[160px]">Pass Rate</th>
                    <th className="th text-right">Avg Duration</th>
                    <th className="th">Last Status</th>
                    <th className="th text-right">Last Run</th>
                  </tr>
                </thead>
                <tbody>
                  {testCases.map((tc) => (
                    <tr key={tc.test_fingerprint} className="table-row">
                      <td className="td max-w-[260px]">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-[var(--color-text)] truncate" title={tc.test_name}>
                            {tc.test_name}
                          </span>
                          {tc.is_flaky && (
                            <span className="flex-shrink-0 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs font-medium bg-[var(--status-flaky-bg)] text-[var(--status-flaky)] ring-1 ring-inset ring-[var(--status-flaky-bd)]">
                              <AlertTriangle className="h-3 w-3" />
                              Flaky
                            </span>
                          )}
                        </div>
                        {tc.last_error && (
                          <p className="text-xs text-[var(--status-failed)]/70 truncate mt-0.5" title={tc.last_error}>
                            {tc.last_error}
                          </p>
                        )}
                      </td>
                      <td className="td text-[var(--color-text-muted)] text-xs max-w-[180px] truncate" title={tc.class_name ?? ''}>
                        {tc.class_name ?? '—'}
                      </td>
                      <td className="td text-right tabular-nums text-[var(--color-text-secondary)]">{tc.total_executions}</td>
                      <td className="td text-right tabular-nums text-[var(--status-passed)]">{tc.passed}</td>
                      <td className="td text-right tabular-nums text-[var(--status-failed)]">{tc.failed}</td>
                      <td className="td text-right tabular-nums text-[var(--status-skipped)]">{tc.skipped}</td>
                      <td className="td w-44">
                        <PassRateBar rate={Number(tc.pass_rate ?? 0)} />
                      </td>
                      <td className="td text-right tabular-nums text-[var(--color-text-muted)] text-xs">
                        {fmt(tc.avg_duration_ms)}
                      </td>
                      <td className="td">
                        <StatusBadge status={tc.last_status ?? 'UNKNOWN'} />
                      </td>
                      <td className="td text-right text-xs text-[var(--color-text-muted)]">
                        {fmtDate(tc.last_run_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            )}
          </div>

          {/* Recent Runs Table */}
          {recentRuns.length > 0 && (
            <div className="card">
              <div className="flex items-center gap-2 mb-4">
                <Calendar className="h-4 w-4 text-[var(--color-text-muted)]" />
                <h3 className="text-sm font-semibold text-[var(--color-text)]">
                  Recent Runs ({recentRuns.length})
                </h3>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr>
                      <th className="th text-left">Build</th>
                      <th className="th text-right">Date</th>
                      <th className="th text-right text-[var(--status-passed)]">Passed</th>
                      <th className="th text-right text-[var(--status-failed)]">Failed</th>
                      <th className="th text-right text-[var(--status-skipped)]">Skipped</th>
                      <th className="th min-w-[160px]">Pass Rate</th>
                      <th className="th text-left">Run</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentRuns.map((r) => (
                      <tr key={r.test_run_id} className="table-row">
                        <td className="td font-mono text-[var(--color-text-secondary)] text-xs">{r.build_number ?? '—'}</td>
                        <td className="td text-right text-xs text-[var(--color-text-muted)]">{fmtDate(r.run_date)}</td>
                        <td className="td text-right tabular-nums text-[var(--status-passed)]">{r.passed}</td>
                        <td className="td text-right tabular-nums text-[var(--status-failed)]">{r.failed}</td>
                        <td className="td text-right tabular-nums text-[var(--status-skipped)]">{r.skipped}</td>
                        <td className="td w-44">
                          <PassRateBar rate={Number(r.pass_rate ?? 0)} />
                        </td>
                        <td className="td">
                          <button
                            onClick={() => navigate(`/runs/${r.test_run_id}`)}
                            className="text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)] transition-colors"
                          >
                            View run →
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface TestCaseRow {
  test_fingerprint: string
  test_name: string
  class_name: string | null
  total_executions: number
  passed: number
  failed: number
  skipped: number
  pass_rate: number
  avg_duration_ms: number | null
  last_status: string | null
  last_error: string | null
  last_run_at: string | null
  is_flaky: boolean
}

interface RunRow {
  test_run_id: string
  build_number: string | null
  run_date: string | null
  passed: number
  failed: number
  skipped: number
  pass_rate: number
}
