import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { GitBranch, Loader2, Package, Sparkles, Stethoscope, X, Zap } from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import PageHeader from '@/components/ui/PageHeader'
import StatusBadge from '@/components/ui/StatusBadge'
import SortableHeader from '@/components/ui/SortableHeader'
import Pagination from '@/components/ui/Pagination'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { buildRunsWorkflow } from '@/components/workflow/workflowPresets'
import { useRuns } from '@/hooks/useRuns'
import { useTableSort } from '@/hooks/useTableSort'
import { formatDateTime, fromNow, formatDuration, formatPassRate } from '@/utils/formatters'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import agentService from '@/services/agentService'
import { runsService } from '@/services/runsService'

interface TestRun {
  id: string
  project_id?: string
  project_name?: string
  build_number: number
  jenkins_job?: string
  branch?: string
  status: string
  passed_tests: number
  failed_tests: number
  total_tests: number
  pass_rate: number
  duration_ms?: number
  created_at: string
  ocp_pod_name?: string
  release_name?: string
  release_id?: string
}

const DAYS_OPTIONS = [
  { label: 'Last 6 days', value: 6 },
  { label: 'Last 14 days', value: 14 },
  { label: 'Last 30 days', value: 30 },
  { label: 'Last 90 days', value: 90 },
  { label: 'All time', value: 0 },
]

