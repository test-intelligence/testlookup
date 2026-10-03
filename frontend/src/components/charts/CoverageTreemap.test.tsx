import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { TreeNodeStats } from '@/lib/viz/contracts'
import CoverageTreemap, { CoverageLegend } from './CoverageTreemap'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import {
  CLASS_KEY_SEPARATOR,
  GAP_WORDS,
  moveInOrder,
  tileValueNote,
  TOP_LEVEL,
  treemapClick,
  treemapKeyboardHint,
  type CoverageChild,
  type CoverageLevel,
} from './coverageMap.model'
import { StaleBuildError } from './engines/lazyChartEngine'

// jsdom has no canvas, so the engine is mocked at the registry: the test
// asserts what the component hands ECharts (option, actions, listeners).
const engine = vi.hoisted(() => {
  const listeners = new Map<string, (params: unknown) => void>()
  const instance = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    dispatchAction: vi.fn(),
    isDisposed: vi.fn(() => false),
    on: vi.fn((name: string, handler: (params: unknown) => void) => listeners.set(name, handler)),
    off: vi.fn((name: string) => listeners.delete(name)),
  }
  return { instance, listeners, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('./engines/registry', () => ({ loadChartEngine: engine.load }))

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const HOSTILE = '<img src=x onerror="window.__xss=1">'

const stats = (over: Partial<TreeNodeStats> = {}): TreeNodeStats => ({
  test_count: 4,
  executions: 12,
  pass_rate: 95,
  flaky_count: 0,
  flaky_share: 0,
  last_executed_at: '2026-09-30T00:00:00Z',
  staleness_days: 1,
  recency: 'seen',
  ...over,
})

const item = (id: string, label: string, kind: CoverageChild['kind'], key: string | null, s: TreeNodeStats = stats()): CoverageChild => ({
  node: { id, parent_id: 'all', label, value: s.test_count, measure: s.pass_rate, stats: s },
  kind,
  key,
})

const SUITES: CoverageChild[] = [
  item('s:payments', 'Payments', 'suite', 'payments', stats({ test_count: 9 })),
  item('s:auth', HOSTILE, 'suite', 'auth'),
  item('s:idle', 'idle', 'suite', 'idle', stats({ executions: 0, pass_rate: null, recency: 'never', last_executed_at: null, staleness_days: null })),
  item('other:all', 'Other (3)', 'other', null, stats({ pass_rate: null })),
]

const LEVEL3: CoverageLevel = { depth: 3, suite: 'payments', classKey: 'k' }
const TESTS: CoverageChild[] = [item('t:fp-1', 'test_pay', 'test', 'fp-1'), item('t:fp-2', 'test_refund', 'test', 'fp-2')]

const actions = () => engine.instance.dispatchAction.mock.calls.map(([payload]) => payload as Record<string, unknown>)

async function renderMap(props: Partial<Parameters<typeof CoverageTreemap>[0]> = {}) {
  const view = render(
    <ChartAnnouncerProvider>
      <CoverageTreemap items={SUITES} level={TOP_LEVEL} colorBy="pass_rate" description="Coverage map." {...props} />
    </ChartAnnouncerProvider>,
  )
  await waitFor(() => expect(view.container.querySelector('[data-chart-type="treemap"]')).toHaveAttribute('data-chart-status', 'ready'))
  const group = view.container.querySelector('[data-chart-keyboard="treemap"]') as HTMLElement
  return { ...view, group }
}

describe('CoverageTreemap', () => {
  const realResizeObserver = globalThis.ResizeObserver
  beforeEach(() => {
    engine.load.mockReset()
    engine.load.mockResolvedValue({ init: engine.init })
    engine.init.mockClear()
    engine.listeners.clear()
    for (const fn of Object.values(engine.instance)) (fn as ReturnType<typeof vi.fn>).mockClear()
    globalThis.ResizeObserver = TestResizeObserver as unknown as typeof ResizeObserver
  })
  afterEach(() => {
    globalThis.ResizeObserver = realResizeObserver
  })

  it('loads the treemap engine lazily and hands it the one-level option, with only the DOM tooltip formatter', async () => {
    await renderMap()
    expect(engine.load).toHaveBeenCalledWith('treemap')
    const [option] = engine.instance.setOption.mock.calls[0] as unknown as [
      { series: { type: string; data: { name: string }[] }[]; tooltip: { formatter: unknown } },
    ]
    expect(option.series[0].type).toBe('treemap')
    // Each tile's name and, under it, its value (F-03); the never-run node and Other have no pass rate.
    expect(option.series[0].data.map((d) => d.name)).toEqual(['Payments\n95.0%', `${HOSTILE}\n95.0%`, 'idle', 'Other (3)'])
    expect((option.tooltip.formatter as (p: unknown) => unknown)({ dataIndex: 1 })).toBeInstanceOf(HTMLElement)
  })

  it('walks the children with the arrow keys: highlight + showTip at dataIndex k + 1, spoken through the page announcer', async () => {
    const { group, container } = await renderMap()
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    expect(actions()).toEqual([
      { type: 'highlight', seriesIndex: 0, dataIndex: 1 },
      { type: 'showTip', seriesIndex: 0, dataIndex: 1 },
    ])
    const spoken = container.querySelector('[data-chart-announcer="assertive"]') as HTMLElement
    expect(spoken.textContent).toContain('Payments')
    expect(spoken.textContent).toContain('1 of 4.')
    engine.instance.dispatchAction.mockClear()
    fireEvent.keyDown(group, { key: 'ArrowDown' })
    expect(actions()).toEqual([
      { type: 'downplay', seriesIndex: 0, dataIndex: 1 },
      { type: 'hideTip' },
      { type: 'highlight', seriesIndex: 0, dataIndex: 2 },
      { type: 'showTip', seriesIndex: 0, dataIndex: 2 },
    ])
    // The hostile label is spoken and shown as text.
    expect(spoken.textContent).toContain(HOSTILE)
    expect(container.querySelector('[data-coverage-focused]')?.textContent).toBe(HOSTILE)
    expect(container.querySelector('img')).toBeNull()
    fireEvent.keyDown(group, { key: 'End' })
    expect(group).toHaveAttribute('data-active-index', '3')
    fireEvent.keyDown(group, { key: 'Escape' })
    expect(group).toHaveAttribute('data-active-index', '')
    // One announcer: the chart has no live region of its own.
    expect(group.querySelector('[aria-live]')).toBeNull()
  })

  it('Enter opens a suite (drill), shows a test its rows, and does nothing on the Other node', async () => {
    const onMarkActivate = vi.fn()
    const { group } = await renderMap({ onMarkActivate })
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    fireEvent.keyDown(group, { key: 'Enter' })
    expect(onMarkActivate).toHaveBeenCalledWith(expect.objectContaining({ dimension: 'suite', value: 'payments', label: 'Payments' }), 'drill')
    // Shift+Enter asks for a filter, which the map does not offer: it is the plain action.
    fireEvent.keyDown(group, { key: 'Enter', shiftKey: true })
    expect(onMarkActivate).toHaveBeenLastCalledWith(expect.objectContaining({ value: 'payments' }), 'drill')
    onMarkActivate.mockClear()
    fireEvent.keyDown(group, { key: 'End' })
    fireEvent.keyDown(group, { key: 'Enter' })
    expect(onMarkActivate).not.toHaveBeenCalled()
  })

  it('a test leaf offers its rows, by Enter and by the button in the action row', async () => {
    const onMarkActivate = vi.fn()
    const { group, container } = await renderMap({ items: TESTS, level: LEVEL3, onMarkActivate })
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    const button = container.querySelector('[data-mark-intent="rows"]') as HTMLButtonElement
    expect(button.textContent).toBe('View rows')
    // Tabbing to the button keeps the node focused (it is inside the chart).
    fireEvent.blur(group, { relatedTarget: button })
    fireEvent.click(button)
    expect(onMarkActivate).toHaveBeenCalledWith(
      expect.objectContaining({
        dimension: 'test',
        value: 'fp-1',
        context: [
          { dimension: 'suite', value: 'payments' },
          { dimension: 'class', value: 'k' },
        ],
      }),
      'rows',
    )
    fireEvent.keyDown(group, { key: 'Enter' })
    expect(onMarkActivate).toHaveBeenCalledTimes(2)
  })

  it('a readout button moves focus back to the chart BEFORE it acts, so a panel it opens returns there (R1B-4)', async () => {
    let focusedWhenActed: Element | null = null
    const onMarkActivate = vi.fn(() => {
      focusedWhenActed = document.activeElement
    })
    const { group, container } = await renderMap({ items: TESTS, level: LEVEL3, onMarkActivate })
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    const button = container.querySelector('[data-mark-intent="rows"]') as HTMLButtonElement
    // The reader tabs to the button (inside the chart: the node stays focused) and presses it.
    button.focus()
    expect(document.activeElement).toBe(button)
    fireEvent.click(button)
    expect(onMarkActivate).toHaveBeenCalledTimes(1)
    // The side panel captures its opener when it opens: the chart, which outlives the button.
    expect(focusedWhenActed).toBe(group)
  })

  it('the action row names the focused suite and offers "Drill into" it; with nothing focused it says the keys', async () => {
    const onMarkActivate = vi.fn()
    const { group, container } = await renderMap({ onMarkActivate, onLevelUp: vi.fn(), level: { depth: 2, suite: 's', classKey: null } })
    expect(screen.getByText(treemapKeyboardHint({ depth: 2, suite: 's', classKey: null }))).toBeInTheDocument()
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    const button = container.querySelector('[data-mark-intent="drill"]') as HTMLButtonElement
    expect(button.textContent).toBe(`Drill into ${HOSTILE}`)
    fireEvent.click(button)
    expect(onMarkActivate).toHaveBeenCalledWith(expect.objectContaining({ value: 'auth' }), 'drill')
    // The hint is still the group's description while a node is focused.
    expect(document.getElementById(group.getAttribute('aria-describedby') as string)?.textContent).toContain('Backspace')
  })

  it('Backspace goes up a level when there is one; at the top it is not taken', async () => {
    const onLevelUp = vi.fn()
    const first = await renderMap({ onLevelUp })
    const event = new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true, cancelable: true })
    first.group.dispatchEvent(event)
    expect(onLevelUp).toHaveBeenCalledTimes(1)
    expect(event.defaultPrevented).toBe(true)
    first.unmount()
    const top = await renderMap()
    const free = new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true, cancelable: true })
    top.group.dispatchEvent(free)
    expect(free.defaultPrevented).toBe(false)
  })

  it('without a handler nothing listens: no click listener, no buttons, Enter untouched', async () => {
    const { group, container } = await renderMap()
    expect(engine.instance.on).not.toHaveBeenCalled()
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    expect(container.querySelector('[data-mark-intent]')).toBeNull()
    const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    group.dispatchEvent(enter)
    expect(enter.defaultPrevented).toBe(false)
  })

  it('a click activates the node it lands on; a touch tap only selects it, and the row offers its action', async () => {
    const onMarkActivate = vi.fn()
    const { container } = await renderMap({ onMarkActivate })
    await waitFor(() => expect(engine.listeners.has('click')).toBe(true))
    const click = engine.listeners.get('click') as (params: unknown) => void
    act(() => click({ dataIndex: 2, event: { event: { shiftKey: false } } }))
    expect(onMarkActivate).toHaveBeenCalledWith(expect.objectContaining({ value: 'auth' }), 'drill')
    onMarkActivate.mockClear()
    // The virtual root, the Other node and a stale index do nothing.
    act(() => click({ dataIndex: 0 }))
    act(() => click({ dataIndex: 4 }))
    act(() => click({ dataIndex: 99 }))
    expect(onMarkActivate).not.toHaveBeenCalled()
    act(() => click({ dataIndex: 1, event: { event: { pointerType: 'touch' } } }))
    expect(onMarkActivate).not.toHaveBeenCalled()
    expect(container.querySelector('[data-coverage-focused]')?.textContent).toBe('Payments')
    fireEvent.click(container.querySelector('[data-mark-intent="drill"]') as HTMLButtonElement)
    expect(onMarkActivate).toHaveBeenCalledWith(expect.objectContaining({ value: 'payments' }), 'drill')
  })

  it('a host can narrow the actions with markIntents, but never open the Other node', async () => {
    const onMarkActivate = vi.fn()
    const { group, container } = await renderMap({ onMarkActivate, markIntents: () => ['rows', 'drill'] })
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    expect([...container.querySelectorAll('[data-mark-intent]')].map((b) => b.getAttribute('data-mark-intent'))).toEqual(['rows', 'drill'])
    // The Other node has no key: whatever the host offers, it does not open.
    fireEvent.keyDown(group, { key: 'End' })
    expect(container.querySelector('[data-coverage-focused]')?.textContent).toBe('Other (3)')
    expect(container.querySelector('[data-mark-intent]')).toBeNull()
    fireEvent.keyDown(group, { key: 'Enter' })
    expect(onMarkActivate).not.toHaveBeenCalled()
  })

  it('shows the error state with a retry when the engine fails, and Reload when the chunk is gone', async () => {
    engine.load.mockRejectedValueOnce(new Error('boom'))
    const view = render(<CoverageTreemap items={SUITES} level={TOP_LEVEL} colorBy="pass_rate" description="d" />)
    await waitFor(() => expect(view.container.querySelector('[data-chart-draw-error="error"]')).not.toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(view.container.querySelector('[data-chart-status="ready"]')).not.toBeNull())
    view.unmount()
    engine.load.mockRejectedValueOnce(new StaleBuildError(new TypeError('Failed to fetch dynamically imported module')))
    const reload = vi.fn()
    const location = window.location
    Object.defineProperty(window, 'location', { configurable: true, value: { ...location, reload } })
    try {
      const stale = render(<CoverageTreemap items={SUITES} level={TOP_LEVEL} colorBy="pass_rate" description="d" />)
      await waitFor(() => expect(stale.container.querySelector('[data-chart-draw-error="stale-build"]')).not.toBeNull())
      fireEvent.click(stale.container.querySelector('button') as HTMLButtonElement)
      expect(reload).toHaveBeenCalled()
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: location })
    }
  })
})

