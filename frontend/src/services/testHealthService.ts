import { api } from './api'

// ── Types ────────────────────────────────────────────────────────────────────

export interface TestHealthViolation {
  pattern: string
  severity: string
  occurrences: number
}

export interface TestHealthFinding {
  test_case_id: string
  test_name: string
  health_score: number
  violations: TestHealthViolation[]
  critical_count: number
  warning_count: number
  recommendation: string
  anti_patterns: string[]
}

export interface TestHealthResponse {
  run_id: string
  total_analyzed: number
  with_violations: number
  avg_health_score: number | null
  findings: TestHealthFinding[]
}

export interface FlakyCoachEntry {
  test_fingerprint: string
  test_name: string
  suite_name: string | null
  failure_rate: number
  total_runs: number
  failed_runs: number
  flaky_since: string | null
  last_failure_at: string | null
  quarantine_recommendation: 'QUARANTINE' | 'INVESTIGATE' | 'MONITOR' | 'HEALTHY'
  stabilization_actions: string[]
  impact_score: number
  status_history: string[]
  // FLK-P1 intermittency signals (read-time; null when no granular window).
  status_volatility?: number | null
  error_signature_diversity?: number | null
  stack_trace_diversity?: number | null
  in_run_retry_rate?: number | null
  intermittency_label?: string | null
  // FLK-P2 Wilson 95% confidence band on the failure ratio (null for
  // manual-triage entries and not-yet-recomputed cached rows).
  flaky_confidence_low?: number | null
  flaky_confidence_high?: number | null
  // FLK-P3 ML flakiness-confidence in [0,1] (null when no trained model).
  is_flaky_confidence?: number | null
  // FLK-P4 likely-cause attribution (null when no granular window).
  flaky_likely_cause?: string | null
  flaky_likely_cause_code?: string | null
  // FLK-P5 granular step-level surgical attribution (null when no step snapshot).
  failing_step?: string | null
  failing_step_detail?: string | null
}

export interface FlakyCoachResponse {
  project_id: string
  total_flaky: number
  quarantine_candidates: number
  entries: FlakyCoachEntry[]
}

// ── Service ──────────────────────────────────────────────────────────────────

export const testHealthService = {
  async getRunTestHealth(runId: string): Promise<TestHealthResponse> {
    const { data } = await api.get<TestHealthResponse>(`/api/v1/runs/${runId}/test-health`)
    return data
  },

  async getFlakyCoach(projectId: string, days = 30, limit = 50): Promise<FlakyCoachResponse> {
    const { data } = await api.get<FlakyCoachResponse>(
      `/api/v1/projects/${projectId}/flaky-coach`,
      { params: { days, limit } },
    )
    return data
  },

  async refreshFlakyCoach(projectId: string, days = 30): Promise<{ status: string; flaky_tests_found: number }> {
    const { data } = await api.post<{ status: string; flaky_tests_found: number }>(
      `/api/v1/projects/${projectId}/flaky-coach/refresh`,
      null,
      { params: { days } },
    )
    return data
  },
}
