/**
 * NTF-02: `parseTransitionPolicy` is the guard every reader of the policy goes
 * through. A missing or malformed answer is `null`, and the pages then show no
 * hint (the dispatcher fails open: a "not sent" hint over a policy that could
 * not be read would be the lie) and no form (Save would write placeholders).
 */
import { describe, expect, it } from 'vitest'
import { parseTransitionPolicy } from './notifications'

const P = '00000000-0000-4000-8000-000000000001'

const VALID = {
  project_id: P,
  transitions_enabled: true,
  per_run_events_enabled: false,
  enabled_events: ['test.newly_failing', 'test.recovered'],
  consecutive_failure_threshold: 3,
  is_default: false,
}

describe('parseTransitionPolicy', () => {
  it.each([
    ['undefined', undefined],
    ['null', null],
    ['an empty object', {}],
    ['a string', 'policy'],
    ['an array', [VALID]],
    ['a number', 7],
  ])('%s → null', (_name, raw) => {
    expect(parseTransitionPolicy(raw)).toBeNull()
  })

  it.each([
    ['project_id missing', { ...VALID, project_id: undefined }],
    ['project_id empty', { ...VALID, project_id: '' }],
    ['project_id a number', { ...VALID, project_id: 42 }],
    ['transitions_enabled a string', { ...VALID, transitions_enabled: 'true' }],
    ['transitions_enabled missing', { ...VALID, transitions_enabled: undefined }],
    ['per_run_events_enabled a number', { ...VALID, per_run_events_enabled: 1 }],
    ['per_run_events_enabled null', { ...VALID, per_run_events_enabled: null }],
    ['enabled_events a string', { ...VALID, enabled_events: 'test.recovered' }],
    ['enabled_events an object', { ...VALID, enabled_events: { 0: 'test.recovered' } }],
    ['enabled_events missing', { ...VALID, enabled_events: undefined }],
  ])('a deciding field of the wrong type (%s) → null', (_name, raw) => {
    expect(parseTransitionPolicy(raw)).toBeNull()
  })

  it('a valid answer reads as itself', () => {
    expect(parseTransitionPolicy(VALID)).toEqual(VALID)
  })

  it('the defaults answer keeps is_default true', () => {
    expect(parseTransitionPolicy({ ...VALID, is_default: true })?.is_default).toBe(true)
  })

  it('drops a non-string event, keeps the rest in order', () => {
    expect(
      parseTransitionPolicy({ ...VALID, enabled_events: ['test.recovered', 3, null, 'test.newly_flaky'] })?.enabled_events,
    ).toEqual(['test.recovered', 'test.newly_flaky'])
  })

  it('a missing or non-numeric threshold reads as the server default (2); a non-true is_default as false', () => {
    const parsed = parseTransitionPolicy({ ...VALID, consecutive_failure_threshold: 'x', is_default: 'yes' })
    expect(parsed?.consecutive_failure_threshold).toBe(2)
    expect(parsed?.is_default).toBe(false)
    expect(parseTransitionPolicy({ ...VALID, consecutive_failure_threshold: undefined })?.consecutive_failure_threshold).toBe(2)
    expect(parseTransitionPolicy({ ...VALID, consecutive_failure_threshold: Number.NaN })?.consecutive_failure_threshold).toBe(2)
  })

  it('is idempotent: a parsed policy parses to an equal one', () => {
    const once = parseTransitionPolicy(VALID)
    expect(parseTransitionPolicy(once)).toEqual(once)
  })
})
