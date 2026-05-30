/**
 * Shared types for the /agents Direction-C compute graph.
 *
 * These mirror the data shape the design handoff specifies (`design/shared.jsx`
 * in the handoff). The mapping from the codebase's existing
 * ``pipelineStages``/``AgentStageResult`` shape lives in
 * ``mapPipelineToComputeGraph`` so the renderer below never has to know what
 * the backend originally returned.
 */

export type StageStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export type ComputeStageId =
  | 'ingestion'
  | 'anomaly'
  | 'rca'
  | 'cluster'
  | 'triage'
  | 'flaky'
  | 'health'
  | 'summary'
  | 'release'

export type StageGlyph =
  | 'database'
  | 'alert'
  | 'bot'
  | 'layers'
  | 'bug'
  | 'warn'
  | 'stethoscope'
  | 'file'
  | 'shield'

export interface ComputeStage {
  id: ComputeStageId
  name: string
  desc: string
  status: StageStatus
  /** Human label, e.g. ``12s``, ``1m 22s``. */
  dur?: string | null
  /** Milliseconds since the run started — used to drive the running progress bar. */
  startMs?: number | null
  endMs?: number | null
  etaMs?: number | null
  metrics: {
    confidence?: string | null
    evidence?: number | null
    tokens?: string | number | null
    cost?: string | null
  }
  glyph: StageGlyph
  skipReason?: string | null
  error?: string | null
  /** Optional structured output / stack-trace text rendered in the rail's blob panel. */
  output?: string | null
  /** Optional log tail rendered in the rail's Logs tab. */
  logs?: string | null
}

export type DecisionId = 'route_analysis_mode'

export interface ComputeDecision {
  id: DecisionId
  label: string
  /** Resolved branch name, e.g. ``llm``. Null while the decision is still pending. */
  chosen: string | null
  alternatives: string[]
  /** Timestamp the decision resolved (``HH:MM:SS.ff``). */
  at?: string | null
  rationale?: string | null
}

export interface ComputeEdgeEvent {
  kind: 'started' | 'completed' | 'failed' | 'retry' | 'decision'
  /** Stage id, or the decision id when kind === 'decision'. */
  stage: string
  at: string
  what: string
}

/** Edge style triad used by the SVG renderer and the edge-label chips. */
export type EdgeKind = 'default' | 'chosen' | 'notchosen' | 'dashed'

export interface ComputeEdge {
  from: ComputeStageId | DecisionId
  to: ComputeStageId | DecisionId
  kind: EdgeKind
  /** Optional label rendered as an absolutely-positioned div on top of the edge. */
  label?: string | null
}

/** Currently-selected canvas element — a stage id or a decision id. */
export type SelectedId = ComputeStageId | DecisionId | null
