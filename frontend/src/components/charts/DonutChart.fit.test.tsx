/**
 * VIZ-106 fix round (B0 findings 3 and 4): a donut's ring is fitted to the
 * plot area Recharts leaves it, with every slice label MEASURED, so no label
 * is cut — "3,910 (88.1%)" lost its first digit in a 330 px frame on Linux,
 * and presentation mode (a drawing laid out 11/16 as wide) cut the ring and
 * every label. Where everything fits, the requested ring is kept exactly.
 *
 * Recharts is a stand-in that reports a chosen plot area through
 * `usePlotArea` and echoes the radii the donut asks for; the text measure is
 * a fixed 6.5 px per character (DejaVu Sans at 11 px is about that).
 */
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DonutPlot } from './DonutChart'
import { DONUT_TIP_SIDES, fitDonut, MIN_LABELLED_OUTER_RADIUS, SLICE_LABEL_OFFSET, sliceArcLabel, sliceMidAngles } from './donutFit'
import { statusDonutModel } from './DonutChart.model'

const PER_CHAR = 6.5
const measureWidth = (text: string) => text.length * PER_CHAR

const chart = vi.hoisted(() => ({ plot: undefined as { width: number; height: number } | undefined }))

vi.mock('./textMeasure', () => ({
  useTextMeasure: () => [() => {}, (text: string) => text.length * 6.5],
}))

vi.mock('recharts', () => ({
  usePlotArea: () => chart.plot,
  useXAxisScale: () => undefined,
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  PieChart: ({ children }: { children?: ReactNode }) => (
    <svg data-chart="pie">
      <g>{children}</g>
    </svg>
  ),
  Pie: ({
    children,
    data,
    label,
    innerRadius,
    outerRadius,
  }: {
    children?: ReactNode
    data?: unknown[]
    label?: ((props: object) => ReactNode) | undefined
    innerRadius?: number
    outerRadius?: number
  }) => (
    <g data-pie="" data-inner={innerRadius} data-outer={outerRadius}>
      {children}
      {typeof label === 'function' &&
        (data ?? []).map((_, index) => <g key={index}>{label({ cx: 100, cy: 100, midAngle: 45, outerRadius, index })}</g>)}
    </g>
  ),
  Cell: () => <path data-cell="" />,
  Label: () => null,
  Tooltip: () => null,
  Legend: ({ content }: { content?: () => ReactNode }) => (
    <div data-legend-host="">{typeof content === 'function' ? content() : null}</div>
  ),
}))

/** The Overview fixture of B0's finding 3: 3,910 passed of 4,437. */
const OVERVIEW = { passed: 3910, failed: 312, broken: 60, skipped: 155 }

/** Every labelled slice of a model with its mid angle and measured label width. */
function labelsOf(counts: Record<string, number>, paddingAngle = 2) {
  const model = statusDonutModel(counts)
  const mids = sliceMidAngles(
    model.slices.map((s) => s.arc),
    paddingAngle,
  )
  const labelled = model.slices.map((slice, i) => ({ slice, mid: mids[i] })).filter(({ slice }) => !slice.tiny)
  return {
    midAngles: labelled.map((l) => l.mid),
    labelWidths: labelled.map((l) => measureWidth(sliceArcLabel(l.slice))),
  }
}

/** The geometry every label must satisfy (the svg edges: plot + 5 px margin at the sides and top). */
function assertLabelsInside(
  plot: { width: number; height: number },
  outer: number,
  midAngles: readonly number[],
  widths: readonly number[],
) {
  midAngles.forEach((angle, i) => {
    const r = (angle * Math.PI) / 180
    const x = (outer + SLICE_LABEL_OFFSET) * Math.cos(r)
    const y = (outer + SLICE_LABEL_OFFSET) * Math.sin(r)
    // A label leans outward from its anchor by its whole width.
    const farX = Math.abs(x) + widths[i]
    expect(farX, `label ${i} across`).toBeLessThanOrEqual(plot.width / 2 + 5 + 1e-6)
    const room = y > 0 ? plot.height / 2 + 5 : plot.height / 2
    expect(Math.abs(y) + 7, `label ${i} up/down`).toBeLessThanOrEqual(room + 1e-6)
  })
}

