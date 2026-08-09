import { Fragment, useMemo, useState, type ElementType } from 'react'
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Cpu,
  Database,
  FlaskConical,
  FileText,
  GitBranch,
  Layers,
  RefreshCw,
  Shield,
  Sparkles,
  Stethoscope,
  TicketCheck,
  TriangleAlert,
  UserRound,
  XCircle,
} from 'lucide-react'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'

export interface WorkflowStageNode {
  stage_name: string
  status: string
  started_at?: string | null
  completed_at?: string | null
  result_data?: Record<string, unknown> | null
  error?: string | null
  skipped_reason?: string | null
  execution_path?: string | null
  fallback_used?: boolean | null
  input_tokens?: number | null
  output_tokens?: number | null
  total_tokens?: number | null
  llm_calls_count?: number | null
  cost_usd?: number | null
  error_category?: string | null
  confidence_score?: number | null
  evidence_count?: number | null
  route_rationale?: string | null
  label?: string
  description?: string
}

export interface WorkflowEventNode {
  event_type: string
  stage_name?: string | null
  test_case_id?: string | null
  timestamp: string
  detail?: Record<string, unknown>
}

interface WorkflowTimelineProps {
  stages: WorkflowStageNode[]
  events?: WorkflowEventNode[]
  title?: string
  subtitle?: string
  stageOrder?: string[]
  compact?: boolean
  showInspector?: boolean
  showEventFeed?: boolean
  className?: string
  emptyTitle?: string
  emptyDescription?: string
}

type StageMeta = {
  label: string
  description: string
  icon: ElementType
}

