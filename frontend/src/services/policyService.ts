import { api } from './api';

// ── Types ───────────────────────────────────────────────────────────────────

export interface PolicyThresholds {
  go_threshold: number;
  no_go_threshold: number;
  pass_rate_minimum: number;
  pass_rate_hard_floor_factor: number;
}

export interface PolicyDimensionWeights {
  user_impact: number;
  env_sensitivity: number;
  reproducibility: number;
  regression_likely: number;
  hist_recurrence: number;
  blast_radius: number;
  diagnosis_conf: number;
}

export interface PolicyRule {
  id: string;
  name: string;
  type: string;
  enabled: boolean;
  params: Record<string, unknown>;
}

export interface PolicyPassRateBands {
  orange_min: number;  // below this → red
  yellow_min: number;  // [orange_min, yellow_min) → orange
  green_min: number;   // [yellow_min, green_min) → yellow; ≥ green_min → green
}

export interface PolicyHardCaps {
  /** Active P0 defects allowed before the band is downgraded one step. */
  max_p0_defects: number;
  /** Flaky-test count allowed before the band is downgraded one step. */
  max_flaky_count: number;
  /** New failures in the last 24h allowed before downgrade. */
  max_new_failures_24h: number;
}

export interface PolicyDocument {
  schema_version: number;
  thresholds: PolicyThresholds;
  dimension_weights: PolicyDimensionWeights;
  rules: PolicyRule[];
  /** Optional on read — older rows omit it; the backend fills defaults on write. */
  pass_rate_bands?: PolicyPassRateBands;
  hard_caps?: PolicyHardCaps;
}

export interface PolicySummary {
  id: string;
  project_id: string | null;
  version: number;
  name: string;
  description: string | null;
  rules: PolicyDocument;
  is_active: boolean;
  is_draft: boolean;
  created_by: string;
  activated_by: string | null;
  activated_at: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface RuleEvaluation {
  rule_id: string;
  rule_name: string;
  rule_type: string;
  passed: boolean;
  action: string;
  message: string;
  actual_value: number | null;
  threshold_value: number | null;
}

export interface SimulateResponse {
  original_recommendation: string;
  simulated_recommendation: string;
  original_composite: number;
  simulated_composite: number;
  rule_evaluations: RuleEvaluation[];
  diff_summary: string;
}

// ── Default values ──────────────────────────────────────────────────────────

export const DEFAULT_THRESHOLDS: PolicyThresholds = {
  go_threshold: 20,
  no_go_threshold: 55,
  pass_rate_minimum: 90,
  pass_rate_hard_floor_factor: 0.7,
};

export const DEFAULT_WEIGHTS: PolicyDimensionWeights = {
  user_impact: 0.25,
  env_sensitivity: 0.10,
  reproducibility: 0.15,
  regression_likely: 0.20,
  hist_recurrence: 0.10,
  blast_radius: 0.15,
  diagnosis_conf: 0.05,
};

export const DEFAULT_PASS_RATE_BANDS: PolicyPassRateBands = {
  orange_min: 90,
  yellow_min: 95,
  green_min: 99,
};

export const DEFAULT_HARD_CAPS: PolicyHardCaps = {
  max_p0_defects: 0,
  max_flaky_count: 10,
  max_new_failures_24h: 20,
};

// ── API ─────────────────────────────────────────────────────────────────────

export async function listPolicies(params?: {
  project_id?: string;
  is_active?: boolean;
}): Promise<PolicySummary[]> {
  const { data } = await api.get<PolicySummary[]>('/api/v1/release-gate-policies', { params });
  return data;
}

export async function getPolicy(id: string): Promise<PolicySummary> {
  const { data } = await api.get<PolicySummary>(`/api/v1/release-gate-policies/${id}`);
  return data;
}

export async function getEffectivePolicy(projectId: string): Promise<PolicySummary | null> {
  const { data } = await api.get<PolicySummary | null>(`/api/v1/release-gate-policies/effective/${projectId}`);
  return data;
}

export async function createPolicy(payload: {
  project_id?: string | null;
  name: string;
  description?: string;
  rules?: PolicyDocument;
}): Promise<PolicySummary> {
  const { data } = await api.post<PolicySummary>('/api/v1/release-gate-policies', payload);
  return data;
}

export async function updatePolicy(id: string, payload: {
  name?: string;
  description?: string;
  rules?: PolicyDocument;
}): Promise<PolicySummary> {
  const { data } = await api.patch<PolicySummary>(`/api/v1/release-gate-policies/${id}`, payload);
  return data;
}

export async function publishPolicy(id: string): Promise<PolicySummary> {
  const { data } = await api.post<PolicySummary>(`/api/v1/release-gate-policies/${id}/publish`);
  return data;
}

export async function deactivatePolicy(id: string): Promise<PolicySummary> {
  const { data } = await api.post<PolicySummary>(`/api/v1/release-gate-policies/${id}/deactivate`);
  return data;
}

export async function simulatePolicy(runId: string, policyDocument: PolicyDocument): Promise<SimulateResponse> {
  const { data } = await api.post<SimulateResponse>('/api/v1/release-gate-policies/simulate', {
    run_id: runId,
    policy_document: policyDocument,
  });
  return data;
}

export async function getPolicyHistory(projectId: string): Promise<PolicySummary[]> {
  const { data } = await api.get<PolicySummary[]>(`/api/v1/release-gate-policies/history/${projectId}`);
  return data;
}

export async function getSystemPolicyHistory(): Promise<PolicySummary[]> {
  const { data } = await api.get<PolicySummary[]>('/api/v1/release-gate-policies/system-history');
  return data;
}
