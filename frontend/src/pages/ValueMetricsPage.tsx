import { useEffect, useState } from 'react'
import {
  BarChart3, Clock, Download, GitMerge, Shield, ShieldAlert, Sparkles, Bug, AlertTriangle,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { buildValueMetricsWorkflow } from '@/components/workflow/workflowPresets'
import { valueMetricsService, type ValueMetrics } from '@/services/valueMetricsService'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'

function MetricCard({ icon: Icon, label, value, sub, color }: {
  icon: React.ElementType; label: string; value: string | number; sub?: string; color: string
}) {
  // Render "—" instead of a stark "0" so an empty metric reads as
  // "no data yet" rather than "shipped exactly zero of these." Same
  // muted tone as the rest of the card so it doesn't draw the eye.
  const isZero = typeof value === 'number' ? value === 0 : value === '0'
  const display = isZero ? '—' : value
  const valueColor = isZero ? 'text-[var(--color-text-faint)]' : color
  return (
    <div className="card space-y-1">
      <div className="flex items-center gap-2">
        <Icon className={clsx('h-4 w-4', isZero ? 'text-[var(--color-text-faint)]' : color)} />
        <span className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">{label}</span>
      </div>
      <p className={clsx('text-2xl font-bold tabular-nums', valueColor)}>{display}</p>
      {sub && <p className="text-xs text-[var(--color-text-faint)]">{sub}</p>}
    </div>
  )
}

export default function ValueMetricsPage() {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const project = useProjectStore(s => s.activeProject)
  const projectId = activeProjectId === ALL_PROJECTS_ID ? undefined : (activeProjectId ?? undefined)
  const [metrics, setMetrics] = useState<ValueMetrics | null>(null)
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(30)

  useEffect(() => {
    setLoading(true)
    valueMetricsService.get(projectId, days)
      .then(setMetrics)
      .catch(() => toast.error('Failed to load value metrics'))
      .finally(() => setLoading(false))
  }, [projectId, days])

  if (loading) return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
  if (!metrics) return null
  const workflow = buildValueMetricsWorkflow(metrics, days, project?.name ?? 'All Projects')

  // Detect the "barely any signal" state: nothing has driven a measurable
  // outcome other than (maybe) AI intelligence reports. This is the most
  // common confusing state on a fresh project — the hero shows time saved
  // but every card is zero, making it look like a data bug. Banner below
  // explains what each pipeline needs to fire so the user can act.
  const nonIntelSignalsAllZero = (
    metrics.defects_auto_grouped === 0 &&
    metrics.duplicate_tickets_avoided === 0 &&
    metrics.defects_promoted === 0 &&
    metrics.flaky_tests_identified === 0 &&
    metrics.risky_releases_blocked === 0 &&
    metrics.releases_conditional === 0 &&
    metrics.release_overrides === 0
  )
  // If even the hero number is essentially "from N intel reports * 20min",
  // qualify it so users don't think it's an aggregate of everything.
  const heroIsFromIntelOnly = nonIntelSignalsAllZero && metrics.intelligence_reports_generated > 0

  return (
    <div className="space-y-6">
      <PageHeader
        title="Value Metrics"
        subtitle="Operational value delivered by AI-powered test intelligence"
        actions={
          <div className="flex items-center gap-3">
            <select
              value={days}
              onChange={e => setDays(Number(e.target.value))}
              className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-1.5"
            >
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
              <option value={365}>Last year</option>
            </select>
            <a
              href={valueMetricsService.exportUrl(projectId, days)}
              className="btn-secondary text-sm flex items-center gap-2"
              download
            >
              <Download className="h-4 w-4" /> Export
            </a>
          </div>
        }
      />

      <WorkflowTimeline
        title="Value Realization Workflow"
        subtitle="Track how RunScope AI converts triage, clustering, and release protection into measurable value."
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
      />

      {/* Hero: triage time saved */}
      <div className="card border border-[var(--color-border-light)] bg-[var(--color-bg-secondary)]/10 p-6">
        <div className="flex items-center gap-3 mb-2">
          <Clock className="h-6 w-6 text-[var(--color-text)]" />
          <p className="text-sm text-[var(--color-text-muted)] uppercase tracking-wider">Triage Time Saved</p>
        </div>
        <p className="text-4xl font-black text-[var(--color-text-secondary)] tabular-nums">
          {metrics.triage_time_saved_hours}h
        </p>
        <p className="text-sm text-[var(--color-text-muted)] mt-1">
          {metrics.triage_time_saved_minutes} minutes saved over the last {days} days
          {heroIsFromIntelOnly && (
            <>
              {' '}
              <span className="text-[var(--color-text-faint)]">
                — entirely from {metrics.intelligence_reports_generated} intelligence report
                {metrics.intelligence_reports_generated === 1 ? '' : 's'}; no clustering,
                defect, flaky, or release-gate signal yet.
              </span>
            </>
          )}
        </p>
      </div>

      {nonIntelSignalsAllZero && (
        // Empty-state banner: data IS accurate but the project hasn't
        // generated any of the signals these cards track. Surface this
        // explicitly so users don't read the zeros as a data bug.
        <div
          className="rounded-xl border p-4 text-sm"
          style={{
            background: 'rgba(245,158,11,0.06)',
            borderColor: 'rgba(245,158,11,0.30)',
            color: 'var(--color-text-secondary)',
          }}
        >
          <p className="font-semibold text-[var(--color-text)] mb-1">No value-generating activity yet for this window.</p>
          <p className="text-[12.5px] leading-relaxed">
            Each card below tracks a specific pipeline. They'll populate as those pipelines fire on your runs:
          </p>
          <ul className="text-[12.5px] mt-1.5 space-y-0.5 list-disc list-inside text-[var(--color-text-muted)]">
            <li><strong className="text-[var(--color-text-secondary)]">Defects auto-grouped</strong> · run deep investigation on failing builds to cluster failures.</li>
            <li><strong className="text-[var(--color-text-secondary)]">Duplicates avoided</strong> · file a defect from a cluster and the system detects duplicates of prior ones.</li>
            <li><strong className="text-[var(--color-text-secondary)]">Flaky tests found</strong> · the Flaky Coach scans history; needs ≥ 5 runs per fingerprint to flag.</li>
            <li><strong className="text-[var(--color-text-secondary)]">Risky releases blocked</strong> · publish a Release Gate Policy and run release-gate evaluation on a build.</li>
            <li><strong className="text-[var(--color-text-secondary)]">Defects promoted</strong> · promote a cluster to a tracked defect from the Failure Analysis page.</li>
          </ul>
        </div>
      )}

      {/* Metric cards grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
        <MetricCard
          icon={GitMerge}
          label="Defects Auto-Grouped"
          value={metrics.defects_auto_grouped}
          sub={`${metrics.tests_grouped} tests grouped into clusters`}
          color="text-violet-400"
        />
        <MetricCard
          icon={AlertTriangle}
          label="Duplicates Avoided"
          value={metrics.duplicate_tickets_avoided}
          sub="Duplicate tickets prevented"
          color="text-amber-400"
        />
        <MetricCard
          icon={Sparkles}
          label="Defects Promoted"
          value={metrics.defects_promoted}
          sub="Cluster → Jira defect"
          color="text-emerald-400"
        />
        <MetricCard
          icon={Bug}
          label="Flaky Tests Found"
          value={metrics.flaky_tests_identified}
          sub={`${metrics.quarantine_recommended} recommended for quarantine`}
          color="text-pink-400"
        />
        <MetricCard
          icon={ShieldAlert}
          label="Risky Releases Blocked"
          value={metrics.risky_releases_blocked}
          sub={`${metrics.releases_conditional} conditional, ${metrics.release_overrides} overridden`}
          color="text-red-400"
        />
        <MetricCard
          icon={BarChart3}
          label="Intelligence Reports"
          value={metrics.intelligence_reports_generated}
          sub="AI analysis reports generated"
          color="text-[var(--color-text)]"
        />
        <MetricCard
          icon={Shield}
          label="Release Decisions"
          value={metrics.risky_releases_blocked + metrics.releases_conditional}
          sub="Automated go/no-go assessments"
          color="text-orange-400"
        />
      </div>
    </div>
  )
}
