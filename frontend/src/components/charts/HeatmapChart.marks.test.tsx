/**
 * Wave 3 (VIZ-501 / the VIZ-602 seam): acting on a heatmap cell — a click,
 * Shift-click, a touch tap, Enter / Shift+Enter, and the buttons under the
 * plot — and, without `onMarkActivate`, exactly today's chart.
 *
 * jsdom has no canvas: the engine is mocked at the registry, and its `on`
 * records the listeners `useEChart` binds, so a test can fire ECharts' own
 * `click` with the params ECharts would hand it.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import HeatmapChart, { HEATMAP_ACTIVATE_HINT, HEATMAP_KEYBOARD_HINT } from './HeatmapChart'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import type { NumericMatrix } from './engines/echarts/heatmapOption'
import type { ChartMark, MarkIntent } from './marks'

const engine = vi.hoisted(() => {
  const listeners = new Map<string, (params: unknown) => void>()
  const instance = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    dispatchAction: vi.fn(),
    on: vi.fn((name: string, listener: (params: unknown) => void) => listeners.set(name, listener)),
    off: vi.fn((name: string) => listeners.delete(name)),
  }
  return { instance, listeners, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('./engines/registry', () => ({ loadChartEngine: engine.load }))

/** 2 rows x 2 columns; row 1 column 1 nobody ran. */
const data: NumericMatrix = {
  kind: 'matrix',
  value_type: 'rate',
  x_labels: ['d1', 'd2'],
  y_labels: ['payments', 'search'],
  cells: [
    { x: 0, y: 0, value: 0.9, n: 10 },
    { x: 1, y: 0, value: 0.8, n: 5 },
    { x: 0, y: 1, value: 0.7, n: 4 },
    { x: 1, y: 1, value: null, n: 0 },
  ],
}

const markOf = (index: number): ChartMark | null => {
  const cell = data.cells[index]
  if (cell.n === 0) return null
  return {
    dimension: 'suite',
    value: data.y_labels[cell.y],
    label: `${data.y_labels[cell.y]}, ${data.x_labels[cell.x]}`,
    y: cell.value,
    n: cell.n,
    context: [{ dimension: 'day', value: data.x_labels[cell.x] }],
  }
}

function renderChart(props: { onMarkActivate?: (mark: ChartMark, intent: MarkIntent) => void; intents?: readonly MarkIntent[]; withMarks?: boolean } = {}) {
  const { onMarkActivate, intents, withMarks = true } = props
  return render(
    <ChartAnnouncerProvider>
      <HeatmapChart
        data={data}
        description="Two suites."
        width={480}
        height={240}
        markOf={withMarks ? markOf : undefined}
        onMarkActivate={onMarkActivate}
        markIntents={intents ? () => intents : undefined}
      />
    </ChartAnnouncerProvider>,
  )
}

const surface = (container: HTMLElement) => container.querySelector('[data-chart-keyboard="heatmap"]') as HTMLElement

async function click(params: unknown) {
  await waitFor(() => expect(engine.listeners.get('click')).toBeDefined())
  engine.listeners.get('click')?.(params)
}

beforeEach(() => {
  engine.listeners.clear()
  engine.load.mockReset()
  engine.load.mockResolvedValue({ init: engine.init })
  engine.instance.on.mockClear()
  engine.instance.setOption.mockClear()
})

