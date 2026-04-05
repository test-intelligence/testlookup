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

export interface AIQualityDashboard {
  agreement: { total_feedback: number; correct: number; partially_correct: number; incorrect: number; agreement_rate: number | null; period_days: number } | null;
  drift: { task_type: string; current: { accuracy: number | null; f1_score: number | null }; previous: { accuracy: number | null }; drift: number | null; drift_direction: string } | null;
  recent_eval_runs: AIEvalRun[];
  model_versions: Array<{ id: string; track: string; model_name: string; status: string; eval_accuracy: number | null; created_at: string }>;
  feedback_summary: Record<string, unknown> | null;
}

export async function getDashboard(days?: number): Promise<AIQualityDashboard> {
  const { data } = await api.get<AIQualityDashboard>('/api/v1/ai-eval/dashboard', {
    params: days ? { days } : undefined,
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
