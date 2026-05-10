import { useState } from 'react'
import { AlertTriangle, Bug, CheckCircle, Clock, LayoutGrid, TrendingUp, Zap } from 'lucide-react'
import MetricCard from '@/components/ui/MetricCard'
import WidgetPicker from '@/components/analytics/WidgetPicker'
import { useAnalyticsView } from '@/hooks/useAnalyticsView'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import TrendChart from '@/components/charts/TrendChart'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { buildOverviewWorkflow } from '@/components/workflow/workflowPresets'
import { useDashboardSummary, useTrendData } from '@/hooks/useMetrics'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { formatDuration } from '@/utils/formatters'
import { clsx } from 'clsx'

const TIME_OPTIONS = [7, 14, 30, 90]

const READINESS_STYLES = {
  GREEN: 'bg-emerald-900/40 text-emerald-300 border-emerald-700/50',
  AMBER: 'bg-amber-900/40 text-amber-300 border-amber-700/50',
  RED:   'bg-red-900/40 text-red-300 border-red-700/50',
}

export default function OverviewPage() {
  const [days, setDays] = useState(7)
  const [showPicker, setShowPicker] = useState(false)
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const analyticsView = useAnalyticsView('dashboard')

  const { data: summary, isLoading: summaryLoading } = useDashboardSummary(days)
  const { data: trends,  isLoading: trendsLoading  } = useTrendData(days)

  if (!project && !isAllProjects) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-center">
        <TrendingUp className="h-12 w-12 text-[var(--color-text-faint)] mb-3" />
        <p className="text-[var(--color-text-muted)] font-medium">Select a project to view the dashboard</p>
        <p className="text-[var(--color-text-muted)] text-sm mt-1">Use the project selector in the top bar</p>
      </div>
    )
  }

  const projectLabel = project?.name ?? 'All Projects'
  const readiness = summary?.release_readiness
  const trendData = (trends?.data ?? []).map((point) => ({
    ...point,
    total: point.total ?? point.passed + point.failed + point.skipped + (point.broken ?? 0),
  }))
  const workflow = buildOverviewWorkflow(summary, trends, days, projectLabel)

  // Widget visibility — derived from analyticsView
  const activeWidgets = new Set(analyticsView.widgetIds)
  const kpiWidgets = analyticsView.widgetIds.filter(id =>
    ['total_executions_kpi', 'avg_pass_rate_kpi', 'active_defects_kpi', 'flaky_tests_kpi', 'new_failures_kpi', 'avg_duration_kpi'].includes(id),
  )
  const chartWidgets = analyticsView.widgetIds.filter(id =>
    ['pass_fail_trend', 'execution_volume', 'pass_rate_gauge'].includes(id),
  )

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        subtitle={projectLabel}
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
              {TIME_OPTIONS.map(d => (
                <button
                  key={d}
                  className={clsx(
                    'px-3 py-1 rounded-md text-sm font-medium transition-colors',
                    days === d ? 'bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-btn-primary-text)]',
                  )}
                  onClick={() => setDays(d)}
                >
                  {d}d
                </button>
              ))}
            </div>
          </div>
        }
      />

      <WorkflowTimeline
        title="Quality workflow"
        subtitle="Snapshot the current quality state, assess readiness, analyze trends, and focus the next actions"
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
        showInspector
      />

      {/* Release Readiness banner — controlled by readiness_summary widget */}
      {activeWidgets.has('readiness_summary') && readiness && (
        <div className={clsx('flex items-center gap-3 px-5 py-3 rounded-xl border', READINESS_STYLES[readiness as keyof typeof READINESS_STYLES])}>
          {readiness === 'GREEN' && <CheckCircle className="h-5 w-5" />}
          {readiness === 'AMBER' && <AlertTriangle className="h-5 w-5" />}
          {readiness === 'RED'   && <Bug className="h-5 w-5" />}
          <div>
            <span className="font-semibold">Release Readiness: {readiness}</span>
            <span className="text-sm ml-2 opacity-75">
              {readiness === 'GREEN' && '✓ All quality gates passing'}
              {readiness === 'AMBER' && '⚠ Some quality criteria need attention'}
              {readiness === 'RED'   && '✗ Critical issues must be resolved before release'}
            </span>
          </div>
        </div>
      )}

      {/* KPI Cards — each controlled by its widget ID */}
      {kpiWidgets.length > 0 && (
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
          {activeWidgets.has('total_executions_kpi') && <MetricCard
            title="Total Executions"
            metric={summary?.total_executions_7d}
            icon={<TrendingUp className="h-5 w-5" />}
            accentColor="default"
            loading={summaryLoading}
          />}
          {activeWidgets.has('avg_pass_rate_kpi') && <MetricCard
            title="Avg Pass Rate"
            metric={summary ? { ...summary.avg_pass_rate_7d, value: `${summary.avg_pass_rate_7d?.value ?? 0}%` } : undefined}
            icon={<CheckCircle className="h-5 w-5" />}
            accentColor="green"
            loading={summaryLoading}
          />}
          {activeWidgets.has('active_defects_kpi') && <MetricCard
            title="Active Defects"
            metric={summary?.active_defects}
            icon={<Bug className="h-5 w-5" />}
            accentColor="red"
            loading={summaryLoading}
          />}
          {activeWidgets.has('flaky_tests_kpi') && <MetricCard
            title="Flaky Tests"
            metric={summary?.flaky_test_count}
            icon={<AlertTriangle className="h-5 w-5" />}
            accentColor="amber"
            loading={summaryLoading}
          />}
          {activeWidgets.has('new_failures_kpi') && <MetricCard
            title="New Failures (24h)"
            metric={summary?.new_failures_24h}
            icon={<Zap className="h-5 w-5" />}
            accentColor="red"
            loading={summaryLoading}
          />}
          {activeWidgets.has('avg_duration_kpi') && <MetricCard
            title="Avg Run Duration"
            metric={summary ? { ...summary.avg_duration_ms, value: formatDuration(summary.avg_duration_ms?.value as number) } : undefined}
            icon={<Clock className="h-5 w-5" />}
            accentColor="purple"
            loading={summaryLoading}
          />}
        </div>
      )}

      {/* Charts — each controlled by widget ID */}
      {chartWidgets.length > 0 && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {activeWidgets.has('pass_fail_trend') && (
            <SectionErrorBoundary message="Failed to load execution trend chart">
              <div className="card">
                <h3 className="text-sm font-semibold text-[var(--color-text)] mb-4">Execution Trend — Pass / Fail / Skip</h3>
                {trendsLoading
                  ? <div className="flex items-center justify-center h-64"><LoadingSpinner /></div>
                  : <TrendChart data={trendData} type="line" />
                }
              </div>
            </SectionErrorBoundary>
          )}
          {activeWidgets.has('execution_volume') && (
            <SectionErrorBoundary message="Failed to load automation growth chart">
              <div className="card">
                <h3 className="text-sm font-semibold text-[var(--color-text)] mb-4">Total Test Automation Growth</h3>
                {trendsLoading
                  ? <div className="flex items-center justify-center h-64"><LoadingSpinner /></div>
                  : <TrendChart data={trendData} type="area" />
                }
              </div>
            </SectionErrorBoundary>
          )}
        </div>
      )}

      {/* Widget Picker Modal */}
      {showPicker && (
        <WidgetPicker
          page="dashboard"
          enabledIds={analyticsView.widgetIds}
          onSave={(ids) => { analyticsView.setWidgets(ids); void analyticsView.save() }}
          onClose={() => setShowPicker(false)}
        />
      )}
    </div>
  )
}
