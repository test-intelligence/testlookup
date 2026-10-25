/**
 * W16 AI evaluation (AGT-09 / AGT-13): the wire shapes of the new and grown
 * `/api/v1/ai-eval/*` answers, and the server vocabularies the page renders.
 *
 * Every enum-like field is a plain `string` on the wire (an unknown value
 * renders as itself). The constants below mirror
 * `backend/app/services/ai_eval_vocab.py` (and `eval_verdict.EvalVerdict`,
 * `eval_gate_service.DEFAULT_AGENT_STACK_GATES`,
 * `tier_comparison_service.TIER_ORDER`); `backend/tests/test_ai_eval_vocab_frontend_parity.py`
 * reads them as TEXT, so each stays a literal (`as const` array or object).
 *
 * Every field a W16 server added is OPTIONAL here, so old fixtures and mocks
 * still type-check. A list answer is `{ items, total }`: read it as
 * `Array.isArray(data?.items) ? data.items : []`.
 */
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import type { AIEvalRun } from '@/services/aiEvalService'

// ── Mirrored vocabularies ────────────────────────────────────────────────────

/** `label_agreement` = the legacy metric (never recomputed); `replay` = a candidate replay. */
export const METRIC_KINDS = ['label_agreement', 'replay'] as const
export type MetricKind = (typeof METRIC_KINDS)[number]

export const METRIC_LABELS: Record<MetricKind, string> = {
  'label_agreement': 'Legacy label-agreement metric',
  'replay': 'Replay of the candidate',
}

/** Legacy rows read `completed`. */
export const RUN_STATUSES = ['queued', 'running', 'completed', 'partial', 'failed'] as const
export type RunStatus = (typeof RUN_STATUSES)[number]
export const TERMINAL_RUN_STATUSES = ['completed', 'partial', 'failed'] as const

/** A run's `error_code` → its sentence (the server fills `error_text` from the same table; a fallback for an old answer). */
export const RUN_ERROR_SENTENCES: Record<string, string> = {
  'worker_lost': "The evaluation stopped part-way (no progress for 15 minutes); the scored items are kept.",
  'dataset_missing': "The dataset was deleted before the evaluation ran.",
  'budget_exhausted': "The project's LLM call budget ran out; the remaining items were not replayed.",
  'project_missing': "The project was deleted before the evaluation ran.",
  'run_error': "The evaluation failed with an internal error; the items already replayed are kept.",
  'candidate_changed': "The AI configuration changed after the evaluation was queued; queue it again.",
}

export const ITEM_STATUSES = ['correct', 'incorrect', 'unscored', 'error'] as const
export type ItemStatus = (typeof ITEM_STATUSES)[number]

export const CANDIDATE_TIERS = ['rules', 'ml', 'llm'] as const
export type CandidateTier = (typeof CANDIDATE_TIERS)[number]

/** The server's `EvalVerdict` values and the words the page shows for them. */
export const EVAL_VERDICT_LABELS: Record<string, string> = {
  'pass': 'PASS',
  'fail': 'FAIL',
  'insufficient_samples': 'Not enough evidence',
}

/** The four pre-release gates of the agent stack (`DEFAULT_AGENT_STACK_GATES`). */
export const DEFAULT_GATES = [
  { task_type: 'classification', agent_name: 'AnalysisAgent', label: 'Classification' },
  { task_type: 'root_cause', agent_name: 'AnalysisAgent', label: 'Root Cause' },
  { task_type: 'duplicate_detection', agent_name: 'DefectPromotionAgent', label: 'Duplicate Detection' },
  { task_type: 'release_decision', agent_name: 'ReleaseRiskAgent', label: 'Release Decision' },
] as const
export type DefaultGate = (typeof DEFAULT_GATES)[number]

/** G2 tiers (`TierComparisonRequest.incumbent_tier` / `candidate_tier`). */
export const TIER_ORDER = ['deterministic', 'slm', 'llm', 'auto'] as const

export const SHADOW_VERDICTS = ['incumbent', 'candidate', 'both', 'neither'] as const
export type ShadowVerdict = (typeof SHADOW_VERDICTS)[number]

/** The labelling queue's four buttons. */
export const SHADOW_VERDICT_LABELS: Record<ShadowVerdict, string> = {
  'incumbent': 'Incumbent correct',
  'candidate': 'Candidate correct',
  'both': 'Both correct',
  'neither': 'Neither',
}

