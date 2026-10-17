/**
 * W09 "notify-flaky" fixtures (`docs/deferred-scope-2026-10-13/work/W09-contract.md` §2, §4.6): the worlds the
 * wave's hermetic specs drive and its new visual regions shoot.
 *
 *  - notifications (NTF-02 / NTF-01): the SMTP and shared-channel reads of Email (SMTP) & channels, the user's
 *    preferences, the project's transition policy (GET, and a PUT the GET then answers), the notification
 *    history the bell and My notifications list (a gate entry staged by an evaluation);
 *  - the release gate's NO_GO transition (NTF-01): the W05 `/releases` world with the hotfix's evaluation
 *    answering NO_GO and staging the in-app entry, as the server does in the same transaction;
 *  - digests (NTF-03 / NTF-04): subscriptions decorated as `digest_scope_service.decorate_subscriptions` does
 *    (`scope_label`, `scope_problem`), the project's saved views (report views, a legacy empty row, a widget
 *    layout that must not be listed), the preview with a saved view's `scope`;
 *  - flaky (FLK-01 / FLK-02): a quarantine world whose Approve and Release change the rows the tabs read.
 *    The `/flaky` defaults (the coach entries with their `flaky_score`, the policy GET / PUT) are
 *    `flakyHandlers` in `fixtures-pages.ts`, which every `/flaky` spec shares.
 *
 * Every factory returns fresh state per call and records the writes it answered (`writes`). Every write handler
 * names its method (`'POST'`, `'PUT'`, `'PATCH'`), so a write the page should not make stays unmocked and fails
 * the spec. Not a spec file. Shapes follow the contract's §1.2 / §1.5 literally.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { HOSTILE_NAME, isoAgo, LAYOUT, NOW, PROJECT_ID, USER } from './fixtures'
import {
  flakyHandlers,
  gateSummary,
  RELEASE_ACTIVE_ID,
  RELEASE_PLANNING_ID,
  RELEASE_SHIPPED_ID,
  releasesReadHandlers,
  releasesWorld,
} from './fixtures-pages'
import { ADMIN, QA_ENGINEER, RELEASE_HOTFIX_ID, releasesHandlers, VIEWER } from './fixtures-releases'

export { ADMIN, QA_ENGINEER, RELEASE_ACTIVE_ID, RELEASE_HOTFIX_ID, RELEASE_PLANNING_ID, RELEASE_SHIPPED_ID, VIEWER }
export { DEFAULT_QUARANTINE_POLICY, FLAKY_SCORES } from './fixtures-pages'

const P = PROJECT_ID
const DAY_MS = 86_400_000

/** One answered write: `METHOD path` and its JSON body. */
export interface RecordedWrite {
  line: string
  body: unknown
}

const bodyOf = (request: ApiRequest): unknown => {
  try {
    return request.route.request().postDataJSON()
  } catch {
    return null
  }
}

function recorder(writes: RecordedWrite[]) {
  return (request: ApiRequest): unknown => {
    const body = bodyOf(request)
    writes.push({ line: `${request.method} ${request.path}`, body })
    return body
  }
}

// ── NTF-02 / NTF-01: notifications ─────────────────────────────────────────

/** `schemas.TRANSITION_EVENT_VALUES`, the server's order. */
export const TRANSITION_EVENTS = [
  'test.newly_failing',
  'test.recovered',
  'test.newly_flaky',
  'test.quarantined',
  'test.unquarantined',
  'test.quarantine_stale',
  'test.ready_to_unquarantine',
] as const

export const TRANSITION_POLICY_PATH = `/api/v1/notifications/projects/${P}/transition-policy`

/** A project with no policy row: the new-project defaults (state changes ON, every run OFF), `is_default`. */
export const DEFAULT_TRANSITION_POLICY = {
  project_id: P,
  transitions_enabled: true,
  per_run_events_enabled: false,
  enabled_events: [...TRANSITION_EVENTS],
  consecutive_failure_threshold: 2,
  is_default: true,
}

/** The PUT body of "Notify on every run" → Save over the defaults (contract §2.1 assertion 10). */
export const EVERY_RUN_POLICY_BODY = {
  transitions_enabled: true,
  per_run_events_enabled: true,
  enabled_events: [...TRANSITION_EVENTS],
  consecutive_failure_threshold: 2,
}

