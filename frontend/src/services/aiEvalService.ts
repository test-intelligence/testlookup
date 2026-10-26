import { api } from './api';
import { getData, postData } from './http';
import type {
  AgentStackGateResult,
  CandidateEvaluationAccepted,
  CandidateOptions,
  EvalCandidate,
  EvalCandidateResolved,
  EvalItemList,
  EvalManifest,
  EvalMetric,
  EvalRunList,
  GateRunList,
  ReviewerQualityRunList,
  ShadowPair,
  ShadowPairLabel,
  ShadowPairList,
  TierComparisonBody,
  TierComparisonResult,
} from '@/types/aiEval';

export interface AIEvalDataset {
  id: string;
  name: string;
  description: string | null;
  task_type: string;
  item_count: number;
  is_active: boolean;
  created_by: string | null;
  created_at: string;
}

export interface AIEvalRun {
  id: string;
  dataset_id: string;
  model_name: string;
  task_type: string;
  precision: number | null;
  recall: number | null;
  f1_score: number | null;
  accuracy: number | null;
  agreement_rate: number | null;
  total_items: number;
  correct_items: number;
  fallback_used: boolean;
  evaluated_at: string;
  duration_ms: number | null;
  // W16 AGT-09 (b): every field optional (an old server, an old fixture).
  /** label_agreement | replay — missing = label_agreement. */
  metric_kind?: string;
  metric_label?: string;
  /** queued | running | completed | partial | failed — legacy rows: completed. */
  status?: string;
  error_code?: string | null;
  error_text?: string | null;
  candidate?: EvalCandidateResolved | null;
  project_id?: string | null;
  requested_by?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  heartbeat_at?: string | null;
  scored_items?: number;
  unscored_items?: number;
  error_items?: number;
  items_capped?: boolean;
  metrics?: EvalMetric[] | null;
  /** False when a replay scored nothing: render `—`, never 0 %. */
  measured?: boolean;
}

export interface LabelComposition {
  counts: Record<string, number>;
  fractions: Record<string, number>;
  total: number;
  human_label_count: number;
  pseudo_included: number;
  pseudo_dropped: number;
  pseudo_cap: number;
  pseudo_weight: number;
  cap_exceeded_to_fill_floor: boolean;
  bootstrap: boolean;
}

export interface LabelHealth {
  human_direct: number;
  human_indirect: number;
  human_label_total: number;
  llm_pseudo_candidates: number;
  human_share_of_pool: number | null;
  human_label_floor: number;
  meets_human_label_floor: boolean;
  ml_maturity: 'not_trained' | 'bootstrap_llm_imitating' | 'human_calibrated';
  last_trained_composition: LabelComposition | null;
  pseudo_label_cap: number;
  pseudo_label_weight: number;
}

export interface AIQualityDashboard {
  agreement: { total_feedback: number; correct: number; partially_correct: number; incorrect: number; agreement_rate: number | null; period_days: number } | null;
  drift: { task_type: string; current: { accuracy: number | null; f1_score: number | null }; previous: { accuracy: number | null }; drift: number | null; drift_direction: string } | null;
  recent_eval_runs: AIEvalRun[];
  model_versions: Array<{ id: string; track: string; model_name: string; status: string; eval_accuracy: number | null; created_at: string }>;
  feedback_summary: Record<string, unknown> | null;
  label_health: LabelHealth | null;
  eval_provenance?: {
    window_days: number;
    window_start: string;
    window_end: string;
    total_runs: number;
    stamped_runs: number;
    resolved_runs: number;
    missing_checksum_count: number;
    unresolvable_run_count: number;
    unresolvable_checksums: string[];
    has_unresolvable_checksums: boolean;
  } | null;
}

export interface DecisionReportEvalCycle {
  id: string;
  cycle_key: string;
  corpus_version: string;
  corpus_sha256: string;
  report_count: number;
  status: 'pass' | 'warn' | 'fail';
  metrics: Record<string, unknown>;
  checks: Array<{ name: string; status: string; detail?: Record<string, unknown> }>;
  unavailable_metrics: string[];
  consecutive_passes: number;
  evaluated_by: string | null;
  evaluated_at: string | null;
}

