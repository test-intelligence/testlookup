/**
 * The notification events, their labels and the project policy's reach
 * (NTF-01 / NTF-02). The ONLY home of the event labels: My notifications'
 * checkboxes and the policy card's state-change list both read them here.
 *
 * What each policy switch governs is the server's code (W09 contract §1.3):
 *   - `per_run_events_enabled` gates ONLY run_failed / run_passed /
 *     high_failure_rate (notification/manager.py);
 *   - `transitions_enabled` and `enabled_events` gate the seven test.*
 *     state-change events (notification_transitions.py);
 *   - ai_analysis_complete, quality_gate_failed, the two "became slower"
 *     events and run.missed (TRND-03) are governed by neither.
 * Pure: no React, no request.
 */
import type { NotificationEventType, TransitionPolicy } from '@/types/notifications'

/** Sent once per run, only when the project's "Notify on every run" is on. */
export const PER_RUN_EVENTS: readonly NotificationEventType[] = ['run_failed', 'run_passed', 'high_failure_rate']

/** The state-change events, in the server's `schemas.TRANSITION_EVENT_VALUES` order. */
export const TRANSITION_EVENTS: readonly NotificationEventType[] = [
  'test.newly_failing',
  'test.recovered',
  'test.newly_flaky',
  'test.quarantined',
  'test.unquarantined',
  'test.quarantine_stale',
  'test.ready_to_unquarantine',
]

export const EVENT_LABELS: Record<NotificationEventType, string> = {
  run_failed: 'Run failed',
  run_passed: 'Run passed',
  high_failure_rate: 'High failure rate',
  ai_analysis_complete: 'AI analysis complete',
  quality_gate_failed: 'Quality gate failed',
  flaky_test_detected: 'Flaky test detected',
  // Transition events — fire on state changes only (alert-fatigue fix)
  'test.newly_failing': 'Test newly failing (transition)',
  'test.recovered': 'Test recovered (transition)',
  'test.newly_flaky': 'Test newly flaky (transition)',
  'test.quarantined': 'Test quarantined (transition)',
  'test.unquarantined': 'Test released from quarantine (transition)',
  // Quarantine lifecycle events (US-5.4 / US-5.5)
  'test.quarantine_stale': 'Quarantine past its SLA (lifecycle)',
  'test.ready_to_unquarantine': 'Quarantined test ready to release (lifecycle)',
  // Duration regressions (EXEC-05): one toggle stands for both.
  'test.newly_slow': 'A test or suite became slower',
  'suite.newly_slow': 'A test or suite became slower',
  // Expected run cadence (TRND-03): opt-in, governed by no project policy switch.
  'run.missed': 'An expected run was missed',
}

/**
 * Never offered: "Flaky test detected" had no producer; it is
 * `test.newly_flaky` (the server maps it on write, migration 0220 rewrote the
 * stored rows).
 */
export const HIDDEN_EVENTS: readonly NotificationEventType[] = ['flaky_test_detected']

/** The server's alias rule (`schemas.LEGACY_EVENT_ALIASES`), mirrored. */
const LEGACY_EVENT_ALIASES: Readonly<Record<string, NotificationEventType>> = {
  flaky_test_detected: 'test.newly_flaky',
}

/**
 * The server's `normalise_notification_events`: alias-mapped, first
 * occurrence kept, order kept. A non-string entry is dropped.
 */
export function normaliseEvents(events: readonly string[]): NotificationEventType[] {
  const out: NotificationEventType[] = []
  if (!Array.isArray(events)) return out
  for (const raw of events) {
    if (typeof raw !== 'string') continue
    const ev = (LEGACY_EVENT_ALIASES[raw] ?? raw) as NotificationEventType
    if (!out.includes(ev)) out.push(ev)
  }
  return out
}

/** The two "became slower" events, offered as one checkbox (one message names both). */
export const SLOW_EVENTS: readonly NotificationEventType[] = ['test.newly_slow', 'suite.newly_slow']

export interface EventOption {
  key: string
  label: string
  events: NotificationEventType[]
}

/** One checkbox per option, in the labels' order; the hidden events are never offered. */
export const EVENT_OPTIONS: readonly EventOption[] = [
  ...(Object.keys(EVENT_LABELS) as NotificationEventType[])
    .filter(ev => !SLOW_EVENTS.includes(ev) && !HIDDEN_EVENTS.includes(ev))
    .map(ev => ({ key: ev, label: EVENT_LABELS[ev], events: [ev] })),
  { key: 'became-slower', label: EVENT_LABELS['test.newly_slow'], events: [...SLOW_EVENTS] },
]

export type Suppression = 'per_run_off' | 'transitions_off' | 'event_off'

/** Why the project's policy keeps `event` from being sent, or null (sent, or not governed, or no policy read). */
export function suppressionFor(event: NotificationEventType, policy: TransitionPolicy | null): Suppression | null {
  if (!policy) return null
  if (PER_RUN_EVENTS.includes(event)) {
    return policy.per_run_events_enabled ? null : 'per_run_off'
  }
  if (TRANSITION_EVENTS.includes(event)) {
    if (!policy.transitions_enabled) return 'transitions_off'
    return policy.enabled_events.includes(event) ? null : 'event_off'
  }
  return null
}

/**
 * An option that stands for several events is "not sent" only when every one
 * of them is; it then reads as its first event's suppression.
 */
export function optionSuppression(events: readonly NotificationEventType[], policy: TransitionPolicy | null): Suppression | null {
  if (!policy || events.length === 0) return null
  const each = events.map(e => suppressionFor(e, policy))
  return each.every(s => s !== null) ? each[0] : null
}

/** The §1.3 hint under an event's label; `projectName` is rendered as React text by the caller. */
export function suppressionText(s: Suppression, projectName: string): string {
  switch (s) {
    case 'per_run_off':
      return `Not sent in ${projectName}: per-run notifications are off`
    case 'transitions_off':
      return `Not sent in ${projectName}: state-change notifications are off`
    case 'event_off':
      return `Not sent in ${projectName}: this state change is turned off`
  }
}

/** All Projects: no policy is read, one muted line instead of the hints. */
export const ALL_PROJECTS_POLICY_NOTE = "Run and state-change events depend on each project's notification policy."

/** For a QA lead or admin after each hint: the policy card's anchor (not a single-project route). */
export const POLICY_LINK_TEXT = 'Change it in Email (SMTP) & channels'
export const POLICY_LINK_PATH = '/settings/notifications#notification-policy'
