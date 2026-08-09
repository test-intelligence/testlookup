import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Bot, ChevronDown, ChevronRight, ChevronUp, GitCommit, GitCompare, ListTree, Loader2, Package, PencilLine, RotateCcw, Stethoscope, TrendingDown, X, Check, Zap } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import StatusBadge from '@/components/ui/StatusBadge'
import SuiteBadge from '@/components/ui/SuiteBadge'
import SortableHeader from '@/components/ui/SortableHeader'
import Pagination from '@/components/ui/Pagination'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useRun, useRuns, useTestCases } from '@/hooks/useRuns'
import { buildCompareWithPreviousHref, findPreviousRunOfSuite } from '@/utils/runComparisons'
import type { TestRun } from '@/types/runs'
import { useTableSort } from '@/hooks/useTableSort'
import { formatDateTime, formatDuration } from '@/utils/formatters'
import { clsx } from 'clsx'
import { runsService } from '@/services/runsService'
import agentService from '@/services/agentService'
import { mutate } from 'swr'
import useSWR from 'swr'
import { api } from '@/services/api'
import { useProjectChangeRedirect } from '@/hooks/useProjectChange'
import { usePermissions } from '@/hooks/usePermissions'
import { KindBadgeWithEvidence } from '@/components/failures/KindEvidence'

interface TestCase {
  id: string
  test_name: string
  class_name?: string
  suite_name?: string
  status: string
  duration_ms?: number
  failure_category?: string
  // AI-classified failure kind (US-9.1 computed field on TestCaseSummary —
  // included in the list response, so the badge costs no extra call; the
  // AI-4 evidence popover fetches on demand by test_case_id when opened).
  failure_kind?: string | null
  // Phase 1 granular steps: # of top-level steps captured for this test's
  // latest-run snapshot. null when no parser emitted a step tree for this
  // producer; 0 when the parser ran but the test had no steps.
  step_count?: number | null
}

const STATUSES = ['', 'FAILED', 'BROKEN', 'PASSED', 'SKIPPED']

interface RegressionDiff {
  baseline_available: boolean
  baseline_run_id?: string
  baseline_build_number?: string
  pass_rate?: number
  baseline_pass_rate?: number
  pass_rate_delta?: number
  new_failing_tests?: Array<{ test_name: string; suite_name: string | null }>
  new_failing_count?: number
  resolved_count?: number
  commit_range?: Array<{ sha: string; message: string; author: string; timestamp: string }>
}

