/**
 * W08 TRND-08: `parseTeamValueMetrics`, the defensive guard over
 * `GET /api/v1/value-metrics/by-team`. A missing or malformed answer is
 * `null` (the view says "not available"); one malformed row is dropped and the
 * rest kept; "not measured" stays `null`, never 0.
 */
import { describe, expect, it } from 'vitest'
import { parseTeamValueMetrics } from './valueMetrics'

const row = (over: Record<string, unknown> = {}) => ({
  team: 'Payments',
  tests: 4,
  executions: 20,
  evaluated: 18,
  passed: 15,
  pass_rate: 83.3,
  flaky_tests: 1,
  defects: 2,
  mttr_hours: 3.25,
  hours_saved: 4.5,
  hours: { triage: 3, dedup: 1.5 },
  ...over,
})

const body = (over: Record<string, unknown> = {}) => ({
  window_days: 30,
  measured: true,
  reason: null,
  rules_configured: true,
  teams: [row(), row({ team: 'Unassigned', pass_rate: null, mttr_hours: null })],
  not_attributable_hours: 1.25,
  headline_hours: 7,
  meta: { as_of: '2026-10-16T00:00:00Z' },
  ...over,
})

describe('parseTeamValueMetrics', () => {
  it.each([
    ['undefined', undefined],
    ['null', null],
    ['an empty object', {}],
    ['an array', []],
    ['a string', 'teams'],
    ['teams not an array', body({ teams: { 0: row() } })],
    ['teams missing', body({ teams: undefined })],
    ['measured not a boolean', body({ measured: 'yes' })],
    ['rules_configured missing', body({ rules_configured: undefined })],
    ['headline_hours a string', body({ headline_hours: '7' })],
    ['not_attributable_hours missing', body({ not_attributable_hours: undefined })],
    ['reason a number', body({ reason: 3 })],
  ])('%s → null', (_label, raw) => {
    expect(parseTeamValueMetrics(raw)).toBeNull()
  })

  it('a valid answer is read as sent, null "not measured" values kept null', () => {
    const parsed = parseTeamValueMetrics(body())
    expect(parsed).not.toBeNull()
    expect(parsed?.teams.map((t) => t.team)).toEqual(['Payments', 'Unassigned'])
    expect(parsed?.teams[1].pass_rate).toBeNull()
    expect(parsed?.teams[1].mttr_hours).toBeNull()
    expect(parsed?.teams[0].hours).toEqual({ triage: 3, dedup: 1.5 })
    expect(parsed?.headline_hours).toBe(7)
    expect(parsed?.not_attributable_hours).toBe(1.25)
    expect(parsed?.rules_configured).toBe(true)
  })

  it('drops a malformed row and keeps the rest (a wrong-typed field, a blank team, a non-object)', () => {
    const parsed = parseTeamValueMetrics(
      body({ teams: [row(), row({ team: 'Bad', tests: '4' }), row({ team: '  ' }), 7, row({ team: 'Ok', pass_rate: '90' }), row({ team: 'Fine' })] }),
    )
    expect(parsed?.teams.map((t) => t.team)).toEqual(['Payments', 'Fine'])
  })

  it('a row without hour legs reads them as 0 (the total is still the row hours_saved)', () => {
    const parsed = parseTeamValueMetrics(body({ teams: [row({ hours: undefined })] }))
    expect(parsed?.teams[0].hours).toEqual({ triage: 0, dedup: 0 })
    expect(parsed?.teams[0].hours_saved).toBe(4.5)
  })

  it('no rules: the empty teams list and the reason survive', () => {
    const parsed = parseTeamValueMetrics(body({ rules_configured: false, measured: false, reason: 'No ownership rules yet.', teams: [] }))
    expect(parsed).toMatchObject({ rules_configured: false, measured: false, reason: 'No ownership rules yet.', teams: [] })
  })
})
