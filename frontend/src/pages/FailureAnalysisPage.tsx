import { useState } from 'react'
import { Bug, TrendingDown, AlertTriangle, Flame, LayoutGrid } from 'lucide-react'
import WidgetPicker from '@/components/analytics/WidgetPicker'
import { useAnalyticsView } from '@/hooks/useAnalyticsView'
import {
  Cell, Pie, PieChart, ResponsiveContainer, Tooltip,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
} from 'recharts'
import { clsx } from 'clsx'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useFlakyTests, useFailureCategories, useTopFailing } from '@/hooks/useMetrics'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { buildFailureAnalysisWorkflow } from '@/components/workflow/workflowPresets'

const PERIODS = [
  { label: '7d',  days: 7 },
  { label: '14d', days: 14 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
]

const CATEGORY_COLORS: Record<string, string> = {
  ASSERTION_ERROR:    '#ef4444',
  NULL_POINTER:       '#f97316',
  TIMEOUT:            '#f59e0b',
  CONNECTION_ERROR:   '#eab308',
  CONFIGURATION:      '#84cc16',
  DATA_MISMATCH:      '#06b6d4',
  INFRA_FAILURE:      '#8b5cf6',
  FLAKY:              '#ec4899',
  UNKNOWN:            '#64748b',
}

const PIE_TOOLTIP_STYLE = {
  backgroundColor: '#1e293b', border: '1px solid #334155',
  borderRadius: '8px', color: '#e2e8f0', fontSize: '12px',
}

const AXIS_TICK = { fill: '#64748b', fontSize: 11 }

function FlakyScore({ rate }: { rate: number }) {
  const color = rate >= 50 ? 'text-red-400' : rate >= 25 ? 'text-orange-400' : 'text-amber-400'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-[var(--color-bg-hover)] rounded-full overflow-hidden">
        <div
          className={clsx('h-full rounded-full', rate >= 50 ? 'bg-red-500' : rate >= 25 ? 'bg-orange-500' : 'bg-amber-500')}
          style={{ width: `${Math.min(rate, 100)}%` }}
        />
      </div>
      <span className={clsx('text-xs font-mono font-semibold w-10 text-right', color)}>{rate}%</span>
    </div>
  )
}