const STAGE_META: Record<string, StageMeta> = {
  ingestion: {
    label: 'Ingestion',
    description: 'Load, validate, and enrich the run data',
    icon: GitBranch,
  },
  anomaly_detection: {
    label: 'Anomaly Detection',
    description: 'Compare against baselines and surface regressions',
    icon: AlertTriangle,
  },
  analysis: {
    label: 'Analysis',
    description: 'Investigate the failing tests and collect evidence',
    icon: Bot,
  },
  root_cause_analysis: {
    label: 'Root Cause Analysis',
    description: 'ReAct investigation across logs, traces, and history',
    icon: Bot,
  },
  failure_clustering: {
    label: 'Failure Clustering',
    description: 'Group related failures into defect-shaped clusters',
    icon: Layers,
  },
  triage: {
    label: 'Defect Triage',
    description: 'Prepare Jira-ready defects and owner guidance',
    icon: TicketCheck,
  },
  flaky_sentinel: {
    label: 'Flaky Sentinel',
    description: 'Detect recurring flaky or unstable tests',
    icon: TriangleAlert,
  },
  test_health: {
    label: 'Test Health',
    description: 'Inspect test-code anti-patterns and health risks',
    icon: Stethoscope,
  },
  summary: {
    label: 'Summary',
    description: 'Compose the executive and role-specific narrative',
    icon: FileText,
  },
  release_risk: {
    label: 'Release Risk',
    description: 'Turn the run into a go / conditional go / no-go call',
    icon: Shield,
  },
  policy_evaluation: {
    label: 'Policy Evaluation',
    description: 'Apply release gate rules and thresholds',
    icon: Shield,
  },
  cluster_review: {
    label: 'Cluster Review',
    description: 'Inspect grouped failures and defect-shaped clusters',
    icon: Layers,
  },
  evidence_synthesis: {
    label: 'Evidence Synthesis',
    description: 'Combine evidence into the decision trail',
    icon: FileText,
  },
  release_decision: {
    label: 'Release Decision',
    description: 'Finalize the go / conditional go / no-go recommendation',
    icon: Shield,
  },
  release_phase: {
    label: 'Release Phase',
    description: 'Track a milestone inside the release lifecycle',
    icon: GitBranch,
  },
  stream_connection: {
    label: 'Stream Connection',
    description: 'Keep the live execution channel healthy',
    icon: Cpu,
  },
  run_monitoring: {
    label: 'Run Monitoring',
    description: 'Monitor active runs and current test execution',
    icon: Bot,
  },
  event_rollup: {
    label: 'Event Rollup',
    description: 'Roll live events into a single timeline',
    icon: Sparkles,
  },
  release_readout: {
    label: 'Release Readout',
    description: 'Summarize the current execution state',
    icon: Shield,
  },
  signal_collection: {
    label: 'Signal Collection',
    description: 'Capture runs, blockers, and quality trends',
    icon: Database,
  },
  view_resolution: {
    label: 'View Resolution',
    description: 'Resolve saved views and audience filters',
    icon: Layers,
  },
  digest_compilation: {
    label: 'Digest Compilation',
    description: 'Rank blockers and assemble the digest',
    icon: FileText,
  },
  delivery: {
    label: 'Delivery',
    description: 'Send the digest to its delivery channel',
    icon: TicketCheck,
  },
  dataset_refresh: {
    label: 'Dataset Refresh',
    description: 'Build evaluation datasets and feedback samples',
    icon: Database,
  },
  quality_evaluation: {
    label: 'Quality Evaluation',
    description: 'Measure accuracy, agreement, and drift',
    icon: Sparkles,
  },
  gate_review: {
    label: 'Gate Review',
    description: 'Compare current quality against the baseline',
    icon: Shield,
  },
  model_governance: {
    label: 'Model Governance',
    description: 'Track prompt and model versions',
    icon: FileText,
  },
  access_events: {
    label: 'Access Events',
    description: 'Track sign-ins, permissions, and user access',
    icon: UserRound,
  },
  configuration_changes: {
    label: 'Configuration Changes',
    description: 'Monitor settings and integration updates',
    icon: Shield,
  },
  quality_rollup: {
    label: 'Quality Rollup',
    description: 'Summarize runs, analyses, and release decisions',
    icon: Sparkles,
  },
  observability_export: {
    label: 'Observability Export',
    description: 'Prepare tenant-scoped telemetry and audit exports',
    icon: FileText,
  },
  onboarding: {
    label: 'Onboarding',
    description: 'Guide a new tenant through activation',
    icon: UserRound,
  },
  digests: {
    label: 'Digests',
    description: 'Summarize recurring signals for scheduled delivery',
    icon: Sparkles,
  },
  create_project: {
    label: 'Create Project',
    description: 'Create the project workspace',
    icon: FlaskConical,
  },
  upload_run: {
    label: 'Upload Run',
    description: 'Bring in a test run for analysis',
    icon: GitBranch,
  },
  connect_jira: {
    label: 'Connect Jira',
    description: 'Route defects into Jira',
    icon: TicketCheck,
  },
  connect_telemetry: {
    label: 'Connect Telemetry',
    description: 'Hook up telemetry and observability',
    icon: Cpu,
  },
  view_intelligence: {
    label: 'View Intelligence',
    description: 'Open the run intelligence command center',
    icon: Bot,
  },
  probe: {
    label: 'Health Probe',
    description: 'Validate an integration endpoint',
    icon: Cpu,
  },
  coverage_snapshot: {
    label: 'Coverage Snapshot',
    description: 'Capture coverage data for the selected project scope',
    icon: Shield,
  },
  suite_breadth: {
    label: 'Suite Breadth',
    description: 'Map execution coverage across suites',
    icon: Layers,
  },
  coverage_risk: {
    label: 'Coverage Risk',
    description: 'Surface coverage gaps and execution imbalance',
    icon: TriangleAlert,
  },
  coverage_actions: {
    label: 'Coverage Actions',
    description: 'Turn coverage insight into follow-up actions',
    icon: Sparkles,
  },
  defect_intake: {
    label: 'Defect Intake',
    description: 'Load defects for the selected scope',
    icon: TicketCheck,
  },
  jira_linkage: {
    label: 'Jira Linkage',
    description: 'Track Jira routing status for defects',
    icon: TicketCheck,
  },
  resolution_flow: {
    label: 'Resolution Flow',
    description: 'Show open, in-progress, and resolved defects',
    icon: GitBranch,
  },
  triage_focus: {
    label: 'Triage Focus',
    description: 'Prioritize the next defect to review',
    icon: Bot,
  },
  trend_capture: {
    label: 'Trend Capture',
    description: 'Collect quality signals over time',
    icon: Database,
  },
  signal_comparison: {
    label: 'Signal Comparison',
    description: 'Compare pass rate, failure rate, and volume trends',
    icon: Layers,
  },
  report_delivery: {
    label: 'Report Delivery',
    description: 'Package trend insights for export or email',
    icon: FileText,
  },
  value_capture: {
    label: 'Value Capture',
    description: 'Summarize the operational value delivered',
    icon: Sparkles,
  },
  roi_calculation: {
    label: 'ROI Calculation',
    description: 'Quantify savings and defect reduction',
    icon: Sparkles,
  },
  value_delivery: {
    label: 'Value Delivery',
    description: 'Prepare the result for leadership and success teams',
    icon: FileText,
  },
  integration_health: {
    label: 'Integration Health',
    description: 'Check the health of upstream and downstream systems',
    icon: Cpu,
  },
  default: {
    label: 'Workflow Step',
    description: 'A step in the workflow pipeline',
    icon: Clock3,
  },
}

