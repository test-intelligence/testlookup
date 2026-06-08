import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  Activity, AlertTriangle, Bot, CheckCircle, ChevronDown, ChevronRight,
  Clock, FileText, GitBranch, Layers, RefreshCw, Shield, Stethoscope, XCircle, Zap,
} from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import SuiteBadge from '@/components/ui/SuiteBadge'
import { formatRunWhen } from '@/utils/formatters'
import ExecutiveSummaryPanel from '@/components/ai/ExecutiveSummaryPanel'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } from '@/hooks/useAgentRuns'
import { useAIConfig } from '@/hooks/useAIConfig'
import { useRuns } from '@/hooks/useRuns'
import { usePermissions } from '@/hooks/usePermissions'
import agentService from '@/services/agentService'
import type { ActiveLiveRun, AgentPipelineRun, AgentStageResult, PipelineTimeline } from '@/types/agent'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import ComputeCanvas from '@/components/agents/computeGraph/ComputeCanvas'
import RightRail from '@/components/agents/computeGraph/RightRail'
import ModeTabs, { type WorkflowMode } from '@/components/agents/computeGraph/ModeTabs'
import { mapPipelineToComputeGraph } from '@/components/agents/computeGraph/mapping'
import type { SelectedId } from '@/components/agents/computeGraph/types'
import { useProjectChangeRedirect, useProjectChangeReset } from '@/hooks/useProjectChange'

// ── Stage metadata ─────────────────────────────────────────────

const STAGE_META: Record<string, { label: string; icon: React.ElementType; description: string }> = {
  ingestion: {
    label: 'Ingestion',
    icon: GitBranch,
    description: 'Validate and enrich test data from PostgreSQL',
  },
  anomaly_detection: {
    label: 'Anomaly Detection',
    icon: AlertTriangle,
    description: 'Compare against baselines, detect regressions and flaky tests',
  },
  root_cause_analysis: {
    label: 'Root Cause Analysis',
    icon: Bot,
    description: 'LangChain ReAct agent investigates each failure',
  },
  summary: {
    label: 'Summary Generation',
    icon: FileText,
    description: 'AI-written run summary and executive report',
  },
  triage: {
    label: 'Defect Triage',
    icon: Zap,
    description: 'Auto-create Jira tickets for high-confidence failures',
  },
  failure_clustering: {
    label: 'Failure Clustering',
    icon: Layers,
    description: 'Semantically group failures to reduce redundant LLM calls',
  },
  flaky_sentinel: {
    label: 'Flaky Sentinel',
    icon: AlertTriangle,
    description: 'Investigate flaky test lifecycles and quarantine candidates',
  },
  test_health: {
    label: 'Test Health',
    icon: Stethoscope,
    description: 'Analyze test code for anti-patterns and automation defects',
  },
  release_risk: {
    label: 'Release Risk',
    icon: Shield,
    description: 'AI go/no-go release recommendation with risk scoring',
  },
}

const STATUS_COLOUR: Record<string, string> = {
  pending: 'text-[var(--color-text-muted)]',
  running: 'text-[var(--color-text)]',
  completed: 'text-emerald-400',
  // `partial` is a degraded-but-finished pipeline/stage (e.g. a stage errored,
  // errors>=1). Render it as a visible amber warning state so the row never
  // looks "missing" — see BUG-004.
  partial: 'text-amber-400',
  failed: 'text-red-400',
  skipped: 'text-[var(--color-text-muted)]',
}

const STATUS_BG: Record<string, string> = {
  pending: 'bg-[var(--color-bg-hover)]',
  running: 'bg-[var(--color-bg-secondary)]/60 border border-[var(--color-border-light)]',
  completed: 'bg-emerald-900/20 border border-emerald-700/30',
  partial: 'bg-amber-900/20 border border-amber-700/30',
  failed: 'bg-red-900/20 border border-red-700/30',
  skipped: 'bg-[var(--color-bg-secondary)]/80',
}

// ── Sub-components ─────────────────────────────────────────────

