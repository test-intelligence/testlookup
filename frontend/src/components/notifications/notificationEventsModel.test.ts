/**
 * NTF-01 / NTF-02: the event vocabulary and the policy's reach (W09 contract
 * §1.3, the ONE table the hints, the docs and the tests read).
 *
 *   run_failed, run_passed, high_failure_rate  → not sent when per_run_events_enabled is false
 *   the seven test.* state changes            → not sent when transitions_enabled is false,
 *                                                or when on and the event is not in enabled_events
 *   ai_analysis_complete, quality_gate_failed,
 *   test.newly_slow / suite.newly_slow,
 *   run.missed (TRND-03)                       → never governed by the policy
 */
import { describe, expect, it } from 'vitest'
import type { NotificationEventType, TransitionPolicy } from '@/types/notifications'
import {
  ALL_PROJECTS_POLICY_NOTE,
  EVENT_LABELS,
  EVENT_OPTIONS,
  HIDDEN_EVENTS,
  PER_RUN_EVENTS,
  POLICY_LINK_PATH,
  TRANSITION_EVENTS,
  normaliseEvents,
  optionSuppression,
  suppressionFor,
  suppressionText,
} from './notificationEventsModel'

function policy(over: Partial<TransitionPolicy> = {}): TransitionPolicy {
  return {
    project_id: 'p1',
    transitions_enabled: true,
    per_run_events_enabled: false,
    enabled_events: [...TRANSITION_EVENTS],
    consecutive_failure_threshold: 2,
    is_default: true,
    ...over,
  }
}

const UNGATED: NotificationEventType[] = [
  'ai_analysis_complete',
  'quality_gate_failed',
  'test.newly_slow',
  'suite.newly_slow',
  'run.missed',
]

describe('the vocabulary', () => {
  it('TRANSITION_EVENTS is the backend order (schemas.TRANSITION_EVENT_VALUES), literally', () => {
    expect(TRANSITION_EVENTS).toEqual([
      'test.newly_failing',
      'test.recovered',
      'test.newly_flaky',
      'test.quarantined',
      'test.unquarantined',
      'test.quarantine_stale',
      'test.ready_to_unquarantine',
    ])
  })

  it('PER_RUN_EVENTS are the three the per-run switch gates', () => {
    expect(PER_RUN_EVENTS).toEqual(['run_failed', 'run_passed', 'high_failure_rate'])
  })

  it('the labels moved unchanged, and "Flaky test detected" is never offered', () => {
    expect(EVENT_LABELS.quality_gate_failed).toBe('Quality gate failed')
    expect(EVENT_LABELS['test.newly_flaky']).toBe('Test newly flaky (transition)')
    expect(HIDDEN_EVENTS).toEqual(['flaky_test_detected'])
    const labels = EVENT_OPTIONS.map(o => o.label)
    expect(labels).not.toContain('Flaky test detected')
    expect(labels).toContain('Quality gate failed')
    expect(EVENT_OPTIONS.flatMap(o => o.events)).not.toContain('flaky_test_detected')
  })

  it('"A test or suite became slower" is one option for both events, last', () => {
    const slower = EVENT_OPTIONS.filter(o => o.label === 'A test or suite became slower')
    expect(slower).toHaveLength(1)
    expect(slower[0].events).toEqual(['test.newly_slow', 'suite.newly_slow'])
    expect(EVENT_OPTIONS[EVENT_OPTIONS.length - 1]).toBe(slower[0])
  })

  // W23a TRND-03 (DELIBERATE): "An expected run was missed" is offered as ONE
  // option of its own and is governed by no project policy switch. It is
  // opt-in on the server (not in the preference default list).
  it('"An expected run was missed" is one option for run.missed, ungoverned', () => {
    expect(EVENT_LABELS['run.missed']).toBe('An expected run was missed')
    const missed = EVENT_OPTIONS.filter(o => o.events.includes('run.missed'))
    expect(missed).toHaveLength(1)
    expect(missed[0]).toEqual({ key: 'run.missed', label: 'An expected run was missed', events: ['run.missed'] })
    expect(PER_RUN_EVENTS).not.toContain('run.missed')
    expect(TRANSITION_EVENTS).not.toContain('run.missed')
    expect(HIDDEN_EVENTS).not.toContain('run.missed')
    expect(suppressionFor('run.missed', policy({ per_run_events_enabled: false, transitions_enabled: false, enabled_events: [] }))).toBeNull()
    expect(optionSuppression(['run.missed'], policy({ transitions_enabled: false }))).toBeNull()
  })
})

