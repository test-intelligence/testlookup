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
}

export interface PendingDefect {
  defect_id: string
  title: string
  severity: string
  component: string | null
  owner_team: string | null
  created_at: string | null
  policy_reasons: string[]
}