/** Parses the markdown report into structured sections and renders as clean cards. */
function StructuredReportDetail({ markdown, hasPanel }: { markdown: string; hasPanel: boolean }) {
  if (!markdown) return null

  // Parse markdown into sections by ## headings
  const sections: Array<{ title: string; lines: string[] }> = []
  let current: { title: string; lines: string[] } | null = null

  for (const line of markdown.split('\n')) {
    const headingMatch = line.match(/^##\s+(.+)/)
    if (headingMatch) {
      if (current) sections.push(current)
      current = { title: headingMatch[1].trim(), lines: [] }
    } else if (current) {
      const trimmed = line.trim()
      if (trimmed) current.lines.push(trimmed)
    }
  }
  if (current) sections.push(current)

  // When executive panel is present, skip the "Executive Summary" section (already shown in panel)
  const filtered = sections.filter(s => !(hasPanel && s.title.toLowerCase().includes('executive summary')))
  if (filtered.length === 0) return null

  return (
    <div className="space-y-3">
      {filtered.map((section, idx) => (
        <details key={idx} open={idx === 0} className="group">
          <summary className="flex items-center gap-2 cursor-pointer text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider hover:text-[var(--color-text-secondary)] transition-colors py-1">
            <ChevronRight className="h-3.5 w-3.5 transition-transform group-open:rotate-90" />
            {section.title}
          </summary>
          <div className="bg-[var(--color-bg-secondary)]/60 border border-[var(--color-border)] rounded-lg p-3 mt-1.5 space-y-1.5">
            {section.lines.map((line, li) => {
              // Bold labels: **Label:** value
              const boldMatch = line.match(/^\*\*(.+?):\*\*\s*(.*)/)
              if (boldMatch) {
                return (
                  <div key={li} className="text-sm">
                    <span className="text-[var(--color-text-muted)] font-medium">{boldMatch[1]}: </span>
                    <span className="text-[var(--color-text)]">{boldMatch[2]}</span>
                  </div>
                )
              }
              // List items: - text
              if (line.startsWith('- ')) {
                return (
                  <div key={li} className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)] pl-1">
                    <span className="text-[var(--color-text-faint)] mt-1.5 h-1 w-1 rounded-full bg-current shrink-0" />
                    {line.slice(2)}
                  </div>
                )
              }
              // Regular text
              return <p key={li} className="text-sm text-[var(--color-text-secondary)]">{line}</p>
            })}
          </div>
        </details>
      ))}
    </div>
  )
}

function StatusIcon({ status }: { status: string }) {
  if (status === 'completed') return <CheckCircle className="w-4 h-4 text-emerald-400" />
  // `partial` = finished with errors (degraded). Amber warning triangle so the
  // pipeline/stage stays visible instead of falling through to the neutral
  // Clock fallback and looking "missing" — see BUG-004.
  if (status === 'partial') return <AlertTriangle className="w-4 h-4 text-amber-400" />
  if (status === 'failed') return <XCircle className="w-4 h-4 text-red-400" />
  if (status === 'running') return <RefreshCw className="w-4 h-4 text-[var(--color-text)] animate-spin" />
  if (status === 'skipped') return <ChevronRight className="w-4 h-4 text-[var(--color-text-muted)]" />
  return <Clock className="w-4 h-4 text-[var(--color-text-muted)]" />
}