export default function FailureAnalysisPage() {
  const [days, setDays] = useState(30)
  const [showPicker, setShowPicker] = useState(false)
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const analyticsView = useAnalyticsView('failures')

  const { data: flakyData,    isLoading: flakyLoading    } = useFlakyTests(days)
  const { data: categoryData, isLoading: categoryLoading } = useFailureCategories(days)
  const { data: topData,      isLoading: topLoading      } = useTopFailing(days)

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<Bug className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to view failure analysis"
      />
    )
  }

  const projectLabel = project?.name ?? 'All Projects'

  const isLoading = flakyLoading || categoryLoading || topLoading

  const flakyItems    = flakyData?.items    ?? []
  const categoryItems = categoryData?.items ?? []
  const topItems      = topData?.items      ?? []
  const workflow = buildFailureAnalysisWorkflow(flakyItems.length, categoryItems.length, topItems.length, days)

  // Widget visibility — derived from analyticsView
  const activeWidgets = new Set(analyticsView.widgetIds)

  const pieData = categoryItems.map((c: { category: string; count: number }) => ({
    name: c.category.replace(/_/g, ' '),
    value: c.count,
    color: CATEGORY_COLORS[c.category] ?? '#64748b',
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

  return (
    <div className="space-y-6">
      <PageHeader
        title="Failure Analysis"
        subtitle={`Flaky leaderboard, error clustering & repeat-failure detection for ${projectLabel}`}
        actions={
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowPicker(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] border border-[var(--color-border)] rounded-lg transition-colors"
            >
              <LayoutGrid className="h-3.5 w-3.5" />
              Customize
            </button>
            {periodSelector}
          </div>
        }
      />

      <WorkflowTimeline
        title="Failure analysis workflow"
        subtitle="Detect flaky tests, cluster failures, rank hotspots, and focus remediation"
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
        showInspector
      />

      {isLoading ? (
        <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
      ) : (
        <>
          {/* Summary KPIs — controlled by failures_kpis widget */}
          {activeWidgets.has('failures_kpis') && <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: 'Flaky Tests',        value: flakyItems.length,  color: 'text-amber-400',  icon: <AlertTriangle className="h-4 w-4" /> },
              { label: 'Top Failing',         value: topItems.length,    color: 'text-red-400',    icon: <TrendingDown className="h-4 w-4" /> },
              { label: 'Failure Categories',  value: categoryItems.length, color: 'text-orange-400', icon: <Flame className="h-4 w-4" /> },
              { label: 'Worst Flakiness',     value: flakyItems[0] ? `${flakyItems[0].failure_rate_pct}%` : '—', color: 'text-red-400', icon: <Bug className="h-4 w-4" /> },
            ].map(({ label, value, color, icon }) => (
              <div key={label} className="card py-3">
                <div className="flex items-center gap-1.5 text-[var(--color-text-muted)] mb-1">
                  {icon}
                  <p className="text-xs uppercase tracking-wider">{label}</p>
                </div>
                <p className={clsx('text-2xl font-bold tabular-nums', color)}>{value}</p>
              </div>
            ))}
          </div>}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Failure Category Pie — controlled by failure_category_pie widget */}
            {activeWidgets.has('failure_category_pie') && <div className="card">
              <h3 className="text-sm font-semibold text-[var(--color-text)] mb-4">Failure Category Distribution</h3>
              {pieData.length === 0 ? (
                <p className="text-sm text-[var(--color-text-muted)] text-center py-8">No failure data in this period</p>
              ) : (
                <ResponsiveContainer width="100%" height={260}>
                  <PieChart>
                    <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={90} label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`} labelLine={false}>
                      {pieData.map((entry: { name: string; color: string }, i: number) => (
                        <Cell key={i} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={PIE_TOOLTIP_STYLE} />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </div>}

            {/* Top Failing Bar Chart — controlled by top_failing_bar widget */}
            {activeWidgets.has('top_failing_bar') && <div className="card">
              <h3 className="text-sm font-semibold text-[var(--color-text)] mb-4">Top Failing Tests (by count)</h3>
              {topItems.length === 0 ? (
                <p className="text-sm text-[var(--color-text-muted)] text-center py-8">No failures in this period</p>
              ) : (
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart
                    data={topItems.slice(0, 10).map((t: { test_name: string; fail_count: number }) => ({
                      name: t.test_name.length > 22 ? t.test_name.slice(0, 22) + '…' : t.test_name,
                      failures: t.fail_count,
                    }))}
                    layout="vertical"
                    margin={{ top: 4, right: 16, left: 8, bottom: 4 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" horizontal={false} />
                    <XAxis type="number" axisLine={false} tickLine={false} tick={AXIS_TICK} />
                    <YAxis type="category" dataKey="name" width={130} axisLine={false} tickLine={false} tick={{ fill: '#94a3b8', fontSize: 10 }} />
                    <Tooltip contentStyle={PIE_TOOLTIP_STYLE} />
                    <Bar dataKey="failures" fill="#ef4444" radius={[0, 3, 3, 0]} name="Failures" />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>}
          </div>

          {/* Flaky Test Leaderboard — controlled by flaky_leaderboard_table widget */}
          {activeWidgets.has('flaky_leaderboard_table') && <div className="card">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-[var(--color-text)]">Flaky Test Leaderboard</h3>
              <span className="text-xs text-[var(--color-text-muted)]">{flakyItems.length} tests with intermittent failures</span>
            </div>
            {flakyItems.length === 0 ? (
              <EmptyState
                icon={<AlertTriangle className="h-8 w-8" />}
                title="No flaky tests detected"
                description="Tests with inconsistent pass/fail results will appear here"
              />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr>
                      <th className="th text-left">#</th>
                      <th className="th text-left">Test Name</th>
                      <th className="th text-left">Suite</th>
                      {isAllProjects && <th className="th text-left">Project</th>}
                      <th className="th text-right">Runs</th>
                      <th className="th text-right">Fails</th>
                      <th className="th min-w-[160px]">Flakiness Rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {flakyItems.map((t: {
                      test_fingerprint: string
                      test_name: string
                      suite_name?: string
                      project_name?: string
                      total_runs: number
                      fail_count: number
                      failure_rate_pct: number
                    }, i: number) => (
                      <tr key={t.test_fingerprint} className="table-row">
                        <td className="td text-[var(--color-text-muted)]">{i + 1}</td>
                        <td className="td font-medium text-[var(--color-text)] max-w-[280px] truncate">{t.test_name}</td>
                        <td className="td text-[var(--color-text-muted)] max-w-[160px] truncate">{t.suite_name ?? '—'}</td>
                        {isAllProjects && <td className="td text-[var(--color-text-muted)] text-xs max-w-[120px] truncate">{t.project_name ?? '—'}</td>}
                        <td className="td text-right tabular-nums text-[var(--color-text-secondary)]">{t.total_runs}</td>
                        <td className="td text-right tabular-nums text-red-400 font-semibold">{t.fail_count}</td>
                        <td className="td w-48">
                          <FlakyScore rate={Number(t.failure_rate_pct)} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>}
        </>
      )}

      {/* Widget Picker Modal */}
      {showPicker && (
        <WidgetPicker
          page="failures"
          enabledIds={analyticsView.widgetIds}
          onSave={(ids) => { analyticsView.setWidgets(ids); void analyticsView.save() }}
          onClose={() => setShowPicker(false)}
        />
      )}
    </div>
  )
}