export interface ReportEvalReadiness {
  status: 'ready' | 'not_ready';
  latest_status: 'pass' | 'warn' | 'fail' | 'missing';
  consecutive_passes: number;
  required_consecutive_passes: number;
  utility_rate: number | null;
  minimum_utility_rate: number;
  corpus_version: string | null;
  corpus_sha256: string | null;
  reasons: string[];
}

export async function getDashboard(days?: number): Promise<AIQualityDashboard> {
  const { data } = await api.get<AIQualityDashboard>('/api/v1/ai-eval/dashboard', {
    params: days ? { days } : undefined,
  });
  return data;
}

export async function listReportEvalCycles(params?: {
  corpus_version?: string;
  limit?: number;
}): Promise<DecisionReportEvalCycle[]> {
  const { data } = await api.get<DecisionReportEvalCycle[]>('/api/v1/ai-eval/report-cycles', { params });
  return data;
}

export async function getReportEvalReadiness(corpusVersion: string): Promise<ReportEvalReadiness> {
  const { data } = await api.get<ReportEvalReadiness>('/api/v1/ai-eval/report-cycles/readiness', {
    params: { corpus_version: corpusVersion },
  });
  return data;
}

export async function listDatasets(taskType?: string): Promise<AIEvalDataset[]> {
  const { data } = await api.get<AIEvalDataset[]>('/api/v1/ai-eval/datasets', {
    params: taskType ? { task_type: taskType } : undefined,
  });
  return data;
}

export async function createDatasetFromFeedback(name?: string): Promise<AIEvalDataset> {
  const { data } = await api.post<AIEvalDataset>('/api/v1/ai-eval/datasets/from-feedback', null, {
    params: name ? { name } : undefined,
  });
  return data;
}

export async function runEvaluation(datasetId: string): Promise<AIEvalRun> {
  const { data } = await api.post<AIEvalRun>(`/api/v1/ai-eval/runs/evaluate/${datasetId}`);
  return data;
}

/** GET runs: `{ items, total }` since W16 (was a bare list no client read). */
export const listEvalRuns = (q: {
  dataset_id?: string;
  task_type?: string;
  metric_kind?: string;
  status?: string;
  limit?: number;
} = {}) => getData<EvalRunList>('/api/v1/ai-eval/runs', { params: q });

export async function getDrift(taskType?: string, windowDays?: number): Promise<Record<string, unknown>> {
  const { data } = await api.get('/api/v1/ai-eval/drift', {
    params: { task_type: taskType || 'classification', window_days: windowDays || 7 },
  });
  return data;
}

// ── Phase 5: Pre-Release Evaluation Gate ────────────────────────────────────

export interface EvalGateRuleResult {
  rule: string;
  passed: boolean;
  detail: string;
  severity?: string | null;
}

export interface EvalGateResult {
  /** The server's EvalVerdict value: pass | fail | insufficient_samples (EVAL_VERDICT_LABELS). */
  status: string;
  task_type: string;
  agent_name: string;
  current_metrics: { accuracy: number | null; precision: number | null; recall: number | null; f1_score: number | null; total: number; correct: number } | null;
  baseline_metrics: { accuracy: number | null; precision: number | null; recall: number | null; f1_score: number | null; min_accuracy: number; min_f1: number; max_regression_pct: number; prompt_version: string; model_name: string | null } | null;
  rule_results: EvalGateRuleResult[];
  evaluated_at: string;
  // W16: optional (an old server answers without them).
  metric_kind?: string;
  candidate_run_id?: string | null;
  baseline_run_id?: string | null;
  dataset_id?: string | null;
}

export interface EvalBaseline {
  id: string;
  task_type: string;
  agent_name: string;
  prompt_version: string;
  model_name: string | null;
  baseline_accuracy: number | null;
  baseline_f1: number | null;
  min_accuracy: number;
  min_f1: number;
  max_regression_pct: number;
  created_at: string | null;
  // W16: optional (legacy rows / an old server).
  metric_kind?: string;
  eval_run_id?: string | null;
  dataset_id?: string | null;
}

/**
 * POST the pre-release gate. Without `candidateRunId` the body is today's
 * (the legacy label-agreement metric); with it the gate scores that replay run.
 * The caller renders a refusal's message in place (no shared toast).
 */
