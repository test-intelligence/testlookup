import { getData, postData } from './http'

/**
 * One-click Jira defect creation — PMF US-6.1 / US-6.3.
 *
 * All endpoints are project-scoped:
 *   GET  /api/v1/projects/{id}/defects/jira/metadata  — picker metadata + availability
 *   GET  /api/v1/projects/{id}/defects/jira/preview   — server-side prefilled payload
 *   POST /api/v1/projects/{id}/defects/jira           — dedup-first create (QA_ENGINEER+)
 */

export interface JiraProjectOption {
  key: string
  name: string | null
}

export interface JiraDefectMetadata {
  available: boolean
  /** offline_mode | disabled | not_configured | unreachable | jira_http_NNN */
  reason: string | null
  projects: JiraProjectOption[]
  issue_types: string[]
  default_project_key: string | null
  webhook_available: boolean
}

export interface JiraDefectOccurrences {
  first_seen: string | null
  last_seen: string | null
  failing_runs: number
}

export interface JiraDefectPreview {
  signature: string
  summary: string
  description: string
  test_name: string | null
  suite_name: string | null
  cluster_id: string | null
  error_message: string | null
  occurrences: JiraDefectOccurrences
  context: {
    branch: string | null
    build_number: string | null
    ci_run_url: string | null
  }
  ai_analysis: {
    root_cause?: string
    confidence?: number
    failure_category?: string
  } | null
  deep_link: string
  latest_run_id: string | null
  existing_defect: {
    defect_id: string
    jira_key: string | null
    jira_url: string | null
    external_status: string | null
  } | null
}

export interface JiraDefectCreatePayload {
  fingerprint?: string
  cluster_id?: string
  issue_type?: string
  jira_project_key?: string
  assignee?: string
  extra_comment?: string
  target?: 'jira' | 'webhook'
}

export interface JiraDefectCreateResult {
  target: 'jira' | 'webhook'
  deduplicated: boolean
  defect_id: string | null
  jira_key: string | null
  jira_url: string | null
  external_status: string | null
  recurrence_count: number
  recurrence_comment_posted: boolean
  subscriptions_notified: number | null
  message: string
}

export const defectJiraService = {
  metadata: (projectId: string) =>
    getData<JiraDefectMetadata>(`/api/v1/projects/${projectId}/defects/jira/metadata`),

  preview: (projectId: string, params: { fingerprint?: string; cluster_id?: string }) =>
    getData<JiraDefectPreview>(`/api/v1/projects/${projectId}/defects/jira/preview`, { params }),

  create: (projectId: string, payload: JiraDefectCreatePayload) =>
    postData<JiraDefectCreateResult>(`/api/v1/projects/${projectId}/defects/jira`, payload),
}
