/**
 * W23a FAIL-11 / TRND-03: the defensive parsers and the pure copy helpers of `types/insights.ts` and
 * `types/runCadence.ts`.
 *
 * A malformed answer is `null` (the reader says "Could not load …" instead of crashing); an unknown insight
 * kind is DROPPED, never rendered; missing optional fields are defaulted; a link is allowed only for a
 * listed path or a run's Changes tab.
 */
import { describe, expect, it } from 'vitest'
import { INSIGHT_KINDS, NOT_MEASURED, isInsightPath, parseInsightList } from './insights'
import { missedLine, parseCadenceStatus, weekdaysLabel, type CadenceWindow } from './runCadence'
import {
  SENTINEL_SUITE,
  cadence,
  cadenceStatus,
  detector,
  insight,
  insightList,
  missedWindow,
  statusItem,
} from '@/test/insightCadenceMocks'

describe('parseInsightList', () => {
  it('reads the real answer as it is', () => {
    const rows = [insight(), insight({ kind: 'flake_rising', severity: 'medium' })]
    const parsed = parseInsightList(insightList(rows))
    expect(parsed?.items.map((i) => i.id)).toEqual(rows.map((r) => r.id))
    expect(parsed?.total).toBe(2)
    expect(parsed?.detectors.map((d) => d.kind)).toEqual([...INSIGHT_KINDS])
    expect(parsed?.items[0].subject_label).toBe(SENTINEL_SUITE)
  })

  it.each([
    ['a string', 'nope'],
    ['an array', []],
    ['no items', { detectors: [] }],
    ['items that are not a list', { items: {} }],
    ['a row without an id', { items: [{ kind: 'perf_drift', severity: 'high' }] }],
    ['a row that is not an object', { items: ['perf_drift'] }],
    ['detectors that are not a list', { items: [], detectors: 'four' }],
    ['a non-boolean enabled', { items: [], enabled: 'yes' }],
    ['a non-numeric total', { items: [], total: '3' }],
  ])('refuses %s', (_label, raw) => {
    expect(parseInsightList(raw)).toBeNull()
  })

  it('drops a row of an unknown kind and keeps the rest', () => {
    const known = insight()
    const parsed = parseInsightList({ ...insightList([known]), items: [{ ...insight(), kind: 'mystery_kind' }, known] })
    expect(parsed?.items.map((i) => i.id)).toEqual([known.id])
  })

  it('defaults every missing optional field', () => {
    const parsed = parseInsightList({ items: [{ id: 'row-sentinel', kind: 'coverage_drop', severity: 'low' }] })
    expect(parsed).not.toBeNull()
    expect(parsed?.enabled).toBe(true)
    expect(parsed?.range).toBe('7d')
    expect(parsed?.total).toBe(1)
    expect(parsed?.dismissed_total).toBe(0)
    expect(parsed?.items[0]).toMatchObject({
      status: 'open',
      title: '',
      evidence: {},
      detection_count: 1,
      dismissed_at: null,
      link: { path: '', label: '' },
      run_path: null,
    })
  })

  it('always returns the four detectors in order; an omitted kind is "not evaluated", never all clear', () => {
    const parsed = parseInsightList({
      items: [],
      detectors: [detector('cost_budget'), detector('perf_drift', { measured: false, reason: NOT_MEASURED.perf_no_findings })],
    })
    expect(parsed?.detectors.map((d) => d.kind)).toEqual(['perf_drift', 'flake_rising', 'coverage_drop', 'cost_budget'])
    expect(parsed?.detectors[0]).toMatchObject({ measured: false, reason: NOT_MEASURED.perf_no_findings })
    expect(parsed?.detectors[1]).toMatchObject({ measured: false, reason: NOT_MEASURED.not_evaluated, evaluated_at: null })
    expect(parsed?.detectors[3].measured).toBe(true)
  })

  it('keeps a <script> title as plain data (rendering it as text is the reader’s job)', () => {
    const title = '<script>alert(1)</script> suite sentinel'
    const parsed = parseInsightList(insightList([insight({ title })]))
    expect(parsed?.items[0].title).toBe(title)
  })
})

describe('isInsightPath', () => {
  it.each(['/trends?tab=durations', '/flaky', '/coverage?tab=history', '/settings/billing'])('allows %s', (path) => {
    expect(isInsightPath(path)).toBe(true)
  })

  it('allows a run’s Changes tab', () => {
    expect(isInsightPath('/runs/00000000-0000-4000-8000-00000000e0b2?tab=changes')).toBe(true)
  })

  it.each([
    'javascript:alert(1)',
    'https://example.com/trends?tab=durations',
    '//example.com/flaky',
    '/trends?tab=durations&suite=x',
    '/runs/not-a-uuid?tab=changes',
    '/runs/00000000-0000-4000-8000-00000000e0b2',
    '/settings/users',
    '',
  ])('refuses %s', (path) => {
    expect(isInsightPath(path)).toBe(false)
  })

  it('refuses a non-string', () => {
    expect(isInsightPath(undefined)).toBe(false)
    expect(isInsightPath(42)).toBe(false)
  })
})

