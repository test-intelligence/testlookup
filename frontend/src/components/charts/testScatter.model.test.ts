import { describe, expect, it } from 'vitest'
import type { PointsChart, PointsChartPoint } from '@/lib/viz/contracts'
import { tooltipText } from './tooltip'
import {
  DEFAULT_SORT,
  QUADRANTS,
  dataRect,
  excludedTotal,
  exclusionSentence,
  formatX,
  keyboardOrder,
  moveInOrder,
  nothingPlacedSentence,
  pointsInRect,
  quadrantCounts,
  quadrantOf,
  quadrantRect,
  rectFromBrushEnd,
  scatterDescription,
  scatterTakeaway,
  scatterTooltipContent,
  selectionSentence,
  sortSelection,
} from './testScatter.model'

const point = (id: string, x: number, y: number, size = 10, n = size, label = id): PointsChartPoint => ({ id, label, x, y, size, n })

function chart(points: PointsChartPoint[], medians?: { x: number; y: number }, excluded?: PointsChart['excluded']): PointsChart {
  return {
    kind: 'points',
    x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
    y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
    size: { key: 'executions', label: 'Executions' },
    points,
    ...(medians ? { medians } : {}),
    ...(excluded ? { excluded } : {}),
  }
}

describe('quadrantOf', () => {
  const medians = { x: 100, y: 0 }
  it('needs STRICTLY above both medians to be slow and flaky', () => {
    expect(quadrantOf({ x: 101, y: 0.1 }, medians)).toBe('slow-flaky')
    expect(quadrantOf({ x: 100, y: 50 }, medians)).toBe('fast-flaky')
    expect(quadrantOf({ x: 500, y: 0 }, medians)).toBe('slow-stable')
    expect(quadrantOf({ x: 1, y: 0 }, medians)).toBe('fast-stable')
  })
  it('never calls a 0% test flaky on a healthy suite whose median is 0%', () => {
    expect(quadrantOf({ x: 9999, y: 0 }, { x: 10, y: 0 })).toBe('slow-stable')
  })
})

describe('quadrantCounts', () => {
  it('counts every quadrant, zeros included', () => {
    const c = chart([point('a', 1, 0), point('b', 300, 20), point('c', 400, 0)], { x: 200, y: 0 })
    expect(quadrantCounts(c)).toEqual({ 'slow-flaky': 1, 'fast-flaky': 0, 'slow-stable': 1, 'fast-stable': 1 })
  })
  it('is all zeros without medians', () => {
    expect(Object.values(quadrantCounts(chart([])))).toEqual([0, 0, 0, 0])
  })
})

describe('pointsInRect', () => {
  const pts = [point('a', 10, 5), point('b', 20, 10), point('c', 30, 15)]
  it('includes a point on any edge (the brush border)', () => {
    expect(pointsInRect(pts, dataRect(10, 20, 5, 10))).toEqual([0, 1])
    expect(pointsInRect(pts, dataRect(20, 30, 10, 15))).toEqual([1, 2])
  })
  it('takes corners in any order', () => {
    expect(pointsInRect(pts, dataRect(30, 20, 15, 10))).toEqual([1, 2])
  })
  it('keeps data order and drops everything outside', () => {
    expect(pointsInRect(pts, dataRect(11, 29, 0, 100))).toEqual([1])
    expect(pointsInRect(pts, dataRect(0, 100, 11, 14))).toEqual([])
  })
})

describe('rectFromBrushEnd', () => {
  it('reads the data range of a rect brush', () => {
    expect(rectFromBrushEnd({ areas: [{ brushType: 'rect', coordRange: [[50, 10], [80, 20]] }] })).toEqual({ x: [10, 50], y: [20, 80] })
  })
  it('is null for a cleared brush or a shape it cannot read', () => {
    expect(rectFromBrushEnd({ areas: [] })).toBeNull()
    expect(rectFromBrushEnd({})).toBeNull()
    expect(rectFromBrushEnd(null)).toBeNull()
    expect(rectFromBrushEnd({ areas: [null] })).toBeNull()
    expect(rectFromBrushEnd({ areas: [{ brushType: 'polygon', coordRange: [[1, 2], [3, 4]] }] })).toBeNull()
    expect(rectFromBrushEnd({ areas: [{ brushType: 'rect', coordRange: [[1, 2]] }] })).toBeNull()
    expect(rectFromBrushEnd({ areas: [{ brushType: 'rect', coordRange: [[1, NaN], [3, 4]] }] })).toBeNull()
    expect(rectFromBrushEnd({ areas: [{ brushType: 'rect', coordRange: [[1, 2], ['3', 4]] }] })).toBeNull()
    expect(rectFromBrushEnd({ areas: [{ brushType: 'rect', coordRange: 'x' }] })).toBeNull()
  })
})

