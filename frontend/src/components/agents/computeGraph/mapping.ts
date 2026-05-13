/**
 * Map the codebase's existing AgentStageResult shape (returned by
 * ``usePipelineStages``) into the handoff's ComputeStage shape used by the
 * compute graph renderer.
 *
 * Stage IDs are normalised to the 9 we render on the canvas. Any stage from
 * the backend that doesn't map to one of those is silently dropped — the
 * canvas is intentionally a curated view, not a passthrough.
 */
import type { AgentStageResult } from '@/types/agent'
import type {
  ComputeDecision,
  ComputeEdge,
  ComputeStage,
  ComputeStageId,
  StageGlyph,
  StageStatus,
} from './types'

/** Backend stage_name → canvas ComputeStageId. */
const STAGE_ID_MAP: Record<string, ComputeStageId> = {
  ingestion: 'ingestion',
  anomaly_detection: 'anomaly',
  analysis: 'rca',
  root_cause_analysis: 'rca',
  failure_clustering: 'cluster',
  cluster_review: 'cluster',
  triage: 'triage',
  flaky_sentinel: 'flaky',
  test_health: 'health',
  summary: 'summary',
  release_risk: 'release',
  release_decision: 'release',
}

const NAME: Record<ComputeStageId, string> = {
  ingestion: 'Ingestion',
  anomaly:   'Anomaly Detection',
  rca:       'Root Cause Analysis',
  cluster:   'Failure Clustering',
  triage:    'Defect Triage',
  flaky:     'Flaky Sentinel',
  health:    'Test Health',
  summary:   'Summary',
  release:   'Release Risk',
}

const DESC: Record<ComputeStageId, string> = {
  ingestion: 'Load, validate, and enrich the run data',
  anomaly:   'Compare against baselines and surface regressions',
  rca:       'ReAct investigation across logs, traces, and history',
  cluster:   'Group related failures into defect-shaped clusters',
  triage:    'Prepare Jira-ready defects and owner guidance',
  flaky:     'Detect recurring flaky or unstable tests',
  health:    'Inspect test-code anti-patterns and health risks',
  summary:   'Compose the executive and role-specific narrative',
  release:   'Turn the run into a go / conditional go / no-go call',
}

const GLYPH: Record<ComputeStageId, StageGlyph> = {
  ingestion: 'database',
  anomaly:   'alert',
  rca:       'bot',
  cluster:   'layers',
  triage:    'bug',
  flaky:     'warn',
  health:    'stethoscope',
  summary:   'file',
  release:   'shield',
}

/** Default fall-through stage for an id that the backend didn't return. */
function defaultStage(id: ComputeStageId): ComputeStage {
  return {
    id,
    name: NAME[id],
    desc: DESC[id],
    status: 'pending',
    metrics: {},
    glyph: GLYPH[id],
  }
}

/** Map backend StageStatus into handoff's StageStatus (``completed`` → ``done``). */
function mapStatus(s: AgentStageResult['status']): StageStatus {
  if (s === 'completed') return 'done'
  return s as StageStatus
}

