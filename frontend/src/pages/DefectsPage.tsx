import { useState } from 'react'
import { Gauge, ExternalLink, ChevronLeft, ChevronRight, LayoutGrid } from 'lucide-react'
import { clsx } from 'clsx'
import WidgetPicker from '@/components/analytics/WidgetPicker'
import { useAnalyticsView } from '@/hooks/useAnalyticsView'
import PageHeader from '@/components/ui/PageHeader'
import SortableHeader from '@/components/ui/SortableHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { buildDefectsWorkflow } from '@/components/workflow/workflowPresets'
import { useDefects } from '@/hooks/useMetrics'
import { useTableSort } from '@/hooks/useTableSort'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'

const RESOLUTION_FILTERS = [
  { label: 'All',        value: undefined       },
  { label: 'Open',       value: 'OPEN'          },
  { label: 'In Progress',value: 'IN_PROGRESS'   },
  { label: 'Resolved',   value: 'RESOLVED'      },
  { label: 'Closed',     value: 'CLOSED'        },
]

const RESOLUTION_COLORS: Record<string, string> = {
  OPEN:        'bg-red-500/15 text-red-400 border-red-500/30',
  IN_PROGRESS: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  RESOLVED:    'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  CLOSED:      'bg-neutral-700/15 text-[var(--color-text-muted)] border-[var(--color-border-light)]/30',
}

const CONFIDENCE_COLOR = (score: number) =>
  score >= 80 ? 'text-emerald-400' : score >= 60 ? 'text-amber-400' : 'text-red-400'

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

interface Defect {
  id: string
  jira_ticket_id?: string
  jira_ticket_url?: string
  jira_status?: string
  failure_category?: string
  resolution_status: string
  ai_confidence_score?: number
  created_at: string
  resolved_at?: string
  test_name: string
  suite_name?: string
  release_name?: string
}