describe('quadrantRect', () => {
  // Medians sit ON data values (x 200, y 0), the case that broke an "at or above" rectangle.
  const pts = [
    point('fast-ok', 50, 0),
    point('median-x', 200, 30),
    point('median-y', 900, 0),
    point('slow-flaky-1', 300, 0.5),
    point('slow-flaky-2', 5000, 100),
    point('fast-flaky', 20, 40),
    point('slow-ok', 250, 0),
  ]
  const c = chart(pts, { x: 200, y: 0 })

  it.each(QUADRANTS)('selects EXACTLY the %s quadrant through the inclusive brush filter', (q) => {
    const rect = quadrantRect(c, q)
    const medians = { x: 200, y: 0 }
    const expected = pts.map((p, i) => (quadrantOf(p, medians) === q ? i : -1)).filter((i) => i >= 0)
    if (rect === null) expect(expected).toEqual([])
    else expect(pointsInRect(pts, rect)).toEqual(expected)
  })

  it('puts the slow-and-flaky low edges on the first values strictly above the medians', () => {
    expect(quadrantRect(c, 'slow-flaky')).toEqual({ x: [250, 5000], y: [0.5, 100] })
  })

  it('is null for an empty quadrant and for a chart with no medians', () => {
    const healthy = chart([point('a', 10, 0), point('b', 20, 0)], { x: 15, y: 0 })
    expect(quadrantRect(healthy, 'slow-flaky')).toBeNull()
    expect(quadrantRect(chart([]), 'slow-flaky')).toBeNull()
  })
})

describe('keyboardOrder and moveInOrder', () => {
  const pts = [point('c', 30, 1), point('a', 10, 5), point('b', 10, 2), point('d', 10, 2)]
  const order = keyboardOrder(pts)
  it('walks by x, then y, then id', () => {
    expect(order).toEqual([2, 3, 1, 0])
  })
  it('starts at the first point (End: the last), steps and stops at the ends', () => {
    expect(moveInOrder(order, null, { key: 'ArrowRight', whole: false })).toBe(2)
    expect(moveInOrder(order, null, { key: 'End', whole: false })).toBe(0)
    expect(moveInOrder(order, 2, { key: 'ArrowRight', whole: false })).toBe(3)
    expect(moveInOrder(order, 2, { key: 'ArrowDown', whole: false })).toBe(3)
    expect(moveInOrder(order, 3, { key: 'ArrowLeft', whole: false })).toBe(2)
    expect(moveInOrder(order, 3, { key: 'ArrowUp', whole: false })).toBe(2)
    expect(moveInOrder(order, 2, { key: 'ArrowLeft', whole: false })).toBe(2)
    expect(moveInOrder(order, 0, { key: 'ArrowRight', whole: false })).toBe(0)
    expect(moveInOrder(order, 1, { key: 'Home', whole: false })).toBe(2)
    expect(moveInOrder(order, 1, { key: 'End', whole: false })).toBe(0)
  })
  it('is null with nothing to walk, and restarts from an index it does not know', () => {
    expect(moveInOrder([], null, { key: 'ArrowRight', whole: false })).toBeNull()
    expect(moveInOrder(order, 99, { key: 'ArrowRight', whole: false })).toBe(2)
  })
})

