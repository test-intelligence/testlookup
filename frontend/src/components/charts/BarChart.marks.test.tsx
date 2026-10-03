/**
 * Wave 3 (VIZ-602 / 603, OD-2): activating a bar. Click / Enter drills;
 * Shift-, Ctrl- or Cmd-click and Shift+Enter filter the page (when the host
 * offers it); a touch tap selects, and the readout's buttons act. A chart
 * given no `onMarkActivate` is the pre-Wave-3 chart: no handler, no pointer
 * cursor, no buttons, the same DOM.
 *
 * Recharts is a stand-in that draws one clickable rectangle per data row and
 * calls the `<Bar onClick>` it was given exactly as Recharts does
 * (`(entry, index, event)`), so the test reads what the chart wired, not what
 * Recharts happens to render in jsdom.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartSeries, SeriesChart } from '@/lib/viz/contracts'
import BarChart, { BreakdownChart } from './BarChart'
import { CURSOR_ACTIVATE_HINT } from './ChartCursor'
import type { ChartMark, MarkIntent } from './marks'
import { MARK_KIT } from './markKit'
import { OTHER_KEY } from './multiSeriesModel'
import type { ChartResponse, ChartState } from './chartState'

const plot = vi.hoisted(() => ({ data: [] as Record<string, unknown>[], bars: [] as Record<string, unknown>[] }))

vi.mock('recharts', () => ({
  usePlotArea: () => undefined,
  useXAxisScale: () => (value: unknown) => value as number,
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  BarChart: ({ children, data }: { children?: ReactNode; data?: Record<string, unknown>[] }) => {
    plot.data = data ?? []
    return (
      <svg data-chart="bar">
        <g>{children}</g>
      </svg>
    )
  },
  Bar: (props: { dataKey?: string; children?: ReactNode; onClick?: (...args: unknown[]) => void; cursor?: string }) => {
    plot.bars.push(props)
    return (
      <g data-bar={props.dataKey} data-cursor={props.cursor ?? ''}>
        {plot.data.map((row, index) => (
          <rect
            key={index}
            data-rect={`${props.dataKey}:${String(row.key)}`}
            onClick={(event) => props.onClick?.({ payload: row }, index, event)}
          />
        ))}
        {props.children}
      </g>
    )
  },
  Cell: () => null,
  LabelList: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  ReferenceLine: () => null,
  Tooltip: () => null,
  Legend: ({ content }: { content?: () => ReactNode }) => <div>{typeof content === 'function' ? content() : null}</div>,
  PieChart: ({ children }: { children?: ReactNode }) => <svg data-chart="pie">{children}</svg>,
  Pie: ({ children }: { children?: ReactNode }) => <g>{children}</g>,
  Label: () => null,
}))

const HOSTILE = '<img src=x onerror="window.__xss=1">'

/** chart-data `metric=failed&group_by=test` (keys are fingerprints, labels the names). */
const TESTS: SeriesChart = {
  kind: 'series',
  dimensions: ['test'],
  x_type: 'category',
  x_labels: { 'fp-a': 'test_pay', 'fp-b': HOSTILE },
  series: [
    {
      key: 'value',
      label: 'Failed',
      points: [
        { x: 'fp-a', y: 9, n: 40 },
        { x: 'fp-b', y: 4, n: 12 },
        { x: OTHER_KEY, y: 2, n: 8 },
      ],
    },
  ],
}

/** chart-data `metric=executions&group_by=suite&group_by=status` (keys lower-cased, labels as spelled). */
const SUITES: SeriesChart = {
  kind: 'series',
  dimensions: ['suite', 'status'],
  x_type: 'category',
  x_labels: { payments: 'Payments' },
  series: [
    { key: 'passed', label: 'passed', points: [{ x: 'payments', y: 30, n: 30 }, { x: 'cart', y: 10, n: 10 }] },
    { key: 'failed', label: 'failed', points: [{ x: 'payments', y: 6, n: 6 }, { x: 'cart', y: 2, n: 2 }] },
  ],
}

const ready = (series: ChartSeries): ChartState<ChartResponse> => ({ status: 'ready', data: { meta: null, series }, meta: null, revalidating: false })

const calls: [ChartMark, MarkIntent][] = []
const onMarkActivate = (mark: ChartMark, intent: MarkIntent) => {
  calls.push([mark, intent])
}
const allIntents = () => ['drill', 'rows', 'filter'] as const

beforeEach(() => {
  calls.length = 0
  plot.bars = []
})
afterEach(() => {
  plot.data = []
})

/** `useId` values differ between two mounts; nothing else may. */
const withoutIds = (html: string) => html.replace(/_r_[0-9a-z]+_/g, '_id_')
const surface = (container: HTMLElement) => container.querySelector('[data-chart-cursor]') as HTMLElement
const rect = (container: HTMLElement, id: string) => container.querySelector(`[data-rect="${id}"]`) as Element

