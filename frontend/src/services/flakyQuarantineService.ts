import { getData, postData } from './http'

export type QuarantineStatus =
  | 'DETECTED'
  | 'PROPOSED'
  | 'APPROVED'
  | 'QUARANTINED'
  | 'RECHECK_SCHEDULED'
  | 'RE_QUARANTINED'
  | 'RELEASED'
  | 'REJECTED'
  | 'EXPIRED'

export interface FlakyQuarantineRead {
  id: string
  project_id: string
  test_fingerprint: string
  test_name: string | null
  suite_name: string | null
  status: QuarantineStatus
  detection_method: string
  flip_rate: number | null
  flip_window_size: number | null
  pass_count: number | null
  fail_count: number | null
  detected_at: string
  last_failure_at: string | null
  proposed_at: string | null
  approved_at: string | null
  approved_by_user_id: string | null
  rejected_at: string | null
  rejected_by_user_id: string | null
  quarantine_start: string | null
  quarantine_expires_at: string | null
  quarantine_duration_days: number
  recheck_at: string | null
  rationale: Record<string, unknown> | null
  reviewer_notes: string | null
  // Lifecycle: owner + SLA + auto-promotion (PMF US-5.4 / US-5.5)
  owner_user_id: string | null
  owner_name: string | null
  defect_id: string | null
  sla_days: number | null
  stale_at: string | null
  stale: boolean
  consecutive_passes: number
  ready_to_promote: boolean
  created_at: string
  updated_at: string
}

export interface QuarantineStatsResponse {
  detected: number
  proposed: number
  approved: number
  quarantined: number
  recheck_scheduled: number
  re_quarantined: number
  released: number
  rejected: number
  expired: number
  total_live: number
}

export interface DecisionPayload {
  notes?: string
  quarantine_duration_days?: number
}

/** Manual quarantine proposal (US-2.4 "Mute test" on /failures). Mirrors the
 *  backend ``QuarantineProposeRequest`` — QA_LEAD+ only; the detection agent
 *  is the primary path, so most fields are optional detection metadata. */
export interface QuarantineProposePayload {
  project_id: string
  test_fingerprint: string
  test_name?: string | null
  suite_name?: string | null
  detection_method?: string
  flip_rate?: number | null
  flip_window_size?: number | null
  pass_count?: number | null
  fail_count?: number | null
  rationale?: Record<string, unknown> | null
  quarantine_duration_days?: number
}

export const flakyQuarantineService = {
  list: (params?: {
    project_id?: string
    status_filter?: QuarantineStatus
    live_only?: boolean
    limit?: number
  }) => getData<FlakyQuarantineRead[]>('/api/v1/quarantine', { params }),

  stats: (project_id?: string) =>
    getData<QuarantineStatsResponse>(
      '/api/v1/quarantine/stats',
      { params: project_id ? { project_id } : undefined },
    ),

  get: (id: string) =>
    getData<FlakyQuarantineRead>(`/api/v1/quarantine/${id}`),

  propose: (payload: QuarantineProposePayload) =>
    postData<FlakyQuarantineRead>('/api/v1/quarantine', payload),

  approve: (id: string, payload: DecisionPayload = {}) =>
    postData<FlakyQuarantineRead>(`/api/v1/quarantine/${id}/approve`, payload),

  reject: (id: string, payload: DecisionPayload = {}) =>
    postData<FlakyQuarantineRead>(`/api/v1/quarantine/${id}/reject`, payload),

  release: (id: string, payload: DecisionPayload = {}) =>
    postData<FlakyQuarantineRead>(`/api/v1/quarantine/${id}/release`, payload),
}