function StageCard({ stage, showLLMMetrics = true }: { stage: AgentStageResult; showLLMMetrics?: boolean }) {
  const [open, setOpen] = useState(stage.status === 'failed')
  const meta = STAGE_META[stage.stage_name]
  if (!meta) return null
  const Icon = meta.icon

  const duration =
    stage.started_at && stage.completed_at
      ? Math.round(
          (new Date(stage.completed_at).getTime() - new Date(stage.started_at).getTime()) / 1000,
        )
      : null

  return (
    <div className={`rounded-lg p-3 ${STATUS_BG[stage.status] || 'bg-[var(--color-bg-secondary)]'}`}>
      <button
        className="w-full flex items-center gap-3 text-left"
        onClick={() => setOpen(v => !v)}
      >
        <StatusIcon status={stage.status} />
        <div className="p-1.5 rounded-md bg-[var(--color-bg-secondary)]/80">
          <Icon className={`w-3.5 h-3.5 ${STATUS_COLOUR[stage.status]}`} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-[var(--color-text)]">{meta.label}</p>
          <p className="text-xs text-[var(--color-text-muted)]">{meta.description}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {/* Phase 6: Token and cost badges (hidden in rules/ML mode) */}
          {showLLMMetrics && stage.total_tokens != null && stage.total_tokens > 0 && (
            <span className="text-[10px] bg-indigo-900/30 text-indigo-400 px-1.5 py-0.5 rounded"
              title={`Input: ${stage.input_tokens ?? 0} / Output: ${stage.output_tokens ?? 0}`}
            >
              {stage.total_tokens.toLocaleString()} tok
            </span>
          )}
          {showLLMMetrics && stage.cost_usd != null && stage.cost_usd > 0 && (
            <span className="text-[10px] bg-amber-900/30 text-amber-400 px-1.5 py-0.5 rounded">
              ${stage.cost_usd.toFixed(4)}
            </span>
          )}
          {stage.confidence_score != null && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${
              stage.confidence_score >= 80 ? 'bg-emerald-900/30 text-emerald-400' :
              stage.confidence_score >= 50 ? 'bg-amber-900/30 text-amber-400' :
              'bg-red-900/30 text-red-400'
            }`}>
              {stage.confidence_score}%
            </span>
          )}
          {duration !== null && (
            <span className="text-xs text-[var(--color-text-muted)]">{duration}s</span>
          )}
          <span className={`text-xs font-mono ${STATUS_COLOUR[stage.status]}`}>
            {stage.status.toUpperCase()}
          </span>
          {open ? <ChevronDown className="w-4 h-4 text-[var(--color-text-muted)]" /> : <ChevronRight className="w-4 h-4 text-[var(--color-text-muted)]" />}
        </div>
      </button>

      {/* All-green skip context shown inline without requiring expand */}
      {stage.status === 'skipped' && stage.skipped_reason && (
        <p className="mt-1 ml-10 text-[10px] text-[var(--color-text-faint)] italic">{stage.skipped_reason}</p>
      )}

      {open && (stage.result_data || stage.error || stage.route_rationale || stage.total_tokens) && (
        <div className="mt-3 ml-10 space-y-2">
          {/* Phase 6: Observability details */}
          {(stage.total_tokens || stage.llm_calls_count || stage.evidence_count) && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {showLLMMetrics && stage.llm_calls_count != null && stage.llm_calls_count > 0 && (
                <div className="bg-[var(--color-bg-card)]/60 rounded p-2 text-center">
                  <div className="text-sm font-bold text-[var(--color-text)]">{stage.llm_calls_count}</div>
                  <div className="text-[9px] text-[var(--color-text-muted)]">LLM Calls</div>
                </div>
              )}
              {showLLMMetrics && stage.total_tokens != null && stage.total_tokens > 0 && (
                <div className="bg-[var(--color-bg-card)]/60 rounded p-2 text-center">
                  <div className="text-sm font-bold text-indigo-400">{stage.total_tokens.toLocaleString()}</div>
                  <div className="text-[9px] text-[var(--color-text-muted)]">Tokens ({stage.input_tokens ?? 0}in/{stage.output_tokens ?? 0}out)</div>
                </div>
              )}
              {showLLMMetrics && stage.cost_usd != null && stage.cost_usd > 0 && (
                <div className="bg-[var(--color-bg-card)]/60 rounded p-2 text-center">
                  <div className="text-sm font-bold text-amber-400">${stage.cost_usd.toFixed(4)}</div>
                  <div className="text-[9px] text-[var(--color-text-muted)]">Est. Cost</div>
                </div>
              )}
              {stage.evidence_count != null && (
                <div className="bg-[var(--color-bg-card)]/60 rounded p-2 text-center">
                  <div className="text-sm font-bold text-cyan-400">{stage.evidence_count}</div>
                  <div className="text-[9px] text-[var(--color-text-muted)]">Evidence Items</div>
                </div>
              )}
            </div>
          )}
          {stage.route_rationale && (
            <div className="bg-[var(--color-bg-card)]/60 rounded p-2 text-xs text-[var(--color-text-muted)]">
              <span className="text-[var(--color-text-muted)] font-medium">Route rationale: </span>
              {stage.route_rationale}
            </div>
          )}
          {stage.error_category && (
            <div className="text-[10px] text-[var(--color-text-muted)]">
              Error category: <span className="text-red-400 font-mono">{stage.error_category}</span>
            </div>
          )}
          {stage.result_data && (
            <div className="bg-[var(--color-bg-card)]/60 rounded p-2 text-xs font-mono text-[var(--color-text-secondary)]">
              {Object.entries(stage.result_data).map(([k, v]) => (
                <div key={k} className="flex gap-2">
                  <span className="text-[var(--color-text-muted)]">{k}:</span>
                  <span>{String(v)}</span>
                </div>
              ))}
            </div>
          )}
          {stage.error && (
            <div className="bg-red-950/40 border border-red-800/30 rounded p-2 text-xs text-red-300">
              {stage.error}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function PipelineCard({
  pipeline,
  onSelect,
  selected,
}: {
  pipeline: AgentPipelineRun
  onSelect: () => void
  selected: boolean
}) {
  const started = pipeline.started_at ? new Date(pipeline.started_at).toLocaleString() : '—'
  const duration =
    pipeline.started_at && pipeline.completed_at
      ? Math.round(
          (new Date(pipeline.completed_at).getTime() - new Date(pipeline.started_at).getTime()) / 1000,
        )
      : null

  return (
    <button
      onClick={onSelect}
      className={`w-full text-left card p-3 hover:border-[var(--color-border-light)] transition-colors ${
        selected ? 'border-[var(--color-border-light)] bg-[var(--color-bg-secondary)]/40' : ''
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <StatusIcon status={pipeline.status} />
        <span className="text-sm font-medium text-[var(--color-text)] capitalize">
          {pipeline.workflow_type} pipeline
        </span>
        <span className={`ml-auto text-xs font-mono ${STATUS_COLOUR[pipeline.status]}`}>
          {pipeline.status.toUpperCase()}
        </span>
      </div>
      {/* Run context: which run / suite this pipeline analysed. ``run_seq`` is
          the same per-(project, suite) "Run #N" shown on /runs + /live; falls
          back to the SDK build number, then to the raw run id, so there's
          always something identifying. */}
      <div className="flex items-center gap-2 mb-1 pl-5 flex-wrap">
        <span className="text-xs font-semibold text-[var(--color-text-secondary)]">
          {pipeline.run_seq != null
            ? `Run #${pipeline.run_seq}`
            : pipeline.build_number
              ? `Build ${pipeline.build_number}`
              : `Run ${pipeline.test_run_id.slice(0, 8)}`}
        </span>
        {pipeline.suite_name && (
          <SuiteBadge primary={pipeline.suite_name} all={[pipeline.suite_name]} />
        )}
      </div>
      <p className="text-xs text-[var(--color-text-muted)] pl-5">{started}</p>
      {duration !== null && (
        <p className="text-xs text-[var(--color-text-muted)] pl-5">Duration: {duration}s</p>
      )}
    </button>
  )
}

function LiveRunCard({ run }: { run: ActiveLiveRun }) {
  const completed = run.passed + run.failed + run.broken
  const progress = run.total > 0 ? (completed / run.total) * 100 : 0

  return (
    <div className="card border border-[var(--color-border-light)] bg-[var(--color-bg-secondary)]/30">
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <Activity className="w-4 h-4 text-[var(--color-text)] animate-pulse" />
        {/* Per-(project, suite) Run #N — server-computed, stable across
            pages. Falls back to the SDK build_number for live sessions
            on legacy runs whose TestRun row hasn't been materialised yet. */}
        <span className="text-sm font-semibold text-[var(--color-text)]">
          {run.run_seq != null ? `Run #${run.run_seq}` : `Build ${run.build_number}`}
        </span>
        <SuiteBadge primary={run.suite_name} all={run.suite_name ? [run.suite_name] : null} />
        {/* When this run started — "Run #N" repeats per (project, suite), so
            the timestamp is what tells two same-numbered live runs apart. */}
        {(() => {
          const when = formatRunWhen(run.started_at)
          return when ? (
            <span className="text-[10px] font-mono text-[var(--color-text-faint)] shrink-0 whitespace-nowrap">
              {when}
            </span>
          ) : null
        })()}
        <span className="ml-auto text-xs bg-[var(--color-bg-secondary)]/60 text-[var(--color-text)] px-2 py-0.5 rounded-full">
          LIVE
        </span>
      </div>
      {run.current_test && (
        <p className="text-xs text-[var(--color-text-muted)] truncate mb-2">
          Running: <span className="text-[var(--color-text-secondary)]">{run.current_test}</span>
        </p>
      )}
      <div className="grid grid-cols-4 gap-2 mb-2 text-center text-xs">
        <div><div className="text-emerald-400 font-mono">{run.passed}</div><div className="text-[var(--color-text-muted)]">Pass</div></div>
        <div><div className="text-red-400 font-mono">{run.failed}</div><div className="text-[var(--color-text-muted)]">Fail</div></div>
        <div><div className="text-yellow-400 font-mono">{run.skipped}</div><div className="text-[var(--color-text-muted)]">Skip</div></div>
        <div><div className="text-[var(--color-text)] font-mono">{run.pass_rate}%</div><div className="text-[var(--color-text-muted)]">Rate</div></div>
      </div>
      <div className="w-full h-1.5 bg-[var(--color-bg-secondary)] rounded-full overflow-hidden">
        <div
          className="h-full bg-neutral-300 rounded-full transition-all"
          style={{ width: `${progress}%` }}
        />
      </div>
      <p className="text-xs text-[var(--color-text-muted)] mt-1">{completed}/{run.total} tests run</p>
    </div>
  )
}

function formatMoney(value: number | null | undefined) {
  const n = Number(value ?? 0)
  return `$${n.toFixed(n < 0.01 ? 4 : 2)}`
}

function ObservabilityPanel({ timeline }: { timeline?: PipelineTimeline }) {
  const obs = timeline?.agent_observability
  const alerts = timeline?.alerts ?? []
  if (!obs && alerts.length === 0) return null

  const costRatio = obs?.cost.budget_usd ? obs.cost.total_usd / obs.cost.budget_usd : 0
  const costTone = costRatio >= 1 ? 'text-red-400' : costRatio >= 0.75 ? 'text-amber-400' : 'text-emerald-400'
  const fallbackTone = (obs?.fallback.count ?? 0) > 0 ? 'text-amber-400' : 'text-emerald-400'
  const errorTone = (obs?.errors.count ?? 0) > 0 ? 'text-red-400' : 'text-emerald-400'
  const avgStageDuration = obs?.latency.avg_stage_duration_seconds == null
    ? '—'
    : `${obs.latency.avg_stage_duration_seconds}s`

  return (
    <div className="card border border-[var(--color-border)] bg-[var(--color-bg-secondary)]/30">
      <div className="flex items-center gap-2 mb-3">
        <Activity className="h-4 w-4 text-[var(--color-text)]" />
        <h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">Pipeline Observability</h3>
        {alerts.length > 0 && (
          <span className="ml-auto text-[10px] px-2 py-0.5 rounded bg-red-900/25 text-red-300">
            {alerts.length} alert{alerts.length === 1 ? '' : 's'}
          </span>
        )}
      </div>

      {obs && (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-2 mb-3">
          <div className="rounded border border-[var(--color-border)] bg-[var(--color-bg-card)]/60 p-2">
            <div className={`text-sm font-semibold ${costTone}`}>{formatMoney(obs.cost.total_usd)}</div>
            <div className="text-[10px] text-[var(--color-text-muted)]">Cost / {formatMoney(obs.cost.budget_usd)}</div>
          </div>
          <div className="rounded border border-[var(--color-border)] bg-[var(--color-bg-card)]/60 p-2">
            <div className="text-sm font-semibold text-indigo-300">{obs.tokens.total.toLocaleString()}</div>
            <div className="text-[10px] text-[var(--color-text-muted)]">{obs.tokens.llm_calls} LLM calls</div>
          </div>
          <div className="rounded border border-[var(--color-border)] bg-[var(--color-bg-card)]/60 p-2">
            <div className="text-sm font-semibold text-[var(--color-text)]">{avgStageDuration}</div>
            <div className="text-[10px] text-[var(--color-text-muted)]">Avg stage</div>
          </div>
          <div className="rounded border border-[var(--color-border)] bg-[var(--color-bg-card)]/60 p-2">
            <div className={`text-sm font-semibold ${fallbackTone}`}>{obs.fallback.count}</div>
            <div className="text-[10px] text-[var(--color-text-muted)]">Fallbacks</div>
          </div>
          <div className="rounded border border-[var(--color-border)] bg-[var(--color-bg-card)]/60 p-2">
            <div className={`text-sm font-semibold ${errorTone}`}>{obs.errors.count}</div>
            <div className="text-[10px] text-[var(--color-text-muted)]">Stage errors</div>
          </div>
        </div>
      )}

      {alerts.length > 0 && (
        <div className="space-y-2">
          {alerts.map((alert, index) => (
            <div key={`${alert.type}-${index}`} className="rounded border border-red-800/30 bg-red-950/20 p-2">
              <div className="flex items-start gap-2">
                <AlertTriangle className="h-3.5 w-3.5 text-red-300 mt-0.5 shrink-0" />
                <div className="min-w-0">
                  <p className="text-xs font-medium text-red-200">{alert.message}</p>
                  {alert.routing && (
                    <div className="mt-1 flex flex-wrap gap-1.5 text-[10px] text-[var(--color-text-muted)]">
                      <span className="px-1.5 py-0.5 rounded bg-[var(--color-bg-secondary)]">owner: {alert.routing.primary_owner}</span>
                      <span className="px-1.5 py-0.5 rounded bg-[var(--color-bg-secondary)]">escalate: {alert.routing.escalation_owner}</span>
                      <span className="px-1.5 py-0.5 rounded bg-[var(--color-bg-secondary)]">{alert.routing.priority}</span>
                    </div>
                  )}
                  {alert.routing?.recommended_action && (
                    <p className="mt-1 text-[10px] text-[var(--color-text-muted)]">{alert.routing.recommended_action}</p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────

const MODE_BADGE: Record<string, { label: string; colour: string }> = {
  llm:   { label: 'LLM Mode',   colour: 'bg-indigo-900/30 text-indigo-400' },
  ml:    { label: 'ML Mode',    colour: 'bg-cyan-900/30 text-cyan-400' },
  rules: { label: 'Rules Mode', colour: 'bg-amber-900/30 text-amber-400' },
  auto:  { label: 'Auto Mode',  colour: 'bg-emerald-900/30 text-emerald-400' },
}

export default function AgentStatusPage() {
  const { runId } = useParams<{ runId?: string }>()
  const navigate = useNavigate()
  const [selectedPipeline, setSelectedPipeline] = useState<string | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  // The AI report is shown by default (expanded) once a pipeline is selected —
  // it's the headline output of the pipeline, so users shouldn't have to click
  // "View AI report" to see it. The toggle still lets them collapse it. The
  // ``useRunSummary`` fetch below is gated on this, so default-true means the
  // report fetches as soon as a pipeline is picked.
  const [showSummary, setShowSummary] = useState(true)
  const { data: aiConfig } = useAIConfig()
  // Recent runs feed the suite+build dropdown so users can browse pipelines
  // across runs instead of only the one in the URL. Size matches the
  // pagination convention applied elsewhere.
  const { data: recentRunsList } = useRuns({ page: 1, size: 25 })
  const recentRuns = recentRunsList?.items ?? []
  const analysisMode = aiConfig?.analysis_mode ?? 'auto'
  const showLLMMetrics = analysisMode !== 'rules' && analysisMode !== 'ml'

  useProjectChangeRedirect('/agents', Boolean(runId))
  useProjectChangeReset(() => {
    setSelectedPipeline(null)
    setSelectedRunId(null)
    // Stay expanded-by-default: the next pipeline the user picks shows its
    // report without a click. (No pipeline is selected right after a reset, so
    // nothing renders until then anyway.)
    setShowSummary(true)
  })

  const { data: rawPipelines = [], isLoading: pipelinesLoading } = usePipelines(runId)
  // Sort descending by created_at client-side as a defensive guarantee
  const pipelines = [...rawPipelines].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )
  const { data: stages = [], isLoading: stagesLoading } = usePipelineStages(selectedPipeline)
  const { data: timeline } = usePipelineTimeline(selectedPipeline)
  const summaryStage = stages.find((stage) => stage.stage_name === 'summary')
  // Direction-C compute graph: map the backend's flat stage list into the
  // node/edge/decision shape the canvas expects. Derived purely from the
  // current pipeline's stages so it stays in lockstep with the rest of the
  // page (no extra fetch).
  const computeGraph = useMemo(
    () => mapPipelineToComputeGraph(stages as AgentStageResult[]),
    [stages],
  )
  const [workflowMode, setWorkflowMode] = useState<WorkflowMode>('debug')
  // Default canvas selection: the first running stage (or first failed) so
  // the rail isn't empty on first paint.
  const [canvasSelection, setCanvasSelection] = useState<SelectedId>(null)
  useEffect(() => {
    if (canvasSelection !== null) return
    const running = computeGraph.stages.find(s => s.status === 'running')
    const failed  = computeGraph.stages.find(s => s.status === 'failed')
    const initial = running ?? failed ?? computeGraph.stages[0] ?? null
    if (initial) setCanvasSelection(initial.id)
  }, [computeGraph.stages, canvasSelection])
  const { data: liveRuns = [] } = useActiveLiveRuns()
  const {
    data: summary,
    isLoading: summaryLoading,
    error: summaryError,
  } = useRunSummary(showSummary ? selectedRunId : null)

  const { isQaEngineer } = usePermissions()
  const [triggerInput, setTriggerInput] = useState('')
  const [triggerSubmitting, setTriggerSubmitting] = useState(false)

  async function handleTriggerByRunId(e: React.FormEvent) {
    e.preventDefault()
    const id = triggerInput.trim()
    if (!id) {
      toast.error('Pick a test suite + build to trigger.')
      return
    }
    setTriggerSubmitting(true)
    try {
      await agentService.triggerPipeline(id)
      toast.success('Pipeline queued — it will appear in the list shortly.')
      setTriggerInput('')
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to trigger pipeline'
      toast.error(detail)
    } finally {
      setTriggerSubmitting(false)
    }
  }


  return (
    <div className="space-y-6">
      <PageHeader
        title="Agent Pipeline"
        subtitle="Multi-agent test analysis: ingestion → anomaly detection → root-cause → summary → triage"
        actions={
          <div className="flex items-center gap-2 flex-wrap">
            {/* Pipeline picker: lets the user jump between recent runs by
                test suite + build number. Each option is labelled
                ``<suite> · #<build>`` so the user picks by attributes they
                recognise, not the opaque run UUID. Selecting routes to
                /agents/<id> so the page state and URL stay in sync. */}
            <label className="text-xs text-[var(--color-text-muted)]">Test Suite &amp; Build:</label>
            <select
              value={runId ?? ''}
              onChange={(e) => {
                const id = e.target.value
                // App.tsx mounts this page at both ``/agents`` and
                // ``/agents/run/:runId`` — match the param form when
                // navigating so the route resolves and useParams reads the id.
                if (id) navigate(`/agents/run/${id}`)
                else navigate('/agents')
              }}
              className="text-xs bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded px-2 py-1 max-w-[320px] truncate"
            >
              <option value="">— All recent pipelines —</option>
              {recentRuns.map((r) => {
                const suite = r.primary_suite_name || (r.suite_names && r.suite_names[0]) || 'Unknown suite'
                // Picker label uses Run #N when available (matches the
                // /runs and /live pages); legacy rows still render the
                // raw build_number prefixed with "#" for continuity.
                const runLabel = r.run_seq != null ? `Run #${r.run_seq}` : `#${r.build_number}`
                const label = `${suite} · ${runLabel}`
                return (
                  <option key={r.id} value={r.id}>{label}</option>
                )
              })}
            </select>
            <span className={`text-xs px-2 py-1 rounded font-medium ${MODE_BADGE[analysisMode]?.colour ?? MODE_BADGE.auto.colour}`}>
              {MODE_BADGE[analysisMode]?.label ?? 'Auto Mode'}
            </span>
          </div>
        }
      />

      {/* Live runs */}
      {liveRuns.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-3">
            Live Executions
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {liveRuns.map((run) => (
              <LiveRunCard key={run.run_id} run={run} />
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Pipeline list */}
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-[var(--color-text-muted)] uppercase tracking-wide">
            Pipeline Runs
          </h3>

          {isQaEngineer && (
            // Manual trigger — pick a recent run by test suite + build and
            // fire its pipeline. Replaces the prior "paste a run UUID" input
            // so users never have to handle the opaque UUID directly. The
            // value carried in state is still the UUID under the hood —
            // it's just selected by suite/build attributes.
            <form onSubmit={handleTriggerByRunId} className="flex items-center gap-1.5">
              <select
                value={triggerInput}
                onChange={e => setTriggerInput(e.target.value)}
                aria-label="Pick a run by test suite and build to trigger"
                title="Choose a recent run by its test suite and build number, then fire its agent pipeline manually. Useful when the auto-trigger was lost (worker crash, etc.)."
                className="flex-1 min-w-0 bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-xs rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-[var(--color-ring)] truncate"
                disabled={triggerSubmitting}
              >
                <option value="">— Pick a test suite &amp; build —</option>
                {recentRuns.map((r) => {
                  const suite = r.primary_suite_name || (r.suite_names && r.suite_names[0]) || 'Unknown suite'
                  const runLabel = r.run_seq != null ? `Run #${r.run_seq}` : `#${r.build_number}`
                  return (
                    <option key={r.id} value={r.id}>
                      {`${suite} · ${runLabel}`}
                    </option>
                  )
                })}
              </select>
              <button
                type="submit"
                disabled={triggerSubmitting || triggerInput.trim().length === 0}
                title="Queue the standard pipeline for the selected run"
                className="inline-flex items-center justify-center h-7 w-7 rounded text-[var(--color-text-muted)] border border-[var(--color-border-light)] hover:bg-[var(--color-bg-hover)]/40 hover:text-[var(--color-text-secondary)] transition-colors disabled:opacity-50"
              >
                {triggerSubmitting
                  ? <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                  : <Zap className="h-3.5 w-3.5" />}
              </button>
            </form>
          )}

          {pipelinesLoading ? (
            <LoadingSpinner size="sm" />
          ) : pipelines.length === 0 ? (
            <div className="card border border-[var(--color-border)] bg-[var(--color-bg-secondary)]/30 p-4 text-sm space-y-3">
              <div className="flex items-start gap-2">
                <Bot className="h-4 w-4 mt-0.5 text-[var(--color-text-muted)] flex-shrink-0" />
                <div>
                  <p className="font-medium text-[var(--color-text)]">No agent pipelines yet</p>
                  <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                    Active analysis mode: <span className="font-mono text-[var(--color-text-secondary)]">{analysisMode}</span>.
                    Pipelines are recorded once a test run finalises.
                  </p>
                </div>
              </div>

              {liveRuns.length > 0 && (
                <p className="text-xs text-amber-300 border-t border-[var(--color-border)] pt-2">
                  {liveRuns.length} live run{liveRuns.length === 1 ? '' : 's'} still streaming.
                  Pipelines fire after each run sends a <code className="px-1 bg-[var(--color-bg-secondary)] rounded">run_complete</code> event.
                  Stale runs are auto-closed after 15 min idle.
                </p>
              )}

              <div className="text-xs text-[var(--color-text-muted)] space-y-1">
                <p className="font-medium text-[var(--color-text-secondary)]">To get a pipeline running:</p>
                <ul className="list-disc list-inside space-y-0.5 marker:text-[var(--color-text-faint)]">
                  <li>Stream tests via the SDK (<code className="px-1 bg-[var(--color-bg-secondary)] rounded">LiveStream</code>) or <code className="px-1 bg-[var(--color-bg-secondary)] rounded">POST /api/v1/stream/ingest</code> with a closing <code className="px-1 bg-[var(--color-bg-secondary)] rounded">run_complete</code> event.</li>
                  <li>Or upload a JUnit / Allure / TestNG report at <Link to="/runs" className="text-[var(--color-text-secondary)] underline">/runs</Link>.</li>
                  <li>The pipeline runs in every mode — <span className="font-mono">llm</span>, <span className="font-mono">ml</span>, <span className="font-mono">rules</span>, <span className="font-mono">auto</span>. The mode only changes which engine each stage uses.</li>
                </ul>
              </div>

              {analysisMode === 'llm' && (
                <p className="text-xs text-[var(--color-text-muted)] border-t border-[var(--color-border)] pt-2">
                  <span className="text-indigo-300 font-medium">LLM mode tip:</span>{' '}
                  the analysis stage needs a reachable LLM (Ollama with the configured model pulled, or a hosted provider).
                  If the LLM is unavailable the pipeline still runs and falls back to the rules engine — the row will appear here either way.
                </p>
              )}
            </div>
          ) : (
            pipelines.map((p: AgentPipelineRun) => (
              <PipelineCard
                key={p.id}
                pipeline={p}
                selected={selectedPipeline === p.id}
                onSelect={() => {
                  setSelectedPipeline(p.id)
                  setSelectedRunId(p.test_run_id)
                  // Keep the AI report expanded by default when switching runs.
                  setShowSummary(true)
                }}
              />
            ))
          )}
        </div>

        {/* Stage detail */}
        <div className="lg:col-span-2 space-y-3">
          {!selectedPipeline ? (
            <div className="card flex flex-col items-center justify-center py-16 text-center">
              <Bot className="w-12 h-12 text-[var(--color-text-faint)] mb-3" />
              <p className="text-[var(--color-text-muted)]">Select a pipeline run to see agent stages</p>
            </div>
          ) : stagesLoading ? (
            <div className="flex justify-center py-8"><LoadingSpinner /></div>
          ) : (
            <>
              <div className="flex items-center gap-3 mb-1">
                <h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">Agent Stages</h3>
                <button
                  onClick={() => setShowSummary(v => !v)}
                  className="ml-auto flex items-center gap-1.5 text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)]"
                >
                  <FileText className="w-3.5 h-3.5" />
                  {showSummary ? 'Hide report' : 'View AI report'}
                </button>
              </div>

              <ObservabilityPanel timeline={timeline} />

              {/* Direction-C compute graph: 1750×560 canvas with absolute-
                  positioned nodes, SVG bezier edges, a decision diamond, and a
                  360px right rail. Debug mode (the default) renders the canvas;
                  the other ModeTabs swap in their own bodies. */}
              <div className="rounded-2xl border border-[var(--color-border)] overflow-hidden">
                <ModeTabs mode={workflowMode} onChange={setWorkflowMode} liveActive={liveRuns.length > 0} />
                {workflowMode === 'debug' ? (
                  <div className="flex" style={{ height: 600 }}>
                    <ComputeCanvas
                      stages={computeGraph.stages}
                      decision={computeGraph.decision}
                      edges={computeGraph.edges}
                      selectedId={canvasSelection}
                      onSelect={setCanvasSelection}
                    />
                    <RightRail
                      selectedId={canvasSelection}
                      stages={computeGraph.stages}
                      decision={computeGraph.decision}
                    />
                  </div>
                ) : (
                  <div className="flex items-center justify-center h-[300px] text-sm text-[var(--color-text-muted)] px-6 text-center">
                    {workflowMode === 'live' && 'Live mode: see the Live Executions strip above.'}
                    {workflowMode === 'audit' && 'Audit view — coming in the next iteration.'}
                    {workflowMode === 'compare' && 'Compare view — coming in the next iteration.'}
                  </div>
                )}
              </div>

              {/* Original chevron-flow timeline kept below as a fallback /
                  power-user surface. Lives in a details disclosure so the
                  compute graph is the default. */}
              <details className="mt-4">
                <summary className="cursor-pointer text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
                  Classic workflow timeline
                </summary>
                <div className="mt-2">
                  <WorkflowTimeline
                    title="Workflow Progress"
                    subtitle="The agent pipeline path for this run, including skip reasons and event history."
                    stages={(timeline?.stages ?? stages) as AgentStageResult[]}
                    events={timeline?.events}
                    showInspector
                    showEventFeed
                  />
                </div>
              </details>

              <details className="card mt-4">
                <summary className="cursor-pointer text-sm font-semibold text-[var(--color-text-secondary)] flex items-center gap-2">
                  <ChevronRight className="h-4 w-4 text-[var(--color-text-muted)]" />
                  Raw stage cards
                </summary>
                <div className="mt-4 space-y-2">
                  {stages.map((stage: AgentStageResult) => (
                    <StageCard key={stage.stage_name} stage={stage} showLLMMetrics={showLLMMetrics} />
                  ))}
                </div>
              </details>

              {showSummary && (
                <div className="card mt-4">
                  {summaryLoading ? (
                    <div className="flex justify-center py-4">
                      <LoadingSpinner size="sm" />
                    </div>
                  ) : summaryError ? (
                    <div className="space-y-1">
                      <p className="text-sm text-amber-300">The AI report could not be loaded.</p>
                      <p className="text-xs text-[var(--color-text-muted)]">
                        {summaryStage?.error || 'The summary endpoint returned an error for this pipeline run.'}
                      </p>
                    </div>
                  ) : !summary ? (
                    <div className="space-y-1">
                      <p className="text-sm text-[var(--color-text-muted)]">No AI summary available yet.</p>
                      {summaryStage?.status === 'failed' && summaryStage.error && (
                        <p className="text-xs text-[var(--color-text-muted)]">{summaryStage.error}</p>
                      )}
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {summary.executive_panel ? (
                        <ExecutiveSummaryPanel panel={summary.executive_panel as unknown as import('@/services/runIntelligenceService').ExecutivePanel} />
                      ) : (
                        <div className="bg-[var(--color-bg-secondary)]/80 rounded-lg p-3">
                          <h4 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase mb-1">Executive Summary</h4>
                          <p className="text-sm text-[var(--color-text)] leading-relaxed">{summary.executive_summary}</p>
                        </div>
                      )}
                      <StructuredReportDetail markdown={summary.markdown_report} hasPanel={!!summary.executive_panel} />
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
