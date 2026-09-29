/**
 * VIZ-104 K1 — the bucket axis as a laid-out browser sizes it. jsdom has no
 * layout and no canvas, so the container width and the text measure are
 * supplied here: a 640 px card, and a font whose widths are the estimate
 * scaled (1.0 ≈ Segoe UI, 1.12 ≈ DejaVu Sans on the Linux CI runner). What is
 * asserted is that the drawn axis FOLLOWS the measure — slanted when labels do
 * not fit, taller in a wider font — rather than a reserve tuned to one font.
 */
import { render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import StackedColumnChart, {
  FLAT_AXIS_HEIGHT,
  SLANT_ANGLE,
  StackedBarTooltip,
  StackedColumnTooltip,
  estimateTextWidth,
} from './StackedColumnChart'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { buildStackedColumnModel } from './stackedColumnModel'
import { STATUS_SERIES, longWindowFixture, statusDailyFixture } from './__fixtures__/stackedColumn'

const font = vi.hoisted(() => ({ factor: 1 }))

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

vi.mock('./chartLayout', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./chartLayout')>()
  return { ...actual, useContainerWidth: () => [() => {}, 640] }
})

vi.mock('./textMeasure', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./textMeasure')>()
  return {
    ...actual,
    useTextMeasure: () => [() => {}, (text: string, size: number) => estimateTextWidth(text, size) * font.factor],
  }
})

const draw = (node: ReactNode) => render(<ChartAnnouncerProvider>{node}</ChartAnnouncerProvider>)
const plotOf = (container: HTMLElement) => container.querySelector('[data-stacked-column-plot]') as HTMLElement

describe('StackedColumnChart · a laid-out axis', () => {
  it('keeps two weeks of short day labels flat, in a 640 px card', () => {
    font.factor = 1
    const { container } = draw(<StackedColumnChart model={statusDailyFixture} animate={false} />)
    expect(plotOf(container).getAttribute('data-stacked-axis-angle')).toBe('0')
    expect(plotOf(container).getAttribute('data-stacked-axis-height')).toBe(String(FLAT_AXIS_HEIGHT))
  })

  it('slants and thins sixty days, anchoring each label at its end', () => {
    font.factor = 1
    const { container } = draw(<StackedColumnChart model={longWindowFixture} animate={false} />)
    const plot = plotOf(container)
    expect(plot.getAttribute('data-stacked-axis-angle')).toBe(String(-SLANT_ANGLE))
    expect(Number(plot.getAttribute('data-stacked-axis-interval'))).toBeGreaterThan(0)
    expect(Number(plot.getAttribute('data-stacked-axis-height'))).toBeGreaterThan(FLAT_AXIS_HEIGHT)
    const tick = container.querySelector('.recharts-cartesian-axis-tick-value')
    expect(tick?.getAttribute('text-anchor')).toBe('end')
  })

  it('labels the NEWEST day of a thinned time axis, whatever the stride (R2 G2, R1 F16)', () => {
    font.factor = 1
    const { container } = draw(<StackedColumnChart model={longWindowFixture} animate={false} />)
    const drawn = [...container.querySelectorAll('.recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value')]
      .map((tick) => tick.textContent ?? '')
      .filter((text) => text !== '')
    const buckets = longWindowFixture.buckets
    expect(drawn.length).toBeGreaterThan(1)
    expect(drawn.length).toBeLessThan(buckets.length)
    expect(drawn[drawn.length - 1]).toBe(buckets[buckets.length - 1].label)
  })

  it('names every category column: slanted, never thinned', () => {
    font.factor = 1.12
    const suites = buildStackedColumnModel({
      buckets: Array.from({ length: 10 }, (_, i) => ({
        key: `suite-${i}`,
        label: `integration-tests/payments/suite-${i}`,
        values: { passed: 10 + i, failed: i % 3 },
      })),
      series: STATUS_SERIES.slice(0, 2),
      valueTitle: 'Executions',
      bucketTitle: 'Suite',
    })
    const { container } = draw(<StackedColumnChart model={suites} animate={false} />)
    const plot = plotOf(container)
    expect(plot.getAttribute('data-stacked-orientation')).toBe('columns')
    expect(plot.getAttribute('data-stacked-axis-interval')).toBe('0')
    const drawn = [...container.querySelectorAll('.recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value')].map((tick) => tick.textContent)
    expect(drawn.filter((text) => text)).toHaveLength(10)
  })

  it('turns more than a dozen categories into horizontal bars, every name beside its bar', () => {
    font.factor = 1
    const suites = buildStackedColumnModel({
      buckets: Array.from({ length: 16 }, (_, i) => ({ key: `suite-${i}`, label: `suite ${i}`, values: { passed: 10 + i, failed: i % 3 } })),
      series: STATUS_SERIES.slice(0, 2),
      valueTitle: 'Executions',
      bucketTitle: 'Suite',
    })
    const { container } = draw(<StackedColumnChart model={suites} animate={false} />)
    expect(plotOf(container).getAttribute('data-stacked-orientation')).toBe('bars')
    const names = [...container.querySelectorAll('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value')].map((tick) => tick.textContent)
    expect(names).toEqual(suites.buckets.map((bucket) => bucket.label))
  })

  it('gives a wider font a taller axis — sized from the label, not tuned to one font', () => {
    const long = buildStackedColumnModel({
      buckets: Array.from({ length: 10 }, (_, i) => ({
        key: `suite-${i}`,
        label: `integration-tests/payments/suite-${i}`,
        values: { passed: 10 + i, failed: i % 3 },
      })),
      series: STATUS_SERIES.slice(0, 2),
      valueTitle: 'Executions',
      bucketTitle: 'Suite',
    })
    font.factor = 1
    const narrow = Number(plotOf(draw(<StackedColumnChart model={long} animate={false} />).container).getAttribute('data-stacked-axis-height'))
    font.factor = 1.12
    const wide = Number(plotOf(draw(<StackedColumnChart model={long} animate={false} />).container).getAttribute('data-stacked-axis-height'))
    expect(wide).toBeGreaterThan(narrow)
  })

  it('widens the value axis for long tick labels in the measured font', () => {
    font.factor = 1.12
    const big = buildStackedColumnModel({
      buckets: [{ key: 'a', label: 'A', values: { passed: 1_250_000 } }],
      series: STATUS_SERIES.slice(0, 1),
      valueTitle: 'Executions',
      bucketTitle: 'Day',
      format: (value) => `${value.toLocaleString('en-US')} executions`,
    })
    const { container } = draw(<StackedColumnChart model={big} animate={false} />)
    const axis = container.querySelector('.recharts-yAxis')
    expect(axis).not.toBeNull()
    // The plot starts right of a value axis wider than Recharts' 60 px default.
    const firstBar = container.querySelector('.recharts-bar-rectangle path')
    expect(Number(firstBar?.getAttribute('x'))).toBeGreaterThan(60)
  })
})