export const SHADOW_LABEL_STATUSES = ['pending', 'human_labelled', 'golden_match'] as const
export type ShadowLabelStatus = (typeof SHADOW_LABEL_STATUSES)[number]

export const SHADOW_AGENT_LABELS: Record<string, string> = {
  'agent.root_cause_analysis.v1': 'Root-cause analysis',
  'agent.summary.v1': 'Run summary',
}

/** Whether a run still moves (the runs list and the run read poll while one does). */
export function isEvalRunActive(run: { status?: string | null } | null | undefined): boolean {
  return run?.status === 'queued' || run?.status === 'running'
}

/** The runs list's poll: REALTIME while a listed run is queued / running, else off.
 *  Never throws on an answer without `items` (a catch-all mock, an old server). */
export function evalRunsRefreshInterval(latest: { items?: unknown } | null | undefined): number {
  const items = latest?.items
  return Array.isArray(items) && items.some((run) => isEvalRunActive(run as { status?: string })) ? REFRESH_INTERVALS.REALTIME : 0
}

/** One run's poll: the same rule. */
export function evalRunRefreshInterval(latest: { status?: string | null } | null | undefined): number {
  return isEvalRunActive(latest) ? REFRESH_INTERVALS.REALTIME : 0
}

// ── AGT-09 (b): candidates, replay runs, items ───────────────────────────────

/** POST `/runs/evaluate/{dataset_id}` body `candidate`: the model and prompt are ASSERTIONS. */
export interface EvalCandidate {
  tier: string
  model?: string | null
  prompt_version?: string | null
}

/** What a run actually replays (`ai_eval_runs.candidate`). */
export interface EvalCandidateResolved {
  tier: string
  model?: string | null
  provider?: string | null
  prompt_version?: string | null
  offline_mode: boolean
  requested: EvalCandidate
}

export interface CandidateTierOption {
  tier: string
  available: boolean
  /** The TIER_UNAVAILABLE sentence when unavailable. */
  reason?: string | null
  model?: string | null
  provider?: string | null
}

/** GET `/candidate-options` (ADMIN). */
export interface CandidateOptions {
  tiers: CandidateTierOption[]
  prompt_version?: string | null
  offline_mode: boolean
  max_items: number
  supported_tiers_by_task: Record<string, string[]>
}

export interface CandidateEvaluationAccepted {
  run_id: string
  status: string
}

/** One replay metric; `measured: false` / `value: null` = not measured (never 0 %). */
export interface EvalMetric {
  name: string
  value: number | null
  n: number
  measured: boolean
  /** score | sub_check */
  kind: string
}

/** GET `/runs`. */
export interface EvalRunList {
  items: AIEvalRun[]
  total: number
}

/** One replayed item (GET `/runs/{id}/items`, ADMIN). Every value is rendered as text. */
export interface EvalItem {
  item_index: number
  item_key?: string | null
  status: string
  error_code?: string | null
  error_text?: string | null
  input?: Record<string, unknown>
  expected: Record<string, unknown>
  output?: Record<string, unknown> | null
  score?: Record<string, unknown>
  latency_ms?: number | null
  tokens?: number | null
  mode_used?: string | null
}

export interface EvalItemList {
  items: EvalItem[]
  total: number
  /** By ITEM_STATUSES over the whole run. */
  counts: Record<string, number>
}

// ── AGT-09 (a): agent stack, manifests, G2 / G3 history ─────────────────────

/** An `AIEvalGateRunResponse` row (agent-stack history, or G2 history when `gate_type` is `tier_comparison`). */
export interface GateRun {
  id: string
  change_id: string
  status: string
  manifest_checksum_sha256: string
  manifest: Record<string, unknown>
  gate_results: Array<Record<string, unknown>>
  blocking_gates: Array<Record<string, unknown>>
  version_changes: Array<Record<string, unknown>>
  gate_type?: string | null
  project_id?: string | null
  agent_id?: string | null
  baseline_tier?: string | null
  candidate_tier?: string | null
  sample_count?: number | null
  evaluated_by?: string | null
  evaluated_at: string
}

export interface GateRunList {
  items: GateRun[]
  total: number
}

/** One gate of the agent-stack answer: a pre-release gate result. */
export interface AgentStackGateEntry {
  status: string
  task_type: string
  agent_name: string
  current_metrics?: Record<string, number | null> | null
  baseline_metrics?: Record<string, unknown> | null
  rule_results?: Array<{ rule: string; passed: boolean; detail: string; severity?: string | null }>
  evaluated_at?: string
  metric_kind?: string
}

