/**
 * W06 DEF-01: the defect triage PATCH (`PATCH /api/v1/projects/{project_id}/defects/{defect_id}`).
 * Shapes are the W06 contract's §1.4 / §1.7; every enum-like field is a plain
 * string (the vocabularies live server-side in `defect_status.py`).
 */
import type { DefectItem } from './analytics'

/** Only the fields SENT are applied; `null` (or `''`) clears assignee, team and Jira key. */
export interface DefectPatchBody {
  resolution_status?: string
  assignee_user_id?: string | null
  owner_team?: string | null
  jira_key?: string | null
  /** Recorded in the activity-log row, never on the defect. */
  note?: string
}

export interface DefectJiraLinkResult {
  /** 'validated' | 'saved_locally' | 'unlinked' */
  status: string
  /** saved_locally: 'offline_mode' | 'disabled' | 'not_configured' */
  reason?: string | null
  jira_key?: string | null
  /** Server-built (`https://{domain}/browse/{KEY}`) or null; render only through `isSafeExternalUrl`. */
  jira_url?: string | null
  /** Jira's issue summary when validated (plain text). */
  summary?: string | null
}

export interface DefectWriteBackResult {
  /** 'sent' | 'skipped' | 'not_sent' | 'failed' */
  status: string
  /** skipped: 'already_in_status' | 'no_transition'; not_sent: an availability reason;
   *  failed: 'HTTP 4xx' | '<ExceptionClass>'. */
  reason?: string | null
}

export interface DefectPatchResponse {
  /** The §1.2 list row, as the server reads it after the change. */
  defect: DefectItem
  changed_fields: string[]
  jira?: DefectJiraLinkResult | null
  write_back?: DefectWriteBackResult | null
}