export const SMTP = {
  enabled: true,
  host: 'smtp.corp.example.com',
  port: 465,
  user: 'mailer@corp.example.com',
  from_address: 'alerts@corp.example.com',
  implicit_tls: true,
  password_set: true,
}

export const INTEGRATIONS = {
  jira_enabled: false, jira_domain: null, jira_email: null, jira_token_set: false, jira_default_project_key: 'QA',
  splunk_enabled: false, splunk_base_url: null, splunk_token_set: false,
  ocp_enabled: false, ocp_api_url: null, ocp_token_set: false, ocp_default_namespace: 'default',
  slack_enabled: true, slack_webhook_url: null, slack_webhook_set: true, slack_default_channel: '#qa-alerts',
  teams_enabled: false, teams_webhook_url: null, teams_webhook_set: false,
  github_repo: null, github_token_set: false,
}

/** A stored notification preference (`NotificationPreferenceResponse`), project-less (every project). */
export function preference(id: string, channel: 'email' | 'slack' | 'teams', events: string[], extra: Record<string, unknown> = {}) {
  return {
    id,
    user_id: USER.id,
    project_id: null as string | null,
    channel,
    enabled: true,
    events,
    failure_rate_threshold: 80,
    email_override: null as string | null,
    slack_webhook_url: null as string | null,
    teams_webhook_url: null as string | null,
    created_at: isoAgo(20),
    updated_at: null as string | null,
    ...extra,
  }
}

export type PreferenceFixture = ReturnType<typeof preference>

/** The email preference of the NTF-01 spec: stored before migration 0220 with the dead "Flaky test detected". */
export const LEGACY_EMAIL_PREF_ID = '00000000-0000-4000-8000-000000000a01'
export const LEGACY_EMAIL_PREF = preference(LEGACY_EMAIL_PREF_ID, 'email', ['run_failed', 'flaky_test_detected'])

/** One row of `GET /notifications/history` (`NotificationLogResponse`, with NTF-01's `link`). */
export function notificationLog(id: string, title: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    channel: 'email',
    event_type: 'run_failed',
    title,
    body: '',
    status: 'sent',
    is_read: false,
    sent_at: isoAgo(0, 1),
    created_at: isoAgo(0, 1),
    link: null as string | null,
    ...extra,
  }
}

export type NotificationLogFixture = ReturnType<typeof notificationLog>

/** The top blocking reason the gate entry quotes (the W05 world's first NO_GO reason). */
export const GATE_REASON = 'test_pay (checkout-e2e) is FAILED'

/** The in-app entry a recorded transition into NO_GO stages for a release (`gate_notifications`, §1.1). */
export function gateNotification(id: string, releaseId: string, releaseName: string) {
  return notificationLog(id, `Quality gate failed: ${releaseName} — ${GATE_REASON}`, {
    event_type: 'quality_gate_failed',
    body: `• ${GATE_REASON}`,
    // Staged, not yet relayed: the bell lists every status.
    status: 'pending',
    sent_at: null,
    created_at: NOW.toISOString(),
    link: `/releases/${releaseId}`,
  })
}

/** The run that a release-readiness decision went NO_GO for (agent or override). */
export const DECISION_RUN_ID = '33333333-3333-4333-8333-0000000000d1'

/** The in-app entry of a run's release-readiness decision into NO_GO: it links to the run's release gate. */
export function runDecisionNotification(id: string) {
  return notificationLog(id, 'Quality gate failed: Checkout build 142 — 3 new failures block the release', {
    event_type: 'quality_gate_failed',
    body: '• 3 new failures block the release',
    status: 'pending',
    sent_at: null,
    created_at: isoAgo(0, 2),
    link: `/release-gate/${DECISION_RUN_ID}`,
  })
}

export interface NotificationsState {
  /** The transition policy GET answers this (a PUT replaces it); `null` → a 500 (an unreadable policy). */
  policy: Record<string, unknown> | null
  preferences: PreferenceFixture[]
  history: NotificationLogFixture[]
  writes: RecordedWrite[]
}

export function notificationsState(initial: Partial<Omit<NotificationsState, 'writes'>> = {}): NotificationsState {
  return {
    policy: initial.policy === undefined ? { ...DEFAULT_TRANSITION_POLICY } : initial.policy,
    preferences: [...(initial.preferences ?? [])],
    history: [...(initial.history ?? [])],
    writes: [],
  }
}

/**
 * The notification reads and writes of the two settings pages and the bell, answered from `state` (no LAYOUT:
 * put these BEFORE any handler set that ends in LAYOUT, whose history answer is `[]`).
 */
