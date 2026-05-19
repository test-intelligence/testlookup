import { Link, useSearchParams, useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import {
  ArrowLeft, Layers, CheckCircle2, XCircle, SkipForward,
  Clock, Activity, AlertTriangle, Calendar, FolderTree,
} from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Legend,
} from 'recharts'
import { clsx } from 'clsx'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useSuiteDetail } from '@/hooks/useMetrics'
import { useSuites } from '@/hooks/useSuites'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import type { SuiteDetailSummary } from '@/types/analytics'
import { testManagementService } from '@/services/testManagementService'

const PERIODS = [
  { label: '7d',  days: 7 },
  { label: '14d', days: 14 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
] as const
const PERIOD_DAYS = PERIODS.map(p => p.days) as readonly number[]

const TOOLTIP_STYLE = {
  backgroundColor: '#1e293b', border: '1px solid #334155',
  borderRadius: '8px', color: '#e2e8f0', fontSize: '12px',
}

const AXIS_TICK = { fill: '#64748b', fontSize: 11 }

// ── Sub-components ────────────────────────────────────────────────────────────

function PassRateBar({ rate }: { rate: number }) {
  const color = rate >= 90 ? '#10b981' : rate >= 70 ? '#f59e0b' : '#ef4444'
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
    s === 'PASSED'  ? 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/20' :
    s === 'FAILED'  ? 'bg-red-500/10 text-red-400 ring-red-500/20' :
    s === 'BROKEN'  ? 'bg-orange-500/10 text-orange-400 ring-orange-500/20' :
    s === 'SKIPPED' ? 'bg-amber-500/10 text-amber-400 ring-amber-500/20' :
                      'bg-neutral-700/10 text-[var(--color-text-muted)] ring-neutral-600/20'
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
  // shows the same numbers. Polled in lockstep with the days selector;
  // refresh is bounded to the user's window so a chatty live-stream
  // run-set doesn't refire the query every poll.
  type SuiteTrendPoint = {
    date: string
    run_count: number
    total_tests: number
    passed_count: number
    failed_count: number
    skipped_count: number
    broken_count: number
  }
  const [trendPoints, setTrendPoints] = useState<SuiteTrendPoint[]>([])
  const [trendLoading, setTrendLoading] = useState(false)
  useEffect(() => {
    if (!suiteName) { setTrendPoints([]); return }
    let alive = true
    setTrendLoading(true)
    const pid = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId
    testManagementService.getSuiteTrend(suiteName, pid, days)
      .then(res => { if (alive) setTrendPoints(res.points || []) })
      .catch(() => { if (alive) setTrendPoints([]) })
      .finally(() => { if (alive) setTrendLoading(false) })
    return () => { alive = false }
  }, [suiteName, activeProjectId, days])
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

  // Chart data — pass rate trend across recent runs (oldest first)
  const chartData = [...recentRuns].reverse().map((r) => ({
    name: r.build_number ?? fmtDate(r.run_date),
    passed:  r.passed,
    failed:  r.failed,
    skipped: r.skipped,
    pass_rate: Number(r.pass_rate ?? 0),
  }))

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
    <div className="flex items-center gap-2">
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
          icon={<AlertTriangle className="h-8 w-8 text-red-400" />}
          title="Failed to load suite details"
          description="Check the console for errors or try again"
        />
      ) : testCases.length === 0 ? (
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
                color: 'text-emerald-400', icon: <CheckCircle2 className="h-4 w-4" />,
              },
              {
                label: 'Failed', value: summary.failed ?? 0,
                color: 'text-red-400', icon: <XCircle className="h-4 w-4" />,
              },
              {
                label: 'Pass Rate',
                value: `${Number(summary.pass_rate ?? 0).toFixed(1)}%`,
                color: Number(summary.pass_rate ?? 0) >= 90 ? 'text-emerald-400'
                     : Number(summary.pass_rate ?? 0) >= 70 ? 'text-amber-400' : 'text-red-400',
                icon: <SkipForward className="h-4 w-4" />,
              },
              {
                label: 'Avg Duration', value: fmt(summary.avg_duration_ms),
                color: 'text-cyan-400', icon: <Clock className="h-4 w-4" />,
              },
            ].map(({ label, value, color, icon }) => (
              <div key={label} className="card py-3">
                <div className="flex items-center gap-1.5 text-[var(--color-text-muted)] mb-1">
                  {icon}
                  <p className="text-xs uppercase tracking-wider">{label}</p>
                </div>
                <p className={clsx('text-2xl font-bold tabular-nums', color)}>{value}</p>
              </div>
            ))}
          </div>

          {/* Run-history trend — per-day count of runs that included this
              suite, broken down by status. Independent of the
              recent-runs pass-rate chart below (which is per-run,
              snapshot-style). Hidden when there's less than one day of
              data so empty suites don't show a flat zero chart. */}
          {trendPoints.some(p => p.run_count > 0) && (
            <div className="card">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-[var(--color-text)]">
                  Run history — last {days} days
                </h3>
                <span className="text-xs text-[var(--color-text-muted)]">
                  {trendPoints.reduce((a, p) => a + p.run_count, 0)} runs ·
                  {' '}{trendPoints.reduce((a, p) => a + p.total_tests, 0)} executions
                </span>
              </div>
              {trendLoading ? (
                <div className="flex items-center justify-center h-48"><LoadingSpinner size="sm" /></div>
              ) : (
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={trendPoints} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="date" tick={AXIS_TICK} axisLine={false} tickLine={false} />
                    <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} allowDecimals={false} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Bar dataKey="passed_count"  name="Passed"  stackId="status" fill="#10b981" />
                    <Bar dataKey="failed_count"  name="Failed"  stackId="status" fill="#ef4444" />
                    <Bar dataKey="skipped_count" name="Skipped" stackId="status" fill="#f59e0b" />
                    <Bar dataKey="broken_count"  name="Broken"  stackId="status" fill="#fb923c" />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          )}

          {/* Pass Rate Trend */}
          {chartData.length > 1 && (
            <div className="card">
              <h3 className="text-sm font-semibold text-[var(--color-text)] mb-4">
                Pass Rate Trend — Last {recentRuns.length} Runs
              </h3>
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
                  <defs>
                    <linearGradient id="passGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor="#10b981" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="name" tick={AXIS_TICK} axisLine={false} tickLine={false} />
                  <YAxis domain={[0, 100]} tick={AXIS_TICK} axisLine={false} tickLine={false} unit="%" />
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => [`${v}%`, 'Pass Rate']} />
                  <Area
                    type="monotone" dataKey="pass_rate" stroke="#10b981"
                    strokeWidth={2} fill="url(#passGrad)" dot={{ r: 3, fill: '#10b981' }}
                    name="Pass Rate"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Test Cases Table */}
          <div className="card">
            <h3 className="text-sm font-semibold text-[var(--color-text)] mb-4">
              Test Cases ({testCases.length})
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th text-left">Test Name</th>
                    <th className="th text-left">Class</th>
                    <th className="th text-right">Runs</th>
                    <th className="th text-right text-emerald-400">Passed</th>
                    <th className="th text-right text-red-400">Failed</th>
                    <th className="th text-right text-amber-400">Skipped</th>
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
                            <span className="flex-shrink-0 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs font-medium bg-amber-500/10 text-amber-400 ring-1 ring-inset ring-amber-500/20">
                              <AlertTriangle className="h-3 w-3" />
                              Flaky
                            </span>
                          )}
                        </div>
                        {tc.last_error && (
                          <p className="text-xs text-red-400/70 truncate mt-0.5" title={tc.last_error}>
                            {tc.last_error}
                          </p>
                        )}
                      </td>
                      <td className="td text-[var(--color-text-muted)] text-xs max-w-[180px] truncate" title={tc.class_name ?? ''}>
                        {tc.class_name ?? '—'}
                      </td>
                      <td className="td text-right tabular-nums text-[var(--color-text-secondary)]">{tc.total_executions}</td>
                      <td className="td text-right tabular-nums text-emerald-400">{tc.passed}</td>
                      <td className="td text-right tabular-nums text-red-400">{tc.failed}</td>
                      <td className="td text-right tabular-nums text-amber-400">{tc.skipped}</td>
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
                      <th className="th text-right text-emerald-400">Passed</th>
                      <th className="th text-right text-red-400">Failed</th>
                      <th className="th text-right text-amber-400">Skipped</th>
                      <th className="th min-w-[160px]">Pass Rate</th>
                      <th className="th text-left">Run</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentRuns.map((r) => (
                      <tr key={r.test_run_id} className="table-row">
                        <td className="td font-mono text-[var(--color-text-secondary)] text-xs">{r.build_number ?? '—'}</td>
                        <td className="td text-right text-xs text-[var(--color-text-muted)]">{fmtDate(r.run_date)}</td>
                        <td className="td text-right tabular-nums text-emerald-400">{r.passed}</td>
                        <td className="td text-right tabular-nums text-red-400">{r.failed}</td>
                        <td className="td text-right tabular-nums text-amber-400">{r.skipped}</td>
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
