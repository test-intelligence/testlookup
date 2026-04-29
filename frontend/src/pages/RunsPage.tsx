import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { GitBranch, Package, Sparkles } from 'lucide-react'
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
                    className="table-row"
                    onClick={() => navigate(
                      run.status === 'FAILED' || run.status === 'failed'
                        ? `/runs/${run.id}/intelligence`
                        : `/runs/${run.id}`,
                    )}
                  >
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
