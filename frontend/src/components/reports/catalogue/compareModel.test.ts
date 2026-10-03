import { describe, expect, it } from 'vitest'
import { buildMultiSeriesModel, SERIES_DASHES } from '@/components/charts/multiSeriesModel'
import type { EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'
import { STATES_META } from '@/pages/dev/chartStatesFixtures'
import { mergeCompareMeta, validateCompare } from './compareData'
import {
  COMPARE_MAX_SERIES,
  compareComparability,
  compareOptions,
  compareRequests,
  compareSeries,
  compareStyles,
  effectiveCompareBy,
  pairKey,
  seriesCount,
  stylesForLines,
  tooManyReason,
  type CompareSlice,
} from './compareModel'

const R1 = '11111111-1111-4111-8111-111111111111'
const R2 = '22222222-2222-4222-8222-222222222222'

const chart = (keys: string[]): SeriesChart => ({
  kind: 'series',
  dimensions: ['day', 'suite'],
  x_type: 'time',
  series: keys.map((key) => ({
    key,
    label: key,
    points: [
      { x: '2026-09-01', y: 90, n: 10 },
      { x: '2026-09-02', y: 80, n: 10 },
    ],
  })),
})

const meta = (over: Partial<EnvelopeMeta> = {}): EnvelopeMeta => ({ ...STATES_META, ...over })

describe('compareOptions: only comparisons that draw two or more lines', () => {
  it('offers by suite, by release, and by both, as the selection allows', () => {
    expect(compareOptions([], [])).toEqual([])
    expect(compareOptions(['payments'], [])).toEqual([])
    expect(compareOptions(['payments', 'cart'], [])).toEqual(['suite'])
    expect(compareOptions([], [R1, R2])).toEqual(['release'])
    expect(compareOptions(['payments'], [R1])).toEqual([])
    expect(compareOptions(['payments'], [R1, R2])).toEqual(['release', 'suite_release'])
    expect(compareOptions(['payments', 'cart'], [R1, R2])).toEqual(['suite', 'release', 'suite_release'])
    // Duplicates are one value.
    expect(compareOptions(['payments', 'payments'], [])).toEqual([])
  })

  it('keeps the reader’s choice while the selection supports it, else the richest comparison', () => {
    const all = compareOptions(['payments', 'cart'], [R1, R2])
    expect(effectiveCompareBy(null, all)).toBe('suite_release')
    expect(effectiveCompareBy('suite', all)).toBe('suite')
    expect(effectiveCompareBy('suite', ['release'])).toBe('release')
    expect(effectiveCompareBy(null, [])).toBeNull()
  })

  it('counts the lines, and says to narrow beyond the cap', () => {
    expect(seriesCount('suite_release', ['a', 'b', 'c'], [R1, R2])).toBe(6)
    expect(seriesCount('suite', ['a', 'b', 'c'], [R1, R2])).toBe(3)
    expect(seriesCount('release', ['a'], [R1, R2])).toBe(2)
    expect(seriesCount('suite_release', ['a', 'b', 'c'], [R1, R2, 'r3'])).toBeGreaterThan(COMPARE_MAX_SERIES)
    expect(tooManyReason(9)).toMatch(/^9 lines are too many .* 8 or fewer/)
  })
})

describe('compareRequests: two group_by at most, so one request per release for pairs', () => {
  const base = { project_id: 'p', days: 30, release_id: [R2, R1], suite_name: ['cart', 'payments'] }

  it('by suite: day x suite over the chosen suites; by release: day x release over the chosen releases', () => {
    expect(compareRequests('suite', base, 'pass_rate', ['payments', 'cart'], [R1, R2])).toEqual([
      { releaseId: null, params: { ...base, metric: 'pass_rate', group_by: ['day', 'suite'], suite_name: ['cart', 'payments'] } },
    ])
    expect(compareRequests('release', base, 'failures', ['payments', 'cart'], [R2, R1])).toEqual([
      { releaseId: null, params: { ...base, metric: 'failures', group_by: ['day', 'release'], release_id: [R1, R2] } },
    ])
  })

  it('by suite and release: one day x suite request per release, in sorted order', () => {
    const requests = compareRequests('suite_release', base, 'executions', ['payments', 'cart'], [R2, R1])
    expect(requests.map((r) => r.releaseId)).toEqual([R1, R2])
    for (const r of requests) {
      expect(r.params.group_by).toEqual(['day', 'suite'])
      expect(r.params.release_id).toBe(r.releaseId)
      expect(r.params.suite_name).toEqual(['cart', 'payments'])
      expect(r.params.metric).toBe('executions')
    }
  })
})

describe('compareStyles: channels from the SORTED keys, stable across reloads', () => {
  it('pairs: colour by suite, dash by release, whatever order the selection was made in', () => {
    const a = compareStyles('suite_release', ['payments', 'cart'], [R2, R1])
    const b = compareStyles('suite_release', ['cart', 'payments'], [R1, R2])
    expect(a).toEqual(b)
    expect(a[pairKey('cart', R1)]).toEqual({ colour: 0, dash: 0 })
    expect(a[pairKey('cart', R2)]).toEqual({ colour: 0, dash: 1 })
    expect(a[pairKey('payments', R1)]).toEqual({ colour: 1, dash: 0 })
    expect(a[pairKey('payments', R2)]).toEqual({ colour: 1, dash: 1 })
  })

  it('one dimension: each line its own colour AND dash', () => {
    expect(compareStyles('suite', ['payments', 'cart'], [])).toEqual({ cart: { colour: 0, dash: 0 }, payments: { colour: 1, dash: 1 } })
  })

  it('matches the server’s lower-cased suite keys to the selection', () => {
    const lines = [{ key: 'paymentsuite', label: 'PaymentSuite', points: [] }]
    expect(stylesForLines('suite', lines, ['PaymentSuite', 'AuthSuite'], [])).toEqual({ paymentsuite: { colour: 1, dash: 1 } })
  })
})

describe('compareSeries + the multi-series model: four named lines, colour by suite, dash by release', () => {
  const slices: CompareSlice[] = [
    { releaseId: R1, chart: chart(['payments', 'cart']), meta: meta() },
    { releaseId: R2, chart: chart(['payments', 'cart']), meta: meta() },
  ]
  const names: Record<string, string> = { [R1]: 'R1', [R2]: 'R2' }

  it('names each pair "suite · release" and draws it with its suite’s colour and its release’s dash', () => {
    const series = compareSeries('suite_release', slices, (id) => names[id])
    expect(series.map((s) => s.label).sort()).toEqual(['cart · R1', 'cart · R2', 'payments · R1', 'payments · R2'])
    const model = buildMultiSeriesModel({
      series,
      metric: { kind: 'rate', title: 'Pass rate %' },
      styles: stylesForLines('suite_release', series, ['payments', 'cart'], [R1, R2]),
    })
    const byLabel = Object.fromEntries(model.lines.map((line) => [line.label, line]))
    expect(byLabel['cart · R1'].styleIndex).toBe(byLabel['cart · R2'].styleIndex)
    expect(byLabel['cart · R1'].styleIndex).not.toBe(byLabel['payments · R1'].styleIndex)
    expect(byLabel['cart · R1'].dash).toBe(byLabel['payments · R1'].dash)
    expect(byLabel['cart · R1'].dash).toBe(SERIES_DASHES[0])
    expect(byLabel['cart · R2'].dash).toBe(SERIES_DASHES[1])
    expect(model.fold).toBeNull()
  })

  it('without styles, a model keeps one slot per line for both channels (unchanged behaviour)', () => {
    const model = buildMultiSeriesModel({
      series: compareSeries('suite', [slices[0]], () => ''),
      metric: { kind: 'rate', title: 'Pass rate %' },
    })
    expect(model.lines.map((line) => [line.styleIndex, line.dash])).toEqual([
      [0, SERIES_DASHES[0]],
      [1, SERIES_DASHES[1]],
    ])
  })
})

describe('the comparison’s envelope and comparability', () => {
  it('merges the slices: every release, matched totals added, unfiltered totals kept', () => {
    const a = meta({
      scope: { ...STATES_META.scope, releases: [{ id: R1, name: 'R1', status: 'active' }] },
      totals: { matched_runs: 3, total_runs: 50, matched_executions: 30, total_executions: 500 },
    })
    const b = meta({
      scope: { ...STATES_META.scope, releases: [{ id: R2, name: 'R2', status: 'active' }] },
      totals: { matched_runs: 4, total_runs: 50, matched_executions: 40, total_executions: 500 },
    })
    const merged = mergeCompareMeta([
      { releaseId: R1, chart: chart([]), meta: a },
      { releaseId: R2, chart: chart([]), meta: b },
    ])
    expect(merged?.scope.releases.map((r) => r.id)).toEqual([R1, R2])
    expect(merged?.totals).toEqual({ matched_runs: 7, total_runs: 50, matched_executions: 70, total_executions: 500 })
  })

  it('flags the comparison when any slice is not comparable, with that slice’s reason', () => {
    const flagged = meta({ comparability: { comparable: false, reason: 'R2 has no runs yet.', reason_code: 'partial_coverage' } })
    expect(compareComparability([{ releaseId: R1, chart: chart([]), meta: meta() }])).toBeNull()
    expect(
      compareComparability([
        { releaseId: R1, chart: chart([]), meta: meta() },
        { releaseId: R2, chart: chart([]), meta: flagged },
      ]),
    ).toEqual({ comparable: false, reason: 'R2 has no runs yet.', reasonCode: 'partial_coverage' })
  })

  it('validates every slice: one invalid slice fails the comparison', () => {
    const good = { meta: STATES_META, series: chart(['payments']) }
    expect(validateCompare({ parts: [{ releaseId: R1, payload: good }, { releaseId: R2, payload: good }] }).ok).toBe(true)
    const bad = validateCompare({ parts: [{ releaseId: R1, payload: good }, { releaseId: R2, payload: { meta: STATES_META } }] })
    expect(bad.ok).toBe(false)
    if (!bad.ok) expect(bad.errors[0]).toMatch(/^slice 1: /)
  })
})
