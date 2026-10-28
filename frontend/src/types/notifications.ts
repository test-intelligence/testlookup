export type NotificationChannel = 'email' | 'slack' | 'teams'

export type NotificationEventType =
  | 'run_failed'
  | 'run_passed'
  | 'high_failure_rate'
  | 'ai_analysis_complete'
  | 'quality_gate_failed'
  // Kept in the vocabulary (stored rows, the channel maps) but never offered:
  // it is `test.newly_flaky` (NTF-01, migration 0220; HIDDEN_EVENTS in
  // `components/notifications/notificationEventsModel.ts`).
  | 'flaky_test_detected'
  // Transition events (PMF US-7.1) — fire on state changes, not per run
  | 'test.newly_failing'
  | 'test.recovered'
  | 'test.newly_flaky'
  | 'test.quarantined'
  | 'test.unquarantined'
  // Quarantine lifecycle events (PMF US-5.4 / US-5.5)
  | 'test.quarantine_stale'
  | 'test.ready_to_unquarantine'
  // Duration regressions (EXEC-05): a test or suite BECAME slower than its baseline
  | 'test.newly_slow'
  | 'suite.newly_slow'

export interface NotificationPreference {
  id: string
  user_id: string
  project_id: string | null
  channel: NotificationChannel
  enabled: boolean
  events: NotificationEventType[]
  failure_rate_threshold: number
  email_override: string | null
  slack_webhook_url: string | null
  teams_webhook_url: string | null
  created_at: string
  updated_at: string | null
}

export interface NotificationPreferencePayload {
  project_id?: string | null
  channel: NotificationChannel
  enabled: boolean
  events: NotificationEventType[]
  failure_rate_threshold: number
  email_override?: string | null
  slack_webhook_url?: string | null
  teams_webhook_url?: string | null
}

export interface NotificationLog {
  id: string
  /**
   * W21d ACT-02: a mention is an in-app row (`in_app`, `test_case.mentioned`).
   * Only the LOG is widened: the preference vocabularies above are not, so no
   * preferences form offers either value (mentions are never mailed yet).
   */
  channel: NotificationChannel | 'in_app'
  event_type: NotificationEventType | 'test_case.mentioned'
  title: string
  body: string
  status: 'pending' | 'sent' | 'failed'
  is_read: boolean
  sent_at: string | null
  created_at: string
  /**
   * NTF-01: the in-app path the entry opens, from the server's allow-list
   * (`/releases/<uuid>`, `/release-gate/<uuid>` or `/test-management?case=<uuid>`), else null. The bell
   * re-checks it by shape and role (`components/layout/notificationHref.ts`).
   */
  link?: string | null
}

/**
 * NTF-02: `GET /api/v1/notifications/projects/{project_id}/transition-policy`.
 * `is_default` is true when the project has no row and the defaults apply
 * (state changes ON, every run OFF): the UI reads it, never re-derives it.
 */
export interface TransitionPolicy {
  project_id: string
  transitions_enabled: boolean
  per_run_events_enabled: boolean
  enabled_events: string[]
  consecutive_failure_threshold: number
  is_default: boolean
}

/** The PUT body: the whole policy (the server replaces every field). */
export interface TransitionPolicyBody {
  transitions_enabled: boolean
  per_run_events_enabled: boolean
  enabled_events: string[]
  consecutive_failure_threshold: number
}

/** The server's default for `consecutive_failure_threshold` (schemas.py). */
const DEFAULT_FAILURE_THRESHOLD = 2

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/**
 * The defensive guard over the policy read: a missing or malformed answer is
 * `null`, and a page then shows NO hint (the dispatcher fails open, so a
 * "not sent" hint over a policy that could not be read would be the lie).
 * The three fields a hint is decided by must have their exact types; a
 * non-string event is dropped; a missing threshold reads as the server's
 * default.
 */
export function parseTransitionPolicy(raw: unknown): TransitionPolicy | null {
  if (!isRecord(raw)) return null
  const { project_id, transitions_enabled, per_run_events_enabled, enabled_events } = raw
  if (typeof project_id !== 'string' || project_id === '') return null
  if (typeof transitions_enabled !== 'boolean') return null
  if (typeof per_run_events_enabled !== 'boolean') return null
  if (!Array.isArray(enabled_events)) return null
  const threshold = raw.consecutive_failure_threshold
  return {
    project_id,
    transitions_enabled,
    per_run_events_enabled,
    enabled_events: enabled_events.filter((e): e is string => typeof e === 'string'),
    consecutive_failure_threshold:
      typeof threshold === 'number' && Number.isFinite(threshold) ? threshold : DEFAULT_FAILURE_THRESHOLD,
    is_default: raw.is_default === true,
  }
}
