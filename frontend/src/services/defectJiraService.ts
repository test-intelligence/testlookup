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
  /** Sent only after a 409 `jira_outcome_unknown`, once the user has checked Jira. */
  confirm_not_filed?: boolean
}

/**
 * 409 detail when an earlier request may already have filed the issue. Search
 * Jira for `jira_label`; if nothing carries it, retry with `confirm_not_filed`.
 */
export interface JiraOutcomeUnknownDetail {
  code: 'jira_outcome_unknown'
  message: string
  jira_label: string
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

/**
 * STRAT-03 — "Create epic for this cluster" (suggest-only, opt-in per project).
 *
 *   GET  /api/v1/projects/{id}/systemic-clusters/epics               — opt-in, Jira availability, stored links
 *   GET  /api/v1/projects/{id}/systemic-clusters/{key}/epic/preview  — the epic's prefilled fields (404 gone, 422)
 *   POST /api/v1/projects/{id}/systemic-clusters/{key}/epic          — file it (QA_ENGINEER+); 201 new, 200 stored
 *
 * Jira is written only by a person's POST; nothing here runs on a schedule. `{key}` is the cluster's
 * `membership_key` (the hash of its exact member set). Each caller renders its own failure (no global toast).
 */
export interface ClusterEpicLink {
  membership_key: string
  jira_key: string
  jira_url: string | null
  cluster_label: string | null
  created_at: string | null
  created_by_name: string | null
}

export interface ClusterEpicsStatus {
  /** The project's opt-in (`cluster_epic_suggestions`). Off: no epic action at all. */
  enabled: boolean
  available: boolean
  /** The human sentence for "Jira is not available" (offline / disabled / not configured), else null. */
  reason: string | null
  /** Null when Jira is unavailable (not asked). */
  epic_type_available: boolean | null
  /** A cluster recurs when `co_failure_runs >= min_co_failure_runs` (3). */
  min_co_failure_runs: number
  links: ClusterEpicLink[]
}

export interface ClusterEpicPreview {
  membership_key: string
  summary: string
  description_text: string
  labels: string[]
  jira_project_key: string | null
  member_count: number
  co_failure_runs: number
  cluster_label: string
}

export interface ClusterEpicCreatePayload {
  jira_project_key: string | null
  /** At most 255 characters; null lets the server use its own summary. */
  summary: string | null
  /** True only after a 409 `jira_outcome_unknown`, once the user has checked Jira. */
  confirm_not_filed: boolean
}

export interface ClusterEpicCreateResult {
  membership_key: string
  jira_key: string
  jira_url: string | null
  /** false: a link was already stored for this cluster and is returned as it is. */
  created: boolean
}

const clusterEpicBase = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}/systemic-clusters`
const clusterEpicPath = (projectId: string, membershipKey: string) =>
  `${clusterEpicBase(projectId)}/${encodeURIComponent(membershipKey)}/epic`

export const clusterEpicService = {
  status: (projectId: string) =>
    getData<ClusterEpicsStatus>(`${clusterEpicBase(projectId)}/epics`, { suppressToast: true }),

  preview: (projectId: string, membershipKey: string) =>
    getData<ClusterEpicPreview>(`${clusterEpicPath(projectId, membershipKey)}/preview`, { suppressToast: true }),

  create: (projectId: string, membershipKey: string, payload: ClusterEpicCreatePayload) =>
    postData<ClusterEpicCreateResult>(clusterEpicPath(projectId, membershipKey), payload, { suppressToast: true }),
}