export default function RunsPage() {
  const navigate = useNavigate()
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [days, setDays] = useState(6)

  const { data, isLoading } = useRuns({ page, size: 20, days, ...(statusFilter && { status: statusFilter }) })
  const runs = useMemo(() => (data?.items ?? []) as TestRun[], [data?.items])
  const { sorted: sortedRuns, sortKey, sortDir, toggleSort } = useTableSort(runs, 'created_at', 'desc')
  const workflow = useMemo(() => buildRunsWorkflow(runs, statusFilter, isAllProjects), [runs, statusFilter, isAllProjects])
  const { isQaEngineer } = usePermissions()
  // The same key tracks both standard and deep triggers so the row's two
  // icons can disable in lockstep. Format: `${runId}:${kind}` while in flight.
  const [triggeringKey, setTriggeringKey] = useState<string | null>(null)
  const [selectedRunIds, setSelectedRunIds] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [failedShortcutBusy, setFailedShortcutBusy] = useState<null | 'standard' | 'deep'>(null)
  const [failedAllPages, setFailedAllPages] = useState(false)
  const [failedAllCount, setFailedAllCount] = useState<number | null>(null)
  const [onlyPending, setOnlyPending] = useState(true)

  // Visible FAILED runs (current page only — the user's pagination context).
  const failedVisible = useMemo(
    () => sortedRuns.filter(r => (r.status ?? '').toUpperCase() === 'FAILED'),
    [sortedRuns],
  )

  // Fetch the full FAILED count when the user toggles "All pages" so the
  // count badge stays accurate. Refreshes when day filter / project /
  // only-pending toggle changes.
  useEffect(() => {
    if (!failedAllPages) {
      setFailedAllCount(null)
      return
    }
    let cancelled = false
    runsService
      .listFailedIds(
        isAllProjects ? null : (activeProjectId ?? null),
        days,
        onlyPending,
      )
      .then(res => {
        if (!cancelled) setFailedAllCount(res.count)
      })
      .catch(() => {
        if (!cancelled) setFailedAllCount(null)
      })
    return () => {
      cancelled = true
    }
  }, [failedAllPages, days, activeProjectId, isAllProjects, onlyPending])

  // Confirmation guard for large fan-outs. Returns true when the user
  // accepts (or count is below the threshold). Skips for ≤50 so the common
  // path stays one click.
  const CONFIRM_THRESHOLD = 50
  function confirmLargeFanOut(count: number, kind: 'standard' | 'deep'): boolean {
    if (count <= CONFIRM_THRESHOLD) return true
    return window.confirm(
      `About to queue ${count} ${kind === 'deep' ? 'deep investigation' : 'standard'} pipelines. ` +
      `This will fan out ${count} parallel API calls. Continue?`,
    )
  }

  async function handleTriggerPipeline(runId: string) {
    setTriggeringKey(`${runId}:standard`)
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
      setTriggeringKey(null)
    }
  }

  async function handleTriggerDeep(runId: string) {
    setTriggeringKey(`${runId}:deep`)
    try {
      await agentService.triggerDeepPipeline(runId)
      toast.success('Deep investigation queued.')
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to trigger deep investigation'
      toast.error(detail)
    } finally {
      setTriggeringKey(null)
    }
  }

  // ── Bulk selection helpers ──────────────────────────────────────────────
  function toggleRow(runId: string) {
    setSelectedRunIds(prev => {
      const next = new Set(prev)
      if (next.has(runId)) next.delete(runId)
      else next.add(runId)
      return next
    })
  }

  function toggleAllVisible(visibleIds: string[]) {
    setSelectedRunIds(prev => {
      const allSelected = visibleIds.every(id => prev.has(id))
      const next = new Set(prev)
      if (allSelected) {
        visibleIds.forEach(id => next.delete(id))
      } else {
        visibleIds.forEach(id => next.add(id))
      }
      return next
    })
  }

  function clearSelection() {
    setSelectedRunIds(new Set())
  }

  async function handleBulkTrigger(kind: 'standard' | 'deep') {
    if (selectedRunIds.size === 0) return
    if (!confirmLargeFanOut(selectedRunIds.size, kind)) return
    setBulkBusy(true)
    try {
      const res = await agentService.bulkTriggerPipelines(
        Array.from(selectedRunIds),
        kind === 'deep' ? 'deep' : 'offline',
      )
      const kindLabel = kind === 'deep' ? 'deep investigation' : 'pipeline'
      const skippedSuffix = res.not_found > 0 ? ` (${res.not_found} not found)` : ''
      if (res.queued > 0) {
        toast.success(`${res.queued} ${kindLabel}${res.queued === 1 ? '' : 's'} queued.${skippedSuffix}`)
        clearSelection()
      } else {
        toast.error(`Nothing queued — all ${res.not_found} run IDs were not found.`)
      }
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Bulk trigger failed'
      toast.error(detail)
    } finally {
      setBulkBusy(false)
    }
  }

  async function handleTriggerAllFailed(kind: 'standard' | 'deep') {
    // Resolve target IDs: visible page or every FAILED in the day window.
    // The backend's only_pending filter excludes runs already in flight.
    let targetIds: string[]
    let truncatedFromServer = false
    if (failedAllPages) {
      try {
        const res = await runsService.listFailedIds(
          isAllProjects ? null : (activeProjectId ?? null),
          days,
          onlyPending,
        )
        targetIds = res.ids
        truncatedFromServer = res.truncated
      } catch (err: unknown) {
        const detail = (err as Error)?.message ?? 'Failed to fetch failed run IDs'
        toast.error(detail)
        return
      }
    } else {
      targetIds = failedVisible.map(r => r.id)
    }

    if (targetIds.length === 0) {
      toast('No failed runs in the current scope to trigger', { icon: 'ℹ️' })
      return
    }
    if (!confirmLargeFanOut(targetIds.length, kind)) return

    setFailedShortcutBusy(kind)
    try {
      const res = await agentService.bulkTriggerPipelines(
        targetIds,
        kind === 'deep' ? 'deep' : 'offline',
      )
      const kindLabel = kind === 'deep' ? 'deep investigation' : 'pipeline'
      const truncSuffix = truncatedFromServer ? ' (server cap reached — more runs exist)' : ''
      const skippedSuffix = res.not_found > 0 ? ` (${res.not_found} not found)` : ''
      if (res.queued > 0) {
        toast.success(
          `Queued ${res.queued} ${kindLabel}${res.queued === 1 ? '' : 's'} for failed runs.${skippedSuffix}${truncSuffix}`,
        )
      } else {
        toast.error(`Nothing queued — all ${res.not_found} run IDs were not found.`)
      }
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Bulk trigger failed'
      toast.error(detail)
    } finally {
      setFailedShortcutBusy(null)
    }
  }

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<GitBranch className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to view test runs"
      />
    )
  }

  const projectLabel = project?.name ?? 'All Projects'

  return (
    <div className="space-y-4">
      <PageHeader
        title="Test Runs"
        subtitle={`Jenkins builds for ${projectLabel}`}
        actions={
          <div className="flex items-center gap-2">
            {isQaEngineer && (() => {
              const failedCount = failedAllPages
                ? (failedAllCount ?? 0)
                : failedVisible.length
              const scopeLabel = failedAllPages ? 'across all pages' : 'on this page'
              const noFailures = failedCount === 0 && !failedAllPages
                ? 'No failed runs in the current view'
                : failedAllPages && failedAllCount === null
                  ? 'Loading failed run count…'
                  : null
              return (
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => handleTriggerAllFailed('standard')}
                    disabled={failedShortcutBusy != null || failedCount === 0}
                    title={noFailures ?? `Re-fire the standard pipeline for every failed run ${scopeLabel} (${failedCount})`}
                    className="btn-secondary text-xs flex items-center gap-1.5 py-1.5 disabled:opacity-50"
                  >
                    {failedShortcutBusy === 'standard'
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Zap className="h-3.5 w-3.5" />}
                    Trigger FAILED
                    <span className="ml-0.5 px-1.5 py-0.5 rounded bg-[var(--color-bg-secondary)] text-[10px] text-[var(--color-text-muted)] font-mono">
                      {failedCount}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleTriggerAllFailed('deep')}
                    disabled={failedShortcutBusy != null || failedCount === 0}
                    title={noFailures ?? `Run deep investigation for every failed run ${scopeLabel} (${failedCount}) — requires LLM or Auto mode`}
                    className="btn-secondary text-xs flex items-center gap-1.5 py-1.5 disabled:opacity-50"
                  >
                    {failedShortcutBusy === 'deep'
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Stethoscope className="h-3.5 w-3.5" />}
                    Deep FAILED
                    <span className="ml-0.5 px-1.5 py-0.5 rounded bg-[var(--color-bg-secondary)] text-[10px] text-[var(--color-text-muted)] font-mono">
                      {failedCount}
                    </span>
                  </button>
                  <label
                    title="When checked, the buttons above operate on every failed run in the day window — not just this page. Server caps at 1000."
                    className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] cursor-pointer select-none px-1"
                  >
                    <input
                      type="checkbox"
                      checked={failedAllPages}
                      onChange={e => setFailedAllPages(e.target.checked)}
                      className="cursor-pointer"
                    />
                    All pages
                  </label>
                  {failedAllPages && (
                    <label
                      title="Skip runs that already have a pipeline running (or one queued in the last 2h, matching the Celery dedup window). Avoids re-firing what's already in flight."
                      className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] cursor-pointer select-none px-1"
                    >
                      <input
                        type="checkbox"
                        checked={onlyPending}
                        onChange={e => setOnlyPending(e.target.checked)}
                        className="cursor-pointer"
                      />
                      Skip in-flight
                    </label>
                  )}
                </div>
              )
            })()}
            <select
              className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text-secondary)] text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
              value={days}
              onChange={e => { setDays(Number(e.target.value)); setPage(1) }}
            >
              {DAYS_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <select
              className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text-secondary)] text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
              value={statusFilter}
              onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
            >
              <option value="">All statuses</option>
              <option value="FAILED">Failed</option>
              <option value="PASSED">Passed</option>
              <option value="IN_PROGRESS">In Progress</option>
            </select>
          </div>
        }
      />

      <WorkflowTimeline
        title="Run workflow"
        subtitle="Ingest builds, detect failures, hand off intelligence, and keep release context visible"
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
        showInspector
      />

      {isQaEngineer && selectedRunIds.size > 0 && (
        <div className="card flex items-center gap-3 px-4 py-2 border-[var(--color-border-light)] bg-[var(--color-bg-secondary)]/50">
          <span className="text-sm text-[var(--color-text)]">
            {selectedRunIds.size} run{selectedRunIds.size === 1 ? '' : 's'} selected
          </span>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => handleBulkTrigger('standard')}
              disabled={bulkBusy}
              className="btn-secondary text-xs flex items-center gap-1.5 py-1 disabled:opacity-50"
              title="Trigger the standard pipeline for every selected run"
            >
              {bulkBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />}
              Trigger {selectedRunIds.size}
            </button>
            <button
              type="button"
              onClick={() => handleBulkTrigger('deep')}
              disabled={bulkBusy}
              className="btn-secondary text-xs flex items-center gap-1.5 py-1 disabled:opacity-50"
              title="Trigger the deep investigation pipeline for every selected run (requires LLM or Auto mode)"
            >
              {bulkBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Stethoscope className="h-3.5 w-3.5" />}
              Deep {selectedRunIds.size}
            </button>
            <button
              type="button"
              onClick={clearSelection}
              disabled={bulkBusy}
              className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] flex items-center gap-1 px-2 py-1 disabled:opacity-50"
              title="Clear selection"
            >
              <X className="h-3.5 w-3.5" />
              Clear
            </button>
          </div>
        </div>
      )}

      <div className="card p-0 overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-20"><LoadingSpinner size="lg" /></div>
        ) : !data?.items?.length ? (
          <EmptyState
            icon={<GitBranch className="h-8 w-8" />}
            title="No runs found"
            description="Test results will appear here after your first Jenkins build"
          />
        ) : (
          <>
            <table className="w-full">
              <thead className="border-b border-[var(--color-border)]">
                <tr>
                  {isQaEngineer && (
                    <th className="th w-8 px-2">
                      <input
                        type="checkbox"
                        aria-label="Select all visible runs"
                        checked={sortedRuns.length > 0 && sortedRuns.every(r => selectedRunIds.has(r.id))}
                        ref={el => {
                          if (el) {
                            const visible = sortedRuns.map(r => r.id)
                            const someSelected = visible.some(id => selectedRunIds.has(id))
                            const allSelected = visible.length > 0 && visible.every(id => selectedRunIds.has(id))
                            el.indeterminate = someSelected && !allSelected
                          }
                        }}
                        onChange={() => toggleAllVisible(sortedRuns.map(r => r.id))}
                        className="cursor-pointer"
                      />
                    </th>
                  )}
                  {isAllProjects && <SortableHeader label="Project" sortKey="project_name" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />}
                  <SortableHeader label="Run ID" sortKey="id" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Build" sortKey="build_number" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Job" sortKey="jenkins_job" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Branch" sortKey="branch" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Release" sortKey="release_name" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Status" sortKey="status" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Tests" sortKey="total_tests" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Pass Rate" sortKey="pass_rate" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Duration" sortKey="duration_ms" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Started" sortKey="created_at" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <th className="th" />
                </tr>
              </thead>
              <tbody>
                {sortedRuns.map((run) => (
                  <tr
                    key={run.id}
                    className={clsx(
                      'table-row',
                      selectedRunIds.has(run.id) && 'bg-[var(--color-bg-secondary)]/30',
                    )}
                    onClick={() => navigate(
                      run.status === 'FAILED' || run.status === 'failed'
                        ? `/runs/${run.id}/intelligence`
                        : `/runs/${run.id}`,
                    )}
                  >
                    {isQaEngineer && (
                      <td className="td w-8 px-2" onClick={e => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={`Select run ${run.build_number}`}
                          checked={selectedRunIds.has(run.id)}
                          onChange={() => toggleRow(run.id)}
                          className="cursor-pointer"
                        />
                      </td>
                    )}
                    {isAllProjects && (
                      <td
                        className="td text-[var(--color-text)] font-medium truncate max-w-[140px] cursor-pointer hover:text-[var(--color-text-secondary)]"
                        onClick={e => { e.stopPropagation(); navigate('/projects') }}
                        title="View projects"
                      >
                        {run.project_name ?? run.project_id?.slice(0, 8) ?? '—'}
                      </td>
                    )}
                    <td className="td font-mono text-[var(--color-text-secondary)] text-xs" title={run.id}>{run.id.slice(0, 8)}</td>
                    <td className="td font-mono text-[var(--color-text)] font-medium">#{run.build_number}</td>
                    <td className="td text-[var(--color-text-muted)] truncate max-w-[160px]">{run.jenkins_job ?? '—'}</td>
                    <td className="td text-[var(--color-text-muted)]">{run.branch ?? '—'}</td>
                    <td className="td">
                      {run.release_name ? (
                        <button
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-violet-900/40 text-violet-300 hover:bg-violet-800/50 transition-colors"
                          onClick={e => { e.stopPropagation(); navigate('/releases') }}
                          title={`Release: ${run.release_name}`}
                        >
                          <Package className="h-3 w-3" />
                          {run.release_name}
                        </button>
                      ) : (
                        <span className="text-[var(--color-text-faint)] text-xs">—</span>
                      )}
                    </td>
                    <td className="td"><StatusBadge status={run.status} /></td>
                    <td className="td">
                      <span className="text-emerald-400">{run.passed_tests}</span>
                      <span className="text-[var(--color-text-faint)] mx-1">/</span>
                      <span className="text-red-400">{run.failed_tests}</span>
                      <span className="text-[var(--color-text-faint)] mx-1">/</span>
                      <span className="text-[var(--color-text-muted)]">{run.total_tests}</span>
                    </td>
                    <td className="td font-medium">{formatPassRate(run.pass_rate)}</td>
                    <td className="td text-[var(--color-text-muted)]">{formatDuration(run.duration_ms)}</td>
                    <td className="td text-[var(--color-text-muted)]" title={formatDateTime(run.created_at)}>{fromNow(run.created_at)}</td>
                    <td className="td" onClick={e => e.stopPropagation()}>
                      <div className="flex items-center gap-1.5 justify-end">
                        {run.status === 'FAILED' && (
                          <button
                            onClick={() => navigate(`/runs/${run.id}/intelligence`)}
                            title="View Run Intelligence"
                            className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium bg-white/10 text-[var(--color-text-secondary)] border border-[var(--color-border-light)] hover:bg-[var(--color-bg-hover)]/40 transition-colors whitespace-nowrap"
                          >
                            <Sparkles className="h-3 w-3" />
                            Intelligence
                          </button>
                        )}
                        {isQaEngineer && (
                          <>
                            <button
                              onClick={() => handleTriggerPipeline(run.id)}
                              disabled={triggeringKey?.startsWith(`${run.id}:`)}
                              title="Trigger standard pipeline (ingestion → anomaly → root cause → summary → triage)"
                              className="inline-flex items-center justify-center h-7 w-7 rounded text-[var(--color-text-muted)] border border-[var(--color-border-light)] hover:bg-[var(--color-bg-hover)]/40 hover:text-[var(--color-text-secondary)] transition-colors disabled:opacity-50"
                            >
                              {triggeringKey === `${run.id}:standard`
                                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                : <Zap className="h-3.5 w-3.5" />}
                            </button>
                            <button
                              onClick={() => handleTriggerDeep(run.id)}
                              disabled={triggeringKey?.startsWith(`${run.id}:`)}
                              title="Trigger deep investigation — adds clustering, flaky sentinel, test health, release risk. Requires LLM or Auto mode."
                              className="inline-flex items-center justify-center h-7 w-7 rounded text-[var(--color-text-muted)] border border-[var(--color-border-light)] hover:bg-[var(--color-bg-hover)]/40 hover:text-[var(--color-text-secondary)] transition-colors disabled:opacity-50"
                            >
                              {triggeringKey === `${run.id}:deep`
                                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                : <Stethoscope className="h-3.5 w-3.5" />}
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination page={page} pages={data.pages} total={data.total} onChange={setPage} />
          </>
        )}
      </div>
    </div>
  )
}