/** POST `/agent-stack-release-gate` (ADMIN). */
export interface AgentStackGateResult {
  status: string
  manifest: Record<string, unknown> & { manifest_checksum_sha256?: string }
  gate_results: AgentStackGateEntry[]
  blocking_gates: Array<{ task_type: string; agent_name: string; status: string }>
  version_changes: Array<Record<string, unknown>>
  evaluated_at: string
  gate_run_id?: string
}

/** GET `/gates/{checksum}`. */
export interface EvalManifest {
  eval_manifest_checksum: string
  source: string
  status: string
  manifest: Record<string, unknown>
  gate_run_id?: string | null
  evaluated_at?: string | null
}

/** POST `/tier-comparison` body (the page sends `pairs: []`; the live labels are its evidence). */
export interface TierComparisonBody {
  project_id: string
  agent_id: string
  capability: string
  incumbent_tier: string
  candidate_tier: string
  delta?: number
  pairs: Array<Record<string, unknown>>
  include_live_labels?: boolean
  persist?: boolean
}

/** One G2 metric with its Wilson / paired 95 % bounds. */
export interface TierComparisonMetric {
  name: string
  value: number | null
  n: number
  ci_low: number | null
  ci_high: number | null
  kind: string
}

export interface TierComparisonScoredPair {
  sample_id: string
  incumbent_correct: boolean
  candidate_correct: boolean
  incumbent_valid: boolean
  candidate_valid: boolean
  /** corpus | human_labelled | golden_match */
  provenance?: string
  [key: string]: unknown
}

/** The answer of POST `/tier-comparison`. */
export interface TierComparisonResult {
  verdict: string
  status?: string
  reason: string
  sample_count: number
  smoke_mode: boolean
  live_labelled_pairs?: number
  manifest_checksum?: string
  manifest?: Record<string, unknown>
  per_gate: { G2: TierComparisonMetric[] }
  regressions?: Array<Record<string, unknown>>
  scored_pairs: TierComparisonScoredPair[]
  gate_run_id?: string
}

/** One G3 row of GET `/reviewer-quality/runs` (no observation arrays). */
export interface ReviewerQualityRun {
  id: string
  project_id: string
  agent_id: string
  /** scheduled | observation_batch */
  source: string
  status: string
  window_started_at: string
  window_ended_at: string
  evaluated_at: string
  metrics: Array<Record<string, unknown>>
  regressions: Array<Record<string, unknown>>
  semantic_sample_counts: Record<string, unknown>
  clean_sample_count: number
  human_outcome_count: number
  deterministic_recall?: number | null
  second_model_recall?: number | null
  recall_delta?: number | null
  false_flag_rate?: number | null
  false_omission_rate?: number | null
  auto_disable_eligible: boolean
  auto_disable_applied: boolean
}

export interface ReviewerQualityRunList {
  items: ReviewerQualityRun[]
  total: number
}

// ── AGT-13: shadow pairs ─────────────────────────────────────────────────────

/** Where a pair came from, resolved inside its project (`null` = no longer available). */
export interface ShadowPairSource {
  /** test_case | test_run */
  kind: string
  id: string
  label?: string | null
  run_id?: string | null
  build_number?: string | null
  /** Built server-side from ids only. */
  href?: string | null
}

export interface ShadowPairLabelValue {
  verdict: string
  failure_category?: string | null
  notes?: string | null
  /** human | feedback */
  source?: string
  feedback_id?: string | null
}

export interface ShadowPair {
  id: string
  project_id: string
  agent_id: string
  agent_label: string
  sample_key: string
  incumbent_tier: string
  candidate_tier: string
  incumbent_output: Record<string, unknown>
  candidate_output: Record<string, unknown>
  incumbent_tokens: number
  candidate_tokens: number
  label_status: string
  label?: ShadowPairLabelValue | null
  labelled_by_name?: string | null
  labelled_at?: string | null
  created_at?: string | null
  source?: ShadowPairSource | null
  /** Pending pairs only: created_at + 30 days. */
  expires_at?: string | null
}

/** GET `/shadow-pairs`. */
export interface ShadowPairList {
  items: ShadowPair[]
  total: number
  /** The project's pending pairs, any agent (the Tiers tab badge). */
  pending_total: number
  /** False = the project has never produced a pair (no live model tier). */
  live_tier_pairs_seen: boolean
}

/** POST `/shadow-pairs/{id}/label` body. */
export interface ShadowPairLabel {
  verdict: string
  failure_category?: string | null
  notes?: string | null
}