describe('StackedColumnChart · notes and the pointer tooltip', () => {
  it('says how many values were not usable numbers', () => {
    const bad = buildStackedColumnModel({
      buckets: [{ key: 'a', label: 'A', values: { passed: Number.NaN, failed: 2 } }],
      series: STATUS_SERIES.slice(0, 2),
      valueTitle: 'Executions',
      bucketTitle: 'Day',
    })
    const { container } = draw(<StackedColumnChart model={bad} animate={false} />)
    expect(container.querySelector('[data-chart-invalid-note]')?.textContent).toBe(
      '1 value was not a count or amount and is shown as not measured (—).',
    )
  })

  it('shows the hovered column’s content, and nothing for an inactive or unknown column', () => {
    const open = render(<StackedColumnTooltip active label="2026-03-09" coordinate={{ x: 100, y: 50 }} model={statusDailyFixture} />)
    expect(open.container.textContent).toContain('Mar 9')
    expect(open.container.textContent).toContain('208')
    const idle = render(<StackedColumnTooltip active={false} label="2026-03-09" model={statusDailyFixture} />)
    expect(idle.container.textContent).toBe('')
    const unknown = render(<StackedColumnTooltip active label="nope" model={statusDailyFixture} />)
    expect(unknown.container.textContent).toBe('')
  })

  it('shows the same content for a hovered row of the bar form', () => {
    const open = render(<StackedBarTooltip active label="2026-03-09" coordinate={{ x: 100, y: 50 }} model={statusDailyFixture} />)
    expect(open.container.textContent).toContain('Mar 9')
    expect(open.container.textContent).toContain('208')
    const idle = render(<StackedBarTooltip active={false} label="2026-03-09" model={statusDailyFixture} />)
    expect(idle.container.textContent).toBe('')
  })
})
