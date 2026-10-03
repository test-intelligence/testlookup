/**
 * The drill ladder's model (VIZ-602 / 603): the level a URL path names, what
 * each level asks, what its marks offer and where they go, which rows they
 * open, and which open rows are this section's.
 */
import { describe, expect, it } from 'vitest'
import type { DrillLevel, EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'
import type { ChartMark } from '@/components/charts/marks'
import {
  DRILL_TITLE,
  LADDER_DROP_WORDS,
  LADDER_TOP_N,
  answersLevel,
  drillLevels,
  ladderFromPath,
  ladderIntents,
  learnSuiteLabels,
  levelChart,
  levelRequest,
  levelSuite,
  ownRows,
  rowsChart,
  rowsSelectors,
  rowsTitle,
  stepLabel,
  type LadderLevel,
} from './FailuresDrill.model'

const suite = (value: string): DrillLevel => ({ dimension: 'suite', value })
const status = (value: string): DrillLevel => ({ dimension: 'status', value })
const SUITES: LadderLevel = { kind: 'suites' }
const IN_PAYMENTS: LadderLevel = { kind: 'statuses', suite: 'payments' }
const FAILED: LadderLevel = { kind: 'status-suites', status: 'failed' }
const LEAF: LadderLevel = { kind: 'tests', suite: 'payments', status: 'failed' }

const mark = (over: Partial<ChartMark> = {}): ChartMark => ({ dimension: 'suite', value: 'payments', label: 'Payments', y: 6, n: 6, ...over })
const SEGMENT = mark({ context: [{ dimension: 'status', value: 'failed' }] })

describe('ladderFromPath', () => {
  it.each([
    [[], SUITES, 0],
    [[suite('payments')], IN_PAYMENTS, 1],
    [[status('failed')], FAILED, 1],
    [[suite('payments'), status('failed')], LEAF, 2],
    [[status('failed'), suite('payments')], LEAF, 2],
  ] as const)('%j -> its level', (path, level, used) => {
    expect(ladderFromPath(path, null)).toEqual({ level, used, dropped: [] })
  })

  it('a dimension the ladder does not walk is cut there, and said', () => {
    expect(ladderFromPath([suite('payments'), { dimension: 'failure_category', value: 'assertion' }], null)).toEqual({
      level: IN_PAYMENTS,
      used: 1,
      dropped: [LADDER_DROP_WORDS.unsupported],
    })
    expect(ladderFromPath([{ dimension: 'test', value: 'fp-1' }, suite('payments')], null)).toEqual({
      level: SUITES,
      used: 0,
      dropped: [LADDER_DROP_WORDS.unsupported],
    })
  })

  it('a suite the page filter does not allow is cut (case and spaces aside, a suite the filter names is kept)', () => {
    expect(ladderFromPath([status('failed'), suite('cart')], ['Payments', 'Checkout'])).toEqual({
      level: FAILED,
      used: 1,
      dropped: [LADDER_DROP_WORDS.outOfScope],
    })
    expect(ladderFromPath([suite('payments')], ' Payments ')).toEqual({ level: IN_PAYMENTS, used: 1, dropped: [] })
    expect(ladderFromPath([suite('payments')], [])).toEqual({ level: IN_PAYMENTS, used: 1, dropped: [] })
  })

  it('"no suite" and the Other roll-up are not suites a request can name', () => {
    for (const value of ['(none)', '__other__', '  ']) {
      expect(ladderFromPath([suite(value)], null)).toEqual({ level: SUITES, used: 0, dropped: [LADDER_DROP_WORDS.outOfScope] })
    }
  })

  it('a status outside the vocabulary is not applied (the URL validator already refuses it; the model does not trust that)', () => {
    expect(ladderFromPath([status('FAILED')], null).level).toEqual(SUITES)
  })
})

describe('levelRequest / levelSuite / levelChart', () => {
  it('each level asks one chart-data question', () => {
    expect(levelRequest(SUITES)).toEqual({ metric: 'executions', group_by: ['suite', 'status'] })
    expect(levelRequest(IN_PAYMENTS)).toEqual({ metric: 'executions', group_by: ['status'] })
    expect(levelRequest(FAILED)).toEqual({ metric: 'failed', group_by: ['suite'] })
    expect(levelRequest(LEAF)).toEqual({ metric: 'failed', group_by: ['test'], top_n: LADDER_TOP_N })
  })

  it('a suite level replaces the page suite filter with its suite; the others keep the page filter', () => {
    expect([SUITES, IN_PAYMENTS, FAILED, LEAF].map(levelSuite)).toEqual([null, 'payments', null, 'payments'])
  })

  it('titles each level in words, the suite as the reader saw it', () => {
    expect(levelChart(SUITES, '')).toEqual({ title: DRILL_TITLE, variant: 'stacked', dimension: 'Suite', valueAxisLabel: 'Executions' })
    expect(levelChart(IN_PAYMENTS, 'Payments').title).toBe('Results in Payments by status')
    expect(levelChart(FAILED, '')).toEqual({
      title: 'Failed results by suite',
      variant: 'ranked',
      dimension: 'Suite',
      valueAxisLabel: 'Failed executions',
    })
    expect(levelChart(LEAF, 'Payments')).toEqual({
      title: 'Failed tests in Payments',
      variant: 'ranked',
      dimension: 'Test',
      valueAxisLabel: 'Failed executions',
      topN: LADDER_TOP_N,
    })
  })
})

describe('answersLevel', () => {
  const series = (dimensions: string[]): SeriesChart => ({ kind: 'series', dimensions, x_type: 'category', series: [] })
  const meta = (suites: string[]) => ({ scope: { suites } }) as unknown as EnvelopeMeta

  it('the response must have the level’s dimensions, in order', () => {
    expect(answersLevel(series(['suite', 'status']), null, SUITES)).toBe(true)
    expect(answersLevel(series(['status', 'suite']), null, SUITES)).toBe(false)
    expect(answersLevel(series(['suite']), null, SUITES)).toBe(false)
    expect(answersLevel(series(['suite']), null, FAILED)).toBe(true)
    expect(answersLevel(null, null, SUITES)).toBe(false)
    expect(answersLevel({ kind: 'matrix' } as unknown as SeriesChart, null, SUITES)).toBe(false)
  })

  it('a suite level’s response must have applied exactly its suite (the previous suite’s response is not it)', () => {
    expect(answersLevel(series(['status']), meta(['payments']), { kind: 'statuses', suite: 'Payments' })).toBe(true)
    expect(answersLevel(series(['status']), meta(['cart']), IN_PAYMENTS)).toBe(false)
    expect(answersLevel(series(['status']), meta(['payments', 'cart']), IN_PAYMENTS)).toBe(false)
    expect(answersLevel(series(['status']), null, IN_PAYMENTS)).toBe(false)
    expect(answersLevel(series(['test']), meta(['payments']), LEAF)).toBe(true)
  })
})

describe('ladderIntents', () => {
  it('L0: a suite drills, opens its rows, and filters the page when the page has a filter', () => {
    expect(ladderIntents(SUITES, SEGMENT, true)).toEqual(['drill', 'rows', 'filter'])
    expect(ladderIntents(SUITES, SEGMENT, false)).toEqual(['drill', 'rows'])
  })

  it('"no suite" cannot be drilled into (no request can name it): its rows only, and the filter is the host’s to refuse', () => {
    expect(ladderIntents(SUITES, mark({ value: '(none)', label: '(none)' }), false)).toEqual(['rows'])
  })

  it('a status level drills and opens rows, never filters (status is not a global dimension)', () => {
    expect(ladderIntents(IN_PAYMENTS, mark({ dimension: 'status', value: 'failed' }), true)).toEqual(['drill', 'rows'])
  })

  it('a suite on a status level drills, opens rows and filters', () => {
    expect(ladderIntents(FAILED, mark(), true)).toEqual(['drill', 'rows', 'filter'])
  })

  it('the leaf offers "View rows" only (EPIC edge: leaf level reached)', () => {
    expect(ladderIntents(LEAF, mark({ dimension: 'test', value: 'fp-1' }), true)).toEqual(['rows'])
  })

  it('a mark of a dimension the level does not draw, or the Other roll-up, offers nothing', () => {
    expect(ladderIntents(SUITES, mark({ dimension: 'test' }), true)).toEqual([])
    expect(ladderIntents(IN_PAYMENTS, mark(), true)).toEqual([])
    expect(ladderIntents(LEAF, mark(), true)).toEqual([])
    expect(ladderIntents(SUITES, mark({ value: '__other__' }), true)).toEqual([])
  })
})

describe('drillLevels', () => {
  it('a segment drills two levels at once (the AC: "payments" failed segment -> tests in payments, failed)', () => {
    expect(drillLevels(SUITES, SEGMENT)).toEqual([suite('payments'), status('failed')])
  })

  it('a whole bar drills one', () => {
    expect(drillLevels(SUITES, mark())).toEqual([suite('payments')])
    expect(drillLevels(IN_PAYMENTS, mark({ dimension: 'status', value: 'broken' }))).toEqual([status('broken')])
    expect(drillLevels(FAILED, mark({ value: 'cart' }))).toEqual([suite('cart')])
  })

  it('the leaf drills nowhere', () => {
    expect(drillLevels(LEAF, mark({ dimension: 'test', value: 'fp-1' }))).toEqual([])
  })
})

describe('rows', () => {
  it('each level’s rows chart is the chart it drew (the leaf adds the status: [test] alone is the scatter’s)', () => {
    expect(rowsChart(SUITES)).toEqual({ metric: 'executions', groupBy: ['suite', 'status'] })
    expect(rowsChart(IN_PAYMENTS)).toEqual({ metric: 'executions', groupBy: ['status'] })
    expect(rowsChart(FAILED)).toEqual({ metric: 'failed', groupBy: ['suite'] })
    expect(rowsChart(LEAF)).toEqual({ metric: 'failed', groupBy: ['test', 'status'] })
  })

  it('a mark’s selectors are its keys; the leaf’s add the level’s status', () => {
    expect(rowsSelectors(SUITES, SEGMENT)).toEqual([suite('payments'), status('failed')])
    expect(rowsSelectors(SUITES, mark())).toEqual([suite('payments')])
    expect(rowsSelectors(LEAF, mark({ dimension: 'test', value: 'fp-1' }))).toEqual([{ dimension: 'test', value: 'fp-1' }, status('failed')])
  })

  it('every selector a level writes is in that level’s rows group_by (the server refuses any other)', () => {
    const cases: [LadderLevel, ChartMark][] = [
      [SUITES, SEGMENT],
      [SUITES, mark()],
      [IN_PAYMENTS, mark({ dimension: 'status', value: 'failed' })],
      [FAILED, mark()],
      [LEAF, mark({ dimension: 'test', value: 'fp-1' })],
    ]
    for (const [level, m] of cases) {
      const groupBy = rowsChart(level).groupBy
      expect(rowsSelectors(level, m).every((s) => groupBy.includes(s.dimension))).toBe(true)
      expect(ownRows(level, rowsSelectors(level, m))).toEqual(rowsSelectors(level, m))
    }
  })

  it('open rows of another shape are another section’s', () => {
    const test = { dimension: 'test', value: 'fp-1' } as DrillLevel
    expect(ownRows(SUITES, [test])).toEqual([])
    expect(ownRows(LEAF, [test])).toEqual([])
    expect(ownRows(LEAF, [test, status('broken')])).toEqual([])
    expect(ownRows(SUITES, [{ dimension: 'error_signature', value: 'x' }])).toEqual([])
    expect(ownRows(IN_PAYMENTS, [suite('payments')])).toEqual([])
    expect(ownRows(FAILED, [status('failed')])).toEqual([])
    expect(ownRows(SUITES, [])).toEqual([])
  })
})

describe('labels', () => {
  it('learns the suites’ display spelling from a suite response; hostile keys stay data', () => {
    const series: SeriesChart = {
      kind: 'series',
      dimensions: ['suite', 'status'],
      x_type: 'category',
      x_labels: JSON.parse('{"payments":"Payments","__proto__":"<img src=x>"}') as Record<string, string>,
      series: [],
    }
    const labels = learnSuiteLabels(series)
    expect(labels).toEqual({ 'suite~payments': 'Payments', 'suite~__proto__': '<img src=x>' })
    expect(stepLabel(labels, suite('payments'))).toBe('Payments')
    expect(stepLabel(labels, suite('constructor'))).toBe('constructor')
    expect(stepLabel({}, status('failed'))).toBe('failed')
    expect(learnSuiteLabels({ ...series, dimensions: ['status'] })).toEqual({})
    expect(learnSuiteLabels(null)).toEqual({})
  })

  it('the rows title names each selector as the reader saw it', () => {
    expect(rowsTitle({ 'suite~payments': 'Payments' }, [suite('payments'), status('failed')])).toBe('Payments, failed')
  })
})