export function notificationHandlers(state: NotificationsState): ApiHandlers {
  const record = recorder(state.writes)
  let created = 0
  return [
    ['/api/v1/settings/smtp', () => SMTP],
    ['/api/v1/settings/integrations', () => INTEGRATIONS],
    [TRANSITION_POLICY_PATH, () => (state.policy === null ? respond(500, { detail: 'Internal Server Error' }) : state.policy)],
    [
      TRANSITION_POLICY_PATH,
      (request) => {
        const body = record(request) as Record<string, unknown>
        state.policy = { ...body, project_id: P, is_default: false }
        return state.policy
      },
      'PUT',
    ],
    ['/api/v1/notifications/preferences', () => state.preferences],
    [
      '/api/v1/notifications/preferences',
      (request) => {
        const body = record(request) as Record<string, unknown>
        created += 1
        const row = { ...preference(`00000000-0000-4000-8000-000000000b${String(created).padStart(2, '0')}`, 'email', []), ...body, updated_at: null }
        state.preferences.push(row as PreferenceFixture)
        return row
      },
      'POST',
    ],
    [
      /^\/api\/v1\/notifications\/preferences\/[^/]+$/,
      (request) => {
        const body = record(request) as Record<string, unknown>
        const id = request.path.split('/').pop()
        const index = state.preferences.findIndex((p) => p.id === id)
        if (index < 0) return respond(404, { detail: 'Preference not found' })
        state.preferences[index] = { ...state.preferences[index], ...body, updated_at: NOW.toISOString() } as PreferenceFixture
        return state.preferences[index]
      },
      'PUT',
    ],
    ['/api/v1/notifications/history/unread-count', () => ({ unread: state.history.filter((log) => !log.is_read).length })],
    ['/api/v1/notifications/history', () => state.history],
  ]
}

/** Email (SMTP) & channels and My notifications, from `state`, with the shell's answers. */
export function notificationsWorld(initial: Partial<Omit<NotificationsState, 'writes'>> = {}) {
  const state = notificationsState(initial)
  return { state, handlers: [...notificationHandlers(state), ...LAYOUT] as ApiHandlers }
}

/** The NO_GO the hotfix's evaluation records (it was never evaluated: a transition into NO_GO). */
export const HOTFIX_NO_GO = gateSummary(
  {
    verdict: 'NO_GO',
    decided_at: NOW.toISOString(),
    pass_rate: 80,
    denominator: 42,
    evidence_count: 40,
    run_count: 3,
    status_counts: { PASSED: 32, FAILED: 8 },
  },
  [GATE_REASON],
)

export const HOTFIX_NAME = '2026.11.1'
export const GATE_NOTIFICATION_ID = '00000000-0000-4000-8000-000000000c01'

/**
 * The W05 `/releases` world (with the never-evaluated hotfix) where evaluating the hotfix records NO_GO and,
 * in the same transaction, stages the subscriber's in-app entry (`gateNotification`): the next history read
 * (the bell's 30 s poll) lists it.
 */
export function gateNotifyWorld(initial: Partial<Omit<NotificationsState, 'writes'>> = {}) {
  const notifications = notificationsState(initial)
  const releases = releasesHandlers({ hotfix: true })
  const evaluate: ApiHandlers[number] = [
    `/api/v1/releases/${RELEASE_HOTFIX_ID}/gate/evaluate`,
    (request) => {
      releases.writes.push({ line: `${request.method} ${request.path}`, body: bodyOf(request) })
      const row = releases.world.rows.find((r) => r.id === RELEASE_HOTFIX_ID)
      if (!row) return respond(404, { detail: 'Release not found' })
      row.gate = { ...HOTFIX_NO_GO }
      releases.world.reasons[RELEASE_HOTFIX_ID] = [GATE_REASON]
      notifications.history.unshift(gateNotification(GATE_NOTIFICATION_ID, RELEASE_HOTFIX_ID, HOTFIX_NAME))
      return {
        release_id: RELEASE_HOTFIX_ID,
        verdict: 'NO_GO',
        blocking_reasons: [GATE_REASON],
        recorded: true,
        decision_id: 'dec-hotfix-1',
        scorecard: { pass_rate: 80, evidence_count: 40, denominator: 42, insufficient_reason: null },
      }
    },
    'POST',
  ]
  return {
    notifications,
    releases,
    handlers: [evaluate, ...notificationHandlers(notifications), ...releases.handlers] as ApiHandlers,
  }
}

