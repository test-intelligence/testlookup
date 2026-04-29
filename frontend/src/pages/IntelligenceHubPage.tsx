import { useNavigate } from 'react-router-dom'
import { Brain, ChevronRight, Sparkles } from 'lucide-react'
import { clsx } from 'clsx'
import { useMemo } from 'react'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import StatusBadge from '@/components/ui/StatusBadge'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { buildIntelligenceHubWorkflow } from '@/components/workflow/workflowPresets'
import { useRuns } from '@/hooks/useRuns'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { fromNow } from '@/utils/formatters'

export default function IntelligenceHubPage() {
  const navigate = useNavigate()
  const projectId = useProjectStore(s => s.activeProjectId)
  const isAll = projectId === ALL_PROJECTS_ID

  // Load recent runs — prioritise failed runs for intelligence
  const { data, isLoading } = useRuns({ page: 1, size: 20 })
  const runs = useMemo(() => data?.items ?? [], [data?.items])

  // Split into failed (intelligence-ready) and passed
  const failedRuns = runs.filter(r => r.status === 'FAILED' || r.status === 'failed')
  const passedRuns = runs.filter(r => r.status !== 'FAILED' && r.status !== 'failed')
  const workflow = useMemo(() => buildIntelligenceHubWorkflow(runs), [runs])

  if (isLoading) {
    return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Run Intelligence"
        subtitle="AI-powered analysis for your test runs — select a run to see the full intelligence report"
      />

      <WorkflowTimeline
        title="Intelligence selection flow"
        subtitle="Select a run, focus failed builds, launch intelligence, and keep passing context nearby"
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
        showInspector
      />

      {runs.length === 0 && (
        <EmptyState
          icon={<Brain className="h-10 w-10" />}
          title="No test runs yet"
          description="Upload test results to generate AI intelligence reports."
        />
      )}

      {/* Failed runs — primary section */}
      {failedRuns.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)] mb-3">
            Failed Runs — AI Analysis Available
          </p>
          <div className="space-y-2">
            {failedRuns.map(run => (
              <button
                key={run.id}
                onClick={() => navigate(`/runs/${run.id}/intelligence`)}
                className="w-full card hover:border-[var(--color-border-light)] transition-colors text-left flex items-center gap-4"
              >
                <div className="p-2 bg-white/10 rounded-lg shrink-0">
                  <Sparkles className="h-4 w-4 text-[var(--color-text)]" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm text-[var(--color-text)] font-medium">#{run.build_number}</span>
                    {run.branch && <span className="text-xs text-[var(--color-text-muted)]">{run.branch}</span>}
                    {isAll && run.project_name && (
                      <span className="text-xs text-[var(--color-text-faint)]">{run.project_name}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 mt-1 text-xs">
                    <span className="text-red-400 font-medium">{run.failed_tests} failures</span>
                    <span className="text-[var(--color-text-muted)]">{run.total_tests} total</span>
                    <span className="text-[var(--color-text-faint)]">{fromNow(run.created_at)}</span>
                  </div>
                </div>
                <StatusBadge status={run.status} />
                <ChevronRight className="h-4 w-4 text-[var(--color-text-faint)] shrink-0" />
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Passed runs — secondary */}
      {passedRuns.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)] mb-3">
            Recent Passing Runs
          </p>
          <div className="space-y-1.5">
            {passedRuns.slice(0, 10).map(run => (
              <button
                key={run.id}
                onClick={() => navigate(`/runs/${run.id}/intelligence`)}
                className="w-full flex items-center gap-3 px-4 py-2.5 rounded-lg hover:bg-[var(--color-bg-hover)]/50 transition-colors text-left"
              >
                <span className="font-mono text-xs text-[var(--color-text-muted)]">#{run.build_number}</span>
                {run.branch && <span className="text-xs text-[var(--color-text-faint)]">{run.branch}</span>}
                <span className={clsx('text-xs font-medium ml-auto', run.pass_rate != null && run.pass_rate >= 90 ? 'text-emerald-400' : 'text-[var(--color-text-muted)]')}>
                  {run.pass_rate != null ? `${run.pass_rate.toFixed(1)}%` : '—'}
                </span>
                <span className="text-xs text-[var(--color-text-faint)]">{fromNow(run.created_at)}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
