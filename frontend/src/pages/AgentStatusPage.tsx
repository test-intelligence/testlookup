import { useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  Activity, AlertTriangle, Bot, CheckCircle, ChevronDown, ChevronRight,
  Clock, FileText, GitBranch, Layers, RefreshCw, Shield, Stethoscope, XCircle, Zap,
} from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import ExecutiveSummaryPanel from '@/components/ai/ExecutiveSummaryPanel'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } from '@/hooks/useAgentRuns'
import { useAIConfig } from '@/hooks/useAIConfig'
import agentService from '@/services/agentService'
import type { ActiveLiveRun, AgentPipelineRun, AgentStageResult } from '@/types/agent'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
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
  failed: 'text-red-400',
  skipped: 'text-[var(--color-text-muted)]',
}

const STATUS_BG: Record<string, string> = {
  pending: 'bg-[var(--color-bg-hover)]',
  running: 'bg-[var(--color-bg-secondary)]/60 border border-[var(--color-border-light)]',
  completed: 'bg-emerald-900/20 border border-emerald-700/30',
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
      <div className="flex items-center gap-2 mb-2">
        <Activity className="w-4 h-4 text-[var(--color-text)] animate-pulse" />
        <span className="text-sm font-semibold text-[var(--color-text)]">Build {run.build_number}</span>
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

// ── Main page ──────────────────────────────────────────────────

const MODE_BADGE: Record<string, { label: string; colour: string }> = {
  llm:   { label: 'LLM Mode',   colour: 'bg-indigo-900/30 text-indigo-400' },
  ml:    { label: 'ML Mode',    colour: 'bg-cyan-900/30 text-cyan-400' },
  rules: { label: 'Rules Mode', colour: 'bg-amber-900/30 text-amber-400' },
  auto:  { label: 'Auto Mode',  colour: 'bg-emerald-900/30 text-emerald-400' },
}

export default function AgentStatusPage() {
  const { runId } = useParams<{ runId?: string }>()
  const [selectedPipeline, setSelectedPipeline] = useState<string | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [showSummary, setShowSummary] = useState(false)
  const { data: aiConfig } = useAIConfig()
  const analysisMode = aiConfig?.analysis_mode ?? 'auto'
  const showLLMMetrics = analysisMode !== 'rules' && analysisMode !== 'ml'

  useProjectChangeRedirect('/agents', Boolean(runId))
  useProjectChangeReset(() => {
    setSelectedPipeline(null)
    setSelectedRunId(null)
    setShowSummary(false)
  })

  const { data: rawPipelines = [], isLoading: pipelinesLoading } = usePipelines(runId)
  // Sort descending by created_at client-side as a defensive guarantee
  const pipelines = [...rawPipelines].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )
  const { data: stages = [], isLoading: stagesLoading } = usePipelineStages(selectedPipeline)
  const { data: timeline } = usePipelineTimeline(selectedPipeline)
  const summaryStage = stages.find((stage) => stage.stage_name === 'summary')
  const { data: liveRuns = [] } = useActiveLiveRuns()
  const {
    data: summary,
    isLoading: summaryLoading,
    error: summaryError,
  } = useRunSummary(showSummary ? selectedRunId : null)

  const _handleTrigger = async (testRunId: string) => {
    try {
      await agentService.triggerPipeline(testRunId)
      toast.success('Pipeline triggered successfully')
    } catch {
      toast.error('Failed to trigger pipeline')
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Agent Pipeline"
        subtitle="Multi-agent test analysis: ingestion → anomaly detection → root-cause → summary → triage"
        actions={
          <span className={`text-xs px-2 py-1 rounded font-medium ${MODE_BADGE[analysisMode]?.colour ?? MODE_BADGE.auto.colour}`}>
            {MODE_BADGE[analysisMode]?.label ?? 'Auto Mode'}
          </span>
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
          {pipelinesLoading ? (
            <LoadingSpinner size="sm" />
          ) : pipelines.length === 0 ? (
            <p className="text-sm text-[var(--color-text-muted)]">No pipelines yet. Upload a test report to trigger one.</p>
          ) : (
            pipelines.map((p: AgentPipelineRun) => (
              <PipelineCard
                key={p.id}
                pipeline={p}
                selected={selectedPipeline === p.id}
                onSelect={() => {
                  setSelectedPipeline(p.id)
                  setSelectedRunId(p.test_run_id)
                  setShowSummary(false)
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

              <WorkflowTimeline
                title="Workflow Progress"
                subtitle="The agent pipeline path for this run, including skip reasons and event history."
                stages={(timeline?.stages ?? stages) as AgentStageResult[]}
                events={timeline?.events}
                showInspector
                showEventFeed
              />

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
