/**
 * Project Summary Report — consolidated view of every test suite for the
 * active project. Two aggregation modes (window / latest-per-suite) plus
 * a PDF export.
 *
 * Data: ``/api/v1/reports/summary`` via ``useSummaryReport``.
 * Export: ``/api/v1/reports/summary/pdf`` via ``summaryReportService.downloadPdf``.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import {
  BarChart3, CheckCircle2, Clock, Download, FileText, Layers, ListChecks,
  MinusCircle, TriangleAlert, XCircle, Zap,
} from 'lucide-react'
import PageHeader from '@/components/ui/PageHeader'
import AllReleasesBadge from '@/components/ui/AllReleasesBadge'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import { useSummaryReport } from '@/hooks/useSummaryReport'
import { summaryReportService } from '@/services/summaryReportService'
import type { SummaryReportMode, SummarySuiteRow } from '@/types/summaryReport'

const DAYS_OPTIONS = [1, 7, 30, 90] as const
/** Aggregation mode is page-local — different from the global window
 *  preference. Persisted here so the user's mode choice survives
 *  navigation without leaking into pages that don't have a mode. */
const LS_MODE_KEY = 'summary-report.mode'

/** Default aggregation mode. ``latest`` shows one snapshot per suite
 *  (the most recent run in the window), which matches user intent on
 *  "show me where things stand right now" — a fresh user landing on
 *  /reports/summary expects current state, not volume-weighted history.
 *  The volume-weighted ``window`` view is still available via the
 *  toggle, but isn't the default because it produces totals scaled by
 *  run count (5 runs × 100 tests = 500 total) which reads as duplicate
 *  rows to anyone who hasn't read the docstring. */
const DEFAULT_MODE: SummaryReportMode = 'latest'

function loadStoredMode(): SummaryReportMode {
  try {
    const raw = localStorage.getItem(LS_MODE_KEY)
    return raw === 'latest' || raw === 'window' ? raw : DEFAULT_MODE
  } catch {
    return DEFAULT_MODE
  }
}

const MODE_LABELS: Record<SummaryReportMode, { label: string; hint: string }> = {
  window: {
    label: 'All runs in window',
    hint: 'Unique tests across every run in the window — the same count the Coverage page shows. Pass rate is volume-weighted across executions.',
  },
  latest: {
    label: 'Latest run per suite',
    hint: 'Only each suite’s most recent run counts — expect fewer tests than the Coverage page, which spans the whole window.',
  },
}

function fmtPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${value.toFixed(1)}%`
}

function fmtInt(value: number | null | undefined): string {
  if (value == null) return '—'
  return value.toLocaleString()
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export default function SummaryReportPage() {
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  // Window is a global preference — picking 24h here propagates to /live,
  // /coverage, /trends, /runs, /failures, /overview, /my-failures.
  // Snap to this page's allowed set so a value picked elsewhere (e.g. 14d
  // on Live) maps to the nearest supported option here.
  const storedDays = useTimeWindowStore(s => s.days)
  const setStoredDays = useTimeWindowStore(s => s.setDays)
  const days = snapToAllowed(storedDays, DAYS_OPTIONS)
  const setDays = setStoredDays

  // Aggregation mode is page-local (no other page has the concept).
  const [mode, setMode] = useState<SummaryReportMode>(() => loadStoredMode())
  const [isDownloading, setIsDownloading] = useState(false)
  // US-7.5: on-demand download of the self-contained HTML analysis report
  // (the same document daily/weekly digest emails attach).
  const [downloadingReport, setDownloadingReport] = useState<'1d' | '7d' | null>(null)

  useEffect(() => {
    try { localStorage.setItem(LS_MODE_KEY, mode) } catch { /* ignore */ }
  }, [mode])

  const { data, isLoading } = useSummaryReport({ days, mode })

  const windowLabel = days === 1 ? '24h' : `${days}d`

  const handleDownloadPdf = async () => {
    if (!project?.id) {
      toast.error('Select a single project before exporting.')
      return
    }
    setIsDownloading(true)
    try {
      const blob = await summaryReportService.downloadPdf({
        project_id: project.id,
        days,
        mode,
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const slug = (project.name || 'project').toLowerCase().replace(/\s+/g, '_')
      a.download = `summary-${slug}-${windowLabel}-${mode}.pdf`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      toast.success('Summary PDF downloaded')
    } catch (err: unknown) {
      toast.error((err as Error).message || 'Failed to download PDF')
    } finally {
      setIsDownloading(false)
    }
  }

  const handleDownloadAnalysisReport = async (window: '1d' | '7d') => {
    if (!project?.id) {
      toast.error('Select a single project before exporting.')
      return
    }
    setDownloadingReport(window)
    try {
      const blob = await summaryReportService.downloadAnalysisReport({
        project_id: project.id,
        window,
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const slug = (project.name || 'project').toLowerCase().replace(/\s+/g, '-')
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '')
      a.download = `testlookup-report-${slug}-${stamp}${window === '7d' ? '-weekly' : ''}.html`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      toast.success('Analysis report downloaded')
    } catch (err: unknown) {
      toast.error((err as Error).message || 'Failed to download analysis report')
    } finally {
      setDownloadingReport(null)
    }
  }

  // The summary is project-scoped; all-projects mode is meaningless here.
  if (isAllProjects || !project) {
    return (
      <>
        <PageHeader
          title="Summary Report"
          subtitle="Consolidated pass / fail / skip / flaky across every test suite for the active project."
        />
        <EmptyState
          title="Pick a single project"
          description="The Summary Report aggregates a single project's test suites. Choose a project from the top bar to generate one."
          icon={<FileText className="h-6 w-6" />}
        />
      </>
    )
  }

  if (isLoading && !data) {
    return (
      <>
        <PageHeader title="Summary Report" subtitle={`Project: ${project.name}`} />
        <div className="flex items-center justify-center py-16"><LoadingSpinner size="lg" /></div>
      </>
    )
  }

  const totals = data?.totals
  const suites = data?.suites ?? []
  const topFailing = data?.top_failing_tests ?? []
  const hasData = totals != null && totals.total_test_cases > 0

  return (
    <>
      <PageHeader
        title="Summary Report"
        subtitle={`Project: ${project.name} · ${MODE_LABELS[mode].hint}`}
        actions={
          <div className="flex items-center gap-2">
            {/* This page is not release-scoped, and its output LEAVES the tool:
                the PDF filename encodes project, window and mode but not a
                release, so a report attached to a go/no-go thread carries no
                trace of what was selected when it was generated. Marking it
                here is the honest interim answer until the report itself can
                answer per release. */}
            <AllReleasesBadge reason="This report and its PDF export cover the selected time window across all releases." />
            {(['1d', '7d'] as const).map(w => (
              <button
                key={w}
                type="button"
                onClick={() => handleDownloadAnalysisReport(w)}
                disabled={downloadingReport !== null}
                title={`Self-contained HTML analysis report for the last ${w === '1d' ? 'day' : '7 days'} — the same document digest emails attach.`}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12.5px] font-medium border transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                style={{
                  background: 'transparent',
                  borderColor: 'var(--color-border)',
                  color: 'var(--color-text-muted)',
                }}
              >
                <Download className="h-3.5 w-3.5" />
                {downloadingReport === w ? 'Generating…' : `Analysis report (${w})`}
              </button>
            ))}
            <button
              type="button"
              onClick={handleDownloadPdf}
              disabled={isDownloading || !hasData}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12.5px] font-medium border transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              style={{
                background: hasData ? 'color-mix(in srgb, var(--color-accent) 14%, transparent)' : 'transparent',
                borderColor: hasData ? 'color-mix(in srgb, var(--color-accent) 30%, transparent)' : 'var(--color-border)',
                color: hasData ? 'var(--color-accent)' : 'var(--color-text-muted)',
              }}
            >
              <Download className="h-3.5 w-3.5" />
              {isDownloading ? 'Generating…' : 'Export PDF'}
            </button>
          </div>
        }
      />

      {/* Filter row: window chips + mode toggle */}
      <div className="flex items-center gap-3 flex-wrap mb-4">
        <span className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] tracking-wider">Window</span>
        <div role="radiogroup" aria-label="Time window" className="flex items-center gap-1.5">
          {DAYS_OPTIONS.map(d => {
            const active = days === d
            return (
              <button
                key={d}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setDays(d)}
                className="inline-flex items-center gap-1 px-2.5 py-1 text-[12.5px] rounded-full border transition-colors"
                style={{
                  background: active ? 'color-mix(in srgb, var(--color-accent) 14%, transparent)' : 'transparent',
                  borderColor: active ? 'color-mix(in srgb, var(--color-accent) 30%, transparent)' : 'var(--color-border)',
                  color: active ? 'var(--color-accent)' : 'var(--color-text-muted)',
                }}
              >
                {d === 1 ? '24h' : `${d}d`}
              </button>
            )
          })}
        </div>
        <span className="w-px h-4" style={{ background: 'var(--color-border)' }} aria-hidden />
        <span className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] tracking-wider">Aggregation</span>
        <div role="radiogroup" aria-label="Aggregation mode" className="flex items-center gap-1.5">
          {(Object.keys(MODE_LABELS) as SummaryReportMode[]).map(m => {
            const active = mode === m
            return (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setMode(m)}
                className="inline-flex items-center gap-1 px-2.5 py-1 text-[12.5px] rounded-full border transition-colors"
                style={{
                  background: active ? 'color-mix(in srgb, var(--status-flaky) 14%, transparent)' : 'transparent',
                  borderColor: active ? 'color-mix(in srgb, var(--status-flaky) 30%, transparent)' : 'var(--color-border)',
                  color: active ? '#d8b4fe' : 'var(--color-text-muted)',
                }}
                title={MODE_LABELS[m].hint}
              >
                {MODE_LABELS[m].label}
              </button>
            )
          })}
        </div>
        {data?.generated_at && (
          <span className="ml-auto text-[11px] text-[var(--color-text-faint)]">
            Generated {fmtDateTime(data.generated_at)}
          </span>
        )}
      </div>

      {!hasData || totals == null ? (
        <EmptyState
          title="No executions in this window"
          description={`No test runs were recorded for ${project.name} in the last ${windowLabel}. Ingest a run or widen the window to populate this report.`}
          icon={<BarChart3 className="h-6 w-6" />}
        />
      ) : (
        <>
          {/* Headline KPIs */}
          <section className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-5">
            <KpiTile label="Total tests"   value={fmtInt(totals.total_test_cases)} icon={<ListChecks className="h-4 w-4" />} tone="neutral" />
            {/* F-067: name the population. This report counts each distinct
                test once ("per unique test", 83.3% on the measured window)
                while /overview counts every execution ("per test execution",
                81.0%). Same window, both correct — and indistinguishable
                without the basis. Falls back to the old copy if the API omits
                it (a cached pre-#778 payload). */}
            <KpiTile label="Pass %"        value={fmtPct(totals.pass_rate_pct)}    icon={<CheckCircle2 className="h-4 w-4" />} tone="good"
                     sub={totals.pass_rate_basis_label
                       ? `${totals.pass_rate_basis_label} · weighted ${fmtPct(totals.weighted_pass_rate_pct)}`
                       : `weighted ${fmtPct(totals.weighted_pass_rate_pct)}`} />
            <KpiTile label="Fail %"        value={fmtPct(totals.fail_rate_pct)}    icon={<XCircle className="h-4 w-4" />} tone="bad" />
            <KpiTile label="Skip %"        value={fmtPct(totals.skip_rate_pct)}    icon={<MinusCircle className="h-4 w-4" />} tone="warn" />
            <KpiTile label="Broken %"      value={fmtPct(totals.broken_rate_pct)}  icon={<TriangleAlert className="h-4 w-4" />} tone="bad" />
            <KpiTile label="Flaky"         value={fmtInt(data?.flaky_test_count)}   icon={<Zap className="h-4 w-4" />} tone="warn"
                     sub={`${fmtPct(data?.flaky_rate_pct)} of total`} />
          </section>

          {/* Counts strip */}
          <section className="card mb-5">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-y-3 text-sm">
              <Count label="Passed"    value={totals.passed}   color="text-[var(--status-passed)]" />
              <Count label="Failed"    value={totals.failed}   color="text-[var(--status-failed)]" />
              <Count label="Skipped"   value={totals.skipped}  color="text-[var(--status-broken)]" />
              <Count label="Broken"    value={totals.broken}   color="text-[var(--status-failed)]" />
              <Count label="Evaluated" value={totals.evaluated} color="text-[var(--color-text)]" />
            </div>
            <div className="mt-3 pt-3 border-t border-[var(--color-border)] text-[11.5px] text-[var(--color-text-muted)] flex flex-wrap gap-x-4 gap-y-1">
              <span className="inline-flex items-center gap-1"><Layers className="h-3 w-3" />Runs in window: <strong className="text-[var(--color-text)] tabular-nums">{fmtInt(data?.run_count)}</strong></span>
              {data?.runs_per_day != null && (
                <span>Avg / day: <strong className="text-[var(--color-text)] tabular-nums">{data.runs_per_day.toFixed(2)}</strong></span>
              )}
              <span className="inline-flex items-center gap-1"><Clock className="h-3 w-3" />Avg duration: <strong className="text-[var(--color-text)] tabular-nums">{fmtInt(data?.avg_duration_ms)} ms</strong></span>
              {data?.latest_run_at && (
                <span>Latest run: <strong className="text-[var(--color-text)]">{fmtDateTime(data.latest_run_at)}</strong></span>
              )}
            </div>
          </section>

          {/* Per-suite breakdown */}
          <section className="mb-5">
            <h2 className="text-[13px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)] mb-2 flex items-center gap-2">
              <Layers className="h-3.5 w-3.5" /> Per-suite breakdown
              <span className="ml-1 text-[10px] font-normal text-[var(--color-text-faint)] normal-case tracking-normal">{suites.length} suite{suites.length === 1 ? '' : 's'}</span>
            </h2>
            <SuiteTable rows={suites} />
          </section>

          {/* Top failing tests */}
          <section>
            <h2 className="text-[13px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)] mb-2 flex items-center gap-2">
              <TriangleAlert className="h-3.5 w-3.5" /> Top failing tests
              <span className="ml-1 text-[10px] font-normal text-[var(--color-text-faint)] normal-case tracking-normal">{topFailing.length} test{topFailing.length === 1 ? '' : 's'}</span>
            </h2>
            {topFailing.length === 0 ? (
              <p className="text-[12.5px] text-[var(--color-text-muted)]">No failures in this window.</p>
            ) : (
              <div className="rounded-xl border border-[var(--color-border)] overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-[var(--color-bg-secondary)]/80">
                    <tr>
                      <th className="px-4 py-2.5 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Test</th>
                      <th className="px-4 py-2.5 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Suite</th>
                      <th className="px-4 py-2.5 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Class</th>
                      <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Failures</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--color-border)]/60">
                    {topFailing.map((t, idx) => (
                      <tr key={`${t.test_name}-${idx}`} className="hover:bg-[var(--color-bg-secondary)]/40">
                        <td className="px-4 py-2.5 text-[var(--color-text)] font-medium text-xs truncate max-w-[420px]" title={t.test_name}>{t.test_name}</td>
                        <td className="px-4 py-2.5 text-[var(--color-text-muted)] text-xs">{t.suite_name ?? '—'}</td>
                        <td className="px-4 py-2.5 text-[var(--color-text-muted)] text-xs">{t.class_name ?? '—'}</td>
                        <td className="px-4 py-2.5 text-right text-xs tabular-nums text-[var(--status-failed)] font-medium">{fmtInt(t.failures)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </>
  )
}


// ── small components ───────────────────────────────────────────────────────

function KpiTile({
  label, value, icon, tone, sub,
}: {
  label: string
  value: string
  icon: React.ReactNode
  tone: 'good' | 'bad' | 'warn' | 'neutral'
  sub?: string
}) {
  const toneClasses: Record<typeof tone, string> = {
    good:    'bg-[var(--status-passed-bg)]/10 text-[var(--status-passed)]',
    bad:     'bg-[var(--status-failed-bg)]/10 text-[var(--status-failed)]',
    warn:    'bg-[var(--status-broken-bg)]/10 text-[var(--status-broken)]',
    neutral: 'bg-white/5 text-[var(--color-text)]',
  }
  return (
    <div className="card flex items-start justify-between gap-3" role="status" aria-live="polite">
      <div className="min-w-0 flex-1">
        <p className="text-[10.5px] font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-1.5">{label}</p>
        <p className="text-[22px] font-bold text-[var(--color-text)] tabular-nums leading-tight">{value}</p>
        {sub && <p className="text-[10.5px] text-[var(--color-text-faint)] mt-0.5">{sub}</p>}
      </div>
      <div className={clsx('p-2 rounded-lg flex-shrink-0', toneClasses[tone])}>{icon}</div>
    </div>
  )
}

function Count({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div>
      <p className="text-[10.5px] font-medium text-[var(--color-text-muted)] uppercase tracking-wider">{label}</p>
      <p className={clsx('text-[18px] font-semibold tabular-nums leading-tight', color)}>{value.toLocaleString()}</p>
    </div>
  )
}

type SuiteSortKey = 'suite' | 'last_run'
type SuiteSortDir = 'asc' | 'desc'

function SuiteTable({ rows }: { rows: SummarySuiteRow[] }) {
  const storedDays = useTimeWindowStore(s => s.days)
  // Default per request: most-recent activity at the top. The user
  // typically wants "what ran last" before "what's biggest", so the
  // initial order is ``last_run DESC``; clicking the Suite header
  // switches to alphabetical, with a toggle for asc/desc.
  const [sortKey, setSortKey] = useState<SuiteSortKey>('last_run')
  const [sortDir, setSortDir] = useState<SuiteSortDir>('desc')

  function handleSort(key: SuiteSortKey) {
    if (sortKey === key) {
      // Same column twice → flip direction.
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      // Suite alpha defaults to ASC (A→Z); date defaults to DESC.
      setSortDir(key === 'suite' ? 'asc' : 'desc')
    }
  }

  const sorted = useMemo(() => {
    const mult = sortDir === 'desc' ? -1 : 1
    const copy = [...rows]
    if (sortKey === 'suite') {
      copy.sort((a, b) => a.suite_name.localeCompare(b.suite_name) * mult)
    } else {
      // last_run sort. Treat missing timestamps as the epoch — they
      // sink to the bottom under DESC, top under ASC.
      copy.sort((a, b) => {
        const ta = a.last_run_at ? new Date(a.last_run_at).getTime() : 0
        const tb = b.last_run_at ? new Date(b.last_run_at).getTime() : 0
        return (ta - tb) * mult
      })
    }
    return copy
  }, [rows, sortKey, sortDir])

  // Step success-rate is a Phase 5 enrichment that may be absent (no
  // captured step data). Only surface the column when at least one suite
  // carries it, so legacy/no-step projects keep the original layout.
  const hasStepData = useMemo(
    () => rows.some(r => r.step_success_rate != null),
    [rows],
  )

  if (sorted.length === 0) {
    return <p className="text-[12.5px] text-[var(--color-text-muted)]">No suites with executions in this window.</p>
  }
  const arrow = (k: SuiteSortKey) => sortKey === k ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''
  return (
    <div className="rounded-xl border border-[var(--color-border)] overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-[var(--color-bg-secondary)]/80">
          <tr>
            <th
              className="px-4 py-2.5 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider cursor-pointer select-none hover:text-[var(--color-text)]"
              onClick={() => handleSort('suite')}
              aria-sort={sortKey === 'suite' ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}
              title="Sort by suite name"
            >
              Suite{arrow('suite')}
            </th>
            <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Total</th>
            <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Pass</th>
            <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Fail</th>
            <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Skip</th>
            <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Broken</th>
            <th className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Pass %</th>
            {hasStepData && (
              <th
                className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider"
                title="Share of captured test steps that passed (where step data exists)"
              >
                Step %
              </th>
            )}
            <th
              className="px-4 py-2.5 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider cursor-pointer select-none hover:text-[var(--color-text)]"
              onClick={() => handleSort('last_run')}
              aria-sort={sortKey === 'last_run' ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}
              title="Sort by last run timestamp"
            >
              Last run{arrow('last_run')}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-border)]/60">
          {sorted.map(s => (
            <tr key={s.suite_name} className="hover:bg-[var(--color-bg-secondary)]/40">
              <td className="px-4 py-2.5 text-xs truncate max-w-[260px]" title={s.suite_name}>
                <Link
                  to={`/coverage/suite?name=${encodeURIComponent(s.suite_name)}&days=${storedDays}`}
                  className="font-medium text-[var(--color-accent)] hover:underline"
                >
                  {s.suite_name}
                </Link>
              </td>
              <td className="px-4 py-2.5 text-right text-xs tabular-nums">{fmtInt(s.total)}</td>
              <td className="px-4 py-2.5 text-right text-xs tabular-nums text-[var(--status-passed)]">{fmtInt(s.passed)}</td>
              <td className="px-4 py-2.5 text-right text-xs tabular-nums text-[var(--status-failed)]">{fmtInt(s.failed)}</td>
              <td className="px-4 py-2.5 text-right text-xs tabular-nums text-[var(--status-broken)]">{fmtInt(s.skipped)}</td>
              <td className="px-4 py-2.5 text-right text-xs tabular-nums text-[var(--status-failed)]">{fmtInt(s.broken)}</td>
              <td className="px-4 py-2.5 text-right text-xs tabular-nums">
                <span className={clsx('inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium', passPctClass(s.pass_rate_pct))}>{fmtPct(s.pass_rate_pct)}</span>
              </td>
              {hasStepData && (
                <td
                  className="px-4 py-2.5 text-right text-[11px] text-[var(--color-text-muted)] tabular-nums"
                  title={
                    s.total_steps != null
                      ? `${fmtInt(s.passed_steps)} / ${fmtInt(s.total_steps)} steps passed`
                      : undefined
                  }
                >
                  {s.step_success_rate != null ? fmtPct(s.step_success_rate) : '—'}
                </td>
              )}
              <td className="px-4 py-2.5 text-right text-[11px] text-[var(--color-text-muted)]">{fmtDateTime(s.last_run_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function passPctClass(pct: number): string {
  if (pct >= 95) return 'bg-[var(--status-passed-bg)]/30 text-[var(--status-passed)]'
  if (pct >= 80) return 'bg-[var(--status-broken-bg)]/30 text-[var(--status-broken)]'
  return 'bg-[var(--status-failed-bg)]/30 text-[var(--status-failed)]'
}
