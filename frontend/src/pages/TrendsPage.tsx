import { useEffect, useRef, useState } from 'react'
import {
  Download, Mail, Plus, Settings2, TrendingUp, X,
} from 'lucide-react'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  PieChart, Pie, Cell,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { buildTrendsWorkflow } from '@/components/workflow/workflowPresets'
import { useTrendData } from '@/hooks/useMetrics'
import { useAnalyticsView } from '@/hooks/useAnalyticsView'
import WidgetPicker from '@/components/analytics/WidgetPicker'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type { TrendPoint } from '@/types/metrics'
import { postData } from '@/services/http'

// ── Constants ──────────────────────────────────────────────────────────────

const PERIODS = [
  { label: '7d',  days: 7 },
  { label: '14d', days: 14 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
]

const TOOLTIP_STYLE = {
  backgroundColor: '#1e293b', border: '1px solid #334155',
  borderRadius: '8px', color: '#e2e8f0', fontSize: '12px',
}
const AXIS_TICK = { fill: '#64748b', fontSize: 11 }

// ── Chart catalog ──────────────────────────────────────────────────────────

interface ChartDef {
  id: string
  label: string
  description: string
  defaultEnabled: boolean
}

const CHART_CATALOG: ChartDef[] = [
  { id: 'daily_breakdown',   label: 'Daily Breakdown',    description: 'Stacked bar of passed/failed/skipped per day',  defaultEnabled: true  },
  { id: 'pass_rate_trend',   label: 'Pass Rate Trend',    description: 'Daily pass rate % line chart',                  defaultEnabled: true  },
  { id: 'cumulative_volume', label: 'Cumulative Volume',  description: 'Total test volume growth area chart',           defaultEnabled: true  },
  { id: 'failure_rate',      label: 'Failure Rate',       description: 'Daily failure rate % over time',               defaultEnabled: false },
  { id: 'broken_trend',      label: 'Broken Tests',       description: 'Broken test count trend bar chart',            defaultEnabled: false },
  { id: 'skipped_trend',     label: 'Skipped Trend',      description: 'Skipped test count trend over time',           defaultEnabled: false },
  { id: 'status_pie',        label: 'Status Distribution',description: 'Pie chart of overall status distribution',     defaultEnabled: false },
]

const STORAGE_KEY = 'testlookup_trend_charts'

function loadEnabledCharts(): string[] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) return JSON.parse(stored)
  } catch {
    // ignore parse errors — fall through to defaults
  }
  return CHART_CATALOG.filter(c => c.defaultEnabled).map(c => c.id)
}

function saveEnabledCharts(ids: string[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ids))
  } catch {
    // ignore storage errors (e.g. private browsing quota)
  }
}

// ── Individual chart components ────────────────────────────────────────────

interface ChartProps { data: TrendPoint[]; height?: number }

function DailyBreakdownChart({ data, height = 300 }: ChartProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} barSize={18} margin={{ top: 4, right: 4, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
        <XAxis dataKey="date" axisLine={false} tickLine={false} tick={AXIS_TICK} dy={8} />
        <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Legend iconType="circle" wrapperStyle={{ paddingTop: 12, fontSize: 12 }} />
        <Bar dataKey="passed"  stackId="a" fill="#10b981" name="Passed"  />
        <Bar dataKey="failed"  stackId="a" fill="#ef4444" name="Failed"  />
        <Bar dataKey="skipped" stackId="a" fill="#f59e0b" name="Skipped" />
        <Bar dataKey="broken"  stackId="a" fill="#f97316" name="Broken"  radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function PassRateTrendChart({ data, height = 240 }: ChartProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 4, right: 4, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
        <XAxis dataKey="date" axisLine={false} tickLine={false} tick={AXIS_TICK} dy={8} />
        <YAxis domain={[0, 100]} axisLine={false} tickLine={false} tick={AXIS_TICK} unit="%" />
        <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => [`${v}%`, 'Pass Rate']} />
        <Line type="monotone" dataKey="pass_rate" stroke="#10b981" strokeWidth={2} dot={false} activeDot={{ r: 4 }} name="Pass Rate %" />
      </LineChart>
    </ResponsiveContainer>
  )
}

