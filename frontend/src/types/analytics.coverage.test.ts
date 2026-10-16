/**
 * W08 TRND-07: the two Coverage guards. A missing or malformed answer is
 * `null` (the section says "not available"); one malformed item is dropped,
 * the rest kept; "not measured" stays `null`, never 0.
 */
import { describe, expect, it } from 'vitest'
import { parseCoverageHistory, parseUntaggedTests } from './analytics'

const point = (over: Record<string, unknown> = {}) => ({
  day: '2026-10-14',
  catalogue_tests: 120,
  exercised_tests: 90,
  exercised_pct: 75,
  suites: 6,
  ...over,
})

const history = (over: Record<string, unknown> = {}) => ({
  window_days: 90,
  measured: true,
  reason: null,
  first_snapshot_day: '2026-09-01',
  points: [point({ day: '2026-10-15' }), point()],
  meta: { as_of: '2026-10-16T00:00:00Z' },
  ...over,
})

describe('parseCoverageHistory', () => {
  it.each([
    ['undefined', undefined],
    ['null', null],
    ['{}', {}],
    ['an array', [point()]],
    ['points not an array', history({ points: 'many' })],
    ['measured missing', history({ measured: undefined })],
    ['measured a string', history({ measured: 'true' })],
    ['reason a number', history({ reason: 4 })],
    ['first_snapshot_day a number', history({ first_snapshot_day: 20261001 })],
  ])('%s → null', (_label, raw) => {
    expect(parseCoverageHistory(raw)).toBeNull()
  })

  it('a valid answer: points ascending by day, the server fields kept', () => {
    const parsed = parseCoverageHistory(history())
    expect(parsed?.points.map((p) => p.day)).toEqual(['2026-10-14', '2026-10-15'])
    expect(parsed).toMatchObject({ window_days: 90, measured: true, reason: null, first_snapshot_day: '2026-09-01' })
  })

  it('before the first snapshot: measured false with the reason, no points', () => {
    const parsed = parseCoverageHistory(
      history({ measured: false, reason: 'No coverage snapshot yet: the first is taken at 00:05 UTC.', first_snapshot_day: null, points: [] }),
    )
    expect(parsed).toMatchObject({ measured: false, first_snapshot_day: null, points: [] })
    expect(parsed?.reason).toMatch(/00:05 UTC/)
  })

  it('drops a malformed point, keeps a null exercised_pct as null (not 0)', () => {
    const parsed = parseCoverageHistory(
      history({
        points: [
          point({ day: 'yesterday' }),
          point({ catalogue_tests: '120' }),
          point({ exercised_pct: '75' }),
          7,
          point({ day: '2026-10-10', catalogue_tests: 0, exercised_tests: 0, exercised_pct: null }),
        ],
      }),
    )
    expect(parsed?.points).toEqual([{ day: '2026-10-10', catalogue_tests: 0, exercised_tests: 0, exercised_pct: null, suites: 6 }])
  })
})

const untagged = (over: Record<string, unknown> = {}) => ({
  canonical_id: 'c-1',
  test_fingerprint: 'fp-1',
  test_name: 'test_checkout_total',
  class_name: 'tests.checkout',
  catalogue_suite_id: 's-default',
  catalogue_suite: 'Default Suite (Project One)',
  executions: 12,
  last_executed_at: '2026-10-15T10:00:00Z',
  ...over,
})

const list = (over: Record<string, unknown> = {}) => ({
  items: [untagged(), untagged({ canonical_id: null, test_fingerprint: 'fp-2', catalogue_suite_id: null, catalogue_suite: null })],
  total: 61,
  page: 1,
  size: 50,
  window_days: 30,
  ...over,
})

describe('parseUntaggedTests', () => {
  it.each([
    ['undefined', undefined],
    ['null', null],
    ['{}', {}],
    ['items not an array', list({ items: { a: 1 } })],
    ['a string', 'items'],
  ])('%s → null', (_label, raw) => {
    expect(parseUntaggedTests(raw)).toBeNull()
  })

  it('a valid page: the total is the server count beyond the page, a null canonical id kept null', () => {
    const parsed = parseUntaggedTests(list())
    expect(parsed?.total).toBe(61)
    expect(parsed?.items).toHaveLength(2)
    expect(parsed?.items[1].canonical_id).toBeNull()
    expect(parsed?.items[1].catalogue_suite).toBeNull()
  })

  it('drops a malformed item (no fingerprint, wrong-typed executions, wrong-typed suite) and keeps the rest', () => {
    const parsed = parseUntaggedTests(
      list({ items: [untagged({ test_fingerprint: '' }), untagged({ executions: '12' }), untagged({ catalogue_suite: 5 }), untagged({ test_fingerprint: 'ok' })] }),
    )
    expect(parsed?.items.map((t) => t.test_fingerprint)).toEqual(['ok'])
  })

  it('a missing total falls back to the items it holds (never a made-up number)', () => {
    expect(parseUntaggedTests(list({ total: undefined }))?.total).toBe(2)
  })
})
