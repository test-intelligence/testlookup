import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Bot, ChevronDown, ChevronRight, ChevronUp, GitCommit, Loader2, Package, PencilLine, Stethoscope, TrendingDown, X, Check, Zap } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import StatusBadge from '@/components/ui/StatusBadge'
import SuiteBadge from '@/components/ui/SuiteBadge'
import SortableHeader from '@/components/ui/SortableHeader'
import Pagination from '@/components/ui/Pagination'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useRun, useTestCases } from '@/hooks/useRuns'
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

interface TestCase {
  id: string
  test_name: string
  class_name?: string
  suite_name?: string
  status: string
  duration_ms?: number
  failure_category?: string
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
          <TrendingDown className="h-4 w-4 text-amber-400" />
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
                  <p className={clsx('text-xl font-bold tabular-nums', (data.pass_rate_delta ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                    {(data.pass_rate_delta ?? 0) >= 0 ? '+' : ''}{data.pass_rate_delta?.toFixed(1)}%
                  </p>
                  <p className="text-xs text-[var(--color-text-muted)] mt-0.5">Pass rate delta</p>
                </div>
                <div className="bg-[var(--color-bg-secondary)]/80 rounded-lg p-2">
                  <p className="text-xl font-bold text-red-400">{data.new_failing_count ?? 0}</p>
                  <p className="text-xs text-[var(--color-text-muted)] mt-0.5">New failures</p>
                </div>
                <div className="bg-[var(--color-bg-secondary)]/80 rounded-lg p-2">
                  <p className="text-xl font-bold text-emerald-400">{data.resolved_count ?? 0}</p>
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
                        <div className="h-1.5 w-1.5 rounded-full bg-red-400 flex-shrink-0" />
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
          className="bg-[var(--color-bg-hover)] border border-[var(--color-border-light)] rounded px-2 py-0.5 text-xs text-[var(--color-text)] placeholder-[var(--color-text-faint)] w-44 focus:outline-none focus:border-neutral-500"
        />
        <button onClick={handleSave} disabled={saving} className="text-emerald-400 hover:text-emerald-300 disabled:opacity-50">
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
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-violet-900/40 text-violet-300 hover:bg-violet-800/50 transition-colors"
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
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border border-dashed border-[var(--color-border-light)] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] hover:border-neutral-500 transition-colors"
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
  const { data, isLoading, error } = useTestCases(runId, {
    page, size: 50,
    ...(statusFilter && { status: statusFilter }),
    ...(suiteFilter && { suite: suiteFilter }),
  })
  const tcItems = (data?.items ?? []) as TestCase[]
  const { sorted: sortedCases, sortKey: tcSortKey, sortDir: tcSortDir, toggleSort: tcToggleSort } = useTableSort(tcItems, 'test_name', 'asc')

  const { isQaEngineer } = usePermissions()
  const [triggeringPipeline, setTriggeringPipeline] = useState(false)
  const [triggeringDeep, setTriggeringDeep] = useState(false)

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
              <span className="text-emerald-400 font-medium">{run.passed_tests} passed</span>
              <span className="text-red-400 font-medium">{run.failed_tests} failed</span>
              <span className="text-amber-400 font-medium">{run.skipped_tests} skipped</span>
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
          <div className="flex items-center justify-center py-16 text-red-400 text-sm gap-2">
            <span>Failed to load test cases — {(error as Error)?.message ?? 'server error'}</span>
          </div>
        ) : !data?.items?.length ? (
          <div className="flex flex-col items-center justify-center py-16 text-[var(--color-text-muted)] text-sm gap-3">
            {run?.trigger_source === 'live_stream' ? (
              <>
                <p>Live test results are being processed. This may take a few moments.</p>
                <button
                  onClick={() => window.location.reload()}
                  className="text-[var(--color-text)] hover:text-[var(--color-text-secondary)] text-xs underline"
                >
                  Refresh page
                </button>
              </>
            ) : (
              <p>No test cases found for this run.</p>
            )}
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
                      <p className="truncate text-[var(--color-text)] text-sm font-medium">{tc.test_name}</p>
                      {tc.class_name && <p className="truncate text-xs text-[var(--color-text-muted)] font-mono mt-0.5">{tc.class_name}</p>}
                    </td>
                    <td className="td text-[var(--color-text-muted)] text-sm truncate max-w-[160px]">{tc.suite_name ?? '—'}</td>
                    <td className="td"><StatusBadge status={tc.status} /></td>
                    <td className="td text-[var(--color-text-muted)]">{formatDuration(tc.duration_ms)}</td>
                    <td className="td">
                      {tc.failure_category && (
                        <span className="text-xs text-[var(--color-text-muted)]">{tc.failure_category.replace('_', ' ')}</span>
                      )}
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