// ── NTF-03 / NTF-04: digests ───────────────────────────────────────────────

export const DIGEST_SUBSCRIPTIONS_PATH = '/api/v1/digests/subscriptions'
export const DIGEST_PREVIEW_PATH = '/api/v1/digests/preview'
export const SAVED_VIEWS_PATH = '/api/v1/saved-views'

/** The 422 texts of `digest_scope_service` (contract §1.1), verbatim. */
export const NEEDS_RELEASE = 'A per-release digest needs a release: choose one.'
export const BAD_RELEASE = 'Choose a release of this project.'
export const NEEDS_SUITE = 'A per-suite digest needs a suite name.'
export const VIEW_SCHEDULE = 'A saved view filters Daily and Weekly digests only.'
export const BAD_VIEW = "Choose one of this project's saved report views."

/** A `DigestSubscriptionResponse` before decoration. */
export function digestSubscription(id: string, name: string, schedule: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    user_id: USER.id,
    project_id: P as string | null,
    saved_view_id: null as string | null,
    name,
    schedule,
    channel: 'email',
    is_active: true,
    is_paused: false,
    scope_type: null as string | null,
    scope_value: null as string | null,
    trigger_filter: null as string | null,
    send_when_unchanged: false,
    report_attachment: false,
    last_delivered_at: null as string | null,
    next_delivery_at: null as string | null,
    delivery_count: 0,
    created_at: isoAgo(10),
    updated_at: null as string | null,
    saved_view_name: null as string | null,
    scope_label: null as string | null,
    scope_problem: null as string | null,
    ...extra,
  }
}

export type DigestSubscriptionFixture = ReturnType<typeof digestSubscription>

/** A `saved_views` row of the project. */
export function savedView(id: string, name: string, filters: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  return {
    id,
    user_id: USER.id,
    project_id: P as string | null,
    name,
    description: null as string | null,
    page: 'trends' as string | null,
    filters,
    is_shared: false,
    is_default: false,
    created_at: isoAgo(5),
    updated_at: null as string | null,
    ...extra,
  }
}

export type SavedViewFixture = ReturnType<typeof savedView>

/** A release id of ANOTHER project (or one deleted since): the server drops it from a view's scope. */
export const FOREIGN_RELEASE_IDS = ['bbbbbbbb-0000-4000-8000-0000000000f1', 'bbbbbbbb-0000-4000-8000-0000000000f2']

export const VIEW_IDS = {
  regressions: '00000000-0000-4000-8000-000000000d01',
  legacy: '00000000-0000-4000-8000-000000000d02',
  layout: '00000000-0000-4000-8000-000000000d03',
  hostile: '00000000-0000-4000-8000-000000000d04',
} as const

/** "Checkout regressions": 2026.11 (and two releases that are no longer this project's) · checkout · 7 days. */
export const REGRESSIONS_VIEW = savedView(
  VIEW_IDS.regressions,
  'Checkout regressions',
  { kind: 'report_view', page: 'trends', window: 7, release_ids: [RELEASE_ACTIVE_ID, ...FOREIGN_RELEASE_IDS], suites: ['checkout'] },
  { is_shared: true },
)
/** What the old "Save View" made: an empty filter set. */
export const LEGACY_VIEW = savedView(VIEW_IDS.legacy, 'Old empty view', {}, { page: null, created_at: isoAgo(40) })
/** A widget layout row (`useAnalyticsView`): never listed as a digest view. */
export const LAYOUT_VIEW = savedView(VIEW_IDS.layout, 'Trends widget layout', { page: 'trends', instances: [{ id: 'w1', kind: 'pass-rate' }], version: 1 })
/** A report view whose name is markup: drawn as text. */
export const HOSTILE_VIEW = savedView(VIEW_IDS.hostile, HOSTILE_NAME, { kind: 'report_view', page: 'failures', window: 30 }, { page: 'failures' })

export const SUB_IDS = {
  weekly: '00000000-0000-4000-8000-000000000e01',
  perRelease: '00000000-0000-4000-8000-000000000e02',
  unscoped: '00000000-0000-4000-8000-000000000e03',
  deletedView: '00000000-0000-4000-8000-000000000e04',
  created: '00000000-0000-4000-8000-000000000e10',
} as const

