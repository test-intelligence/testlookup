/**
 * VIZ-601 "Placement": the tooltip never leaves the visible part of the chart
 * and never covers the mark it describes. Pure geometry, asserted as tables.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ANY_SIDE,
  COLUMN_SIDES,
  SWEEP_MARGIN,
  alongSweep,
  approachStep,
  bandGap,
  clipRectsOf,
  columnMark,
  echartsTipPosition,
  headsFor,
  intersect,
  onSweepLine,
  placeTip,
  TIP_MIN_WIDTH,
  tipCorridor,
  tipMaxWidth,
  visibleBounds,
  type TipRect,
} from './tipPlacement'

const CHART: TipRect = { left: 0, top: 0, width: 600, height: 300 }
const TIP = { width: 150, height: 60 }

const overlaps = (a: TipRect, b: TipRect) =>
  a.left < b.left + b.width && b.left < a.left + a.width && a.top < b.top + b.height && b.top < a.top + a.height
const inside = (a: TipRect, b: TipRect) =>
  a.left >= b.left && a.top >= b.top && a.left + a.width <= b.left + b.width && a.top + a.height <= b.top + b.height

describe('placeTip', () => {
  it.each([
    // [what, mark, expected side, expected left, expected top]
    ['room on the right: beside it, centred on it', { left: 100, top: 120, width: 40, height: 20 }, 'right', 152, 100],
    ['at the right edge: flipped left', { left: 500, top: 120, width: 40, height: 20 }, 'left', 338, 100],
    ['a full-width row: below it', { left: 0, top: 100, width: 600, height: 20 }, 'below', 225, 132],
    ['a full-width row at the bottom: above it', { left: 0, top: 260, width: 600, height: 20 }, 'above', 225, 188],
    ['near the top: centred, then clamped down into the chart', { left: 100, top: 0, width: 40, height: 10 }, 'right', 152, 0],
    ['near the bottom: clamped up into the chart', { left: 100, top: 290, width: 40, height: 10 }, 'right', 152, 240],
  ] as const)('%s', (_what, mark, side, left, top) => {
    const placed = placeTip({ mark, tip: TIP, bounds: CHART })
    expect(placed).toEqual({ side, left, top, fits: true })
    const box = { left: placed.left, top: placed.top, ...TIP }
    expect(overlaps(box, mark)).toBe(false)
    expect(inside(box, CHART)).toBe(true)
  })

  it('never covers its mark and never leaves the bounds, for any mark in a grid of positions', () => {
    for (let x = 0; x <= 560; x += 40) {
      for (let y = 0; y <= 280; y += 20) {
        const mark = { left: x, top: y, width: 40, height: 20 }
        const placed = placeTip({ mark, tip: TIP, bounds: CHART })
        const box = { left: placed.left, top: placed.top, ...TIP }
        expect(placed.fits, `mark at ${x},${y}`).toBe(true)
        expect(overlaps(box, mark), `covers the mark at ${x},${y}`).toBe(false)
        expect(inside(box, CHART), `leaves the chart at ${x},${y}`).toBe(true)
      }
    }
  })

  it('a column tooltip goes left or right only, lined up with the top of the plot', () => {
    const mark = columnMark(300, 7, { top: 16, height: 200 })
    expect(placeTip({ mark, tip: TIP, bounds: CHART, sides: COLUMN_SIDES, align: 'start' })).toEqual({
      side: 'right',
      left: 319,
      top: 16,
      fits: true,
    })
  })

  it('when nothing fits, it stays VISIBLE (clamped), on the roomiest side, and says it did not fit', () => {
    const wide = { width: 700, height: 60 }
    const placed = placeTip({ mark: { left: 100, top: 100, width: 10, height: 10 }, tip: wide, bounds: CHART, sides: COLUMN_SIDES })
    expect(placed.fits).toBe(false)
    expect(placed.left).toBe(0)
    expect(placed.side).toBe('right')
  })

  it('keeps inside a bounds rectangle that does not start at the origin (a chart scrolled half out of view)', () => {
    const visible = { left: 0, top: 120, width: 600, height: 180 }
    const placed = placeTip({ mark: { left: 100, top: 130, width: 40, height: 20 }, tip: TIP, bounds: visible })
    expect(placed.top).toBe(120)
    expect(inside({ left: placed.left, top: placed.top, ...TIP }, visible)).toBe(true)
  })

  it('tries sides in the order given', () => {
    const mark = { left: 100, top: 120, width: 40, height: 20 }
    expect(placeTip({ mark, tip: TIP, bounds: CHART, sides: ['below', ...ANY_SIDE] }).side).toBe('below')
  })
})

describe('tipMaxWidth — wrap to fit BESIDE the mark rather than cover it', () => {
  // A 420 px window's worth of chart, the right-most day's column near its right edge.
  const NARROW: TipRect = { left: 0, top: 0, width: 400, height: 300 }
  const LAST_DAY = { left: 300, top: 20, width: 14, height: 240 }
  const WIDE_TIP = { width: 340, height: 150 }

  it('leaves a tooltip that fits somewhere at its natural width alone (the bounds are the only cap)', () => {
    const mark = { left: 40, top: 20, width: 14, height: 240 }
    expect(tipMaxWidth({ mark, tip: TIP, bounds: NARROW, sides: COLUMN_SIDES, align: 'start' })).toBe(NARROW.width)
  })

  it('wraps a tooltip that fits on no side to the room on the roomiest side, which then fits clear of the mark', () => {
    // Left room = 300 - 12 - 0 = 288; right room = 400 - 326 = 74.
    expect(placeTip({ mark: LAST_DAY, tip: WIDE_TIP, bounds: NARROW, sides: COLUMN_SIDES, align: 'start' }).fits).toBe(false)
    const width = tipMaxWidth({ mark: LAST_DAY, tip: WIDE_TIP, bounds: NARROW, sides: COLUMN_SIDES, align: 'start' })
    expect(width).toBe(288)
    // Wrapped, the box is taller — and now fits beside the day, clear of it.
    const wrapped = { width, height: 220 }
    const placed = placeTip({ mark: LAST_DAY, tip: wrapped, bounds: NARROW, sides: COLUMN_SIDES, align: 'start' })
    expect(placed).toMatchObject({ side: 'left', fits: true })
    expect(overlaps({ left: placed.left, top: placed.top, ...wrapped }, LAST_DAY)).toBe(false)
    expect(inside({ left: placed.left, top: placed.top, ...wrapped }, NARROW)).toBe(true)
  })

  it(`never squeezes below ${TIP_MIN_WIDTH} px: a cramped chart keeps the clamped fallback`, () => {
    const tiny: TipRect = { left: 0, top: 0, width: 300, height: 300 }
    const middle = { left: 140, top: 20, width: 14, height: 240 } // 128 px either side
    expect(tipMaxWidth({ mark: middle, tip: WIDE_TIP, bounds: tiny, sides: COLUMN_SIDES, align: 'start' })).toBe(tiny.width)
  })

  it('does not wrap a tooltip that already fits on SOME side at its natural width (here, below the bar)', () => {
    // 200 px either side of a 12 px bar: too little for 340 px, enough to wrap into —
    // but the tooltip fits whole below the bar, so it is left as it is.
    const bar = { left: 194, top: 20, width: 12, height: 20 }
    const tall: TipRect = { left: 0, top: 0, width: 400, height: 300 }
    expect(placeTip({ mark: bar, tip: WIDE_TIP, bounds: tall, sides: ANY_SIDE }).side).toBe('below')
    expect(tipMaxWidth({ mark: bar, tip: WIDE_TIP, bounds: tall, sides: ANY_SIDE })).toBe(tall.width)
  })

  it('only a left/right side is widened into: vertical-only sides keep the bounds', () => {
    expect(tipMaxWidth({ mark: LAST_DAY, tip: WIDE_TIP, bounds: NARROW, sides: ['below', 'above'] })).toBe(NARROW.width)
  })
})

describe('the visible part of a chart', () => {
  it.each([
    ['wholly visible', { left: 10, top: 10, width: 300, height: 200 }, [{ left: 0, top: 0, width: 1000, height: 800 }], { left: 0, top: 0, width: 300, height: 200 }],
    ['cut by the viewport bottom', { left: 10, top: 700, width: 300, height: 200 }, [{ left: 0, top: 0, width: 1000, height: 800 }], { left: 0, top: 0, width: 300, height: 100 }],
    [
      'cut by a scroll box on its left',
      { left: -50, top: 10, width: 300, height: 200 },
      [
        { left: 0, top: 0, width: 1000, height: 800 },
        { left: 0, top: 0, width: 200, height: 800 },
      ],
      { left: 50, top: 0, width: 200, height: 200 },
    ],
    ['scrolled wholly away: the chart box, there is no better place', { left: 10, top: 900, width: 300, height: 200 }, [{ left: 0, top: 0, width: 1000, height: 800 }], { left: 0, top: 0, width: 300, height: 200 }],
  ] as const)('%s', (_what, chart, clips, expected) => {
    expect(visibleBounds(chart, clips)).toEqual(expected)
  })

  it('intersects rectangles, and reports none for rectangles that only touch', () => {
    expect(intersect({ left: 0, top: 0, width: 10, height: 10 }, { left: 5, top: 5, width: 10, height: 10 })).toEqual({
      left: 5,
      top: 5,
      width: 5,
      height: 5,
    })
    expect(intersect({ left: 0, top: 0, width: 10, height: 10 }, { left: 10, top: 0, width: 10, height: 10 })).toBeNull()
  })
})

describe('a column band', () => {
  it.each([
    // [band, mark half, gap]
    [100, 7, 12], // a wide day: the full gap
    [30, 7, 8], // a narrow day: only as far as the band's edge
    [10, 7, 0], // the mark fills the band: flush against it
    [200, 0, 12], // a line's point: MultiSeriesChart's min(TIP_GAP, step / 2)
    [16, 0, 8],
  ])('band %d, mark half %d: the tooltip starts %d px from the mark', (band, half, gap) => {
    expect(bandGap(band, half)).toBe(gap)
  })
})

describe('clipRectsOf', () => {
  afterEach(() => vi.restoreAllMocks())

  it('collects the viewport and every ancestor that clips its overflow', () => {
    const outer = document.createElement('div')
    const scroller = document.createElement('div')
    scroller.style.overflow = 'auto'
    const chart = document.createElement('div')
    scroller.appendChild(chart)
    outer.appendChild(scroller)
    document.body.appendChild(outer)
    vi.spyOn(scroller, 'getBoundingClientRect').mockReturnValue({ left: 20, top: 30, width: 200, height: 100 } as DOMRect)
    vi.spyOn(scroller, 'clientWidth', 'get').mockReturnValue(190)
    vi.spyOn(scroller, 'clientHeight', 'get').mockReturnValue(100)
    const clips = clipRectsOf(chart)
    expect(clips[0].left).toBe(0)
    expect(clips).toContainEqual({ left: 20, top: 30, width: 190, height: 100 })
    // The plain wrapper does not clip, so it is not listed.
    expect(clips).toHaveLength(2)
    outer.remove()
  })
})

describe('echartsTipPosition', () => {
  it('places beside the mark the chart names, inside the ECharts view when there is no layout to measure', () => {
    const position = echartsTipPosition((_point, rect) => (rect ? { left: rect.x, top: rect.y, width: rect.width, height: rect.height } : null))
    const size = { contentSize: [100, 40], viewSize: [400, 200] }
    expect(position([0, 0], {}, null, { x: 50, y: 80, width: 20, height: 20 }, size)).toEqual([82, 70])
    // No mark: the pointer is the mark.
    expect(position([390, 100], {}, null, undefined, size)).toEqual([278, 80])
  })
})

// ── Wave 2.4 review A2/F3: a tooltip never gets in the way of a sweep ──────────

describe("placeTip with a sweep — off the pointer's line", () => {
  // A day's column over a 200 px plot; the box is 150 × 60.
  const day = columnMark(300, 7, { top: 16, height: 200 })
  const column = { mark: day, tip: TIP, bounds: CHART, sides: COLUMN_SIDES, align: 'start' as const }
  const covers = (top: number, height: number, line: number) => top + height > line - SWEEP_MARGIN && top < line + SWEEP_MARGIN

  it.each([
    // [pointer's y, expected top]: at the plot's top while the line is below
    // the box, else at the plot's bottom, else hugging the line.
    [150, 16],
    [60, 156],
    [20, 156],
    [216, 16],
  ])("a day's tooltip keeps off the line y = %i (top %i)", (line, top) => {
    const placed = placeTip({ ...column, sweep: { axis: 'x', at: line } })
    expect(placed).toMatchObject({ side: 'right', top, fits: true })
    expect(covers(placed.top, TIP.height, line)).toBe(false)
  })

  it('every pointer height on a day leaves the tooltip off its line, or says it does not fit', () => {
    for (let line = 0; line <= 300; line += 5) {
      const placed = placeTip({ ...column, sweep: { axis: 'x', at: line } })
      if (placed.fits) expect(covers(placed.top, TIP.height, line), `line ${line}`).toBe(false)
    }
  })

  it('a tooltip too tall to keep off the line does not fit, on either side', () => {
    const tall = { width: 150, height: 270 }
    expect(placeTip({ ...column, tip: tall, sweep: { axis: 'x', at: 150 } }).fits).toBe(false)
    // Without the sweep the same box fits beside the day (the old placement, over the line).
    expect(placeTip({ ...column, tip: tall }).fits).toBe(true)
  })

  it("a row's tooltip BESIDE its bar is across the sweep and ignores the line; one below the bar keeps off it", () => {
    const bar = { left: 100, top: 100, width: 300, height: 16 }
    // Right of the bar's end: a pointer moving down the rows never comes at it from its bar.
    expect(placeTip({ mark: bar, tip: TIP, bounds: CHART, sweep: { axis: 'y', at: 450 } })).toMatchObject({
      side: 'right',
      left: 412,
      fits: true,
    })
    // A bar across the whole chart: below it, and moved sideways off the pointer's x.
    const wide = { left: 0, top: 100, width: 600, height: 16 }
    const placed = placeTip({ mark: wide, tip: TIP, bounds: CHART, sweep: { axis: 'y', at: 300 } })
    expect(placed.side).toBe('below')
    expect(placed.left + TIP.width <= 300 - SWEEP_MARGIN || placed.left >= 300 + SWEEP_MARGIN).toBe(true)
  })

  it('alongSweep: left/right run along a sweep across days; below/above along a sweep down rows', () => {
    expect(alongSweep('right', { axis: 'x', at: 0 })).toBe(true)
    expect(alongSweep('below', { axis: 'x', at: 0 })).toBe(false)
    expect(alongSweep('below', { axis: 'y', at: 0 })).toBe(true)
    expect(alongSweep('left', { axis: 'y', at: 0 })).toBe(false)
    expect(alongSweep('right', undefined)).toBe(false)
  })

  it('tipMaxWidth judges the fit WITH the sweep, and wraps to the room beside the mark', () => {
    const wide = { width: 420, height: 60 }
    // Neither side has 420 px: wrapped to the 281 px left of the day.
    expect(tipMaxWidth({ ...column, tip: wide, sweep: { axis: 'x', at: 150 } })).toBe(281)
  })
})

describe('the approach — a tooltip holds only a pointer that came to it from its mark', () => {
  // Day at x 300 (its bar 293..307), plot 16..216; its tooltip right of it, at the top.
  const mark = { left: 293, top: 16, width: 14, height: 200 }
  const tip = { left: 319, top: 16, width: 150, height: 60 }
  const g = { mark, tip, side: 'right' as const }
  type P = { x: number; y: number }

  /** Feed a path through `approachStep`, as `PinnedTip`'s listener does. */
  function walk(points: P[], geometry = g) {
    let armed: P | null = null
    let previous: P | null = null
    return points.map((point) => {
      const step = approachStep(armed, previous, point, geometry)
      armed = step.armed
      previous = point
      return step
    })
  }

  it('the corridor runs from inside the mark to the tooltip, across the span of both', () => {
    expect(tipCorridor(mark, tip, 'right')).toEqual({ left: 295, top: 16, width: 24, height: 200 })
    // A day that is a line (zero wide): from the line itself.
    expect(tipCorridor({ left: 300, top: 16, width: 0, height: 200 }, tip, 'right')).toEqual({ left: 300, top: 16, width: 19, height: 200 })
    expect(tipCorridor(mark, { ...tip, left: 131 }, 'left')).toEqual({ left: 281, top: 16, width: 24, height: 200 })
    const bar = { left: 100, top: 100, width: 300, height: 16 }
    expect(tipCorridor(bar, { left: 250, top: 128, width: 150, height: 60 }, 'below')).toEqual({ left: 100, top: 104, width: 300, height: 24 })
  })

  it('headsFor: a move whose ray meets the tooltip, and that has not passed it', () => {
    const box = { left: 100, top: 0, width: 50, height: 50 }
    expect(headsFor({ x: 0, y: 25 }, { x: 10, y: 25 }, box)).toBe(true)
    expect(headsFor({ x: 0, y: 100 }, { x: 10, y: 90 }, box)).toBe(true) // diagonally, up to it
    expect(headsFor({ x: 0, y: 100 }, { x: 10, y: 100 }, box)).toBe(false) // along a line below it
    expect(headsFor({ x: 0, y: 25 }, { x: -10, y: 25 }, box)).toBe(false) // away from it
    expect(headsFor({ x: 0, y: 25 }, { x: 200, y: 25 }, box)).toBe(false) // already past it
    expect(headsFor({ x: 0, y: 25 }, { x: 0, y: 25 }, box)).toBe(false) // standing still
    expect(headsFor({ x: 120, y: 100 }, { x: 120, y: 90 }, box)).toBe(true) // straight up into it
    expect(headsFor({ x: 90, y: 100 }, { x: 90, y: 90 }, box)).toBe(false) // straight up beside it
  })

  it('a sweep along the days below the tooltip never arms it: every move reaches the chart', () => {
    const path = Array.from({ length: 60 }, (_, i) => ({ x: 280 + i * 5, y: 150 }))
    expect(walk(path).every((step) => !step.armed && !step.hold && !step.suppress)).toBe(true)
  })

  it('a pointer that runs into the tooltip from above, or from beyond it, is not held: its moves go on to the chart', () => {
    expect(walk(Array.from({ length: 10 }, (_, i) => ({ x: 400, y: 5 + i * 5 }))).some((step) => step.hold)).toBe(false)
    expect(walk(Array.from({ length: 30 }, (_, i) => ({ x: 560 - i * 5, y: 40 }))).some((step) => step.hold)).toBe(false)
  })

  it('a pointer that leaves its mark for the tooltip is held on it — diagonally, over the next days, kept from the chart on the way', () => {
    // From the bar, half-way down the plot, up and right to the box.
    const path = Array.from({ length: 13 }, (_, i) => ({ x: 300 + i * 5, y: 150 - i * 8 }))
    const steps = walk(path)
    const onTip = steps.findIndex((step) => step.hold)
    expect(onTip).toBeGreaterThan(1)
    // On its way, every move was kept from the chart: the day cannot change under it …
    expect(steps.slice(1, onTip).every((step) => step.suppress)).toBe(true)
    // … and once on the box it stays held while it moves about on it.
    const about = walk([...path, { x: 400, y: 40 }, { x: 440, y: 30 }, { x: 380, y: 60 }])
    expect(about.slice(-3).every((step) => step.hold)).toBe(true)
  })

  it('a pointer that turns away on its way is let go', () => {
    const steps = walk([
      { x: 300, y: 150 },
      { x: 305, y: 142 },
      { x: 310, y: 134 },
      { x: 330, y: 200 },
    ])
    expect(steps[2].suppress).toBe(true)
    expect(steps[3]).toEqual({ armed: null, hold: false, suppress: false })
  })

  it('a sweep down the rows past a tooltip beside a bar never arms it — not even just in front of its edge', () => {
    const rows = { mark: { left: 100, top: 100, width: 200, height: 16 }, tip: { left: 312, top: 80, width: 150, height: 56 }, side: 'right' as const }
    for (const x of [290, 305, 311, 400]) {
      const path = Array.from({ length: 36 }, (_, i) => ({ x, y: 60 + i * 4 }))
      expect(walk(path, rows).some((step) => step.hold || step.suppress), `x ${x}`).toBe(false)
    }
  })

  it('onSweepLine: a pointer on the line of a tooltip placed along the sweep — and only that', () => {
    const sweep = { axis: 'x' as const, at: 150 }
    expect(onSweepLine({ x: 300, y: 70 }, tip, 'right', sweep)).toBe(true)
    expect(onSweepLine({ x: 300, y: 76 + SWEEP_MARGIN / 2 }, tip, 'right', sweep)).toBe(true)
    expect(onSweepLine({ x: 300, y: 77 + SWEEP_MARGIN / 2 }, tip, 'right', sweep)).toBe(false)
    expect(onSweepLine({ x: 300, y: 70 }, tip, 'right', undefined)).toBe(false)
    // Across the sweep (right of a row's bar): never re-placed.
    expect(onSweepLine({ x: 400, y: 70 }, tip, 'right', { axis: 'y', at: 400 })).toBe(false)
  })
})
