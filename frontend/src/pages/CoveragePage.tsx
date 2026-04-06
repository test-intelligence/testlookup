import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { LayoutGrid, ShieldCheck, Layers, TestTube } from 'lucide-react'
import WidgetPicker from '@/components/analytics/WidgetPicker'
import { useAnalyticsView } from '@/hooks/useAnalyticsView'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { clsx } from 'clsx'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { buildCoverageWorkflow } from '@/components/workflow/workflowPresets'
import { useCoverage } from '@/hooks/useMetrics'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type { CoverageSummary } from '@/types/analytics'

const PERIODS = [
  { label: '7d',  days: 7 },
  { label: '14d', days: 14 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
]

const AXIS_TICK = { fill: '#64748b', fontSize: 11 }

const TOOLTIP_STYLE = {
  backgroundColor: '#1e293b', border: '1px solid #334155',
  borderRadius: '8px', color: '#e2e8f0', fontSize: '12px',
}

function PassRateBar({ rate }: { rate: number }) {
  const color = rate >= 90 ? '#10b981' : rate >= 70 ? '#f59e0b' : '#ef4444'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-[var(--color-bg-hover)] rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${Math.min(rate, 100)}%`, backgroundColor: color }} />
      </div>
      <span className="text-xs font-mono font-semibold w-12 text-right" style={{ color }}>{rate}%</span>
    </div>
  )
}

interface SuiteRow {
  suite_name: string
  project_name?: string
  unique_tests: number
  passed: number
  failed: number
  skipped: number
  pass_rate: number
}

export default function CoveragePage() {
  const [days, setDays] = useState(30)
  const [showPicker, setShowPicker] = useState(false)
  const navigate = useNavigate()
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const { data: coverageData, isLoading } = useCoverage(days)
  const analyticsView = useAnalyticsView('coverage')

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<ShieldCheck className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to view coverage data"
      />
    )
  }

  const projectLabel = project?.name ?? 'All Projects'

  const summary: Partial<CoverageSummary> = coverageData?.summary ?? {}
  const suites: SuiteRow[] = coverageData?.suites ?? []
  const workflow = buildCoverageWorkflow(summary, suites, days, projectLabel)

  const topSuites = suites.slice(0, 15).map((s) => ({
    name: s.suite_name.length > 24 ? s.suite_name.slice(0, 24) + '…' : s.suite_name,
    passed: s.passed,
    failed: s.failed,
    skipped: s.skipped,
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
        title="Test Coverage"
        subtitle={`Suite breakdown and execution coverage for ${projectLabel}`}
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
        title="Coverage Workflow"
        subtitle="A guided view of how coverage data is captured, mapped, and turned into follow-up actions."
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
      />

      {isLoading ? (
        <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
      ) : suites.length === 0 ? (
        <EmptyState
          icon={<ShieldCheck className="h-8 w-8" />}
          title="No coverage data yet"
          description="Upload test results to see coverage breakdowns"
        />
      ) : (
        <>
          {/* Summary KPIs — controlled by coverage_kpis widget */}
          {analyticsView.widgetIds.includes('coverage_kpis') && <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {[
              { label: 'Unique Tests',      value: summary.unique_tests      ?? 0, color: 'text-[var(--color-text)]',    icon: <TestTube className="h-4 w-4" /> },
              { label: 'Test Suites',        value: summary.suite_count       ?? 0, color: 'text-violet-400',  icon: <Layers className="h-4 w-4" /> },
              { label: 'Total Executions',   value: summary.total_executions  ?? 0, color: 'text-[var(--color-text-secondary)]',   icon: null },
              { label: 'Avg Pass Rate',      value: `${summary.avg_pass_rate ?? 0}%`, color: 'text-emerald-400', icon: null },
              { label: 'Days with Runs',     value: summary.days_with_runs   ?? 0, color: 'text-cyan-400',    icon: null },
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

          {/* Suite Stacked Bar Chart — controlled by top_suites_bar widget */}
          {analyticsView.widgetIds.includes('top_suites_bar') && <div className="card">
            <h3 className="text-sm font-semibold text-[var(--color-text)] mb-4">Top Suites — Test Execution Breakdown</h3>
            <ResponsiveContainer width="100%" height={320}>
              <BarChart data={topSuites} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 4 }} barSize={14}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" horizontal={false} />
                <XAxis type="number" axisLine={false} tickLine={false} tick={AXIS_TICK} />
                <YAxis type="category" dataKey="name" width={140} axisLine={false} tickLine={false} tick={{ fill: '#94a3b8', fontSize: 10 }} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Bar dataKey="passed"  stackId="a" fill="#10b981" name="Passed"  />
                <Bar dataKey="failed"  stackId="a" fill="#ef4444" name="Failed"  />
                <Bar dataKey="skipped" stackId="a" fill="#f59e0b" name="Skipped" radius={[0, 3, 3, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>}

          {/* Suite Table — always visible */}
          <div className="card">
            <h3 className="text-sm font-semibold text-[var(--color-text)] mb-4">Suite Coverage Details</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    {isAllProjects && <th className="th text-left">Project</th>}
                    <th className="th text-left">Suite</th>
                    <th className="th text-right">Unique Tests</th>
                    <th className="th text-right text-emerald-400">Passed</th>
                    <th className="th text-right text-red-400">Failed</th>
                    <th className="th text-right text-amber-400">Skipped</th>
                    <th className="th min-w-[180px]">Pass Rate</th>
                  </tr>
                </thead>
                <tbody>
                  {suites.map((s) => (
                    <tr
                      key={s.suite_name}
                      className="table-row cursor-pointer hover:bg-[var(--color-bg-hover)]/50"
                      onClick={() => navigate(`/coverage/suite?name=${encodeURIComponent(s.suite_name)}&days=${days}`)}
                    >
                      {isAllProjects && (
                        <td className="td text-[var(--color-text-muted)] text-xs max-w-[120px] truncate">{s.project_name ?? '—'}</td>
                      )}
                      <td className="td font-medium text-[var(--color-text)] hover:text-[var(--color-text-secondary)] max-w-[240px] truncate">{s.suite_name}</td>
                      <td className="td text-right tabular-nums text-[var(--color-text-secondary)]">{s.unique_tests}</td>
                      <td className="td text-right tabular-nums text-emerald-400">{s.passed}</td>
                      <td className="td text-right tabular-nums text-red-400">{s.failed}</td>
                      <td className="td text-right tabular-nums text-amber-400">{s.skipped}</td>
                      <td className="td w-52">
                        <PassRateBar rate={Number(s.pass_rate ?? 0)} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {showPicker && (
        <WidgetPicker
          page="coverage"
          enabledIds={analyticsView.widgetIds}
          onSave={(ids) => { analyticsView.setWidgets(ids); void analyticsView.save() }}
          onClose={() => setShowPicker(false)}
        />
      )}
    </div>
  )
}