/** A Weekly digest filtered by "Checkout regressions". */
export const WEEKLY_WITH_VIEW = digestSubscription(SUB_IDS.weekly, 'Weekly QA summary', 'WEEKLY', {
  saved_view_id: VIEW_IDS.regressions,
  saved_view_name: REGRESSIONS_VIEW.name,
  next_delivery_at: new Date(NOW.getTime() + 3 * DAY_MS).toISOString(),
  delivery_count: 4,
})
/** A Per Release digest for 2026.11. */
export const PER_RELEASE_SUB = digestSubscription(SUB_IDS.perRelease, '2026.11 release digest', 'PER_RELEASE', {
  scope_type: 'release',
  scope_value: RELEASE_ACTIVE_ID,
  channel: 'slack',
  delivery_count: 2,
})
/** A Per Release digest saved before W09 with no release: it never delivered ("Not delivering — choose a release"). */
export const UNSCOPED_PER_RELEASE_SUB = digestSubscription(SUB_IDS.unscoped, 'Release digest (legacy)', 'PER_RELEASE', {
  scope_type: 'project',
  created_at: isoAgo(60),
})
/** A Daily digest whose view was deleted: the FK set the id NULL, the name snapshot remains (0221). */
export const DELETED_VIEW_SUB = digestSubscription(SUB_IDS.deletedView, 'Daily payments digest', 'DAILY', {
  saved_view_id: null,
  saved_view_name: 'Payments only',
  delivery_count: 9,
})