describe('CoverageLegend', () => {
  it('lists the five bins of the measure, then only the gaps and the Other grey the level draws', () => {
    const { container } = render(<CoverageLegend items={SUITES} colorBy="pass_rate" />)
    expect([...container.querySelectorAll('[data-coverage-bin]')].map((li) => li.textContent)).toEqual([
      '90–100%',
      '75–90%',
      '50–75%',
      '25–50%',
      'Under 25%',
    ])
    expect([...container.querySelectorAll('[data-coverage-gap]')].map((li) => li.textContent)).toEqual([GAP_WORDS.never])
    expect(container.querySelector('[data-coverage-other]')).not.toBeNull()
    // The gap's swatch is a pattern, not a colour alone.
    expect(container.querySelector('[data-coverage-gap-pattern="crosshatch"]')).not.toBeNull()
  })

  it('says the tiles print the value of the measure it keys, so the colour is not the only channel (F-03)', () => {
    const { container, rerender } = render(<CoverageLegend items={SUITES} colorBy="pass_rate" />)
    const note = () => container.querySelector('[data-coverage-tile-value]')?.textContent
    expect(note()).toBe(tileValueNote('pass_rate'))
    expect(note()).toBe('Tiles with room show their pass rate under the name.')
    rerender(<CoverageLegend items={SUITES} colorBy="staleness" />)
    expect(note()).toBe('Tiles with room show their days since last run under the name.')
    rerender(<CoverageLegend items={SUITES} colorBy="flaky_share" />)
    expect(note()).toBe('Tiles with room show their flaky share under the name.')
  })

  it('has no gap and no Other entry when every node has a value', () => {
    const { container } = render(<CoverageLegend items={SUITES.slice(0, 2)} colorBy="staleness" />)
    expect(container.querySelectorAll('[data-coverage-bin]')).toHaveLength(5)
    expect(container.querySelector('[data-coverage-gap]')).toBeNull()
    expect(container.querySelector('[data-coverage-other]')).toBeNull()
    expect(container.querySelector('pattern')).toBeNull()
  })

  it('names "last run unknown" and "not run in this window" with their own patterns', () => {
    const items = [
      item('s:u', 'u', 'suite', 'u', stats({ executions: 0, pass_rate: null, recency: 'unknown', last_executed_at: null, staleness_days: null })),
      item('s:n', 'n', 'suite', 'n', stats({ executions: 0, pass_rate: null, staleness_days: 40 })),
    ]
    const { container } = render(<CoverageLegend items={items} colorBy="pass_rate" />)
    expect([...container.querySelectorAll('[data-coverage-gap]')].map((li) => li.getAttribute('data-coverage-gap'))).toEqual([
      'not_run',
      'unknown',
    ])
    expect(container.querySelector('[data-coverage-gap-pattern="dots"]')).not.toBeNull()
    expect(container.querySelector('[data-coverage-gap-pattern="diagonal"]')).not.toBeNull()
  })
})