const STATUS_META: Record<string, { label: string; icon: ElementType; cls: string }> = {
  pending: { label: 'Pending', icon: Clock3, cls: 'text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)]/70 border-[var(--color-border)]' },
  running: { label: 'Running', icon: RefreshCw, cls: 'text-[var(--color-text-secondary)] bg-[var(--color-bg-secondary)]/40 border-[var(--color-border-light)]' },
  completed: { label: 'Done', icon: CheckCircle2, cls: 'text-[var(--status-passed)] bg-[var(--status-passed-bg)] border-[var(--status-passed-bd)]' },
  failed: { label: 'Failed', icon: XCircle, cls: 'text-[var(--status-failed)] bg-[var(--status-failed-bg)] border-[var(--status-failed-bd)]' },
  skipped: { label: 'Skipped', icon: ChevronRight, cls: 'text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)]/80 border-[var(--color-border)]' },
}

function formatValue(value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return value.map(formatValue).join(', ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function formatDuration(startedAt?: string | null, completedAt?: string | null): string | null {
  if (!startedAt || !completedAt) return null
  const start = new Date(startedAt).getTime()
  const end = new Date(completedAt).getTime()
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null
  const seconds = Math.max(0, Math.round((end - start) / 1000))
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  return `${seconds}s`
}

function stageKey(stageName: string): string {
  return stageName.split(':')[0]
}

function stageDisplayName(stageName: string): string {
  const meta = STAGE_META[stageName] ?? STAGE_META[stageKey(stageName)]
  return meta?.label ?? stageKey(stageName).replace(/_/g, ' ')
}

function eventLabel(event: WorkflowEventNode): string {
  const detail = event.detail ?? {}
  const name = event.stage_name ? stageDisplayName(event.stage_name) : 'Stage'
  if (event.event_type === 'stage_started') return `${name} started`
  if (event.event_type === 'stage_completed') return `${name} completed`
  if (event.event_type === 'stage_failed') return `${name} failed`
  if (event.event_type === 'stage_skipped') return `${name} skipped`
  if (event.event_type === 'tool_invoked') return formatValue(detail['tool_name'] ?? detail['tool'] ?? 'Tool invoked')
  if (event.event_type === 'llm_called') return `LLM call in ${event.stage_name ? stageDisplayName(event.stage_name) : 'workflow'}`
  if (event.event_type === 'cache_hit') return `Cache hit in ${event.stage_name ? stageDisplayName(event.stage_name) : 'workflow'}`
  if (event.event_type === 'checkpoint_restored') return `Checkpoint restored in ${event.stage_name ? stageDisplayName(event.stage_name) : 'workflow'}`
  if (event.event_type === 'pipeline_completed') return 'Pipeline completed'
  return event.event_type.replace(/_/g, ' ')
}

function EventIcon({ event }: { event: WorkflowEventNode }) {
  const type = event.event_type
  if (type === 'stage_completed' || type === 'pipeline_completed') return <CheckCircle2 className="h-3.5 w-3.5 text-[var(--status-passed)]" />
  if (type === 'stage_failed') return <XCircle className="h-3.5 w-3.5 text-[var(--status-failed)]" />
  if (type === 'stage_skipped') return <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
  if (type === 'stage_started') return <ArrowRight className="h-3.5 w-3.5 text-[var(--color-text)]" />
  if (type === 'llm_called') return <Bot className="h-3.5 w-3.5 text-[var(--status-flaky)]" />
  if (type === 'tool_invoked') return <Cpu className="h-3.5 w-3.5 text-[var(--color-accent)]" />
  if (type === 'cache_hit') return <Database className="h-3.5 w-3.5 text-[var(--status-passed)]" />
  if (type === 'checkpoint_restored') return <Shield className="h-3.5 w-3.5 text-[var(--status-broken)]" />
  return <AlertCircle className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
}

function StageNode({
  stage,
  active,
  onClick,
  compact,
}: {
  stage: WorkflowStageNode
  active: boolean
  onClick: () => void
  compact: boolean
}) {
  const key = stageKey(stage.stage_name)
  const meta = STAGE_META[stage.stage_name] ?? STAGE_META[key] ?? STAGE_META.default
  const status = STATUS_META[stage.status] ?? STATUS_META.pending
  const StatusIcon = status.icon
  const duration = formatDuration(stage.started_at, stage.completed_at)

  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        'w-full text-left rounded-2xl border transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)]/50',
        compact ? 'p-3' : 'p-4',
        active
          ? 'border-[var(--color-border-light)]/60 bg-[var(--color-bg-secondary)]/40 shadow-[0_0_0_1px_rgba(255,255,255,0.1)]'
          : 'border-[var(--color-border)] bg-[var(--color-bg-card)]/40 hover:border-[var(--color-border)] hover:bg-[var(--color-bg-card)]/70',
      )}
    >
      <div className="flex items-start gap-3">
        <div className={clsx('mt-0.5 rounded-xl border p-2', status.cls)}>
          <StatusIcon className={clsx('h-4 w-4', stage.status === 'running' && 'animate-spin')} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-sm font-semibold text-[var(--color-text)] truncate">{stage.label ?? meta.label}</p>
            <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">{status.label}</span>
          </div>
          {!compact && <p className="text-xs text-[var(--color-text-muted)] mt-0.5">{stage.description ?? meta.description}</p>}
          <div className="mt-2 flex flex-wrap gap-1.5">
            {duration && <span className="badge bg-[var(--color-bg-secondary)]/80 text-[var(--color-text-secondary)] border border-[var(--color-border)]/70 text-[10px]">{duration}</span>}
            {stage.confidence_score != null && (
              <span className={clsx(
                'badge text-[10px]',
                stage.confidence_score >= 80
                  ? 'bg-[var(--status-passed-bg)] text-[var(--status-passed)] border-[var(--status-passed-bd)]'
                  : stage.confidence_score >= 50
                    ? 'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border-[var(--status-broken-bd)]'
                    : 'bg-[var(--status-failed-bg)] text-[var(--status-failed)] border-[var(--status-failed-bd)]',
              )}>
                {stage.confidence_score}% confidence
              </span>
            )}
            {stage.evidence_count != null && (
              <span className="badge bg-[var(--color-accent-muted)]/20 text-[var(--color-accent)] border border-[var(--color-accent)]/30 text-[10px]">
                {stage.evidence_count} evidence
              </span>
            )}
            {stage.total_tokens != null && stage.total_tokens > 0 && (
              <span className="badge bg-[var(--color-accent-muted)]/20 text-[var(--color-accent)] border border-[var(--color-accent)]/30 text-[10px]">
                {stage.total_tokens.toLocaleString()} tok
              </span>
            )}
            {stage.cost_usd != null && stage.cost_usd > 0 && (
              <span className="badge bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)] text-[10px]">
                ${stage.cost_usd.toFixed(4)}
              </span>
            )}
          </div>
          {stage.skipped_reason && !compact && (
            <p className="mt-2 text-xs text-[var(--color-text-muted)] italic">{stage.skipped_reason}</p>
          )}
          {stage.error && !compact && (
            <p className="mt-2 text-xs text-[var(--status-failed)] bg-[var(--status-failed-bg)] border border-[var(--status-failed-bd)] rounded-lg px-2.5 py-1.5">
              {stage.error}
            </p>
          )}
        </div>
      </div>
    </button>
  )
}