export interface DigestsState {
  subscriptions: DigestSubscriptionFixture[]
  views: SavedViewFixture[]
  writes: RecordedWrite[]
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

/**
 * The digest world. The subscriptions are decorated on every read and write as the server's
 * `decorate_subscriptions` does; the creates are judged with `validate_subscription`'s rules (§1.1); the
 * preview answers `scope` for a saved view (its releases re-checked against the project, the rest dropped
 * and counted). The release list is the W05 world's (2026.11 in progress and active, 2026.12 planning,
 * 2026.10 released), the top bar's own read.
 */
export function digestsWorld(initial: Partial<Omit<DigestsState, 'writes'>> = {}) {
  const state: DigestsState = {
    subscriptions: [...(initial.subscriptions ?? [WEEKLY_WITH_VIEW, PER_RELEASE_SUB, UNSCOPED_PER_RELEASE_SUB, DELETED_VIEW_SUB])].map((s) => ({ ...s })),
    views: [...(initial.views ?? [REGRESSIONS_VIEW, LEGACY_VIEW, LAYOUT_VIEW])].map((v) => ({ ...v, filters: { ...v.filters } })),
    writes: [],
  }
  const releases = releasesWorld()
  const releaseName = (id: string | null | undefined) => releases.rows.find((r) => r.id === id)?.name ?? null
  const isReportView = (v: SavedViewFixture | undefined) => v?.filters?.kind === 'report_view'
  const readable = (v: SavedViewFixture | undefined) => !!v && v.project_id === P && isReportView(v) && (v.user_id === USER.id || v.is_shared)
  const record = recorder(state.writes)

  function decorate(sub: DigestSubscriptionFixture): DigestSubscriptionFixture {
    let label: string | null = null
    let problem: string | null = null
    if (sub.schedule === 'PER_RELEASE') {
      if (!sub.scope_value?.trim()) problem = 'unscoped'
      else if (releaseName(sub.scope_value)) label = releaseName(sub.scope_value)
      else problem = 'release_missing'
    } else if (sub.schedule === 'PER_SUITE') {
      if (sub.scope_value?.trim()) label = sub.scope_value
      else problem = 'unscoped'
    }
    if (!problem && sub.saved_view_id === null && sub.saved_view_name) problem = 'saved_view_deleted'
    if (!problem && sub.saved_view_id !== null && !readable(state.views.find((v) => v.id === sub.saved_view_id))) problem = 'saved_view_unavailable'
    return { ...sub, scope_label: label, scope_problem: problem }
  }

  /** `validate_subscription` over the MERGED state: the values to store, or the 422 text. */
  function validate(merged: DigestSubscriptionFixture): { stored: Partial<DigestSubscriptionFixture> } | { refused: string } {
    const stored: Partial<DigestSubscriptionFixture> = {}
    if (merged.schedule === 'PER_RELEASE') {
      const value = (merged.scope_value ?? '').trim()
      if (!value) return { refused: NEEDS_RELEASE }
      if (!UUID.test(value) || !releaseName(value)) return { refused: BAD_RELEASE }
      stored.scope_type = 'release'
      stored.scope_value = value
    } else if (merged.schedule === 'PER_SUITE') {
      const value = (merged.scope_value ?? '').trim()
      if (!value) return { refused: NEEDS_SUITE }
      stored.scope_type = 'suite'
      stored.scope_value = value
    }
    if (merged.saved_view_id) {
      if (merged.schedule !== 'DAILY' && merged.schedule !== 'WEEKLY') return { refused: VIEW_SCHEDULE }
      const view = state.views.find((v) => v.id === merged.saved_view_id)
      if (!readable(view)) return { refused: BAD_VIEW }
      stored.saved_view_name = view?.name ?? null
    }
    return { stored }
  }

  function scopeOf(viewId: string) {
    const view = state.views.find((v) => v.id === viewId)
    if (!view) return { source: 'saved_view_unavailable', view_id: null, view_name: null, release_ids: [], release_names: [], suites: [], window_days: null, dropped_release_count: 0 }
    const asked = Array.isArray(view.filters.release_ids) ? (view.filters.release_ids as string[]) : []
    const kept = asked.filter((id) => releaseName(id) !== null)
    const window = view.filters.window
    return {
      source: 'saved_view',
      view_id: view.id,
      view_name: view.name,
      release_ids: kept,
      release_names: kept.map((id) => releaseName(id) as string),
      suites: Array.isArray(view.filters.suites) ? (view.filters.suites as string[]).map((s) => s.trim().toLowerCase()) : [],
      window_days: typeof window === 'number' ? window : null,
      dropped_release_count: asked.length - kept.length,
    }
  }

  let createdSubs = 0
  let createdViews = 0
  const handlers: ApiHandlers = [
    [DIGEST_SUBSCRIPTIONS_PATH, () => state.subscriptions.map(decorate)],
    [
      DIGEST_SUBSCRIPTIONS_PATH,
      (request) => {
        const body = (record(request) ?? {}) as Record<string, unknown>
        createdSubs += 1
        const draft = digestSubscription(`${SUB_IDS.created.slice(0, -2)}${String(10 + createdSubs)}`, String(body.name ?? ''), String(body.schedule ?? 'WEEKLY'), {
          ...body,
          created_at: NOW.toISOString(),
        })
        const verdict = validate(draft)
        if ('refused' in verdict) return respond(422, { detail: verdict.refused })
        const row = { ...draft, ...verdict.stored }
        state.subscriptions.push(row)
        return decorate(row)
      },
      'POST',
    ],
    [
      /^\/api\/v1\/digests\/subscriptions\/[^/]+$/,
      (request) => {
        const body = (record(request) ?? {}) as Record<string, unknown>
        const id = request.path.split('/').pop()
        const index = state.subscriptions.findIndex((s) => s.id === id)
        if (index < 0) return respond(404, { detail: 'Subscription not found' })
        const merged = { ...state.subscriptions[index], ...body } as DigestSubscriptionFixture
        const touchesScope = ['schedule', 'scope_type', 'scope_value', 'saved_view_id'].some((key) => key in body)
        let stored: Partial<DigestSubscriptionFixture> = {}
        if (touchesScope) {
          const verdict = validate(merged)
          if ('refused' in verdict) return respond(422, { detail: verdict.refused })
          stored = verdict.stored
          if ('saved_view_id' in body && body.saved_view_id === null) stored.saved_view_name = null
        }
        state.subscriptions[index] = { ...merged, ...stored, updated_at: NOW.toISOString() }
        return decorate(state.subscriptions[index])
      },
      'PATCH',
    ],
    [SAVED_VIEWS_PATH, ({ url }) => {
      const project = url.searchParams.get('project_id')
      return state.views.filter((v) => !project || v.project_id === project)
    }],
    [
      SAVED_VIEWS_PATH,
      (request) => {
        const body = (record(request) ?? {}) as Record<string, unknown>
        createdViews += 1
        const row = savedView(`00000000-0000-4000-8000-000000000d${String(10 + createdViews)}`, String(body.name ?? ''), (body.filters ?? {}) as Record<string, unknown>, {
          ...body,
          created_at: NOW.toISOString(),
        })
        state.views.push(row)
        return row
      },
      'POST',
    ],
    [
      /^\/api\/v1\/saved-views\/[^/]+$/,
      (request) => {
        const body = (record(request) ?? {}) as Record<string, unknown>
        const id = request.path.split('/').pop()
        const index = state.views.findIndex((v) => v.id === id)
        if (index < 0) return respond(404, { detail: 'Saved view not found' })
        state.views[index] = { ...state.views[index], ...body, updated_at: NOW.toISOString() } as SavedViewFixture
        return state.views[index]
      },
      'PATCH',
    ],
    [DIGEST_PREVIEW_PATH, ({ url }) => {
      const viewId = url.searchParams.get('saved_view_id')
      const period = url.searchParams.get('period') ?? 'weekly'
      const content: Record<string, unknown> = {
        project_name: 'Checkout',
        period,
        generated_at: NOW.toISOString(),
        total_runs: viewId ? 5 : 12,
        avg_pass_rate: viewId ? 91.4 : 94.2,
        pass_rate_trend: -1.3,
        new_regressions: viewId ? 1 : 2,
        top_blockers: [GATE_REASON],
        top_clusters: [],
        flaky_test_count: viewId ? 1 : 3,
        release_decisions: [],
        action_items: ['Investigate test_pay before the 2026.11 sign-off'],
      }
      if (viewId) {
        const view = state.views.find((v) => v.id === viewId)
        if (!readable(view)) return respond(422, { detail: BAD_VIEW })
        content.scope = scopeOf(viewId)
      }
      return content
    }],
    ...releasesReadHandlers(releases),
    ...LAYOUT,
  ]
  return { state, handlers }
}

// ── FLK-01 / FLK-02: the quarantine workflow on /flaky ─────────────────────

/** A `FlakyQuarantineRead` (the shape of `fixtures-pages.ts`' rows). */
export function quarantineRequest(id: string, fingerprint: string, testName: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    project_id: P,
    test_fingerprint: fingerprint,
    test_name: testName,
    suite_name: 'Checkout',
    status,
    detection_method: 'flip_rate',
    flip_rate: 0.3,
    flip_window_size: 10,
    pass_count: 7,
    fail_count: 3,
    detected_at: isoAgo(6),
    last_failure_at: isoAgo(1),
    proposed_at: isoAgo(5),
    approved_at: null as string | null,
    approved_by_user_id: null as string | null,
    rejected_at: null as string | null,
    rejected_by_user_id: null as string | null,
    quarantine_start: null as string | null,
    quarantine_expires_at: null as string | null,
    quarantine_duration_days: 14,
    recheck_at: null as string | null,
    rationale: null as unknown,
    reviewer_notes: null as string | null,
    owner_user_id: null as string | null,
    owner_name: 'QA Lead',
    defect_id: null as string | null,
    defect_jira_key: null as string | null,
    defect_jira_url: null as string | null,
    defect_external_status: null as string | null,
    defect_external_status_conflict: false,
    sla_days: null as number | null,
    stale_at: null as string | null,
    stale: false,
    consecutive_passes: 0,
    ready_to_promote: false,
    created_at: isoAgo(6),
    updated_at: isoAgo(1),
    ...extra,
  }
}