describe('sortSelection', () => {
  const pts = [point('1', 10, 50, 3, 3, 'b'), point('2', 99, 50, 9, 9, 'a'), point('3', 5, 80, 1, 1, 'c'), point('4', 7, 50, 2, 2, 'a')]
  it('sorts by the default (failure rate, high first), ties by name then id', () => {
    expect(sortSelection(pts, [0, 1, 2, 3], DEFAULT_SORT.key, DEFAULT_SORT.direction)).toEqual([2, 1, 3, 0])
  })
  it('sorts by name by code unit, and by each number both ways', () => {
    expect(sortSelection(pts, [0, 1, 2, 3], 'label', 'asc')).toEqual([1, 3, 0, 2])
    expect(sortSelection(pts, [0, 1, 2, 3], 'x', 'asc')).toEqual([2, 3, 0, 1])
    expect(sortSelection(pts, [0, 1, 2, 3], 'x', 'desc')).toEqual([1, 0, 3, 2])
    expect(sortSelection(pts, [0, 1, 2, 3], 'size', 'desc')).toEqual([1, 0, 3, 2])
  })
  it('orders names by code unit, never by the reader’s locale ("Z" before "a")', () => {
    const named = [point('1', 1, 0, 1, 1, 'a'), point('2', 1, 0, 1, 1, 'Z')]
    expect(sortSelection(named, [0, 1], 'label', 'asc')).toEqual([1, 0])
  })
  it('sorts only the given indices and never mutates them', () => {
    const given = [3, 0]
    expect(sortSelection(pts, given, 'x', 'asc')).toEqual([3, 0])
    expect(given).toEqual([3, 0])
  })
})

describe('words', () => {
  const c = chart([point('fp', 1840.5, 12.5, 40, 38, 'test_checkout'), point('fp2', 1, 0, 5, 5)], { x: 920.75, y: 6.25 })

  it('a tooltip names the test, the values, the evaluated sample and the quadrant', () => {
    expect(tooltipText(scatterTooltipContent(c, 0))).toBe(
      'test_checkout. p95 duration (ms): 1,841 ms. Failure rate (%): 12.5%. Executions: 40. Evaluated: 38. Quadrant: Slow and flaky',
    )
  })
  it('says a p95 on the log floor may be lower', () => {
    expect(formatX(c, 1)).toBe('1 ms or less')
    expect(formatX(c, 2)).toBe('2 ms')
    expect(formatX({ ...c, x: { ...c.x, scale: 'linear' } }, 1)).toBe('1 ms')
  })
  it('a tooltip for a missing index is empty, and one without medians has no quadrant', () => {
    expect(scatterTooltipContent(c, 9)).toEqual({ rows: [] })
    expect(tooltipText(scatterTooltipContent({ ...c, medians: undefined }, 0))).not.toContain('Quadrant')
  })
  it('states every exclusion reason, as TESTS, and says so when nothing is left out', () => {
    expect(exclusionSentence({ below_min_executions: 14, no_duration: 1, no_evaluated: 2 }, 5)).toBe(
      'Not shown: 14 tests with fewer than 5 executions, 1 test with no duration, 2 tests with only skipped or unknown results.',
    )
    expect(exclusionSentence({ below_min_executions: 0, no_duration: 0, no_evaluated: 0 }, 5)).toBe('No test left out.')
    expect(exclusionSentence(undefined, 5)).toBe('No test left out.')
    expect(excludedTotal({ below_min_executions: 14, no_duration: 1, no_evaluated: 2 })).toBe(17)
    expect(excludedTotal(undefined)).toBe(0)
  })
  it('explains an all-excluded scatter instead of drawing an empty plot', () => {
    expect(nothingPlacedSentence({ below_min_executions: 3, no_duration: 0, no_evaluated: 0 }, 5)).toBe(
      'No test can be placed on this chart: 3 tests with fewer than 5 executions.',
    )
    expect(nothingPlacedSentence(undefined, 5)).toBe('No test can be placed on this chart.')
  })
  it('takes away the salient quadrant count, and nothing with no points', () => {
    expect(scatterTakeaway(c)).toBe('1 of 2 tests slow and flaky (above both medians)')
    expect(scatterTakeaway(chart([]))).toBeUndefined()
  })
  it('describes the axes and the scale', () => {
    expect(scatterDescription('Duration vs failure rate', c)).toBe(
      'Duration vs failure rate: 2 tests, p95 duration (ms), log scale across, Failure rate (%) up, size is executions.',
    )
    expect(scatterDescription('T', { ...c, x: { ...c.x, scale: 'linear' } })).toContain('(ms) across')
  })
  it('counts the selection', () => {
    expect(selectionSentence(0)).toBe('No test selected')
    expect(selectionSentence(1)).toBe('1 test selected')
    expect(selectionSentence(1200)).toBe('1,200 tests selected')
  })
})