describe('BarChart marks — no handler: the pre-Wave-3 chart', () => {
  it.each([
    ['ranked', TESTS],
    ['stacked', SUITES],
  ] as const)('%s: no click handler, no pointer cursor, no Enter, no buttons, and the same DOM', (variant, series) => {
    const plain = render(<BarChart title="Bars" state={ready(series)} variant={variant} animate={false} />)
    const plainHtml = withoutIds(plain.container.innerHTML)
    expect(plot.bars.every((bar) => bar.onClick === undefined && bar.cursor === undefined)).toBe(true)
    expect(plot.bars.every((bar) => !('onClick' in bar) && !('cursor' in bar))).toBe(true)
    expect(surface(plain.container).getAttribute('aria-label')).not.toContain(CURSOR_ACTIVATE_HINT)
    fireEvent.keyDown(surface(plain.container), { key: 'ArrowDown' })
    fireEvent.keyDown(surface(plain.container), { key: 'Enter' })
    expect(plain.container.querySelector('[data-mark-actions]')).toBeNull()
    plain.unmount()

    // Intents without a handler are nothing either: the DOM is byte-identical.
    const withIntents = render(<BarChart title="Bars" state={ready(series)} variant={variant} animate={false} markIntents={allIntents} />)
    expect(withoutIds(withIntents.container.innerHTML)).toBe(plainHtml)
  })
})