describe('where a slice’s tooltip goes', () => {
  // The browser proof is chart-tooltip.spec.ts at 320 px (a fitted ring left
  // room below it, and the way down from the Broken slice crossed Passed).
  it('beside the ring, right or left, never below or above it (the way there would cross another slice)', () => {
    expect(DONUT_TIP_SIDES).toEqual(['right', 'left'])
  })
})

describe('sliceMidAngles — the angles Recharts draws the slices at', () => {
  it('runs counter-clockwise from 0, leaves the padding between non-zero slices, and spans the full ring', () => {
    // 3:1 with 2 degrees of padding per slice: 356 degrees to share.
    const [a, b] = sliceMidAngles([3, 1], 2)
    expect(a).toBeCloseTo(133.5, 6) // 0 .. 267
    expect(b).toBeCloseTo(269 + 44.5, 6) // 269 .. 358
  })

  it('a single slice has no padding: its middle is half way round', () => {
    expect(sliceMidAngles([5], 2)).toEqual([180])
  })

  it('a zero slice takes no padding and sits on its neighbour’s end; the others are drawn as without it', () => {
    const [a, zero, b] = sliceMidAngles([3, 0, 1], 2)
    expect(a).toBeCloseTo(133.5, 6)
    expect(zero).toBeCloseTo(267, 6)
    expect(b).toBeCloseTo(269 + 44.5, 6) // exactly the [3, 1] case
  })

  it('nothing to draw (every slice 0): every angle is 0, never NaN', () => {
    expect(sliceMidAngles([0, 0, 0], 2)).toEqual([0, 0, 0])
  })
})

