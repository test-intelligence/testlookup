/**
 * W22b ACT-06 — the pure copy of the purge schedule and the inheritance markers.
 *
 * Every sentence the schedule field, the inherited markers and the Instance
 * defaults card show is pinned here once. Timestamps are built inside each test.
 */
import { describe, expect, it } from 'vitest'
import {
  formatFieldValue,
  formatLocalDateTime,
  formatUtcDateTime,
  HOURS,
  hourLabel,
  inheritedValue,
  inheritPayload,
  markerLine,
  modesFromPolicy,
  PAUSE_LABEL,
  PROJECT_VALUE_LABEL,
  SCHEDULE_OFF_SENTENCE,
  scheduleLine,
  type MarkerSource,
} from './scheduleModel'

function policyWithSources(overrides: Partial<MarkerSource> = {}): MarkerSource {
  return {
    enabled: false,
    raw_events_days: 90,
    runs_days: 180,
    artifacts_days: 180,
    audit_days: 2555,
    keep_last_runs: 10,
    purge_window_start_utc: 2,
    field_sources: {
      enabled: 'default',
      raw_events_days: 'project',
      runs_days: 'instance',
      artifacts_days: 'default',
      audit_days: 'default',
      keep_last_runs: 'default',
      purge_window_start_utc: 'default',
    },
    inherit_values: {
      enabled: false,
      raw_events_days: 90,
      runs_days: 180,
      artifacts_days: 180,
      audit_days: 2555,
      keep_last_runs: 10,
      purge_window_start_utc: 2,
    },
    ...overrides,
  }
}

describe('HOURS', () => {
  it('lists the 24 window hours 00:00 … 23:00', () => {
    expect(HOURS).toHaveLength(24)
    expect(HOURS[0]).toEqual({ value: 0, label: '00:00' })
    expect(HOURS[3]).toEqual({ value: 3, label: '03:00' })
    expect(HOURS[23]).toEqual({ value: 23, label: '23:00' })
    expect(hourLabel(9)).toBe('09:00')
  })
})

describe('markerLine', () => {
  it('reads an instance value as "<value> (instance default)"', () => {
    expect(markerLine('runs_days', policyWithSources())).toBe('180 days (instance default)')
  })

  it('reads a code default as "<value> (default)" per unit', () => {
    const policy = policyWithSources()
    expect(markerLine('artifacts_days', policy)).toBe('180 days (default)')
    expect(markerLine('keep_last_runs', policy)).toBe('10 runs (default)')
    expect(markerLine('purge_window_start_utc', policy)).toBe('02:00 UTC (default)')
    expect(markerLine('enabled', policy)).toBe('Off (default)')
  })

  it('reads a project value as "Project value"', () => {
    expect(markerLine('raw_events_days', policyWithSources())).toBe(PROJECT_VALUE_LABEL)
    expect(PROJECT_VALUE_LABEL).toBe('Project value')
  })

  it('shows nothing when the server sent no sources (absence is not a value)', () => {
    const { field_sources: _omit, ...legacy } = policyWithSources()
    expect(markerLine('runs_days', legacy)).toBeNull()
    expect(markerLine('runs_days', { ...legacy, field_sources: null })).toBeNull()
    expect(markerLine('runs_days', { ...legacy, field_sources: { runs_days: 'elsewhere' } })).toBeNull()
  })

  it('falls back to inherit_values when the effective value is absent', () => {
    const policy: MarkerSource = {
      field_sources: { runs_days: 'default' },
      inherit_values: { runs_days: 365 },
    }
    expect(markerLine('runs_days', policy)).toBe('365 days (default)')
    expect(markerLine('runs_days', policy, 'days')).toBe('365 days (default)')
  })

  it('formats each unit once', () => {
    expect(formatFieldValue(1, 'days')).toBe('1 day')
    expect(formatFieldValue(1, 'runs')).toBe('1 run')
    expect(formatFieldValue(true, 'switch')).toBe('On')
    expect(formatFieldValue(23, 'hour')).toBe('23:00 UTC')
  })

  it('names the inherited value Reset to inherited puts in the input', () => {
    expect(inheritedValue('raw_events_days', policyWithSources())).toBe(90)
    expect(inheritedValue('raw_events_days', {})).toBeNull()
  })
})