function CumulativeVolumeChart({ data, height = 240 }: ChartProps) {
  const derived = data.map(d => ({
    ...d,
    total: d.passed + d.failed + d.skipped + d.broken,
  }))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={derived} margin={{ top: 4, right: 4, left: -16, bottom: 0 }}>
        <defs>
          <linearGradient id="totalGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}   />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
        <XAxis dataKey="date" axisLine={false} tickLine={false} tick={AXIS_TICK} dy={8} />
        <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Area type="monotone" dataKey="total"  stroke="#3b82f6" fill="url(#totalGrad)" strokeWidth={2} name="Total Tests" />
        <Area type="monotone" dataKey="passed" stroke="#10b981" fill="transparent"    strokeWidth={1.5} name="Passed" />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function FailureRateChart({ data, height = 240 }: ChartProps) {
  const derived = data.map(d => {
    const total = d.passed + d.failed + d.skipped + d.broken
    return { ...d, failure_rate: total > 0 ? Number(((d.failed + d.broken) / total * 100).toFixed(1)) : 0 }
  })
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={derived} margin={{ top: 4, right: 4, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
        <XAxis dataKey="date" axisLine={false} tickLine={false} tick={AXIS_TICK} dy={8} />
        <YAxis domain={[0, 100]} axisLine={false} tickLine={false} tick={AXIS_TICK} unit="%" />
        <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => [`${v}%`, 'Failure Rate']} />
        <Line type="monotone" dataKey="failure_rate" stroke="#ef4444" strokeWidth={2} dot={false} activeDot={{ r: 4 }} name="Failure Rate %" />
      </LineChart>
    </ResponsiveContainer>
  )
}

function BrokenTrendChart({ data, height = 240 }: ChartProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} barSize={18} margin={{ top: 4, right: 4, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
        <XAxis dataKey="date" axisLine={false} tickLine={false} tick={AXIS_TICK} dy={8} />
        <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Bar dataKey="broken" fill="#f97316" name="Broken Tests" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function SkippedTrendChart({ data, height = 240 }: ChartProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 4, right: 4, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
        <XAxis dataKey="date" axisLine={false} tickLine={false} tick={AXIS_TICK} dy={8} />
        <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Line type="monotone" dataKey="skipped" stroke="#f59e0b" strokeWidth={2} dot={false} activeDot={{ r: 4 }} name="Skipped" strokeDasharray="4 2" />
      </LineChart>
    </ResponsiveContainer>
  )
}

function StatusPieChart({ data, height = 240 }: ChartProps) {
  const totals = data.reduce(
    (acc, d) => ({
      passed:  acc.passed  + d.passed,
      failed:  acc.failed  + d.failed,
      skipped: acc.skipped + d.skipped,
      broken:  acc.broken  + (d.broken ?? 0),
    }),
    { passed: 0, failed: 0, skipped: 0, broken: 0 },
  )
  const pieData = [
    { name: 'Passed',  value: totals.passed,  fill: '#10b981' },
    { name: 'Failed',  value: totals.failed,  fill: '#ef4444' },
    { name: 'Skipped', value: totals.skipped, fill: '#f59e0b' },
    { name: 'Broken',  value: totals.broken,  fill: '#f97316' },
  ].filter(d => d.value > 0)

  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie data={pieData} dataKey="value" cx="50%" cy="50%" outerRadius={90} label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`} labelLine={false}>
          {pieData.map((entry) => <Cell key={entry.name} fill={entry.fill} />)}
        </Pie>
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
      </PieChart>
    </ResponsiveContainer>
  )
}

function renderChart(id: string, data: TrendPoint[]) {
  switch (id) {
    case 'daily_breakdown':   return <DailyBreakdownChart data={data} />
    case 'pass_rate_trend':   return <PassRateTrendChart data={data} />
    case 'cumulative_volume': return <CumulativeVolumeChart data={data} />
    case 'failure_rate':      return <FailureRateChart data={data} />
    case 'broken_trend':      return <BrokenTrendChart data={data} />
    case 'skipped_trend':     return <SkippedTrendChart data={data} />
    case 'status_pie':        return <StatusPieChart data={data} />
    default: return null
  }
}

// ── Email modal ────────────────────────────────────────────────────────────

interface EmailModalProps {
  onClose: () => void
  projectId: string
  days: number
  enabledCharts: string[]
}

function EmailModal({ onClose, projectId, days, enabledCharts }: EmailModalProps) {
  const [email, setEmail] = useState('')
  const [sending, setSending] = useState(false)

  async function handleSend() {
    if (!email.trim()) { toast.error('Enter a recipient email'); return }
    setSending(true)
    try {
      await postData('/api/v1/reports/email-trends', {
        project_id: projectId,
        days,
        recipient_email: email.trim(),
        chart_ids: enabledCharts,
      })
      toast.success('Report sent successfully')
      onClose()
    } catch {
      toast.error('Failed to send report — check SMTP settings')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]/60" onClick={onClose}>
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-6 w-full max-w-md shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-[var(--color-text)]">Email Trends Report</h2>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"><X className="h-4 w-4" /></button>
        </div>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">
          Send a snapshot of the current trend data ({days}-day period, {enabledCharts.length} chart{enabledCharts.length !== 1 ? 's' : ''}) to an email address.
        </p>
        <label className="block text-xs text-[var(--color-text-muted)] mb-1">Recipient Email</label>
        <input
          type="email"
          value={email}
          onChange={e => setEmail(e.target.value)}
          placeholder="you@example.com"
          className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border-light)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-faint)] focus:outline-none focus:border-neutral-500 mb-4"
          onKeyDown={e => e.key === 'Enter' && handleSend()}
        />
        <div className="flex gap-3 justify-end">
          <button onClick={onClose} className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
          <button
            onClick={handleSend}
            disabled={sending}
            className="px-4 py-2 text-sm bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] rounded-lg font-medium flex items-center gap-2"
          >
            {sending ? <LoadingSpinner size="sm" /> : <Mail className="h-4 w-4" />}
            {sending ? 'Sending…' : 'Send Report'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Chart picker modal ─────────────────────────────────────────────────────

interface ChartPickerProps {
  enabled: string[]
  onToggle: (id: string) => void
  onClose: () => void
}

function ChartPickerModal({ enabled, onToggle, onClose }: ChartPickerProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]/60" onClick={onClose}>
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-6 w-full max-w-lg shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-[var(--color-text)]">Customize Charts</h2>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"><X className="h-4 w-4" /></button>
        </div>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">Select which charts to display on the Trends page.</p>
        <div className="space-y-2">
          {CHART_CATALOG.map(chart => (
            <div
              key={chart.id}
              className={clsx(
                'flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors',
                enabled.includes(chart.id)
                  ? 'border-[var(--color-border-light)]/50 bg-neutral-300/10'
                  : 'border-[var(--color-border)] bg-[var(--color-bg-hover)]/50 hover:border-[var(--color-border-light)]',
              )}
              onClick={() => onToggle(chart.id)}
            >
              <div className={clsx(
                'h-4 w-4 rounded border-2 flex items-center justify-center flex-shrink-0',
                enabled.includes(chart.id) ? 'border-[var(--color-border-light)] bg-neutral-300' : 'border-[var(--color-border-light)]',
              )}>
                {enabled.includes(chart.id) && (
                  <svg className="h-2.5 w-2.5 text-[var(--color-text)]" fill="none" viewBox="0 0 12 12">
                    <path d="M2 6l3 3 5-5" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </div>
              <div>
                <p className="text-sm font-medium text-[var(--color-text)]">{chart.label}</p>
                <p className="text-xs text-[var(--color-text-muted)]">{chart.description}</p>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-4 flex justify-end">
          <button onClick={onClose} className="px-4 py-2 text-sm bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] rounded-lg font-medium">Done</button>
        </div>
      </div>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────

const PRINT_CHART_WIDTH = 680

export default function TrendsPage() {
  const [days, setDays]           = useState(30)
  const analyticsView = useAnalyticsView('trends')
  const [enabledCharts, setEnabled] = useState<string[]>(loadEnabledCharts)
  const [showPicker, setShowPicker] = useState(false)
  const [showWidgetPicker, setShowWidgetPicker] = useState(false)
  const [showEmail, setShowEmail]   = useState(false)
  const [exportingPdf, setExportingPdf] = useState(false)
  const project   = useProjectStore(s => s.activeProject)
  const projectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = projectId === ALL_PROJECTS_ID
  const { data: trends, isLoading } = useTrendData(days)
  const contentRef = useRef<HTMLDivElement>(null)

  // Sync from analytics view when loaded from server
  useEffect(() => {
    if (!analyticsView.loading && analyticsView.widgetIds.length > 0) {
      setEnabled(analyticsView.widgetIds)
    }
  }, [analyticsView.loading, analyticsView.widgetIds])

  useEffect(() => { saveEnabledCharts(enabledCharts) }, [enabledCharts])

  function toggleChart(id: string) {
    setEnabled(prev =>
      prev.includes(id) ? prev.filter(c => c !== id) : [...prev, id],
    )
  }

  function removeChart(id: string) {
    setEnabled(prev => prev.filter(c => c !== id))
  }

  async function handleExportPdf() {
    if (!contentRef.current) return
    setExportingPdf(true)
    try {
      const [{ default: jsPDF }, { default: html2canvas }] = await Promise.all([
        import('jspdf'),
        import('html2canvas'),
      ])
      const canvas = await html2canvas(contentRef.current, {
        scale: 2,
        useCORS: true,
        backgroundColor: '#0f172a',
        logging: false,
        windowWidth: contentRef.current.scrollWidth,
        windowHeight: contentRef.current.scrollHeight,
      })
      const imgData = canvas.toDataURL('image/png')
      const pdf = new jsPDF({ orientation: 'portrait', unit: 'px', format: 'a4' })
      const pdfWidth = pdf.internal.pageSize.getWidth()
      const pdfHeight = pdf.internal.pageSize.getHeight()
      const imgWidth = canvas.width
      const imgHeight = canvas.height
      const ratio = pdfWidth / imgWidth
      const scaledHeight = imgHeight * ratio
      let yOffset = 0
      while (yOffset < scaledHeight) {
        if (yOffset > 0) pdf.addPage()
        pdf.addImage(imgData, 'PNG', 0, -yOffset, pdfWidth, scaledHeight)
        yOffset += pdfHeight
      }
      const projectLabel = isAllProjects ? 'All-Projects' : (project?.name ?? 'QA')
      pdf.save(`QA-Insight-Trends-${projectLabel}-${days}d.pdf`)
      toast.success('PDF exported successfully')
    } catch (err) {
      console.error('PDF export failed', err)
      toast.error('PDF export failed — please try again')
    } finally {
      setExportingPdf(false)
    }
  }

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<TrendingUp className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to view trend data"
      />
    )
  }

  const projectLabel = project?.name ?? 'All Projects'

  const trendData = trends?.data ?? []
  const workflow = buildTrendsWorkflow(days, enabledCharts, trendData, projectLabel)

  const totalPassed  = trendData.reduce((s: number, d: TrendPoint) => s + d.passed,  0)
  const totalFailed  = trendData.reduce((s: number, d: TrendPoint) => s + d.failed,  0)
  const totalSkipped = trendData.reduce((s: number, d: TrendPoint) => s + d.skipped, 0)
  const totalBroken  = trendData.reduce((s: number, d: TrendPoint) => s + d.broken,  0)
  const avgPassRate  = trendData.length > 0
    ? (trendData.reduce((s: number, d: TrendPoint) => s + d.pass_rate, 0) / trendData.length).toFixed(1)
    : '—'

  const availableToAdd = CHART_CATALOG.filter(c => !enabledCharts.includes(c.id))

  return (
    <>
      {/* Modals */}
      {showPicker && (
        <ChartPickerModal enabled={enabledCharts} onToggle={toggleChart} onClose={() => setShowPicker(false)} />
      )}
      {showWidgetPicker && (
        <WidgetPicker
          page="trends"
          enabledIds={enabledCharts}
          onSave={(ids) => { setEnabled(ids); analyticsView.setWidgets(ids); void analyticsView.save() }}
          onClose={() => setShowWidgetPicker(false)}
        />
      )}
      {showEmail && projectId && (
        <EmailModal onClose={() => setShowEmail(false)} projectId={projectId} days={days} enabledCharts={enabledCharts} />
      )}

      <div ref={contentRef} className="space-y-6 print:space-y-4">
        <PageHeader
          title="Trends"
          subtitle={`Historical trends for ${projectLabel}`}
          actions={
            <div className="flex items-center gap-2 print:hidden">
              {/* Period selector */}
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
              {/* Customize */}
              <button
                onClick={() => setShowWidgetPicker(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-[var(--color-text-secondary)] bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] border border-[var(--color-border)] rounded-lg transition-colors"
              >
                <Settings2 className="h-3.5 w-3.5" />
                Customize
              </button>
              {/* Export PDF */}
              <button
                onClick={handleExportPdf}
                disabled={exportingPdf}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-[var(--color-text-secondary)] bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] border border-[var(--color-border)] rounded-lg transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {exportingPdf ? <LoadingSpinner size="sm" /> : <Download className="h-3.5 w-3.5" />}
                {exportingPdf ? 'Exporting…' : 'Export PDF'}
              </button>
              {/* Email */}
              <button
                onClick={() => setShowEmail(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-[var(--color-btn-primary-text)] bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] rounded-lg transition-colors"
              >
                <Mail className="h-3.5 w-3.5" />
                Email Report
              </button>
            </div>
          }
        />

        <WorkflowTimeline
          title="Trend Workflow"
          subtitle="Capture signals, compare quality trends, and package the view for export or email."
          stages={workflow.stages}
          events={workflow.events}
          stageOrder={workflow.stageOrder}
          compact
        />

        {/* Summary strip */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          {[
            { label: 'Avg Pass Rate', value: `${avgPassRate}%`, color: 'text-emerald-400' },
            { label: 'Total Passed',  value: totalPassed,       color: 'text-emerald-400' },
            { label: 'Total Failed',  value: totalFailed,       color: 'text-red-400'     },
            { label: 'Total Skipped', value: totalSkipped,      color: 'text-amber-400'   },
            { label: 'Total Broken',  value: totalBroken,       color: 'text-orange-400'  },
          ].map(({ label, value, color }) => (
            <div key={label} className="card py-3">
              <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">{label}</p>
              <p className={clsx('text-2xl font-bold tabular-nums mt-1', color)}>{value}</p>
            </div>
          ))}
        </div>

        {/* Charts */}
        {isLoading ? (
          <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
        ) : trendData.length === 0 ? (
          <EmptyState
            icon={<TrendingUp className="h-8 w-8" />}
            title="No trend data yet"
            description="Run some tests to see trends over time"
          />
        ) : (
          <>
            <div className="space-y-4">
              {enabledCharts.map(chartId => {
                const def = CHART_CATALOG.find(c => c.id === chartId)
                if (!def) return null
                return (
                  <div key={chartId} className="card relative group">
                    <div className="flex items-center justify-between mb-4">
                      <h3 className="text-sm font-semibold text-[var(--color-text)]">{def.label}</h3>
                      <button
                        onClick={() => removeChart(chartId)}
                        className="opacity-0 group-hover:opacity-100 text-[var(--color-text-muted)] hover:text-red-400 transition-all print:hidden"
                        title="Remove chart"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                    {renderChart(chartId, trendData)}
                  </div>
                )
              })}
            </div>

            {/* Add chart button */}
            {availableToAdd.length > 0 && (
              <div className="print:hidden">
                <button
                  onClick={() => setShowWidgetPicker(true)}
                  className="w-full flex items-center justify-center gap-2 py-4 border-2 border-dashed border-[var(--color-border)] hover:border-[var(--color-border-light)] rounded-xl text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors text-sm font-medium"
                >
                  <Plus className="h-4 w-4" />
                  Add Chart ({availableToAdd.length} available)
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {/* Print styles
          Key design: apply print-color-adjust:exact ONLY to SVG elements so that
          Recharts chart colours (bar fills, line strokes) are preserved.
          ALL HTML element backgrounds are forced to white so dark Tailwind
          utility classes (bg-[var(--color-bg-secondary)]/900) do NOT produce a black page.
      */}
      <style>{`
        @media print {
          /* ── Page defaults ── */
          @page { margin: 15mm; }
          html, body {
            background: white !important;
            color: #1e293b !important;
            margin: 0;
          }

          /* ── Force white background on every HTML element ── */
          div, section, article, main, header, aside,
          span, p, h1, h2, h3, h4, h5, h6,
          table, thead, tbody, tr, td, th, ul, li, button, select, input {
            background: white !important;
            background-color: white !important;
            color: #1e293b !important;
            border-color: #e2e8f0 !important;
            box-shadow: none !important;
          }

          /* ── Hide chrome / controls ── */
          .print\\:hidden { display: none !important; }
          nav, aside, [data-sidebar], [data-topbar] { display: none !important; }
          .recharts-tooltip-wrapper { display: none !important; }

          /* ── Layout ── */
          main { margin: 0 !important; padding: 0 !important; width: 100% !important; max-width: none !important; }
          .grid { display: grid !important; }

          /* ── Cards ── */
          .card {
            border: 1px solid #cbd5e1 !important;
            margin-bottom: 16px !important;
            break-inside: avoid !important;
            page-break-inside: avoid !important;
            padding: 12px !important;
          }

          /* ── Headings ── */
          h1, h2, h3 { color: #0f172a !important; font-weight: 700; }

          /* ── SVGs: preserve chart data colours ── */
          svg {
            -webkit-print-color-adjust: exact !important;
            print-color-adjust: exact !important;
            overflow: visible !important;
          }

          /* ── Recharts axis text → dark so it's readable on white ── */
          .recharts-text tspan,
          .recharts-cartesian-axis-tick-value tspan {
            fill: #475569 !important;
          }

          /* ── Grid lines → light grey on white background ── */
          .recharts-cartesian-grid-horizontal line,
          .recharts-cartesian-grid-vertical line {
            stroke: #e2e8f0 !important;
          }

          /* ── Axis lines ── */
          .recharts-cartesian-axis-line { stroke: #94a3b8 !important; }

          /* ── Legend text ── */
          .recharts-legend-item-text { color: #475569 !important; fill: #475569 !important; }

          /* ── Recharts sizing: lock to explicit px so ResizeObserver cannot
             collapse the SVG to 0 when @media print reflows the page ── */
          .recharts-responsive-container {
            width: ${PRINT_CHART_WIDTH}px !important;
            min-width: ${PRINT_CHART_WIDTH}px !important;
            overflow: visible !important;
          }
          .recharts-wrapper {
            width: ${PRINT_CHART_WIDTH}px !important;
            overflow: visible !important;
          }
          .recharts-surface {
            width: ${PRINT_CHART_WIDTH}px !important;
            overflow: visible !important;
          }

          /* ── Print title at top of first page ── */
          body::before {
            content: "TestLookup — Trends Report";
            display: block;
            font-size: 20px;
            font-weight: 700;
            color: #0f172a;
            margin-bottom: 12px;
            border-bottom: 2px solid #e2e8f0;
            padding-bottom: 8px;
          }
        }
      `}</style>
    </>
  )
}