describe('fitDonut', () => {
  it('keeps the requested ring EXACTLY where every label fits (a wide gallery frame): nothing committed moves', () => {
    const { midAngles, labelWidths } = labelsOf({ passed: 880, failed: 60, broken: 20, skipped: 40 })
    expect(fitDonut({ plot: { width: 596, height: 202 }, outerRadius: 88, innerRadius: 56, midAngles, labelWidths })).toEqual({
      outerRadius: 88,
      innerRadius: 56,
      sliceLabels: true,
    })
  })

  it('keeps the requested ring when nothing is measured yet (jsdom, a chart not laid out)', () => {
    expect(fitDonut({ plot: null, outerRadius: 88, innerRadius: 56, midAngles: [120], labelWidths: [80] })).toEqual({
      outerRadius: 88,
      innerRadius: 56,
      sliceLabels: true,
    })
  })

  it('B0 finding 3: in a 330 px frame the ring shrinks until "3,910 (88.1%)" is wholly inside the svg', () => {
    const plot = { width: 328, height: 202 }
    const { midAngles, labelWidths } = labelsOf(OVERVIEW)
    // At the old fixed 88 px the passed label's far end is past the left edge.
    expect(() => assertLabelsInside(plot, 88, midAngles, labelWidths)).toThrow()
    const fit = fitDonut({ plot, outerRadius: 88, innerRadius: 56, midAngles, labelWidths })
    expect(fit.sliceLabels).toBe(true)
    expect(fit.outerRadius).toBeLessThan(88)
    expect(fit.outerRadius).toBeGreaterThanOrEqual(MIN_LABELLED_OUTER_RADIUS)
    assertLabelsInside(plot, fit.outerRadius, midAngles, labelWidths)
    // As large as it can be: one px more and a label is cut.
    expect(() => assertLabelsInside(plot, fit.outerRadius + 1, midAngles, labelWidths)).toThrow()
    // The hole keeps its proportion.
    expect(fit.innerRadius).toBe(Math.round((fit.outerRadius * 56) / 88))
  })

  it('B0 finding 4: presentation mode lays the same frame out 11/16 as wide; the ring still fits it', () => {
    const plot = { width: Math.round((328 * 11) / 16), height: 202 }
    const { midAngles, labelWidths } = labelsOf(OVERVIEW)
    const fit = fitDonut({ plot, outerRadius: 88, innerRadius: 56, midAngles, labelWidths })
    if (fit.sliceLabels) assertLabelsInside(plot, fit.outerRadius, midAngles, labelWidths)
    expect(fit.outerRadius).toBeLessThanOrEqual(Math.min(plot.width, plot.height) / 2)
  })

  it('a label straight above the ring is fitted against the top edge, not only the sides', () => {
    const plot = { width: 600, height: 150 }
    const fit = fitDonut({ plot, outerRadius: 88, innerRadius: 56, midAngles: [90], labelWidths: [60] })
    // 150 / 2 + 5 - 7 = 73 of room above the centre, less the 14 px offset.
    expect(fit.outerRadius).toBe(59)
  })

  it('a label straight above that is wider than half the svg cannot be placed: the labels go to the legend', () => {
    // Half width 50 + the 5 px margin leaves 55 px; the label hangs 70 px to the left of its anchor.
    const fit = fitDonut({ plot: { width: 100, height: 400 }, outerRadius: 88, innerRadius: 56, midAngles: [90], labelWidths: [70] })
    expect(fit).toEqual({ outerRadius: 50, innerRadius: 32, sliceLabels: false })
  })

  it('a label at 3 o’clock is fitted by the sides alone (no height above or below it to fit)', () => {
    // 600 / 2 + 5 - 60 = 245 of room, less 14: far more than the ring's own 75 px limit.
    const fit = fitDonut({ plot: { width: 600, height: 150 }, outerRadius: 88, innerRadius: 56, midAngles: [0], labelWidths: [60] })
    expect(fit).toEqual({ outerRadius: 75, innerRadius: 48, sliceLabels: true })
  })

  it('a label with no measured width is fitted as zero wide (it is placed by its anchor alone)', () => {
    const plot = { width: 200, height: 400 }
    const unmeasured = fitDonut({ plot, outerRadius: 88, innerRadius: 56, midAngles: [0, 180], labelWidths: [40] })
    const zeroWide = fitDonut({ plot, outerRadius: 88, innerRadius: 56, midAngles: [0, 180], labelWidths: [40, 0] })
    expect(unmeasured).toEqual(zeroWide)
  })

  it('too narrow for labels beside any readable ring: the ring keeps its room and the labels go to the legend', () => {
    const plot = { width: 150, height: 202 }
    const { midAngles, labelWidths } = labelsOf(OVERVIEW)
    const fit = fitDonut({ plot, outerRadius: 88, innerRadius: 56, midAngles, labelWidths })
    expect(fit.sliceLabels).toBe(false)
    expect(fit.outerRadius).toBe(75) // the plot's half width: the ring alone fits
  })
})

describe('DonutPlot draws the fitted ring', () => {
  beforeEach(() => {
    chart.plot = undefined
  })

  it('asks Recharts for the requested radii until the plot area is known', () => {
    const { container } = render(<DonutPlot title="Status" model={statusDonutModel(OVERVIEW)} animate={false} />)
    const pie = container.querySelector('[data-pie]')
    expect(pie?.getAttribute('data-outer')).toBe('88')
    expect(pie?.getAttribute('data-inner')).toBe('56')
  })

  it('in a 330 px frame: a smaller ring, every label still drawn beside it', () => {
    chart.plot = { width: 328, height: 202 }
    const { container } = render(<DonutPlot title="Status" model={statusDonutModel(OVERVIEW)} animate={false} />)
    const outer = Number(container.querySelector('[data-pie]')?.getAttribute('data-outer'))
    expect(outer).toBeLessThan(88)
    expect(container.querySelector('[data-donut-slice-label="passed"]')?.textContent).toBe('3,910 (88.1%)')
  })

  it('too narrow for labels: none on the arcs, and the legend names every slice with its count and share', () => {
    chart.plot = { width: 150, height: 202 }
    const { container } = render(<DonutPlot title="Status" model={statusDonutModel(OVERVIEW)} animate={false} />)
    expect(container.querySelectorAll('[data-donut-slice-label]')).toHaveLength(0)
    const legend = Array.from(container.querySelectorAll('[data-chart-legend] li')).map((li) => li.textContent)
    expect(legend).toContain('Passed 3,910 (88.1%)')
    expect(legend).toContain('Failed 312 (7.0%)')
  })
})