export type QuarantineRequestFixture = ReturnType<typeof quarantineRequest>

const ACTIVE = ['APPROVED', 'QUARANTINED', 'RECHECK_SCHEDULED', 'RE_QUARANTINED']
const SETTLED = ['RELEASED', 'REJECTED', 'EXPIRED']

/** The SLA the world's approvals apply (the default policy's): due 14 days after the approval. */
export const WORLD_SLA_DAYS = 14
export const SLA_DUE_AFTER_APPROVAL = new Date(NOW.getTime() + WORLD_SLA_DAYS * DAY_MS).toISOString()

export const QUARANTINE_IDS = {
  liveProposal: '00000000-0000-4000-8000-000000000f01',
  quarantined: '00000000-0000-4000-8000-000000000f02',
  stale: '00000000-0000-4000-8000-000000000f03',
  released: '00000000-0000-4000-8000-000000000f04',
} as const

export interface FlakyWorldOptions {
  /** The quarantined rows beyond the two the world always has (to push the count past a policy's cap). */
  extraQuarantined?: number
}

/**
 * `/flaky` with a quarantine world that the decisions change: the coach entries, the policy GET / PUT and the
 * shell come from `flakyHandlers`; the requests, their counts and the four decisions (propose, approve,
 * reject, release) from here. A proposal is approved into QUARANTINED with `stale_at` 14 days on
 * (`SLA due …`); a release settles it into History.
 */