export default function DefectsPage() {
  const [page, setPage] = useState(1)
  const [resolutionFilter, setResolutionFilter] = useState<string | undefined>(undefined)
  const [showPicker, setShowPicker] = useState(false)
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const { data, isLoading } = useDefects(page, resolutionFilter)
  const analyticsView = useAnalyticsView('defects')
  const defectItems: Defect[] = data?.items ?? []
  const { sorted: sortedItems, sortKey, sortDir, toggleSort } = useTableSort(defectItems, 'created_at', 'desc')

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<Gauge className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to view defects"
      />
    )
  }

  const projectLabel = project?.name ?? 'All Projects'

  const items = defectItems
  const total: number   = data?.total ?? 0
  const pages: number   = data?.pages ?? 1
  const workflow = buildDefectsWorkflow(items, resolutionFilter, projectLabel, page, pages)

  function handleFilterChange(value: string | undefined) {
    setResolutionFilter(value)
    setPage(1)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Defects"
        subtitle={`Defect tracking and Jira integration for ${projectLabel}`}
        actions={
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowPicker(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] border border-[var(--color-border)] rounded-lg transition-colors"
            >
              <LayoutGrid className="h-3.5 w-3.5" />
              Customize
            </button>
            <div className="flex items-center gap-1 bg-[var(--color-bg-secondary)] rounded-lg p-1">
              {RESOLUTION_FILTERS.map(({ label, value }) => (
                <button
                  key={label}
                  onClick={() => handleFilterChange(value)}
                  className={clsx(
                    'px-3 py-1 rounded-md text-sm font-medium transition-colors',
                    resolutionFilter === value ? 'bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-btn-primary-text)]',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        }
      />

      <WorkflowTimeline
        title="Defect Workflow"
        subtitle="A guided view of intake, Jira linkage, and triage priority for the current defect queue."
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
      />

      {isLoading ? (
        <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Gauge className="h-8 w-8" />}
          title="No defects found"
          description={resolutionFilter ? `No ${resolutionFilter.toLowerCase()} defects for this project` : 'Defects created via AI analysis and Jira integration will appear here'}
        />
      ) : (
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">
              {total} defect{total !== 1 ? 's' : ''} found
            </h3>
            {pages > 1 && (
              <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
                <button
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page === 1}
                  className="p-1 rounded hover:bg-[var(--color-bg-hover)] disabled:opacity-30"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                <span>Page {page} of {pages}</span>
                <button
                  onClick={() => setPage(p => Math.min(pages, p + 1))}
                  disabled={page === pages}
                  className="p-1 rounded hover:bg-[var(--color-bg-hover)] disabled:opacity-30"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <SortableHeader label="Test / Suite" sortKey="test_name" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Category" sortKey="failure_category" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Jira Ticket" sortKey="jira_ticket_id" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Status" sortKey="resolution_status" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Release" sortKey="release_name" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="AI Confidence" sortKey="ai_confidence_score" currentKey={sortKey} dir={sortDir} onSort={toggleSort} align="right" />
                  <SortableHeader label="Created" sortKey="created_at" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Resolved" sortKey="resolved_at" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                </tr>
              </thead>
              <tbody>
                {sortedItems.map((d) => (
                  <tr key={d.id} className="table-row">
                    <td className="td">
                      <div className="font-medium text-[var(--color-text)] max-w-[220px] truncate">{d.test_name}</div>
                      {d.suite_name && <div className="text-xs text-[var(--color-text-muted)] truncate max-w-[220px]">{d.suite_name}</div>}
                    </td>
                    <td className="td">
                      {d.failure_category ? (
                        <span className="text-xs px-2 py-0.5 rounded bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] font-mono">
                          {d.failure_category.replace(/_/g, ' ')}
                        </span>
                      ) : (
                        <span className="text-[var(--color-text-muted)]">—</span>
                      )}
                    </td>
                    <td className="td">
                      {d.jira_ticket_id ? (
                        <a
                          href={d.jira_ticket_url ?? '#'}
                          target="_blank"
                          rel="noreferrer"
                          className="flex items-center gap-1 text-[var(--color-text)] hover:text-[var(--color-text-secondary)] font-mono text-xs"
                        >
                          {d.jira_ticket_id}
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      ) : (
                        <span className="text-[var(--color-text-muted)] text-xs">Not linked</span>
                      )}
                    </td>
                    <td className="td">
                      <span className={clsx(
                        'text-xs px-2 py-0.5 rounded border font-medium',
                        RESOLUTION_COLORS[d.resolution_status] ?? 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]',
                      )}>
                        {d.resolution_status.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td className="td">
                      {d.release_name ? (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-violet-900/40 text-violet-300 font-medium">
                          {d.release_name}
                        </span>
                      ) : (
                        <span className="text-[var(--color-text-muted)]">—</span>
                      )}
                    </td>
                    <td className="td text-right tabular-nums">
                      {d.ai_confidence_score != null ? (
                        <span className={clsx('font-semibold', CONFIDENCE_COLOR(d.ai_confidence_score))}>
                          {d.ai_confidence_score}%
                        </span>
                      ) : (
                        <span className="text-[var(--color-text-muted)]">—</span>
                      )}
                    </td>
                    <td className="td text-[var(--color-text-muted)] text-xs">{formatDate(d.created_at)}</td>
                    <td className="td text-[var(--color-text-muted)] text-xs">{d.resolved_at ? formatDate(d.resolved_at) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {pages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-4 text-sm text-[var(--color-text-muted)]">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page === 1}
                className="flex items-center gap-1 px-3 py-1.5 rounded bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-30"
              >
                <ChevronLeft className="h-4 w-4" /> Prev
              </button>
              <span>Page {page} of {pages}</span>
              <button
                onClick={() => setPage(p => Math.min(pages, p + 1))}
                disabled={page === pages}
                className="flex items-center gap-1 px-3 py-1.5 rounded bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-30"
              >
                Next <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          )}
        </div>
      )}

      {showPicker && (
        <WidgetPicker
          page="defects"
          enabledIds={analyticsView.widgetIds}
          onSave={(ids) => { analyticsView.setWidgets(ids); void analyticsView.save() }}
          onClose={() => setShowPicker(false)}
        />
      )}
    </div>
  )
}
