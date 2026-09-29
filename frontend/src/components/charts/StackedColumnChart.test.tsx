/**
 * VIZ-104 K1 — `StackedColumnChart`, drawn by REAL Recharts (only the
 * `ResponsiveContainer` is given a size, because jsdom lays nothing out), so
 * what is asserted is what reaches the SVG: the status decals, the legend
 * swatches, the measured-zero tick, the gap note, hostile labels as text, and
 * the keyboard cursor reading a column in the tooltip's own words.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import StackedColumnChart, {
  FLAT_AXIS_HEIGHT,
  MAX_CATEGORY_COLUMNS,
  MIN_CATEGORY_LABEL_CHARS,
  SLANT_ANGLE,
  STEEP_SLANT_ANGLE,
  VALUE_AXIS_MIN_WIDTH,
  barAxisLayout,
  columnAxisLayout,
  drawsLabel,
  estimateTextWidth,
  stackRowMark,
  valueAxisWidth,
} from './StackedColumnChart'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { CHART_VARS, STATUS_ENCODING } from './tokens'
import { stackedColumnTipContent } from './stackedColumnModel'
import { tooltipText } from './tooltip'
import { readTooltip } from './tooltipTestUtils'
import {
  HOSTILE_BUCKET_LABEL,
  HOSTILE_SERIES_LABEL,
  hostileLabelsFixture,
  seriesMonthlyFixture,
  singleBucketFixture,
  statusDailyFixture,
} from './__fixtures__/stackedColumn'

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 640, height: 280 })
        : null,
  }
})

const draw = (node: ReactNode) => render(<ChartAnnouncerProvider>{node}</ChartAnnouncerProvider>)

/** The `<pattern>` a status's bars and legend swatch are filled with. */
function patternOf(container: HTMLElement, status: string): Element {
  const found = container.querySelector(`pattern[id$="-${STATUS_ENCODING[status as 'passed'].patternId}"]`)
  expect(found, `a pattern for ${status}`).not.toBeNull()
  return found as Element
}

