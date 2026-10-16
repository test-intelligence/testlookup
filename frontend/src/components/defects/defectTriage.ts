/**
 * What the three defect triage dialogs (W06 DEF-01: change status, assign,
 * link a Jira issue) share: the status vocabulary and labels, the Jira reason
 * labels, the key pattern, and how a failed PATCH is shown.
 *
 * The vocabulary mirrors the server's `defect_status.py` (the ONE list); the
 * server refuses anything else with a 422, so a drift here shows as an error,
 * never as a silent write.
 */
import { extractErrorMessage } from '@/services/apiErrors'

/** What a person may set (`DEFECT_STATUSES`), in the order the dialog lists them. */
export const DEFECT_STATUS_OPTIONS = [
  { id: 'OPEN', label: 'Open' },
  { id: 'IN_PROGRESS', label: 'In progress' },
  { id: 'RESOLVED', label: 'Resolved' },
  { id: 'INVALID', label: 'Invalid' },
] as const

export function defectStatusLabel(status: string): string {
  return DEFECT_STATUS_OPTIONS.find((o) => o.id === status)?.label ?? status
}

/** The stored word, as the server normalises it (strip + upper case). */
export function normalizeDefectStatus(status: string | null | undefined): string {
  return String(status ?? '').trim().toUpperCase()
}

/** Why Jira was not used (`availability_reason`), as a sentence fragment. */
const JIRA_REASON_LABELS: Record<string, string> = {
  offline_mode: 'offline mode',
  disabled: 'Jira integration disabled',
  not_configured: 'Jira not configured',
}

/** The availability reasons under which the server saves a key without asking Jira. */
export const JIRA_LOCAL_ONLY_REASONS: ReadonlySet<string> = new Set(Object.keys(JIRA_REASON_LABELS))

export function jiraReasonLabel(reason: string | null | undefined): string {
  if (!reason) return 'reason unknown'
  return JIRA_REASON_LABELS[reason] ?? reason
}

/** A Jira issue key after upper-casing (`JIRA_KEY_RE` on the server). */
export const JIRA_KEY_PATTERN = /^[A-Z][A-Z0-9_]+-[0-9]+$/
/** The column is `String(50)`. */
export const JIRA_KEY_MAX_LENGTH = 50

/**
 * How a failed triage PATCH is shown. A 409 (`defect_already_open`: the test
 * already has a live defect) is rendered inside the dialog, which stays open;
 * anything else is ONE error toast with the server's detail verbatim. The
 * request is sent with `suppressToast`, so the global interceptor raises none
 * of its own: never two toasts for one failure.
 */
export interface TriageFailure {
  inline: boolean
  message: string
}

export function describeTriageFailure(err: unknown): TriageFailure {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response
  const detail = response?.data?.detail
  if (response?.status === 409) {
    const message =
      detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string'
        ? (detail as { message: string }).message
        : extractErrorMessage(detail, 'This defect could not be changed.')
    return { inline: true, message }
  }
  return { inline: false, message: extractErrorMessage(detail, 'Update failed') }
}
