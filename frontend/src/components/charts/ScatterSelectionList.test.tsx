import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { PointsChart, PointsChartPoint } from '@/lib/viz/contracts'
import ScatterSelectionList, { SELECTION_LIST_TITLE } from './ScatterSelectionList'
import { SELECTION_LIST_LIMIT } from './testScatter.model'
import { ChartFrameContext } from './chartFrameContext'

const HOSTILE = '<img src=x onerror="window.__xss=1">'
const point = (id: string, x: number, y: number, size: number, label = id): PointsChartPoint => ({ id, label, x, y, size, n: size })

const chart: PointsChart = {
  kind: 'points',
  x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
  y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
  size: { key: 'executions', label: 'Executions' },
  points: [point('a', 1, 10, 5, 'beta'), point('b', 900, 50, 40, HOSTILE), point('c', 30, 0, 9, 'alpha'), point('constructor', 70, 50, 2, '__proto__')],
  medians: { x: 50, y: 10 },
}

const names = () =>
  within(screen.getByRole('table'))
    .getAllByRole('rowheader')
    .map((th) => th.textContent)

describe('ScatterSelectionList', () => {
  it('lists the selected tests, most failing first, with every value and quadrant as text', () => {
    render(<ScatterSelectionList chart={chart} indices={[0, 1, 3]} />)
    // F-08: the count once, not "Selected tests: 3 tests selected" (the region keeps the title as its name).
    expect(screen.getByRole('heading', { level: 4 }).textContent).toBe('3 tests selected')
    expect(screen.getByRole('region', { name: SELECTION_LIST_TITLE })).toBeInTheDocument()
    // y desc; the two 50% tests tie and go by name (code unit: "<" before "_").
    expect(names()).toEqual([HOSTILE, '__proto__', 'beta'])
    const row = screen.getByRole('rowheader', { name: HOSTILE }).parentElement as HTMLElement
    expect(Array.from(row.querySelectorAll('td'), (td) => td.textContent)).toEqual(['900 ms', '50.0%', '40', 'Slow and flaky'])
    const floored = screen.getByRole('rowheader', { name: 'beta' }).parentElement as HTMLElement
    expect(floored.querySelector('td')?.textContent).toBe('1 ms or less')
    // Hostile names are text: nothing parsed, nothing on Object's prototype read.
    expect(document.querySelector('img')).toBeNull()
    expect(screen.getByRole('rowheader', { name: HOSTILE })).toHaveAttribute('title', HOSTILE)
  })

  it('sorts by any column, both ways, and says which with aria-sort', () => {
    render(<ScatterSelectionList chart={chart} indices={[0, 1, 2]} />)
    const header = (key: string) => document.querySelector(`[data-sort-key="${key}"]`) as HTMLElement
    expect(header('y').closest('th')).toHaveAttribute('aria-sort', 'descending')
    fireEvent.click(header('label'))
    expect(names()).toEqual(['<img src=x onerror="window.__xss=1">', 'alpha', 'beta'])
    expect(header('label').closest('th')).toHaveAttribute('aria-sort', 'ascending')
    expect(header('y').closest('th')).toHaveAttribute('aria-sort', 'none')
    fireEvent.click(header('label'))
    expect(names()).toEqual(['beta', 'alpha', HOSTILE])
    fireEvent.click(header('x'))
    expect(names()).toEqual([HOSTILE, 'alpha', 'beta'])
    fireEvent.click(header('size'))
    expect(names()).toEqual([HOSTILE, 'alpha', 'beta'])
    fireEvent.click(header('size'))
    expect(names()).toEqual(['beta', 'alpha', HOSTILE])
    expect(header('size').closest('th')).toHaveAttribute('aria-sort', 'ascending')
  })

  it('offers "View rows" per test when the host does', () => {
    const onViewRows = vi.fn()
    render(<ScatterSelectionList chart={chart} indices={[1, 2]} onViewRows={onViewRows} />)
    fireEvent.click(screen.getByRole('button', { name: `View rows: ${HOSTILE}` }))
    expect(onViewRows).toHaveBeenCalledWith(1)
  })

  // B0's axe run (lab theme): `.th`'s muted colour is 4.15:1 there, under AA for 12 px text.
  it('draws every visible column header in the secondary text colour, not the muted one', () => {
    render(<ScatterSelectionList chart={chart} indices={[0, 1]} />)
    const headers = screen.getAllByRole('columnheader').filter((th) => th.textContent?.trim())
    expect(headers.length).toBeGreaterThan(3)
    for (const th of headers) expect(th.className).toContain('text-[var(--color-text-secondary)]')
  })

  it('F-08: column headers in the Wave 3 table style (ChartTable / groups table), not the letter-spaced `.th`', () => {
    render(<ScatterSelectionList chart={chart} indices={[0, 1]} onViewRows={vi.fn()} />)
    for (const th of screen.getAllByRole('columnheader')) {
      expect(th.classList.contains('th')).toBe(false)
      expect(th.className).toContain('border-b border-[var(--color-border)] bg-[var(--color-bg-card)] px-2 py-1')
    }
    // Cells on the same 8 px gutter as their headers.
    for (const td of screen.getAllByRole('cell')) expect(td.className).toContain('px-2 py-1')
  })

  it('F-08: one precision for p95 (whole ms from 10 ms; a tenth below)', () => {
    const timed: PointsChart = { ...chart, points: [point('p', 1778.3, 10, 5), point('q', 2884, 20, 5), point('r', 3.45, 30, 5)] }
    render(<ScatterSelectionList chart={timed} indices={[0, 1, 2]} />)
    const p95 = (name: string) => (screen.getByRole('rowheader', { name }).parentElement as HTMLElement).querySelector('td')?.textContent
    expect([p95('p'), p95('q'), p95('r')]).toEqual(['1,778 ms', '2,884 ms', '3.5 ms'])
  })

  it('draws nothing while its frame is full screen (only the plot is enlarged; the list is back on exit)', () => {
    const { container, rerender } = render(
      <ChartFrameContext.Provider value={{ fullscreen: true, bodyHeight: 600, portalContainer: null }}>
        <ScatterSelectionList chart={chart} indices={[0]} />
      </ChartFrameContext.Provider>,
    )
    expect(container.innerHTML).toBe('')
    rerender(
      <ChartFrameContext.Provider value={{ fullscreen: false, bodyHeight: null, portalContainer: null }}>
        <ScatterSelectionList chart={chart} indices={[0]} />
      </ChartFrameContext.Provider>,
    )
    expect(container.querySelector('[data-scatter-selection]')).not.toBeNull()
  })

  it('has no action column without a handler and no quadrant without medians', () => {
    render(<ScatterSelectionList chart={{ ...chart, medians: undefined }} indices={[0]} />)
    expect(screen.queryByRole('button', { name: /View rows/ })).toBeNull()
    expect(within(screen.getByRole('table')).getAllByRole('columnheader')).toHaveLength(4)
  })

  it('an empty selection says so, with no table', () => {
    render(<ScatterSelectionList chart={chart} indices={[]} headingLevel={3} />)
    expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent('No test selected')
    expect(screen.queryByRole('table')).toBeNull()
  })

  it(`lists at most ${SELECTION_LIST_LIMIT} and counts the rest`, () => {
    const many: PointsChart = {
      ...chart,
      points: Array.from({ length: SELECTION_LIST_LIMIT + 12 }, (_, i) => point(`t${i}`, i + 1, i % 100, 5)),
    }
    render(<ScatterSelectionList chart={many} indices={many.points.map((_, i) => i)} />)
    expect(names()).toHaveLength(SELECTION_LIST_LIMIT)
    expect(document.querySelector('[data-scatter-selection-more]')?.textContent).toMatch(/^And 12 more, not listed/)
    expect(screen.getByRole('heading', { level: 4 })).toHaveTextContent(`${SELECTION_LIST_LIMIT + 12} tests selected`)
  })
})