describe('normaliseEvents (the server alias rule, mirrored)', () => {
  it.each([
    [['flaky_test_detected'], ['test.newly_flaky']],
    [['flaky_test_detected', 'run_failed', 'test.newly_flaky'], ['test.newly_flaky', 'run_failed']],
    [['run_failed', 'flaky_test_detected', 'test.newly_flaky'], ['run_failed', 'test.newly_flaky']],
    [['run_failed', 'run_failed'], ['run_failed']],
    [[], []],
    [['run_failed', 'quality_gate_failed'], ['run_failed', 'quality_gate_failed']],
  ])('%j → %j', (input, expected) => {
    expect(normaliseEvents(input)).toEqual(expected)
  })

  it('drops non-strings and survives a non-array', () => {
    expect(normaliseEvents(['run_failed', 3, null] as unknown as string[])).toEqual(['run_failed'])
    expect(normaliseEvents(null as unknown as string[])).toEqual([])
  })
})

describe('suppressionFor over every row of §1.3', () => {
  it.each(PER_RUN_EVENTS.map(e => [e]))('%s: per-run off → per_run_off; on → sent', event => {
    expect(suppressionFor(event, policy({ per_run_events_enabled: false }))).toBe('per_run_off')
    expect(suppressionFor(event, policy({ per_run_events_enabled: true }))).toBeNull()
    // The state-change switches do not touch it.
    expect(suppressionFor(event, policy({ per_run_events_enabled: true, transitions_enabled: false, enabled_events: [] }))).toBeNull()
  })

  it.each(TRANSITION_EVENTS.map(e => [e]))('%s: transitions off / on and disabled / on and enabled', event => {
    expect(suppressionFor(event, policy({ transitions_enabled: false }))).toBe('transitions_off')
    expect(
      suppressionFor(event, policy({ transitions_enabled: true, enabled_events: TRANSITION_EVENTS.filter(e => e !== event) })),
    ).toBe('event_off')
    expect(suppressionFor(event, policy({ transitions_enabled: true, enabled_events: [event] }))).toBeNull()
    // The per-run switch does not touch it.
    expect(suppressionFor(event, policy({ per_run_events_enabled: false, enabled_events: [event] }))).toBeNull()
  })

  it.each(UNGATED.map(e => [e]))('%s is governed by no policy', event => {
    for (const p of [
      policy(),
      policy({ per_run_events_enabled: false, transitions_enabled: false, enabled_events: [] }),
      policy({ per_run_events_enabled: true, transitions_enabled: true }),
    ]) {
      expect(suppressionFor(event, p)).toBeNull()
    }
  })

  it('no policy (not read, unreadable, All Projects) → never a hint', () => {
    for (const event of [...PER_RUN_EVENTS, ...TRANSITION_EVENTS, ...UNGATED]) {
      expect(suppressionFor(event, null)).toBeNull()
    }
  })

  it('an option for several events is suppressed only when all of them are', () => {
    const off = policy({ transitions_enabled: true, enabled_events: ['test.recovered'] })
    expect(optionSuppression(['test.newly_failing', 'test.quarantined'], off)).toBe('event_off')
    expect(optionSuppression(['test.newly_failing', 'test.recovered'], off)).toBeNull()
    expect(optionSuppression(['test.newly_slow', 'suite.newly_slow'], off)).toBeNull()
    expect(optionSuppression(['run_failed'], null)).toBeNull()
    expect(optionSuppression([], off)).toBeNull()
  })
})

describe('the hint texts', () => {
  it('names the project', () => {
    expect(suppressionText('per_run_off', 'Checkout')).toBe('Not sent in Checkout: per-run notifications are off')
    expect(suppressionText('transitions_off', 'Checkout')).toBe('Not sent in Checkout: state-change notifications are off')
    expect(suppressionText('event_off', 'Checkout')).toBe('Not sent in Checkout: this state change is turned off')
  })

  it('All Projects line and the policy link', () => {
    expect(ALL_PROJECTS_POLICY_NOTE).toBe("Run and state-change events depend on each project's notification policy.")
    expect(POLICY_LINK_PATH).toBe('/settings/notifications#notification-policy')
  })
})