describe('pure helpers', () => {
  it('moves through the drawn order, clamped at both ends', () => {
    const req = (key: Parameters<typeof moveInOrder>[2]['key']) => ({ key, whole: false })
    expect(moveInOrder(0, null, req('ArrowRight'))).toBeNull()
    expect(moveInOrder(3, null, req('ArrowRight'))).toBe(0)
    expect(moveInOrder(3, null, req('End'))).toBe(2)
    expect(moveInOrder(3, 7, req('ArrowLeft'))).toBe(0)
    expect(moveInOrder(3, 2, req('ArrowRight'))).toBe(2)
    expect(moveInOrder(3, 0, req('ArrowUp'))).toBe(0)
    expect(moveInOrder(3, 1, req('Home'))).toBe(0)
    expect(moveInOrder(3, 1, req('End'))).toBe(2)
  })

  it('reads a click without trusting its shape', () => {
    expect(treemapClick({ dataIndex: 3, event: { event: { shiftKey: true, ctrlKey: 1, metaKey: true, pointerType: 'mouse' } } })).toEqual({
      index: 2,
      modifiers: { shiftKey: true, ctrlKey: false, metaKey: true, pointerType: 'mouse' },
    })
    expect(treemapClick({ dataIndex: 1 })).toEqual({ index: 0, modifiers: { shiftKey: false, ctrlKey: false, metaKey: false } })
    for (const bad of [null, undefined, {}, { dataIndex: 0 }, { dataIndex: '2' }, { dataIndex: 1.5 }]) expect(treemapClick(bad)).toBeNull()
  })

  it('says Backspace only where there is a level above, and what Enter does on each level', () => {
    expect(treemapKeyboardHint(TOP_LEVEL)).toBe('Arrow keys move, Enter opens it, Escape clears, Tab leaves')
    expect(treemapKeyboardHint(LEVEL3)).toBe('Arrow keys move, Enter shows its runs, Backspace goes up a level, Escape clears, Tab leaves')
    expect(CLASS_KEY_SEPARATOR).toBe('␟')
  })
})