function RegressionDiffPanel({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false)
  const { data, isLoading } = useSWR<RegressionDiff>(
    open ? `regression-diff-${runId}` : null,
    () => api.get(`/api/v1/runs/${runId}/regression-diff`).then(r => r.data),
    { revalidateOnFocus: false },
  )

  return (
    <div className="card">
      <button
        onClick={() => setOpen(x => !x)}
        className="w-full flex items-center justify-between text-sm font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text)]"
      >
        <span className="flex items-center gap-2">
          <TrendingDown className="h-4 w-4 text-[var(--status-broken)]" />
          What changed since last good run?
        </span>
        {open ? <ChevronUp className="h-4 w-4 text-[var(--color-text-muted)]" /> : <ChevronDown className="h-4 w-4 text-[var(--color-text-muted)]" />}
      </button>

      {open && (
        <div className="mt-4 space-y-3">
          {isLoading && <p className="text-sm text-[var(--color-text-muted)]">Loading diff…</p>}
          {!isLoading && data && !data.baseline_available && (
            <p className="text-sm text-[var(--color-text-muted)]">No recent passing baseline run found for comparison.</p>
          )}
          {!isLoading && data && data.baseline_available && (
            <>
              <div className="grid grid-cols-3 gap-3 text-center">
                <div className="bg-[var(--color-bg-secondary)]/80 rounded-lg p-2">
                  <p className={clsx('text-xl font-bold tabular-nums', (data.pass_rate_delta ?? 0) >= 0 ? 'text-[var(--status-passed)]' : 'text-[var(--status-failed)]')}>
                    {(data.pass_rate_delta ?? 0) >= 0 ? '+' : ''}{data.pass_rate_delta?.toFixed(1)}%
                  </p>
                  <p className="text-xs text-[var(--color-text-muted)] mt-0.5">Pass rate delta</p>
                </div>
                <div className="bg-[var(--color-bg-secondary)]/80 rounded-lg p-2">
                  <p className="text-xl font-bold text-[var(--status-failed)]">{data.new_failing_count ?? 0}</p>
                  <p className="text-xs text-[var(--color-text-muted)] mt-0.5">New failures</p>
                </div>
                <div className="bg-[var(--color-bg-secondary)]/80 rounded-lg p-2">
                  <p className="text-xl font-bold text-[var(--status-passed)]">{data.resolved_count ?? 0}</p>
                  <p className="text-xs text-[var(--color-text-muted)] mt-0.5">Resolved</p>
                </div>
              </div>

              <p className="text-xs text-[var(--color-text-muted)]">Baseline: Build #{data.baseline_build_number}</p>

              {(data.new_failing_tests?.length ?? 0) > 0 && (
                <div>
                  <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-1.5">New failures</p>
                  <ul className="space-y-1">
                    {data.new_failing_tests?.slice(0, 10).map((t, i) => (
                      <li key={i} className="text-sm text-[var(--color-text-secondary)] flex items-center gap-2">
                        <div className="h-1.5 w-1.5 rounded-full bg-[var(--status-failed-bg)] flex-shrink-0" />
                        {t.test_name}
                        {t.suite_name && <span className="text-[var(--color-text-muted)] text-xs">· {t.suite_name}</span>}
                      </li>
                    ))}
                    {(data.new_failing_count ?? 0) > 10 && (
                      <li className="text-xs text-[var(--color-text-muted)]">…and {(data.new_failing_count ?? 0) - 10} more</li>
                    )}
                  </ul>
                </div>
              )}

              {(data.commit_range?.length ?? 0) > 0 && (
                <div>
                  <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-1.5">
                    Commits since baseline ({data.commit_range?.length})
                  </p>
                  <ul className="space-y-1">
                    {data.commit_range?.slice(0, 5).map((c) => (
                      <li key={c.sha} className="flex items-start gap-2 text-xs text-[var(--color-text-muted)]">
                        <GitCommit className="h-3.5 w-3.5 text-[var(--color-text-faint)] flex-shrink-0 mt-0.5" />
                        <span className="font-mono text-[var(--color-text)] mr-1">{c.sha}</span>
                        <span>{c.message}</span>
                        <span className="text-[var(--color-text-faint)]">— {c.author}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

function ReleaseTag({ releaseName, onSet }: {
  releaseName?: string
  onSet: (name: string) => Promise<void>
}) {
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)

  async function handleSave() {
    const name = value.trim()
    if (!name) return
    setSaving(true)
    try {
      await onSet(name)
      setEditing(false)
      setValue('')
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
        <input
          autoFocus
          type="text"
          placeholder="Release name (e.g. v2.5.0)"
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleSave(); if (e.key === 'Escape') setEditing(false) }}
          className="bg-[var(--color-bg-hover)] border border-[var(--color-border-light)] rounded px-2 py-0.5 text-xs text-[var(--color-text)] placeholder-[var(--color-text-faint)] w-44 focus:outline-none focus:border-[var(--color-border)]"
        />
        <button onClick={handleSave} disabled={saving} className="text-[var(--status-passed)] hover:text-[var(--status-passed)] disabled:opacity-50">
          <Check className="h-4 w-4" />
        </button>
        <button onClick={() => setEditing(false)} className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]">
          <X className="h-4 w-4" />
        </button>
      </div>
    )
  }

  if (releaseName) {
    return (
      <div className="flex items-center gap-2">
        <button
          onClick={() => navigate('/releases')}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-[var(--status-flaky-bg)]/40 text-[var(--status-flaky)] hover:bg-[var(--status-flaky-bg)]/50 transition-colors"
        >
          <Package className="h-3 w-3" />
          {releaseName}
        </button>
        <button onClick={() => setEditing(true)} title="Change release" className="text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]">
          <PencilLine className="h-3.5 w-3.5" />
        </button>
      </div>
    )
  }

  return (
    <button
      onClick={() => setEditing(true)}
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border border-dashed border-[var(--color-border-light)] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] hover:border-[var(--color-border)] transition-colors"
    >
      <Package className="h-3 w-3" />
      Set release
    </button>
  )
}

export default function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  // P4-5: Persist filters in URL params so they survive navigation
  const [page, setPage] = useState(() => Number(searchParams.get('page')) || 1)
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get('status') || '')
  const [suiteFilter, setSuiteFilter] = useState(() => searchParams.get('suite') || '')

  // P4-5: Sync state changes back to URL (replace to avoid history spam).
  // NOTE: setSearchParams is intentionally excluded from deps — including it
  // causes an infinite loop because react-router returns a new reference each render.
  useEffect(() => {
    const params: Record<string, string> = {}
    if (statusFilter) params.status = statusFilter
    if (suiteFilter) params.suite = suiteFilter
    if (page > 1) params.page = String(page)
    setSearchParams(params, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, suiteFilter, page])

  useProjectChangeRedirect('/runs', Boolean(runId))

  const { data: run } = useRun(runId)

  // Fetch a small page of recent runs for THIS run's suite so the
  // "Compare with previous run" CTA can pick the chronologically
  // immediately preceding run. We fetch only when we know the suite
  // (i.e. ``run.primary_suite_name`` is populated); the conditional
  // ``suite_name`` param leaves the hook idle for runs without suite
  // attribution. 50 results is plenty — the previous run is almost
  // always one or two slots away from the current one.
  const suiteForCompare = run?.primary_suite_name ?? null
  const { data: suiteRunsData } = useRuns(
    suiteForCompare
      ? { page: 1, size: 50, days: 0, suite_name: suiteForCompare }
      : undefined,
  )
  const suiteRuns = (suiteRunsData?.items ?? []) as TestRun[]

  function handleCompareWithPrevious() {
    if (!run) return
    if (!run.primary_suite_name) {
      toast('This run has no suite attribution — cannot pick a previous-of-same-suite.', { icon: '⚠️' })
      return
    }
    const previous = findPreviousRunOfSuite(run, suiteRuns)
    const href = buildCompareWithPreviousHref(run, previous)
    if (!href) {
      toast(
        `No earlier run of "${run.primary_suite_name}" found — this may be the first ingested run for the suite.`,
        { icon: '⚠️' },
      )
      return
    }
    navigate(href)
  }

  const { data, isLoading, error } = useTestCases(runId, {
    page, size: 25,
    ...(statusFilter && { status: statusFilter }),
    ...(suiteFilter && { suite: suiteFilter }),
  })
  const tcItems = (data?.items ?? []) as TestCase[]
  const { sorted: sortedCases, sortKey: tcSortKey, sortDir: tcSortDir, toggleSort: tcToggleSort } = useTableSort(tcItems, 'test_name', 'asc')

  const { isQaEngineer } = usePermissions()
  const [triggeringPipeline, setTriggeringPipeline] = useState(false)
  const [triggeringDeep, setTriggeringDeep] = useState(false)
  const [recoveringLive, setRecoveringLive] = useState(false)

  async function handleRecoverLive() {
    if (!runId || recoveringLive) return
    setRecoveringLive(true)
    try {
      const resp = await api.post<{ queued: boolean; buffered_events: number }>(
        `/api/v1/runs/${runId}/recover-live`,
      )
      toast.success(
        `Replaying ${resp.data.buffered_events} buffered events. Refreshing shortly…`,
        { icon: '↻', duration: 5000 },
      )
      // Persist task runs async on the ingestion worker. Give it a moment
      // then revalidate the SWR test-cases cache so the table populates
      // without a full page reload.
      setTimeout(() => { mutate(['test-cases', runId, { page, size: 25 }]) }, 2500)
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to queue recovery'
      toast.error(detail)
    } finally {
      setRecoveringLive(false)
    }
  }

  async function handleSetRelease(name: string) {
    if (!runId) return
    await runsService.setRelease(runId, name)
    mutate(['run', runId])
  }

  async function handleTriggerPipeline() {
    if (!runId) return
    setTriggeringPipeline(true)
    try {
      await agentService.triggerPipeline(runId)
      toast.success('Pipeline queued. Track progress on /agents.')
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to trigger pipeline'
      toast.error(detail)
    } finally {
      setTriggeringPipeline(false)
    }
  }

  async function handleTriggerDeep() {
    if (!runId) return
    setTriggeringDeep(true)
    try {
      await agentService.triggerDeepPipeline(runId)
      toast.success('Deep investigation queued — opening live view…')
      navigate(`/deep-investigate/${runId}`)
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to trigger deep investigation'
      toast.error(detail)
    } finally {
      setTriggeringDeep(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)] mb-2">
        <button onClick={() => navigate('/runs')} className="hover:text-[var(--color-text)] flex items-center gap-1">
          <ArrowLeft className="h-4 w-4" /> Runs
        </button>
        <ChevronRight className="h-3 w-3" />
        <span className="text-[var(--color-text)] font-mono">#{run?.build_number ?? '…'}</span>
      </div>

      {run && (
        <PageHeader
          title={`Run #${run.build_number}`}
          subtitle={`${run.jenkins_job ?? 'Jenkins'} · ${formatDateTime(run.created_at)}`}
          actions={
            <div className="flex items-center gap-3 text-sm flex-wrap">
              <SuiteBadge primary={run.primary_suite_name} all={run.suite_names} />
              <ReleaseTag
                releaseName={run.release_name}
                onSet={handleSetRelease}
              />
              <span className="text-[var(--status-passed)] font-medium">{run.passed_tests} passed</span>
              <span className="text-[var(--status-failed)] font-medium">{run.failed_tests} failed</span>
              <span className="text-[var(--status-broken)] font-medium">{run.skipped_tests} skipped</span>
              <span className="text-[var(--color-text-muted)]">/ {run.total_tests} total</span>
              <StatusBadge status={run.status} />
              {isQaEngineer && (
                <>
                  <button
                    type="button"
                    onClick={handleTriggerPipeline}
                    disabled={triggeringPipeline || triggeringDeep}
                    title="Re-run the multi-agent analysis pipeline (ingestion → anomaly → root cause → summary → triage). Useful after changing AI mode or fixing an upstream issue."
                    className="btn-secondary text-xs flex items-center gap-1.5 py-1 disabled:opacity-50"
                  >
                    {triggeringPipeline ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />}
                    {triggeringPipeline ? 'Queuing…' : 'Trigger pipeline'}
                  </button>
                  <button
                    type="button"
                    onClick={handleTriggerDeep}
                    disabled={triggeringPipeline || triggeringDeep}
                    title="Run the deep investigation pipeline — adds failure clustering, flaky sentinel, test health, and release risk on top of the standard stages. Requires LLM or Auto mode."
                    className="btn-secondary text-xs flex items-center gap-1.5 py-1 disabled:opacity-50"
                  >
                    {triggeringDeep ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Stethoscope className="h-3.5 w-3.5" />}
                    {triggeringDeep ? 'Queuing…' : 'Deep investigate'}
                  </button>
                </>
              )}
              <button
                type="button"
                onClick={handleCompareWithPrevious}
                disabled={!run.primary_suite_name}
                title={
                  run.primary_suite_name
                    ? `Compare this run to the previous run of "${run.primary_suite_name}"`
                    : 'No suite attribution on this run — cannot pick a previous-of-same-suite'
                }
                className="btn-secondary text-xs flex items-center gap-1.5 py-1 disabled:opacity-50"
              >
                <GitCompare className="h-3.5 w-3.5" />
                Compare to previous
              </button>
              <Link
                to={`/runs/${runId}/intelligence`}
                className="btn-primary text-xs flex items-center gap-1.5 py-1"
              >
                <Bot className="h-3.5 w-3.5" />
                Run Intelligence
              </Link>
            </div>
          }
        />
      )}

      {/* Regression diff */}
      {runId && <RegressionDiffPanel runId={runId} />}

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1 bg-[var(--color-bg-secondary)] rounded-lg p-1">
          {STATUSES.map(s => (
            <button
              key={s || 'all'}
              onClick={() => { setStatusFilter(s); setPage(1) }}
              aria-pressed={statusFilter === s}
              className={clsx(
                'px-3 py-1 rounded-md text-sm font-medium transition-colors',
                statusFilter === s ? 'bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-btn-primary-text)]',
              )}
            >
              {s || 'All'}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="Filter by suite…"
          className="input w-48 h-9"
          value={suiteFilter}
          onChange={e => { setSuiteFilter(e.target.value); setPage(1) }}
        />
      </div>

      {/* Test case table */}
      <div className="card p-0 overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-20"><LoadingSpinner size="lg" /></div>
        ) : error ? (
          <div className="flex items-center justify-center py-16 text-[var(--status-failed)] text-sm gap-2">
            <span>Failed to load test cases — {(error as Error)?.message ?? 'server error'}</span>
          </div>
        ) : !data?.items?.length ? (
          <div className="flex flex-col items-center justify-center py-16 text-[var(--color-text-muted)] text-sm gap-3 px-6 text-center max-w-2xl mx-auto">
            {(() => {
              const isLive = run?.trigger_source === 'live_stream'
              const totalReported = run?.total_tests ?? 0
              const hasAggregates = totalReported > 0
              const hasActiveFilter = Boolean(statusFilter || suiteFilter)

              if (hasActiveFilter) {
                return (
                  <>
                    <p>No test cases match the current filters{statusFilter && ` (status: ${statusFilter})`}{suiteFilter && ` (suite: ${suiteFilter})`}.</p>
                    <button
                      onClick={() => { setStatusFilter(''); setSuiteFilter(''); setPage(1) }}
                      className="text-[var(--color-text)] hover:text-[var(--color-text-secondary)] text-xs underline"
                    >
                      Clear filters
                    </button>
                  </>
                )
              }

              if (isLive && hasAggregates) {
                // The run record carries aggregate counts from the live state
                // hash (HINCRBY) but persist_live_session didn't materialise
                // per-test rows — usually the persistence task crashed after
                // setting its dedup key (so retries silently skipped) while
                // the Redis event buffer (25h TTL) still has the data. The
                // ``Recover from buffer`` button below triggers a fresh
                // persist task that idempotency-checks based on actual
                // TestCase row count rather than a stuck dedup flag.
                // Migration 0086 also archives the events on the TestRun
                // row at session-close time so the 15-day fallback path
                // works even after the 25-hour Redis TTL has lapsed.
                return (
                  <>
                    <p className="text-[var(--color-text)]">
                      This live run reported <strong>{totalReported}</strong> test{totalReported === 1 ? '' : 's'}
                      {' '}({run?.passed_tests ?? 0} passed, {run?.failed_tests ?? 0} failed
                      {(run?.skipped_tests ?? 0) > 0 && `, ${run?.skipped_tests} skipped`}
                      {(run?.broken_tests ?? 0) > 0 && `, ${run?.broken_tests} broken`}),
                      but per-test details aren't loaded yet.
                    </p>
                    <p className="text-xs">
                      Buffered events are held in Redis for 25 hours after a run
                      closes, and a durable copy is archived on the run for{' '}
                      <strong>up to 15 days</strong>. If persistence didn&apos;t
                      finish on the first try (worker crash, transient error),
                      use the button below to replay from whichever source is
                      still available. After 15 days, re-run the suite or
                      re-ingest the results as a file upload.
                    </p>
                    <div className="flex items-center gap-3 mt-1">
                      <button
                        type="button"
                        disabled={recoveringLive || !runId}
                        onClick={() => handleRecoverLive()}
                        className="btn-primary text-xs flex items-center gap-1.5 disabled:opacity-50"
                      >
                        {recoveringLive
                          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          : <RotateCcw className="h-3.5 w-3.5" />}
                        {recoveringLive ? 'Replaying…' : 'Recover from buffer'}
                      </button>
                      <button
                        type="button"
                        onClick={() => window.location.reload()}
                        className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-xs underline"
                      >
                        Refresh
                      </button>
                    </div>
                  </>
                )
              }

              if (isLive) {
                return (
                  <>
                    <p>Live test results are still being processed. This usually takes a few seconds after the session closes.</p>
                    <button
                      onClick={() => window.location.reload()}
                      className="text-[var(--color-text)] hover:text-[var(--color-text-secondary)] text-xs underline"
                    >
                      Refresh page
                    </button>
                  </>
                )
              }

              return <p>No test cases found for this run.</p>
            })()}
          </div>
        ) : (
          <>
            <table className="w-full">
              <thead className="border-b border-[var(--color-border)]">
                <tr>
                  <SortableHeader label="Test Name" sortKey="test_name" currentKey={tcSortKey} dir={tcSortDir} onSort={tcToggleSort} />
                  <SortableHeader label="Suite" sortKey="suite_name" currentKey={tcSortKey} dir={tcSortDir} onSort={tcToggleSort} />
                  <SortableHeader label="Status" sortKey="status" currentKey={tcSortKey} dir={tcSortDir} onSort={tcToggleSort} />
                  <SortableHeader label="Duration" sortKey="duration_ms" currentKey={tcSortKey} dir={tcSortDir} onSort={tcToggleSort} />
                  <SortableHeader label="Category" sortKey="failure_category" currentKey={tcSortKey} dir={tcSortDir} onSort={tcToggleSort} />
                  <th className="th" />
                </tr>
              </thead>
              <tbody>
                {sortedCases.map((tc) => (
                  <tr
                    key={tc.id}
                    className="table-row"
                    onClick={() => navigate(`/runs/${runId}/tests/${tc.id}`)}
                  >
                    <td className="td max-w-[280px]">
                      <div className="flex items-center gap-2">
                        <p className="truncate text-[var(--color-text)] text-sm font-medium">{tc.test_name}</p>
                        {typeof tc.step_count === 'number' && tc.step_count > 0 && (
                          <span
                            title={`${tc.step_count} captured step${tc.step_count === 1 ? '' : 's'} — open to view the step tree`}
                            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] flex-shrink-0 tabular-nums"
                          >
                            <ListTree className="h-3 w-3" />
                            {tc.step_count}
                          </span>
                        )}
                      </div>
                      {tc.class_name && <p className="truncate text-xs text-[var(--color-text-muted)] font-mono mt-0.5">{tc.class_name}</p>}
                    </td>
                    <td className="td text-[var(--color-text-muted)] text-sm truncate max-w-[160px]">{tc.suite_name ?? '—'}</td>
                    <td className="td"><StatusBadge status={tc.status} /></td>
                    <td className="td text-[var(--color-text-muted)]">{formatDuration(tc.duration_ms)}</td>
                    <td className="td">
                      <span className="inline-flex items-center gap-2">
                        {tc.failure_category && (
                          <span className="text-xs text-[var(--color-text-muted)]">{tc.failure_category.replace('_', ' ')}</span>
                        )}
                        {tc.failure_kind && (tc.status === 'FAILED' || tc.status === 'BROKEN') && (
                          <KindBadgeWithEvidence kind={tc.failure_kind} testCaseId={tc.id} compact />
                        )}
                      </span>
                    </td>
                    <td className="td text-[var(--color-text-faint)]">
                      <ChevronRight className="h-4 w-4" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data && <Pagination page={page} pages={data.pages} total={data.total} onChange={setPage} />}
          </>
        )}
      </div>
    </div>
  )
}