describe('parseCadenceStatus', () => {
  it('reads "no cadence configured" honestly', () => {
    const parsed = parseCadenceStatus(cadenceStatus('none'))
    expect(parsed).toMatchObject({ configured: false, items: [], expected: 0, met: 0, missed: 0, paused: 0 })
  })

  it('reads a configured status with its misses', () => {
    const item = statusItem({ expected: 5, met: 3, missed: 2, missed_windows: [missedWindow(1), missedWindow(2)] })
    const parsed = parseCadenceStatus(cadenceStatus([item]))
    expect(parsed?.configured).toBe(true)
    expect(parsed?.missed).toBe(2)
    expect(parsed?.items[0].missed_windows).toHaveLength(2)
    expect(parsed?.items[0].cadence.suite_name).toBe('nightly-regression')
  })

  it.each([
    ['null', null],
    ['no items', { configured: true }],
    ['a non-boolean configured', { configured: 'yes', items: [] }],
    ['an item without a cadence', { configured: true, items: [{ expected: 1 }] }],
    ['a cadence without an id', { configured: true, items: [{ cadence: { mode: 'weekly' } }] }],
    ['missed windows that are not a list', { configured: true, items: [{ cadence: cadence(), missed_windows: 'Tue' }] }],
    ['a window without a deadline', { configured: true, items: [{ cadence: cadence(), missed_windows: [{ outcome: 'missed' }] }] }],
  ])('refuses %s', (_label, raw) => {
    expect(parseCadenceStatus(raw)).toBeNull()
  })

  it('defaults missing counts and derives the sums and configured from the items', () => {
    const parsed = parseCadenceStatus({ items: [{ cadence: { id: 'cadence-sentinel' }, missed: 2, met: 1 }] })
    expect(parsed?.configured).toBe(true)
    expect(parsed?.items[0]).toMatchObject({ expected: 0, met: 1, missed: 2, paused: 0, pending: 0, measured: false })
    expect(parsed?.items[0].cadence).toMatchObject({ id: 'cadence-sentinel', timezone: 'UTC', grace_minutes: 60, paused: false })
    expect(parsed).toMatchObject({ missed: 2, met: 1, expected: 0, total: 1, days: 14 })
  })
})

describe('missedLine', () => {
  function windowOn(deadline: string, label: string): CadenceWindow {
    return { deadline, outcome: 'missed', local_label: label }
  }

  it('says nothing when nothing was missed', () => {
    expect(missedLine({ missed: 0, missed_windows: [] })).toBe('')
  })

  it('is singular for one miss', () => {
    expect(missedLine({ missed: 1, missed_windows: [windowOn('2026-10-28T05:00:00Z', 'Wed 28 Oct')] })).toBe(
      '1 expected run missed (Wed)',
    )
  })

  it('is plural and oldest first for two (the server lists newest first)', () => {
    const windows = [windowOn('2026-10-28T05:00:00Z', 'Wed 28 Oct'), windowOn('2026-10-27T05:00:00Z', 'Tue 27 Oct')]
    expect(missedLine({ missed: 2, missed_windows: windows })).toBe('2 expected runs missed (Tue, Wed)')
  })

  it('names five days, then "and n more" from the uncapped count', () => {
    const labels = ['Mon 19 Oct', 'Tue 20 Oct', 'Wed 21 Oct', 'Thu 22 Oct', 'Fri 23 Oct', 'Mon 26 Oct', 'Tue 27 Oct', 'Wed 28 Oct']
    const windows = labels.map((label, k) => windowOn(new Date(Date.UTC(2026, 9, 19 + k, 5)).toISOString(), label)).reverse()
    expect(missedLine({ missed: 8, missed_windows: windows })).toBe(
      '8 expected runs missed (Mon, Tue, Wed, Thu, Fri, and 3 more)',
    )
  })

  it('falls back to the UTC weekday when a window has no label', () => {
    expect(missedLine({ missed: 1, missed_windows: [{ deadline: '2026-10-28T05:00:00Z', outcome: 'missed' }] })).toBe(
      '1 expected run missed (Wed)',
    )
  })
})

describe('weekdaysLabel', () => {
  it.each([
    [['mon', 'tue', 'wed', 'thu', 'fri'], 'Mon–Fri'],
    [['fri', 'mon', 'wed'], 'Mon, Wed, Fri'],
    [['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], 'Every day'],
    [['sat', 'sun'], 'Sat, Sun'],
    [['tue', 'wed', 'thu', 'tue'], 'Tue–Thu'],
    [['MON', 'Tue', 'wed'], 'Mon–Wed'],
    [['mon', 'tue', 'thu'], 'Mon, Tue, Thu'],
    [['funday'], ''],
    [[], ''],
  ])('%j -> %s', (names, label) => {
    expect(weekdaysLabel(names)).toBe(label)
  })

  it('reads null as no days', () => {
    expect(weekdaysLabel(null)).toBe('')
  })
})