describe('BarChart marks — ranked', () => {
  const draw = (intents: () => readonly MarkIntent[] = allIntents) =>
    render(
      <BarChart title="Failing tests" state={ready(TESTS)} variant="ranked" animate={false} onMarkActivate={onMarkActivate} markKit={MARK_KIT} markIntents={intents} />,
    ).container

  it('a click drills the bar: its KEY, full label, drawn value and sample', () => {
    const container = draw()
    fireEvent.click(rect(container, 'value:fp-a'))
    expect(calls).toEqual([[{ dimension: 'test', value: 'fp-a', label: 'test_pay', y: 9, n: 40 }, 'drill']])
    expect(container.querySelector('[data-bar="value"]')?.getAttribute('data-cursor')).toBe('pointer')
    expect(surface(container).getAttribute('aria-label')).toContain(CURSOR_ACTIVATE_HINT)
  })

  it.each([['shiftKey'], ['ctrlKey'], ['metaKey']])('a %s click filters the page by the bar', (modifier) => {
    const container = draw()
    fireEvent.click(rect(container, 'value:fp-b'), { [modifier]: true })
    expect(calls.map(([mark, intent]) => [mark.value, intent])).toEqual([['fp-b', 'filter']])
  })

  it('a modified click where the host offers no filter is a plain click', () => {
    const container = draw(() => ['drill'])
    fireEvent.click(rect(container, 'value:fp-a'), { shiftKey: true })
    expect(calls.map(([, intent]) => intent)).toEqual(['drill'])
  })

  it('the Other roll-up bar does nothing', () => {
    const container = draw()
    fireEvent.click(rect(container, `value:${OTHER_KEY}`))
    expect(calls).toEqual([])
  })

  it('a touch tap selects the bar: nothing happens until a readout button is pressed', () => {
    const container = draw()
    const target = rect(container, 'value:fp-b')
    // jsdom's click is a MouseEvent; a touch tap's is a PointerEvent with pointerType "touch".
    const tap = new MouseEvent('click', { bubbles: true })
    Object.defineProperty(tap, 'pointerType', { value: 'touch' })
    fireEvent(target, tap)
    expect(calls).toEqual([])
    const actions = container.querySelector('[data-mark-actions]') as HTMLElement
    expect(within(actions).getAllByRole('button').map((b) => b.textContent)).toEqual([
      `Drill into ${HOSTILE}`,
      'View rows',
      'Filter page by this',
    ])
    expect(document.querySelector('img')).toBeNull()
    fireEvent.click(within(actions).getByRole('button', { name: 'View rows' }))
    expect(calls.map(([mark, intent]) => [mark.value, intent])).toEqual([['fp-b', 'rows']])
  })

  it('Enter drills the focused bar and Shift+Enter filters by it; the readout offers all three', () => {
    const container = draw()
    const chart = surface(container)
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowDown' })
    fireEvent.keyDown(chart, { key: 'Enter' })
    fireEvent.keyDown(chart, { key: 'Enter', shiftKey: true })
    expect(calls.map(([mark, intent]) => [mark.value, intent])).toEqual([
      ['fp-a', 'drill'],
      ['fp-a', 'filter'],
    ])
    fireEvent.click(screen.getByRole('button', { name: 'Drill into test_pay' }))
    expect(calls[2][1]).toBe('drill')
  })

  it('page 2: a click on its first bar activates the 51st bar, not the first', () => {
    const many: SeriesChart = {
      ...TESTS,
      x_labels: undefined,
      series: [{ key: 'value', label: 'Failed', points: Array.from({ length: 60 }, (_, i) => ({ x: `fp-${String(i).padStart(2, '0')}`, y: 100 - i, n: 100 })) }],
    }
    const { container } = render(
      <BarChart title="Failing tests" state={ready(many)} variant="ranked" animate={false} onMarkActivate={onMarkActivate} markKit={MARK_KIT} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Next bars' }))
    fireEvent.click(rect(container, 'value:fp-50'))
    expect(calls.map(([mark, intent]) => [mark.value, mark.y, intent])).toEqual([['fp-50', 50, 'drill']])
  })
})

describe('BarChart marks — stacked', () => {
  const draw = (extra: Record<string, unknown> = {}) =>
    render(
      <BarChart
        title="Results by suite"
        state={ready(SUITES)}
        variant="stacked"
        animate={false}
        onMarkActivate={onMarkActivate} markKit={MARK_KIT}
        markIntents={allIntents}
        {...extra}
      />,
    ).container

  it('a click on a segment drills into the bar AND the segment: the suite, with the status as context', () => {
    const container = draw()
    fireEvent.click(rect(container, 'failed:payments'))
    expect(calls).toEqual([
      [{ dimension: 'suite', value: 'payments', label: 'Payments', y: 6, n: 6, context: [{ dimension: 'status', value: 'failed' }] }, 'drill'],
    ])
    expect(container.querySelector('[data-bar="failed"]')?.getAttribute('data-cursor')).toBe('pointer')
  })

  it('a Shift-click on a segment filters with that segment', () => {
    const container = draw()
    fireEvent.click(rect(container, 'passed:cart'), { shiftKey: true })
    expect(calls.map(([mark, intent]) => [mark.value, mark.context?.[0]?.value, intent])).toEqual([['cart', 'passed', 'filter']])
  })

  it('in 100% mode a segment still carries its TRUE count', () => {
    const container = draw({ initialMode: 'percent' })
    fireEvent.click(rect(container, 'failed:cart'))
    expect(calls[0][0].y).toBe(2)
  })

  it('a touch tap on a segment selects its bar; the buttons then act on the whole bar', () => {
    const container = draw()
    const tap = new MouseEvent('click', { bubbles: true })
    Object.defineProperty(tap, 'pointerType', { value: 'touch' })
    fireEvent(rect(container, 'failed:payments'), tap)
    expect(calls).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Drill into Payments' }))
    expect(calls).toEqual([[{ dimension: 'suite', value: 'payments', label: 'Payments', y: 36, n: 36 }, 'drill']])
  })

  it('the keyboard stop is the whole bar: Enter drills into the suite alone', () => {
    const container = draw()
    const chart = surface(container)
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowDown' })
    fireEvent.keyDown(chart, { key: 'Enter' })
    expect(calls).toEqual([[{ dimension: 'suite', value: 'payments', label: 'Payments', y: 36, n: 36 }, 'drill']])
  })

  it('a series whose dimensions the contract does not know draws marks nowhere', () => {
    const odd: SeriesChart = { ...SUITES, dimensions: ['category', 'status'] }
    const { container } = render(
      <BarChart title="Odd" state={ready(odd)} variant="stacked" animate={false} onMarkActivate={onMarkActivate} markKit={MARK_KIT} />,
    )
    expect(surface(container).getAttribute('aria-label')).not.toContain(CURSOR_ACTIVATE_HINT)
    expect(plot.bars.every((bar) => !('onClick' in bar))).toBe(true)
  })
})

describe('BarChart — the frame scope slot', () => {
  it('renders the caller’s scope content inside the frame, and nothing without it', () => {
    const { container, rerender } = render(<BarChart title="Bars" state={ready(TESTS)} variant="ranked" animate={false} />)
    expect(container.querySelector('[data-chart-scope]')).toBeNull()
    rerender(<BarChart title="Bars" state={ready(TESTS)} variant="ranked" animate={false} scope={<nav aria-label="Breadcrumb">here</nav>} />)
    expect(within(container.querySelector('[data-chart-scope]') as HTMLElement).getByRole('navigation', { name: 'Breadcrumb' })).toBeInTheDocument()
  })

  it('BreakdownChart passes activation through to the ranked bars it picks', () => {
    const many: SeriesChart = {
      ...TESTS,
      x_labels: undefined,
      series: [{ key: 'value', label: 'Failed', points: Array.from({ length: 12 }, (_, i) => ({ x: `fp-${i}`, y: 20 - i, n: 20 })) }],
    }
    const { container } = render(<BreakdownChart title="Breakdown" state={ready(many)} animate={false} onMarkActivate={onMarkActivate} markKit={MARK_KIT} />)
    fireEvent.click(rect(container, 'value:fp-0'))
    expect(calls.map(([mark, intent]) => [mark.value, intent])).toEqual([['fp-0', 'drill']])
  })
})
