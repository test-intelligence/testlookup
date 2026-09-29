/**
 * VIZ-104 (K3) — the sparkline's model. A sparkline draws a real series or
 * nothing: fewer than 2 measured points is no model, a `null` breaks the line,
 * a flat series is a middle line (not NaN), the series is read in the order
 * given (oldest first), and the accessible name carries latest / min / max.
 */
import { describe, expect, it } from 'vitest'
import {
  type SparklineModel,
  SPARKLINE_HEIGHT,
  SPARKLINE_INSET,
  buildSparklineModel,
  sparklineAreaPath,
  sparklineLabel,
  sparklineLinePath,
} from './Sparkline.model'

const TOP = SPARKLINE_INSET
const BOTTOM = SPARKLINE_HEIGHT - SPARKLINE_INSET
const MIDDLE = SPARKLINE_HEIGHT / 2

/** A model the test expects to exist: fails loudly instead of asserting non-null. */
function modelOf(...args: Parameters<typeof buildSparklineModel>): SparklineModel {
  const model = buildSparklineModel(...args)
  if (!model) throw new Error('expected a sparkline model')
  return model
}

/** The subpaths of a path: one per `M`. */
const subpaths = (d: string) => d.split('M').map((s) => s.trim()).filter(Boolean)

describe('buildSparklineModel', () => {
  it.each([
    ['no values', []],
    ['one value', [42]],
    ['one measured value among gaps', [null, 42, null]],
    ['only gaps', [null, null, null]],
    ['one value and non-finite noise', [7, Number.NaN, Number.POSITIVE_INFINITY]],
  ])('is null for fewer than 2 measured points (%s): no invented line', (_name, series) => {
    expect(buildSparklineModel(series)).toBeNull()
  })

  it('draws 2 points: the lower at the bottom inset, the higher at the top inset', () => {
    const model = modelOf([10, 20])
    expect(model.points.map((p) => [p.x, p.y])).toEqual([
      [0, BOTTOM],
      [100, TOP],
    ])
    expect(model.runs).toHaveLength(1)
  })

  it('breaks the line at a null: two runs, never one joined across the gap', () => {
    const model = modelOf([80, 90, null, 70, 85])
    expect(model.runs.map((run) => run.map((p) => p.value))).toEqual([
      [80, 90],
      [70, 85],
    ])
    expect(model.missing).toBe(1)
    const d = sparklineLinePath(model)
    expect(subpaths(d)).toHaveLength(2)
    // The gap keeps its slot on the axis: the second run starts at slot 3 of 0-4.
    expect(model.runs[1][0].x).toBe(75)
  })

  it('treats a non-finite number as not measured, like null — never as 0', () => {
    const model = modelOf([50, Number.NaN, 60])
    expect(model.runs).toHaveLength(2)
    expect(model.min).toBe(50)
    expect(model.points.map((p) => p.value)).toEqual([50, 60])
  })

  it('draws a lone point between two gaps as a dot, not joined to its neighbours', () => {
    const model = modelOf([10, null, 30, null, 20])
    expect(model.runs.map((run) => run.length)).toEqual([1, 1, 1])
    // Each lone point is a zero-length subpath: a round cap renders it as a dot.
    for (const sub of subpaths(sparklineLinePath(model))) {
      // `subpaths` drops the `M`: "x y L x y".
      const [x1, y1, op, x2, y2, ...more] = sub.split(' ')
      expect(op).toBe('L')
      expect(more).toEqual([])
      expect([x2, y2]).toEqual([x1, y1])
    }
  })

  it('draws a flat series as a line through the middle — no NaN from a zero span', () => {
    const model = modelOf([5, 5, 5, 5])
    expect(model.points.every((p) => p.y === MIDDLE)).toBe(true)
    const d = sparklineLinePath(model)
    expect(d).not.toMatch(/NaN|Infinity/)
    expect(d).toBe(`M 0 ${MIDDLE} L 33.33 ${MIDDLE} L 66.67 ${MIDDLE} L 100 ${MIDDLE}`)
  })

  it('draws a degenerate domain (min = max) through the middle too', () => {
    const model = modelOf([1, 9], { domain: [50, 50] })
    expect(model.points.map((p) => p.y)).toEqual([MIDDLE, MIDDLE])
  })

  it('places values in a fixed domain, and clamps the DRAWN value (never the true one) to it', () => {
    const model = modelOf([0, 50, 100, 140], { domain: [0, 100] })
    expect(model.points.map((p) => p.y)).toEqual([BOTTOM, MIDDLE, TOP, TOP])
    expect(model.points.map((p) => p.value)).toEqual([0, 50, 100, 140])
    expect(model.max).toBe(140)
  })

  it('reads the series in the order given — oldest first, the latest on the right — and never sorts it', () => {
    const model = modelOf([90, 60, 75])
    expect(model.points.map((p) => p.value)).toEqual([90, 60, 75])
    expect(model.last).toMatchObject({ index: 2, value: 75, x: 100 })
    expect(model.points[0]).toMatchObject({ value: 90, x: 0, y: TOP })
  })

  it('puts the end dot on the newest MEASURED point when the series ends in a gap', () => {
    const model = modelOf([70, 80, 90, null])
    expect(model.last).toMatchObject({ index: 2, value: 90 })
    expect(model.last.x).toBeCloseTo(66.667, 2)
  })

  it('honours a custom height, keeping the inset', () => {
    const model = modelOf([0, 1], { height: 40 })
    expect(model.points.map((p) => p.y)).toEqual([40 - SPARKLINE_INSET, SPARKLINE_INSET])
  })
})

describe('sparklineAreaPath', () => {
  it('closes one area per run of 2+ points on the bottom edge, and leaves gaps and lone points empty', () => {
    const model = modelOf([10, 20, null, 30, null, 40, 50])
    const areas = subpaths(sparklineAreaPath(model))
    expect(areas).toHaveLength(2)
    for (const a of areas) expect(a.endsWith('Z')).toBe(true)
    expect(areas[0]).toContain(`L 16.67 ${SPARKLINE_HEIGHT} L 0 ${SPARKLINE_HEIGHT} Z`)
  })
})

describe('sparklineLabel', () => {
  it('names the series and carries the count, latest, min and max', () => {
    const model = modelOf([81, 97.5, 92.4])
    expect(sparklineLabel('Pass rate', model, (v) => `${v}%`)).toBe(
      'Pass rate: 3 points, latest 92.4%, min 81%, max 97.5%',
    )
  })

  it('says how many values were not measured, and counts only measured points', () => {
    const model = modelOf([null, 1200, null, 1284])
    expect(sparklineLabel('Executions', model)).toBe(
      'Executions: 2 points, latest 1,284, min 1,200, max 1,284, 2 not measured',
    )
  })

  it('reports the true extremes even when the drawing is clamped to the domain', () => {
    const model = modelOf([-5, 120], { domain: [0, 100] })
    expect(sparklineLabel('x', model)).toBe('x: 2 points, latest 120, min -5, max 120')
  })
})