function WorkflowEventFeed({ events }: { events: WorkflowEventNode[] }) {
  if (events.length === 0) {
    return (
      <EmptyState
        icon={<Sparkles className="h-6 w-6" />}
        title="No workflow events yet"
        description="The pipeline event stream will appear here as the workflow runs."
      />
    )
  }

  return (
    <div className="space-y-2">
      {events.slice(0, 24).map((event, index) => (
        <div key={`${event.event_type}-${event.stage_name ?? 'none'}-${event.timestamp}-${index}`} className="flex gap-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)]/30 px-3 py-2">
          <EventIcon event={event} />
          <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-sm text-[var(--color-text)]">{eventLabel(event)}</p>
                {event.stage_name && <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">{stageDisplayName(event.stage_name)}</span>}
              </div>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              {new Date(event.timestamp).toLocaleString()}
            </p>
            {event.detail && Object.keys(event.detail).length > 0 && (
              <div className="mt-1 text-xs text-[var(--color-text-muted)] space-y-0.5">
                {Object.entries(event.detail).slice(0, 3).map(([key, value]) => (
                  <div key={key} className="flex gap-2">
                    <span className="text-[var(--color-text-faint)]">{key}:</span>
                    <span className="truncate">{formatValue(value)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

export default function WorkflowTimeline({
  stages,
  events = [],
  title = 'Workflow Progress',
  subtitle,
  stageOrder,
  compact = false,
  showInspector = false,
  showEventFeed = false,
  className,
  emptyTitle = 'No workflow stages found',
  emptyDescription = 'This workflow has not emitted any stage data yet.',
}: WorkflowTimelineProps) {
  const orderedStages = useMemo(() => {
    const score = (stageName: string): number => {
      if (stageOrder && stageOrder.length > 0) {
        const customIndex = stageOrder.indexOf(stageName)
        if (customIndex >= 0) return customIndex
      }
      const order = [
        'ingestion',
        'anomaly_detection',
        'analysis',
        'root_cause_analysis',
        'failure_clustering',
        'triage',
        'flaky_sentinel',
        'test_health',
        'summary',
        'release_risk',
        'onboarding',
        'digests',
        'integration_health',
      ]
      const index = order.indexOf(stageName)
      return index >= 0 ? index : order.length + stageName.length
    }
    return [...stages]
      .map((stage, index) => ({ stage, index }))
      .sort((a, b) => {
        const delta = score(a.stage.stage_name) - score(b.stage.stage_name)
        if (delta !== 0) return delta
        return a.index - b.index
      })
      .map(({ stage }) => stage)
  }, [stages, stageOrder])

  const defaultStage = useMemo(
    () => orderedStages.find(stage => stage.status === 'running')
      ?? orderedStages.find(stage => stage.status === 'failed')
      ?? orderedStages[0]
      ?? null,
    [orderedStages],
  )
  const defaultStageName = defaultStage?.stage_name ?? null
  const [selectedStageName, setSelectedStageName] = useState<string | null>(defaultStageName)
  // Single toggle that collapses the whole DAG into a one-line summary so
  // users can hide the pipeline detail when they're focused on the rest of
  // the page (recent activity, KPIs, etc.) without losing access to it.
  const [pipelineCollapsed, setPipelineCollapsed] = useState(false)

  // Follow the computed default stage (first running, then failed, then first)
  // as the pipeline progresses. Synced during render via previous-value
  // tracking instead of a cascading setState-in-effect.
  const [prevDefaultStageName, setPrevDefaultStageName] = useState(defaultStageName)
  if (prevDefaultStageName !== defaultStageName) {
    setPrevDefaultStageName(defaultStageName)
    setSelectedStageName(defaultStageName)
  }

  if (orderedStages.length === 0) {
    return (
      <EmptyState
        icon={<Sparkles className="h-6 w-6" />}
        title={emptyTitle}
        description={emptyDescription}
      />
    )
  }

  const selectedStage = orderedStages.find(stage => stage.stage_name === selectedStageName) ?? defaultStage ?? orderedStages[0]

  return (
    <div className={clsx('space-y-4', className)}>
      {(title || subtitle) && (
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <div className="flex items-center gap-2">
              {title && <p className="text-sm font-semibold text-[var(--color-text)]">{title}</p>}
              {compact && <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">compact</span>}
            </div>
            {subtitle && <p className="text-xs text-[var(--color-text-muted)] mt-0.5">{subtitle}</p>}
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-[var(--color-text-muted)]">{orderedStages.length} stages</span>
            {selectedStage && (
              <span className="text-[var(--color-text-muted)]">
                {selectedStage.status.replace(/_/g, ' ')}
              </span>
            )}
            <button
              type="button"
              onClick={() => setPipelineCollapsed(v => !v)}
              className="ml-1 inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-border-light)]"
              title={pipelineCollapsed ? 'Expand pipeline' : 'Collapse pipeline'}
              aria-expanded={!pipelineCollapsed}
            >
              {pipelineCollapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              {pipelineCollapsed ? 'Expand' : 'Collapse'}
            </button>
          </div>
        </div>
      )}

      <div className={clsx(
        'grid gap-4',
        showEventFeed ? 'xl:grid-cols-[minmax(0,1fr)_340px]' : 'grid-cols-1',
      )}>
        <div className="space-y-3">
          {pipelineCollapsed ? (
            <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-card)]/40 px-3.5 py-2.5 text-xs text-[var(--color-text-muted)]">
              {orderedStages.length} stages collapsed — expand to view the pipeline.
            </div>
          ) : (
            /* Horizontal DAG flow: stages wrap onto multiple rows when the
               viewport is narrow, with a ChevronRight connector between
               consecutive stages so the directed flow stays obvious. The
               connector hides between the last stage of one row and the
               first of the next — flex-wrap eats it visually. */
            <div className="flex flex-wrap items-stretch gap-2">
              {orderedStages.map((stage, i) => (
                <Fragment key={stage.stage_name}>
                  <div className="flex-1 min-w-[220px] max-w-[300px] flex">
                    <StageNode
                      stage={stage}
                      active={selectedStage?.stage_name === stage.stage_name}
                      onClick={() => setSelectedStageName(stage.stage_name)}
                      compact
                    />
                  </div>
                  {i < orderedStages.length - 1 && (
                    <div className="self-center text-[var(--color-text-faint)] shrink-0 hidden sm:flex" aria-hidden>
                      <ChevronRight className="h-4 w-4" />
                    </div>
                  )}
                </Fragment>
              ))}
            </div>
          )}

          {showInspector && selectedStage && (
            <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg)]/30 p-4 space-y-3">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div>
                  <p className="text-sm font-semibold text-[var(--color-text)]">
                    {selectedStage.label ?? STAGE_META[selectedStage.stage_name]?.label ?? selectedStage.stage_name.replace(/_/g, ' ')}
                  </p>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    {selectedStage.description ?? STAGE_META[selectedStage.stage_name]?.description ?? 'Workflow detail'}
                  </p>
                </div>
                {selectedStage.execution_path && (
                  <span className="badge bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] border border-[var(--color-border)] text-[10px]">
                    {selectedStage.execution_path.replace(/_/g, ' ')}
                  </span>
                )}
              </div>

              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
                <div className="rounded-xl bg-[var(--color-bg-card)]/60 p-3">
                  <p className="text-[var(--color-text-muted)] uppercase tracking-wider text-[10px]">Status</p>
                  <p className="mt-1 text-[var(--color-text)] font-medium capitalize">{selectedStage.status}</p>
                </div>
                <div className="rounded-xl bg-[var(--color-bg-card)]/60 p-3">
                  <p className="text-[var(--color-text-muted)] uppercase tracking-wider text-[10px]">Confidence</p>
                  <p className="mt-1 text-[var(--color-text)] font-medium">{selectedStage.confidence_score != null ? `${selectedStage.confidence_score}%` : '—'}</p>
                </div>
                <div className="rounded-xl bg-[var(--color-bg-card)]/60 p-3">
                  <p className="text-[var(--color-text-muted)] uppercase tracking-wider text-[10px]">Evidence</p>
                  <p className="mt-1 text-[var(--color-text)] font-medium">{selectedStage.evidence_count ?? 0}</p>
                </div>
                <div className="rounded-xl bg-[var(--color-bg-card)]/60 p-3">
                  <p className="text-[var(--color-text-muted)] uppercase tracking-wider text-[10px]">Cost</p>
                  <p className="mt-1 text-[var(--color-text)] font-medium">{selectedStage.cost_usd != null ? `$${selectedStage.cost_usd.toFixed(4)}` : '—'}</p>
                </div>
              </div>

              <div className="grid lg:grid-cols-2 gap-3 text-sm">
                {selectedStage.skipped_reason && (
                  <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)]/40 p-3">
                    <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Skip reason</p>
                    <p className="mt-1 text-[var(--color-text-secondary)]">{selectedStage.skipped_reason}</p>
                  </div>
                )}
                {selectedStage.route_rationale && (
                  <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)]/40 p-3">
                    <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Route rationale</p>
                    <p className="mt-1 text-[var(--color-text-secondary)]">{selectedStage.route_rationale}</p>
                  </div>
                )}
              </div>

              {(selectedStage.input_tokens || selectedStage.output_tokens || selectedStage.llm_calls_count) && (
                <div className="grid grid-cols-3 gap-3 text-xs">
                  <div className="rounded-xl bg-[var(--color-bg-card)]/60 p-3">
                    <p className="text-[var(--color-text-muted)] uppercase tracking-wider text-[10px]">Input</p>
                    <p className="mt-1 text-[var(--color-text)] font-medium">{selectedStage.input_tokens ?? 0}</p>
                  </div>
                  <div className="rounded-xl bg-[var(--color-bg-card)]/60 p-3">
                    <p className="text-[var(--color-text-muted)] uppercase tracking-wider text-[10px]">Output</p>
                    <p className="mt-1 text-[var(--color-text)] font-medium">{selectedStage.output_tokens ?? 0}</p>
                  </div>
                  <div className="rounded-xl bg-[var(--color-bg-card)]/60 p-3">
                    <p className="text-[var(--color-text-muted)] uppercase tracking-wider text-[10px]">LLM calls</p>
                    <p className="mt-1 text-[var(--color-text)] font-medium">{selectedStage.llm_calls_count ?? 0}</p>
                  </div>
                </div>
              )}

              {selectedStage.result_data && Object.keys(selectedStage.result_data).length > 0 && (
                <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)]/40 p-3">
                  <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] mb-2">Result data</p>
                  <div className="space-y-1 text-xs font-mono text-[var(--color-text-secondary)]">
                    {Object.entries(selectedStage.result_data).map(([key, value]) => (
                      <div key={key} className="flex gap-2">
                        <span className="text-[var(--color-text-muted)]">{key}:</span>
                        <span className="break-all">{formatValue(value)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {selectedStage.error && (
                <div className="rounded-xl border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] p-3 text-sm text-[var(--status-failed)]">
                  {selectedStage.error}
                </div>
              )}
            </div>
          )}
        </div>

        {showEventFeed && (
          <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg)]/30 p-4">
            <div className="flex items-center justify-between gap-2 mb-3">
              <div>
                <p className="text-sm font-semibold text-[var(--color-text)]">Workflow Event Feed</p>
                <p className="text-xs text-[var(--color-text-muted)]">Ordered event history from the pipeline log</p>
              </div>
              <span className="text-xs text-[var(--color-text-muted)]">{events.length} events</span>
            </div>
            <WorkflowEventFeed events={events} />
          </div>
        )}
      </div>
    </div>
  )
}