describe('scheduleLine', () => {
  it('reads the next purge in UTC', () => {
    const next = new Date(Date.UTC(2026, 9, 29, 3, 0, 0)).toISOString()
    const line = scheduleLine({ enabled: true, next_scheduled_purge_at: next })
    expect(line).toEqual({ state: 'next', text: 'Next scheduled purge Oct 29, 2026, 03:00 UTC' })
  })

  it('reads a pause in the viewer\'s local time, over the next purge', () => {
    const paused = new Date(2026, 10, 4, 14, 0, 0).toISOString()
    const next = new Date(Date.UTC(2026, 10, 5, 3, 0, 0)).toISOString()
    const line = scheduleLine({ enabled: true, purge_paused_until: paused, next_scheduled_purge_at: next })
    expect(line).toEqual({ state: 'paused', text: 'Paused until Nov 4, 2026, 14:00' })
  })

  it('says purging is off when disabled', () => {
    expect(scheduleLine({ enabled: false, next_scheduled_purge_at: null })).toEqual({
      state: 'off',
      text: SCHEDULE_OFF_SENTENCE,
    })
    expect(SCHEDULE_OFF_SENTENCE).toBe('Scheduled purging is off for this project.')
  })

  it('says nothing for an enabled policy without a next purge (an older server)', () => {
    expect(scheduleLine({ enabled: true })).toEqual({ state: 'unknown', text: null })
  })

  it('takes an injected formatter', () => {
    const line = scheduleLine(
      { enabled: true, next_scheduled_purge_at: '2026-10-29T03:00:00Z' },
      (iso, zone) => `${zone}:${iso.slice(0, 10)}`,
    )
    expect(line.text).toBe('Next scheduled purge utc:2026-10-29')
  })

  it('formats dates by hand, never "Invalid Date"', () => {
    expect(formatUtcDateTime('not a date')).toBe('—')
    expect(formatLocalDateTime(null)).toBe('—')
    expect(PAUSE_LABEL).toBe('Pause 7 days')
  })
})

describe('inheritPayload', () => {
  it('maps the saved sources to modes; none without sources', () => {
    const modes = modesFromPolicy(policyWithSources())
    expect(modes.raw_events_days).toBe('own')
    expect(modes.runs_days).toBe('inherit')
    expect(modes.purge_window_start_utc).toBe('inherit')
    expect(modesFromPolicy({})).toEqual({})
  })

  it('keeps inherited fields out of the PUT when nothing changed', () => {
    const saved = policyWithSources()
    const plan = inheritPayload({ modes: modesFromPolicy(saved) }, saved)
    expect(plan.inherit).toEqual([])
    expect(plan.override).toEqual([])
    expect(plan.inherited).toEqual([
      'enabled',
      'runs_days',
      'artifacts_days',
      'audit_days',
      'keep_last_runs',
      'purge_window_start_utc',
    ])
  })

  it('sends Reset to inherited as inherit, and an Override as a value', () => {
    const saved = policyWithSources()
    const modes = { ...modesFromPolicy(saved), raw_events_days: 'inherit', runs_days: 'own' } as const
    const plan = inheritPayload({ modes }, saved)
    expect(plan.inherit).toEqual(['raw_events_days'])
    expect(plan.override).toEqual(['runs_days'])
    expect(plan.inherited).not.toContain('runs_days')
  })

  it('leaves a policy without sources to today\'s diff', () => {
    expect(inheritPayload({ modes: {} }, { runs_days: 365 })).toEqual({
      inherit: [],
      override: [],
      inherited: [],
    })
  })
})