export function flakyQuarantineWorld(options: FlakyWorldOptions = {}) {
  const writes: RecordedWrite[] = []
  const policyWrites: unknown[] = []
  const record = recorder(writes)
  const rows: QuarantineRequestFixture[] = [
    quarantineRequest(QUARANTINE_IDS.liveProposal, 'fp-00', 'test_checkout_flow_0', 'PROPOSED'),
    quarantineRequest(QUARANTINE_IDS.quarantined, 'fp-q2', 'test_quarantine_due', 'QUARANTINED', {
      approved_at: isoAgo(4),
      quarantine_start: isoAgo(4),
      stale_at: new Date(NOW.getTime() + 10 * DAY_MS).toISOString(),
      sla_days: 14,
    }),
    quarantineRequest(QUARANTINE_IDS.stale, 'fp-q3', 'test_quarantine_stale', 'QUARANTINED', {
      approved_at: isoAgo(20),
      quarantine_start: isoAgo(20),
      stale_at: isoAgo(6),
      stale: true,
      sla_days: 14,
    }),
    quarantineRequest(QUARANTINE_IDS.released, 'fp-q4', 'test_quarantine_released', 'RELEASED', { approved_at: isoAgo(30) }),
    ...Array.from({ length: options.extraQuarantined ?? 0 }, (_, i) =>
      quarantineRequest(`00000000-0000-4000-8000-0000000010${String(i).padStart(2, '0')}`, `fp-qx${i}`, `test_quarantine_extra_${i}`, 'QUARANTINED', {
        approved_at: isoAgo(3),
        quarantine_start: isoAgo(3),
        stale_at: new Date(NOW.getTime() + 11 * DAY_MS).toISOString(),
        sla_days: 14,
      }),
    ),
  ]
  const count = (status: string) => rows.filter((r) => r.status === status).length
  const stats = () => ({
    detected: count('DETECTED'),
    proposed: count('PROPOSED'),
    approved: count('APPROVED'),
    quarantined: count('QUARANTINED'),
    recheck_scheduled: count('RECHECK_SCHEDULED'),
    re_quarantined: count('RE_QUARANTINED'),
    released: count('RELEASED'),
    rejected: count('REJECTED'),
    expired: count('EXPIRED'),
    total_live: rows.filter((r) => !SETTLED.includes(r.status)).length,
  })
  const decide = (verb: string, apply: (row: QuarantineRequestFixture) => void) =>
    [
      new RegExp(`^/api/v1/quarantine/[^/]+/${verb}$`),
      (request: ApiRequest) => {
        record(request)
        const id = request.path.split('/')[4]
        const row = rows.find((r) => r.id === id)
        if (!row) return respond(404, { detail: 'Quarantine request not found' })
        apply(row)
        row.updated_at = NOW.toISOString()
        return row
      },
      'POST',
    ] as const
  let proposed = 0
  const handlers: ApiHandlers = [
    ['/api/v1/quarantine/stats', stats],
    ['/api/v1/quarantine', ({ url }) => (url.searchParams.get('live_only') === 'true' ? rows.filter((r) => !SETTLED.includes(r.status)) : rows)],
    [
      '/api/v1/quarantine',
      (request) => {
        const body = record(request) as { test_fingerprint: string; test_name?: string; suite_name?: string }
        proposed += 1
        const row = quarantineRequest(`00000000-0000-4000-8000-0000000011${String(proposed).padStart(2, '0')}`, body.test_fingerprint, body.test_name ?? body.test_fingerprint, 'PROPOSED', {
          suite_name: body.suite_name ?? null,
          detection_method: 'manual',
          proposed_at: NOW.toISOString(),
          created_at: NOW.toISOString(),
        })
        rows.push(row)
        return row
      },
      'POST',
    ],
    decide('approve', (row) => {
      row.status = 'QUARANTINED'
      row.approved_at = NOW.toISOString()
      row.approved_by_user_id = USER.id
      row.quarantine_start = NOW.toISOString()
      row.sla_days = WORLD_SLA_DAYS
      row.stale_at = SLA_DUE_AFTER_APPROVAL
    }),
    decide('reject', (row) => {
      row.status = 'REJECTED'
      row.rejected_at = NOW.toISOString()
    }),
    decide('release', (row) => {
      row.status = 'RELEASED'
    }),
    ...flakyHandlers([], policyWrites),
  ]
  return { rows, writes, policyWrites, handlers, active: () => rows.filter((r) => ACTIVE.includes(r.status)) }
}