function formatDuration(startedAt: string | null, completedAt: string | null): string | null {
  if (!startedAt) return null
  const start = new Date(startedAt).getTime()
  const end = completedAt ? new Date(completedAt).getTime() : Date.now()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null
  const seconds = Math.max(0, Math.round((end - start) / 1000))
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

function formatTokens(n: number | null | undefined): string | null {
  if (n == null) return null
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

function formatCost(n: number | null | undefined): string | null {
  if (n == null || n === 0) return null
  return `$${n.toFixed(n < 0.01 ? 4 : 2)}`
}

/** Convert one backend AgentStageResult into a ComputeStage. */
function mergeStage(base: ComputeStage, src: AgentStageResult): ComputeStage {
  return {
    ...base,
    status: mapStatus(src.status),
    dur: formatDuration(src.started_at, src.completed_at),
    startMs: src.started_at ? new Date(src.started_at).getTime() / 1000 : null,
    endMs: src.completed_at ? new Date(src.completed_at).getTime() / 1000 : null,
    metrics: {
      confidence: src.confidence_score != null ? `${src.confidence_score}%` : null,
      evidence: src.evidence_count ?? null,
      tokens: formatTokens(src.total_tokens),
      cost: formatCost(src.cost_usd),
    },
    skipReason: src.skipped_reason,
    error: src.error,
    output: src.result_data
      ? JSON.stringify(src.result_data, null, 2)
      : null,
  }
}

/**
 * Build the 9-stage compute graph from a backend stages list. Stages the
 * backend hasn't reported yet land as ``pending`` placeholders so the canvas
 * always renders the full pipeline.
 */
export function mapPipelineToComputeGraph(
  stages: AgentStageResult[],
): { stages: ComputeStage[]; decision: ComputeDecision | null; edges: ComputeEdge[] } {
  const byId: Map<ComputeStageId, ComputeStage> = new Map(
    (Object.keys(NAME) as ComputeStageId[]).map(id => [id, defaultStage(id)]),
  )

  for (const src of stages) {
    const id = STAGE_ID_MAP[src.stage_name]
    if (!id) continue
    const base = byId.get(id) ?? defaultStage(id)
    byId.set(id, mergeStage(base, src))
  }

  // Derive the analysis-mode decision from the analysis stage's
  // ``execution_path`` field (set by the analysis_router). If the field is
  // present it's authoritative; otherwise leave the decision pending.
  const analysisSrc = stages.find(s =>
    s.stage_name === 'analysis' || s.stage_name === 'root_cause_analysis',
  )
  const chosen = analysisSrc?.execution_path ?? null
  const decision: ComputeDecision | null = analysisSrc
    ? {
        id: 'route_analysis_mode',
        label: 'route_analysis_mode',
        chosen,
        alternatives: ['llm', 'ml', 'rules', 'heuristic'],
        at: analysisSrc.started_at ?? null,
        rationale: analysisSrc.route_rationale ?? null,
      }
    : null

  // Edge set — fixed topology. ``chosen`` flag is computed from the
  // decision's chosen branch so the renderer can colour the right edge.
  const decisionChosenIsHeuristic = chosen === 'rules' || chosen === 'heuristic'
  const decisionChosenIsLlm = chosen === 'llm' || chosen === 'ml' || chosen === null
  const edges: ComputeEdge[] = [
    { from: 'ingestion', to: 'anomaly', kind: 'default' },
    { from: 'anomaly',   to: 'route_analysis_mode', kind: 'default' },
    {
      from: 'route_analysis_mode', to: 'rca',
      kind: decisionChosenIsLlm ? 'chosen' : 'notchosen',
      label: 'llm',
    },
    {
      from: 'route_analysis_mode', to: 'cluster',
      kind: decisionChosenIsLlm ? 'chosen' : 'notchosen',
      label: 'parallel',
    },
    {
      from: 'route_analysis_mode', to: 'triage',
      kind: decisionChosenIsHeuristic ? 'chosen' : 'notchosen',
      label: 'heuristic',
    },
    { from: 'rca',     to: 'summary', kind: 'dashed' },
    { from: 'cluster', to: 'flaky',   kind: 'default' },
    { from: 'flaky',   to: 'health',  kind: 'dashed' },
    { from: 'health',  to: 'summary', kind: 'dashed' },
    { from: 'summary', to: 'release', kind: 'dashed' },
  ]

  // If the heuristic branch was chosen, mark triage as something other than
  // skipped — it's actually the path that ran.
  if (decisionChosenIsHeuristic) {
    const triage = byId.get('triage')
    if (triage && triage.status === 'pending') {
      byId.set('triage', { ...triage, status: 'running' })
    }
  }

  return {
    stages: Array.from(byId.values()),
    decision,
    edges,
  }
}