describe('HeatmapChart mark activation', () => {
  it('a click on a cell acts on its mark with the first intent', async () => {
    const onMarkActivate = vi.fn()
    renderChart({ onMarkActivate, intents: ['rows'] })
    await click({ value: [1, 0, 0.8], event: { event: { shiftKey: false } } })
    expect(onMarkActivate).toHaveBeenCalledTimes(1)
    const [mark, intent] = onMarkActivate.mock.calls[0]
    expect(intent).toBe('rows')
    expect(mark).toMatchObject({ dimension: 'suite', value: 'payments', label: 'payments, d2', n: 5 })
  })

  it('Shift-click filters when filtering is offered, else acts as a plain click', async () => {
    const onMarkActivate = vi.fn()
    renderChart({ onMarkActivate, intents: ['rows', 'filter'] })
    await click({ value: [0, 1, 0.7], event: { event: { shiftKey: true } } })
    expect(onMarkActivate.mock.calls[0][1]).toBe('filter')
    await click({ value: [0, 1, 0.7], event: { event: { ctrlKey: false } } })
    expect(onMarkActivate.mock.calls[1][1]).toBe('rows')
  })

  it('a cell with no mark (nobody ran it), or params with no cell, do nothing', async () => {
    const onMarkActivate = vi.fn()
    renderChart({ onMarkActivate, intents: ['rows'] })
    await click({ value: [1, 1, 0] })
    await click({ value: 'nonsense' })
    await click(null)
    expect(onMarkActivate).not.toHaveBeenCalled()
  })

  it('a touch tap only selects: the buttons appear, and acting is the reader’s next tap', async () => {
    const onMarkActivate = vi.fn()
    const { container } = renderChart({ onMarkActivate, intents: ['rows'] })
    expect(container.querySelector('[data-mark-actions]')).toBeNull()
    await click({ value: [0, 0, 0.9], event: { event: { pointerType: 'touch' } } })
    expect(onMarkActivate).not.toHaveBeenCalled()
    const button = await screen.findByRole('button', { name: 'View rows' })
    fireEvent.click(button)
    expect(onMarkActivate).toHaveBeenCalledWith(expect.objectContaining({ label: 'payments, d1' }), 'rows')
  })

  it('Enter acts on the highlighted cell; Shift+Enter filters when offered', async () => {
    const onMarkActivate = vi.fn()
    const { container } = renderChart({ onMarkActivate, intents: ['rows', 'filter'] })
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    const chart = surface(container)
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    const label = markOf(Number(chart.getAttribute('data-active-index')))?.label
    expect(label).toBeDefined()
    fireEvent.keyDown(chart, { key: 'Enter' })
    expect(onMarkActivate).toHaveBeenLastCalledWith(expect.objectContaining({ label }), 'rows')
    fireEvent.keyDown(chart, { key: 'Enter', shiftKey: true })
    expect(onMarkActivate).toHaveBeenLastCalledWith(expect.objectContaining({ label }), 'filter')
    // Ctrl/Alt/Meta+Enter is not ours.
    fireEvent.keyDown(chart, { key: 'Enter', ctrlKey: true })
    expect(onMarkActivate).toHaveBeenCalledTimes(2)
  })

  it('the highlighted cell’s actions are real, Tab-reachable buttons inside the chart’s focus group', async () => {
    const onMarkActivate = vi.fn()
    const { container } = renderChart({ onMarkActivate, intents: ['rows'] })
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    const chart = surface(container)
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    const button = screen.getByRole('button', { name: 'View rows' })
    expect(chart.contains(button)).toBe(true)
    // Focus on the button keeps the cell (the chart's blur ignores focus moving inside it).
    button.focus()
    fireEvent.blur(chart, { relatedTarget: button })
    expect(screen.getByRole('button', { name: 'View rows' })).toBe(button)
    // Enter ON the button is the button's, not the chart's second activation.
    fireEvent.keyDown(button, { key: 'Enter' })
    expect(onMarkActivate).not.toHaveBeenCalled()
    fireEvent.click(button)
    expect(onMarkActivate).toHaveBeenCalledTimes(1)
    // The chart has focus again, so the panel the action opens returns it there.
    expect(document.activeElement).toBe(chart)
  })

  it('a highlighted cell nobody ran offers no buttons', async () => {
    const { container } = renderChart({ onMarkActivate: vi.fn(), intents: ['rows'] })
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    const chart = surface(container)
    chart.focus()
    // Top-down is off here: ArrowRight lands on (0, 0); ArrowRight, ArrowDown moves to the empty (1, 1)
    // only if it is the row below in ECharts' order. Walk until the active index is cell 3.
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    for (const key of ['ArrowRight', 'ArrowUp', 'ArrowDown']) {
      if (chart.getAttribute('data-active-index') === '3') break
      fireEvent.keyDown(chart, { key })
    }
    expect(chart.getAttribute('data-active-index')).toBe('3')
    expect(container.querySelector('[data-mark-actions]')).toBeNull()
  })

  it('names Enter in the keyboard hint, and keeps a row for the buttons under the plot', async () => {
    const { container } = renderChart({ onMarkActivate: vi.fn() })
    expect(container.querySelector('[data-chart-keyboard-hint]')).toHaveTextContent(`${HEATMAP_KEYBOARD_HINT}, ${HEATMAP_ACTIVATE_HINT}`)
    expect(container.querySelector('[data-heatmap-mark-row]')).not.toBeNull()
    // The plot keeps its own height; the focus group grows by the row.
    expect((container.querySelector('[data-chart-type="heatmap"]') as HTMLElement).style.height).toBe('240px')
    expect(surface(container).style.height).toBe('')
  })

  it('the hint is IN the row under the plot, in flow (F-21), and the row reserves nothing at rest (F-11)', async () => {
    const { container } = renderChart({ onMarkActivate: vi.fn() })
    const row = container.querySelector('[data-heatmap-mark-row]') as HTMLElement
    const hint = container.querySelector('[data-chart-keyboard-hint]') as HTMLElement
    // In flow: inside the row, never drawn over the next line (the legend, the footer).
    expect(row.contains(hint)).toBe(true)
    expect(hint.className.split(/\s+/)).not.toContain('absolute')
    // Shown only while the chart has keyboard focus; the row has no height of its own.
    expect(hint.className.split(/\s+/)).toEqual(expect.arrayContaining(['hidden', 'group-focus-visible:block']))
    expect(row.className.split(/\s+/).some((name) => /^(min-)?h-/.test(name))).toBe(false)
    // Still the chart's accessible description.
    expect(surface(container).getAttribute('aria-describedby')).toBe(hint.id)
  })
})

describe('HeatmapChart without activation: exactly as before', () => {
  it.each([
    ['no handler', { withMarks: true }],
    ['no markOf', { onMarkActivate: vi.fn(), withMarks: false }],
  ])('%s: no click listener, no buttons, no row, the old hint and box', async (_name, props) => {
    const { container } = renderChart(props)
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    expect(engine.instance.on).not.toHaveBeenCalled()
    const chart = surface(container)
    expect(chart.style.height).toBe('240px')
    expect((container.querySelector('[data-chart-type="heatmap"]') as HTMLElement).style.height).toBe('100%')
    expect(container.querySelector('[data-heatmap-mark-row]')).toBeNull()
    expect(container.querySelector('[data-chart-keyboard-hint]')).toHaveTextContent(new RegExp(`^${HEATMAP_KEYBOARD_HINT}$`))
    // The old overlay, below the plot.
    expect((container.querySelector('[data-chart-keyboard-hint]') as HTMLElement).className.split(/\s+/)).toEqual(
      expect.arrayContaining(['absolute', 'top-full']),
    )
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    fireEvent.keyDown(chart, { key: 'Enter' })
    expect(container.querySelector('[data-mark-actions]')).toBeNull()
  })
})
