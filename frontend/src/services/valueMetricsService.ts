import { api } from './api'
import type {
  AssumptionsRead,
  AssumptionsSource,
  AssumptionsWrite,
  ValueAssumptions,
  ValueMethodology,
  ValueMetrics,
} from '@/types/valueMetrics'

// Re-export so existing importers (hook, workflowPresets) keep working — the
// contract types now live in @/types/valueMetrics.
export type { ValueMetrics } from '@/types/valueMetrics'

/**
 * Normalize the assumptions GET/PUT response.
 *
 * CONTRACT RECONCILIATION NOTE: the pinned contract says the GET may return
 * either the nested shape `{assumptions: {…3 fields}, source}` or the flat
 * shape `{triage_minutes_per_failure, …, source}` — the backend is being
 * built in parallel, so we accept both here. Missing/non-numeric fields fall
 * back to the caller-provided current values (from the main value-metrics
 * response) rather than invented client-side defaults. Once the backend
 * lands, tighten this to the one real shape.
 */
export function normalizeAssumptions(raw: unknown, fallback: ValueAssumptions): AssumptionsRead {
  const obj = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const nested =
    obj.assumptions && typeof obj.assumptions === 'object'
      ? (obj.assumptions as Record<string, unknown>)
      : obj
  const num = (v: unknown, fb: number): number =>
    typeof v === 'number' && Number.isFinite(v) ? v : fb
  const source: AssumptionsSource = obj.source === 'custom' ? 'custom' : 'default'
  return {
    assumptions: {
      triage_minutes_per_failure: num(nested.triage_minutes_per_failure, fallback.triage_minutes_per_failure),
      blocked_run_wait_minutes: num(nested.blocked_run_wait_minutes, fallback.blocked_run_wait_minutes),
      defect_filing_minutes: num(nested.defect_filing_minutes, fallback.defect_filing_minutes),
    },
    source,
  }
}

export const valueMetricsService = {
  /**
   * Main value-metrics payload. `days` drives the legacy flat counters,
   * `months` (contract default 6) drives the hours-saved monthly series.
   */
  get: (projectId?: string, days = 30, months = 6) =>
    api.get<ValueMetrics>('/api/v1/value-metrics', {
      params: { ...(projectId ? { project_id: projectId } : {}), days, months },
    }).then(r => r.data),

  /** Methodology legs/formulas/caveats — "show the math" (US-12.1 AC). */
  getMethodology: () =>
    api.get<ValueMethodology>('/api/v1/value-metrics/methodology').then(r => r.data),

  /** Per-project assumptions; response shape normalized (see above). */
  getAssumptions: (projectId: string, fallback: ValueAssumptions) =>
    api.get<unknown>(`/api/v1/projects/${projectId}/value-metrics/assumptions`)
      .then(r => normalizeAssumptions(r.data, fallback)),

  /**
   * Partial update (QA_LEAD+). Send only the fields being changed; bounds
   * 0 < x <= 480 are enforced client-side before calling this too.
   */
  putAssumptions: (projectId: string, patch: AssumptionsWrite, current: ValueAssumptions) =>
    api.put<unknown>(`/api/v1/projects/${projectId}/value-metrics/assumptions`, patch)
      .then(r => normalizeAssumptions(r.data, current)),

  exportUrl: (projectId?: string, days = 30) => {
    const params = new URLSearchParams({ days: String(days) })
    if (projectId) params.set('project_id', projectId)
    return `/api/v1/value-metrics/export?${params}`
  },
}
