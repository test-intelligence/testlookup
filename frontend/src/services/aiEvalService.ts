import { api } from './api';

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

export async function listEvalRuns(params?: { dataset_id?: string; task_type?: string; limit?: number }): Promise<AIEvalRun[]> {
  const { data } = await api.get<AIEvalRun[]>('/api/v1/ai-eval/runs', { params });
  return data;
}

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
  status: 'PASS' | 'FAIL' | 'WARN' | 'NO_BASELINE';
  task_type: string;
  agent_name: string;
  current_metrics: { accuracy: number | null; precision: number | null; recall: number | null; f1_score: number | null; total: number; correct: number } | null;
  baseline_metrics: { accuracy: number | null; precision: number | null; recall: number | null; f1_score: number | null; min_accuracy: number; min_f1: number; max_regression_pct: number; prompt_version: string; model_name: string | null } | null;
  rule_results: EvalGateRuleResult[];
  evaluated_at: string;
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
}

export async function runPreReleaseGate(taskType: string, agentName: string, datasetId?: string): Promise<EvalGateResult> {
  const { data } = await api.post<EvalGateResult>('/api/v1/ai-eval/pre-release-gate', {
    task_type: taskType,
    agent_name: agentName,
    dataset_id: datasetId || null,
  });
  return data;
}

export async function setBaseline(params: { task_type: string; agent_name: string; prompt_version?: string; dataset_id?: string; min_accuracy?: number; min_f1?: number; max_regression_pct?: number }): Promise<Record<string, unknown>> {
  const { data } = await api.post('/api/v1/ai-eval/baselines', params);
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