export async function runPreReleaseGate(
  taskType: string,
  agentName: string,
  datasetId?: string,
  candidateRunId?: string,
): Promise<EvalGateResult> {
  const { data } = await api.post<EvalGateResult>('/api/v1/ai-eval/pre-release-gate', {
    task_type: taskType,
    agent_name: agentName,
    dataset_id: datasetId || null,
    ...(candidateRunId ? { candidate_run_id: candidateRunId } : {}),
  }, { suppressToast: true });
  return data;
}

/** POST a baseline. With `eval_run_id` the metrics come from that completed replay run. */
export async function setBaseline(params: { task_type: string; agent_name: string; prompt_version?: string; dataset_id?: string; min_accuracy?: number; min_f1?: number; max_regression_pct?: number; eval_run_id?: string }): Promise<Record<string, unknown>> {
  const { data } = await api.post('/api/v1/ai-eval/baselines', params, { suppressToast: true });
  return data;
}

export async function listBaselines(taskType?: string): Promise<EvalBaseline[]> {
  const { data } = await api.get<EvalBaseline[]>('/api/v1/ai-eval/baselines', {
    params: taskType ? { task_type: taskType } : undefined,
  });
  return data;
}

export async function seedGoldenDatasets(): Promise<{ seeded: string[]; total: number }> {
  const { data } = await api.post<{ seeded: string[]; total: number }>('/api/v1/ai-eval/golden-datasets/seed');
  return data;
}

// ── W16 AGT-09 / AGT-13 ─────────────────────────────────────────────────────
// Every path is written out in full in its call (the SPA wire-contract guard
// reads the quoted literals). Writes suppress the shared toast: the component
// renders the server's `{ code, message }` sentence where the action was taken.

export const listAgentStackGateRuns = (q: { status?: string; limit?: number } = {}) =>
  getData<GateRunList>('/api/v1/ai-eval/agent-stack-release-gate/runs', { params: q });

export const runAgentStackGate = (body: { change_id: string }) =>
  postData<AgentStackGateResult>('/api/v1/ai-eval/agent-stack-release-gate', body, { suppressToast: true });

export const getEvalManifest = (checksum: string) =>
  getData<EvalManifest>(`/api/v1/ai-eval/gates/${encodeURIComponent(checksum)}`);

export const getCandidateOptions = () =>
  getData<CandidateOptions>('/api/v1/ai-eval/candidate-options');

export const evaluateCandidate = (
  datasetId: string,
  body: { candidate: EvalCandidate; project_id: string; limit?: number },
) =>
  postData<CandidateEvaluationAccepted>(
    `/api/v1/ai-eval/runs/evaluate/${encodeURIComponent(datasetId)}`,
    body,
    { suppressToast: true },
  );

export const getEvalRun = (runId: string) =>
  getData<AIEvalRun>(`/api/v1/ai-eval/runs/${encodeURIComponent(runId)}`);

export const listEvalRunItems = (runId: string, q: { status?: string; limit?: number; offset?: number } = {}) =>
  getData<EvalItemList>(`/api/v1/ai-eval/runs/${encodeURIComponent(runId)}/items`, { params: q });

export const runTierComparison = (body: TierComparisonBody) =>
  postData<TierComparisonResult>('/api/v1/ai-eval/tier-comparison', body, { suppressToast: true });

export const listTierComparisonRuns = (projectId: string, q: { agent_id?: string; limit?: number } = {}) =>
  getData<GateRunList>('/api/v1/ai-eval/tier-comparison/runs', { params: { project_id: projectId, ...q } });

export const listReviewerQualityRuns = (projectId: string, q: { limit?: number } = {}) =>
  getData<ReviewerQualityRunList>('/api/v1/ai-eval/reviewer-quality/runs', {
    params: { project_id: projectId, ...q },
  });

export const listShadowPairs = (
  projectId: string,
  q: { agent_id?: string; label_status?: string; limit?: number; offset?: number } = {},
) =>
  getData<ShadowPairList>('/api/v1/ai-eval/shadow-pairs', { params: { project_id: projectId, ...q } });

export const labelShadowPair = (pairId: string, body: ShadowPairLabel) =>
  postData<ShadowPair>(
    `/api/v1/ai-eval/shadow-pairs/${encodeURIComponent(pairId)}/label`,
    body,
    { suppressToast: true },
  );