describe('StackedColumnChart · what reaches the SVG', () => {
  it('draws a real plot with Recharts’ accessibility layer off and a named cursor surface instead', () => {
    const { container } = draw(<StackedColumnChart model={statusDailyFixture} title="Execution trend" animate={false} />)
    expect(container.querySelector('svg.recharts-surface')).not.toBeNull()
    expect(container.querySelectorAll('[role="application"]')).toHaveLength(0)
    const surface = container.querySelector('[data-chart-cursor]') as HTMLElement
    expect(surface.getAttribute('role')).toBe('group')
    expect(surface.getAttribute('aria-label')).toMatch(/^Execution trend/)
    expect(surface.getAttribute('aria-label')).toMatch(/arrow keys/i)
  })

  it('fills each status with its own colour AND decal: Skipped and Broken differ in both', () => {
    const { container } = draw(<StackedColumnChart model={statusDailyFixture} animate={false} />)
    for (const status of ['passed', 'failed', 'broken', 'skipped'] as const) {
      const pattern = patternOf(container, status)
      expect(pattern.getAttribute('data-chart-pattern')).toBe(STATUS_ENCODING[status].decal)
      expect(pattern.querySelector('rect')?.getAttribute('fill')).toBe(CHART_VARS.status[status])
    }
    // The decal is drawn, not only named: every status but Passed cuts marks out of its fill.
    expect(patternOf(container, 'broken').children.length).toBeGreaterThan(1)
    expect(patternOf(container, 'skipped').children.length).toBeGreaterThan(1)
  })

  it('fills the columns and the legend swatches with the SAME pattern per series', () => {
    const { container } = draw(<StackedColumnChart model={statusDailyFixture} animate={false} />)
    const barFills = new Set(Array.from(container.querySelectorAll('.recharts-bar-rectangle path'), (el) => el.getAttribute('fill')))
    const legend = Array.from(container.querySelectorAll('[data-chart-legend] li'))
    expect(legend.map((li) => li.textContent)).toEqual(['Passed', 'Failed', 'Broken', 'Skipped'])
    expect(legend.map((li) => li.getAttribute('data-legend-status'))).toEqual(['passed', 'failed', 'broken', 'skipped'])
    for (const li of legend) {
      const fill = li.querySelector('[data-legend-swatch]')?.getAttribute('fill')
      expect(fill).toMatch(/^url\(#.+\)$/)
      expect(barFills.has(fill as string), `${li.textContent}'s swatch fill is on a column`).toBe(true)
    }
  })

  it('gives non-status series series colours and category decals', () => {
    const { container } = draw(<StackedColumnChart model={seriesMonthlyFixture} animate={false} />)
    const patterns = Array.from(container.querySelectorAll('pattern[id*="-chart-pattern-series-"]'))
    expect(patterns.map((p) => p.querySelector('rect')?.getAttribute('fill'))).toEqual(CHART_VARS.series.slice(0, 3))
    expect(patterns.map((p) => p.getAttribute('data-chart-pattern'))).toEqual(['solid', 'diagonal', 'crosshatch'])
  })

  it('marks a MEASURED zero on the baseline, and never a gap', () => {
    const { container } = draw(<StackedColumnChart model={statusDailyFixture} animate={false} />)
    expect(container.querySelector('[data-stacked-zero="2026-03-07"]')).not.toBeNull()
    expect(container.querySelector('[data-stacked-zero="2026-03-04"]')).toBeNull()
    expect(container.querySelectorAll('[data-stacked-zero]')).toHaveLength(1)
  })

  it('states the gap under the plot in words, in the caller’s noun', () => {
    const { container } = draw(<StackedColumnChart model={statusDailyFixture} animate={false} bucketNoun="day" />)
    expect(container.querySelector('[data-chart-gap-note]')?.textContent).toBe(
      '1 day has no measured value (—), drawn as a gap rather than 0.',
    )
    expect(container.querySelector('figure')?.getAttribute('data-stacked-gaps')).toBe('1')
    const clean = draw(<StackedColumnChart model={singleBucketFixture} animate={false} />)
    expect(clean.container.querySelector('[data-chart-gap-note]')).toBeNull()
  })

  it('draws one bucket as one column of four segments', () => {
    const { container } = draw(<StackedColumnChart model={singleBucketFixture} animate={false} />)
    // Passed 42, failed 3, broken 1, skipped 0: three drawn segments (a zero draws none).
    expect(container.querySelectorAll('.recharts-bar-rectangle path').length).toBe(3)
    expect(container.querySelectorAll('.recharts-xAxis .recharts-cartesian-axis-tick')).toHaveLength(1)
  })
})

describe('StackedColumnChart · hostile labels are text', () => {
  it('puts bucket and series names in the DOM as text, never as markup', () => {
    const { container } = draw(<StackedColumnChart model={hostileLabelsFixture} title="Suites" animate={false} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    // The axis draws the (middle-truncated) name as text…
    const ticks = Array.from(container.querySelectorAll('.recharts-cartesian-axis-tick-value'), (el) => el.textContent)
    expect(ticks.some((text) => text?.startsWith('<img src=x'))).toBe(true)
    // …the legend the series name whole.
    expect(screen.getByText(HOSTILE_SERIES_LABEL)).toBeInTheDocument()
  })

  it('reads a hostile column through the keyboard cursor as text, in the tooltip’s words', () => {
    const { container } = draw(<StackedColumnChart model={hostileLabelsFixture} title="Suites" animate={false} />)
    const surface = container.querySelector('[data-chart-cursor]') as HTMLElement
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const readout = container.querySelector('[data-chart-readout]') as HTMLElement
    expect(readout).not.toBeNull()
    expect(readout.querySelector('img, script')).toBeNull()
    expect(tooltipText(readTooltip(readout))).toBe(tooltipText(stackedColumnTipContent(hostileLabelsFixture, 0)))
    expect(readout.textContent).toContain(HOSTILE_BUCKET_LABEL)
    expect(document.querySelector('[data-chart-announcer="assertive"]')?.textContent).toContain(HOSTILE_BUCKET_LABEL)
  })
})

describe('StackedColumnChart · keyboard', () => {
  it('walks columns in order and reads a gap as not measured', () => {
    const { container } = draw(<StackedColumnChart model={statusDailyFixture} title="Execution trend" animate={false} bucketNoun="day" />)
    const surface = container.querySelector('[data-chart-cursor]') as HTMLElement
    const gap = statusDailyFixture.buckets.findIndex((bucket) => bucket.key === '2026-03-04')
    for (let i = 0; i <= gap; i++) fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const readout = container.querySelector('[data-chart-readout]') as HTMLElement
    expect(readTooltip(readout).title).toBe('Mar 4')
    expect(readout.textContent).toMatch(/not measured/)
    expect(readout.textContent).not.toMatch(/Passed\s*0/)
  })
})

describe('columnAxisLayout · the bucket axis is sized from the labels', () => {
  const labels = ['Mar 1', 'Mar 2', 'Mar 3']
  /** A font 12 % wider than the estimate: DejaVu Sans against Segoe UI, as the Linux runner draws them. */
  const wider = (text: string, size: number) => estimateTextWidth(text, size) * 1.12

  it('lays labels flat, every one, when they fit their column', () => {
    expect(columnAxisLayout(labels, 120, estimateTextWidth)).toMatchObject({ angle: 0, height: FLAT_AXIS_HEIGHT, interval: 0 })
  })

  it('thins flat TIME labels to every Nth when they nearly fit', () => {
    const layout = columnAxisLayout(labels, 25, estimateTextWidth, { xType: 'time' })
    expect(layout.angle).toBe(0)
    expect(layout.interval).toBeGreaterThan(0)
  })

  it('counts a thinned time axis back from the NEWEST bucket, so today is always labelled (R2 G2, R1 F16)', () => {
    for (let count = 1; count <= 90; count++) {
      for (let interval = 0; interval <= 6; interval++) {
        expect(drawsLabel(count - 1, count, interval)).toBe(true)
      }
    }
    // Fourteen days at every 3rd: today, and every third day back from it; not the oldest.
    const drawn = Array.from({ length: 14 }, (_, i) => i).filter((i) => drawsLabel(i, 14, 2))
    expect(drawn).toEqual([1, 4, 7, 10, 13])
  })

  it('slants long labels, and the axis grows with the WIDTH the font gives them — not a fixed reserve', () => {
    const long = ['integration-tests/payments/checkout', 'integration-tests/cart']
    const narrow = columnAxisLayout(long, 30, estimateTextWidth)
    const wide = columnAxisLayout(long, 30, wider)
    expect(narrow.angle).toBe(-SLANT_ANGLE)
    expect(wide.height).toBeGreaterThan(narrow.height)
    // The slanted label's lowest point fits: anchor + its drop + its line's depth.
    const drop = wide.longest * Math.sin((SLANT_ANGLE * Math.PI) / 180)
    expect(wide.height).toBeGreaterThanOrEqual(8 + drop)
  })

  it('never thins a CATEGORY axis, at any column width (R2’s accepted design call)', () => {
    const suites = ['checkout', 'payments-api', 'search', 'integration-tests/payments/checkout-flow', 'cart', 'login-sso']
    for (let band = 4; band <= 240; band += 2) {
      for (const measure of [estimateTextWidth, wider]) {
        const layout = columnAxisLayout(suites, band, measure)
        expect(layout.interval, `band ${band}`).toBe(0)
      }
    }
  })

  it('slants a category axis at 30°, then 45° when the columns are too narrow for 30°', () => {
    const suites = ['checkout-service', 'payments-api', 'search-indexer', 'cart-and-basket']
    // 11 px text: a line is 13.75 px, so 30° needs bands of 27.5 px and 45° of 19.4 px.
    expect(columnAxisLayout(suites, 40, estimateTextWidth).angle).toBe(-SLANT_ANGLE)
    expect(columnAxisLayout(suites, 22, estimateTextWidth).angle).toBe(-STEEP_SLANT_ANGLE)
    expect(columnAxisLayout(suites, 22, estimateTextWidth).orientation).toBe('columns')
    // Narrower than a line even at 45°: bars, never overlapping names.
    expect(columnAxisLayout(suites, 16, estimateTextWidth).orientation).toBe('bars')
  })

  it('cuts long category names in the middle to fit a capped axis, down to 10 characters, then turns to bars', () => {
    const long = ['integration-tests/payments/checkout-flow-with-saved-card', 'integration-tests/cart/merge-anonymous']
    const roomy = columnAxisLayout(long, 40, estimateTextWidth, { maxHeight: 200 })
    expect(roomy).toMatchObject({ orientation: 'columns', angle: -SLANT_ANGLE, maxChars: 24 })
    const capped = columnAxisLayout(long, 40, estimateTextWidth, { maxHeight: 60 })
    expect(capped.orientation).toBe('columns')
    expect(capped.maxChars).toBeLessThan(24)
    expect(capped.maxChars).toBeGreaterThanOrEqual(MIN_CATEGORY_LABEL_CHARS)
    expect(capped.height).toBeLessThanOrEqual(61)
    // No room even for 10 characters: the bar form, not a name cut to nothing.
    expect(columnAxisLayout(long, 40, estimateTextWidth, { maxHeight: 30 }).orientation).toBe('bars')
  })

  it('draws more than a dozen categories as horizontal bars, whatever the width', () => {
    const many = Array.from({ length: MAX_CATEGORY_COLUMNS + 1 }, (_, i) => `suite-${i}`)
    expect(columnAxisLayout(many, 400, estimateTextWidth).orientation).toBe('bars')
    expect(columnAxisLayout(many, 0, estimateTextWidth).orientation).toBe('bars')
    expect(columnAxisLayout(many.slice(0, MAX_CATEGORY_COLUMNS), 400, estimateTextWidth).orientation).toBe('columns')
    // A time axis of the same count stays columns: days thin, they do not turn into rows.
    expect(columnAxisLayout(many, 400, estimateTextWidth, { xType: 'time' }).orientation).toBe('columns')
  })

  it('sizes the bar form’s name axis from the names, cut in the middle at 40% of the chart', () => {
    const names = ['checkout', 'integration-tests/payments/checkout-flow-with-saved-card']
    const wide = barAxisLayout(names, 1000, estimateTextWidth)
    expect(wide.maxChars).toBe(24)
    const narrow = barAxisLayout(names, 300, estimateTextWidth)
    expect(narrow.maxChars).toBeLessThan(24)
    expect(narrow.width).toBeLessThanOrEqual(300 * 0.4)
  })

  it('pins the bar form’s tooltip beside the whole stack of the hovered row', () => {
    const scale = (value: number) => 100 + value * 2
    expect(stackRowMark(30, 16, 50, scale)).toEqual({ left: 100, top: 42, width: 60, height: 16 })
    // A measured-zero row is a point at the axis, still a place to pin beside.
    expect(stackRowMark(0, 16, 50, scale)).toEqual({ left: 100, top: 42, width: 0, height: 16 })
    expect(stackRowMark(30, 16, undefined, scale)).toBeNull()
    expect(stackRowMark(30, 16, 50, undefined)).toBeNull()
  })

  it('lies flat with every label where no width is known yet (no layout)', () => {
    expect(columnAxisLayout(labels, 0, estimateTextWidth)).toMatchObject({ angle: 0, height: FLAT_AXIS_HEIGHT, interval: 0 })
  })

  it('sizes the value axis from its widest tick label, never under Recharts’ default', () => {
    expect(valueAxisWidth(['0', '50'], null)).toBe(VALUE_AXIS_MIN_WIDTH)
    expect(valueAxisWidth(['0', '5'], estimateTextWidth)).toBe(VALUE_AXIS_MIN_WIDTH)
    const long = ['0', '1,250,000 h']
    expect(valueAxisWidth(long, wider)).toBeGreaterThan(valueAxisWidth(long, estimateTextWidth))
    expect(valueAxisWidth(long, wider)).toBeGreaterThan(VALUE_AXIS_MIN_WIDTH)
  })

  it('draws the chart with the layout it computed', () => {
    const { container } = draw(<StackedColumnChart model={statusDailyFixture} animate={false} />)
    const plot = container.querySelector('[data-stacked-column-plot]') as HTMLElement
    // jsdom: no width, so flat and every label — the fallback every unit test sees.
    expect(plot.getAttribute('data-stacked-axis-angle')).toBe('0')
    expect(plot.getAttribute('data-stacked-axis-height')).toBe(String(FLAT_AXIS_HEIGHT))
  })
})
