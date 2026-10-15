export interface DefectCandidateResponse {
  cluster_id: string
  run_id: string
  title: string
  description: string
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'
  component: string
  owner_team: string
  labels: string[]
  duplicate_hint: string
  duplicate_detected: boolean
  duplicate_defect_id: string | null
  criticality_scores: Record<string, number>
  composite_score: number
  evidence_bundle: {
    stack_traces: string[]
    log_anomalies: string[]
    data_sources: string[]
  }
  failure_category: string
  member_count: number
}

export interface DefectPromotionRequest {
  title: string
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'
  component: string
  owner_team: string
  labels: string[]
  description: string
  project_key?: string
}

export interface DefectPromotionResponse {
  defect_id: string
  cluster_id: string
  severity: string
  title: string
  owner_team: string | null
  component: string | null
  duplicate_detected: boolean
  duplicate_defect_id: string | null
  jira_ticket: Record<string, unknown> | null
  jira_url: string | null
  // Phase 4: Approval workflow
  approval_status: string | null
  requires_approval: boolean | null
  policy_reasons: string[] | null
}

export type ApprovalAction = 'approve' | 'reject'

export interface DefectApprovalRequest {
  action: ApprovalAction
  reason?: string
}

export interface DefectApprovalResponse {
  defect_id: string
  approval_status: string
  message: string
  jira_ticket: Record<string, unknown> | null
  jira_url: string | null
  /** W06 DEF-06 (approve only): what the approval's Jira filing did:
   *  'filed' | 'linked' | 'not_filed' | 'failed' | 'unknown' | 'not_requested'. */
  jira_status?: string | null
  /** not_filed: 'offline_mode' | 'disabled' | 'not_configured'. */
  jira_reason?: string | null
  /** failed / unknown: the server's detail text. */
  jira_error?: string | null
}

export interface PendingDefect {
  defect_id: string
  /** W06 DEF-06: the defect's project (the queue is read per project). */
  project_id: string
  title: string | null
  severity: string | null
  component: string | null
  owner_team: string | null
  created_at: string | null
  policy_reasons: string[]
  /** The Jira project key given at promotion (approve files the ticket there); null = none. */
  jira_project_key?: string | null
  /** The run the defect was promoted from; null when unknown. */
  run_id?: string | null
  cluster_id?: string | null
  ai_confidence_score?: number | null
}

/** GET /api/v1/deep-investigate/defects/pending-review (W06 D-10): `total` is a
 *  COUNT over the same scope, not the page's length. */
export interface PendingDefectList {
  items: PendingDefect[]
  total: number
  page: number
  page_size: number
}
