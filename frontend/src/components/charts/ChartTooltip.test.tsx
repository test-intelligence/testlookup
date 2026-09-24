/**
 * VIZ-601 — `ChartTooltip`, the one tooltip markup, and `PinnedTip`, the box
 * every Recharts chart hands its `<Tooltip content>`.
 *
 *   - the React body and the ECharts DOM node are the SAME markup;
 *   - a hostile name is text in both;
 *   - the box places itself beside its mark (off the pointer's line), lingers
 *     so the pointer can reach it, and holds a pointer that came to it FROM
 *     ITS MARK — its moves never reach the chart (SC 1.4.13 Hoverable) — but
 *     never one that ran into it any other way (Wave 2.4 A2/F3);
 *   - where it fits nowhere beside its mark it goes in the readout slot below
 *     the plot, never over it;
 *   - it renders INSIDE the chart's own DOM, never portalled to `<body>`, so a
 *     chart in full screen keeps its tooltip.
 */
import { act, fireEvent, render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import ChartTooltip, { ChartTooltipBody, PinnedTip, TIP_LINGER_MS } from './ChartTooltip'
import { buildTooltipNode, sampleRow, tipContent, type TooltipContent } from './tooltip'
import { COLUMN_SIDES } from './tipPlacement'
import { readTooltip } from './tooltipTestUtils'

const HOSTILE = '<img src=x onerror="window.__xss=1">'

const CONTENT: TooltipContent = tipContent('2026-03-02 (UTC)', [
  { kind: 'dimension', label: 'Release', value: HOSTILE },
  { label: 'Pass rate %', value: '91.2%', color: 'var(--chart-series-1)' },
  { label: 'search', value: '98.0%', detail: '100 samples', color: 'var(--chart-series-2)', mark: 'line', dash: '4 2', data: { 'data-tip-series': 'search' } },
  sampleRow(120),
  { kind: 'note', label: 'Why', value: 'every test was skipped' },
])

/** Every element under `root`, as [tag, class, data-*, text-if-leaf, inline declarations sorted]. */
function shape(root: Element) {
  return Array.from([root, ...root.querySelectorAll('*')], (el) => [
    el.tagName.toLowerCase(),
    el.getAttribute('class') ?? '',
    Array.from(el.attributes)
      .filter((a) => a.name.startsWith('data-') || a.name === 'aria-hidden')
      .map((a) => `${a.name}=${a.value}`)
      .sort()
      .join(' '),
    el.children.length === 0 ? el.textContent : '',
    (el.getAttribute('style') ?? '')
      .split(';')
      .map((part) => part.trim())
      .filter(Boolean)
      .sort()
      .join('; '),
  ])
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  delete (window as { __xss?: unknown }).__xss
})

describe('ChartTooltip: one markup for both engines', () => {
  it('the React body and the ECharts DOM node are the same elements, classes, hooks, text and styles', () => {
    const { container } = render(<ChartTooltipBody content={CONTENT} marked />)
    const react = container.querySelector('[data-chart-tooltip]') as HTMLElement
    const dom = buildTooltipNode(CONTENT)
    // Title, five rows, their swatches, labels, values and a detail: not two empty lists.
    expect(shape(dom).length).toBeGreaterThan(15)
    expect(shape(react)).toEqual(shape(dom))
    expect(readTooltip(react)).toEqual(readTooltip(dom))
  })

  it('renders a hostile name as literal text: no element is created and nothing runs', () => {
    const { container } = render(<ChartTooltip content={CONTENT} />)
    expect(container.textContent).toContain(HOSTILE)
    expect(container.querySelector('img')).toBeNull()
    expect((window as { __xss?: unknown }).__xss).toBeUndefined()
  })

  it('renders nothing without content', () => {
    const { container } = render(<ChartTooltip content={null} />)
    expect(container.innerHTML).toBe('')
  })
})

describe('PinnedTip', () => {
  const box = { left: 0, top: 0, width: 600, height: 300 }
  const mark = { left: 300, top: 16, width: 14, height: 200 }

  it('places itself beside its mark, in chart coordinates, and says which side', () => {
    const { container } = render(<PinnedTip content={CONTENT} mark={mark} sides={COLUMN_SIDES} align="start" chartBox={box} />)
    const tip = container.querySelector('[data-chart-tooltip]') as HTMLElement
    expect(tip.style.position).toBe('absolute')
    expect(tip.style.left).toBe('326px')
    expect(tip.style.top).toBe('16px')
    expect(tip.getAttribute('data-tip-side')).toBe('right')
    expect(tip.style.visibility).toBe('visible')
  })

  it('is not drawn until it has been placed: never a frame over the mark', () => {
    // No mark yet (Recharts has no geometry): hidden rather than guessed.
    const { container } = render(<PinnedTip content={CONTENT} mark={null} chartBox={box} />)
    expect((container.querySelector('[data-chart-tooltip]') as HTMLElement).style.visibility).toBe('hidden')
  })

  /**
   * A chart element as Recharts draws one: the wrapper the box is portalled
   * into, inside the cursor surface (the readout slot), inside a parent that
   * stands in for Recharts' own move handler. jsdom lays nothing out, so the
   * box is given a size, and the pointer's client coordinates ARE chart
   * coordinates (the wrapper's rectangle is all zeros).
   */
  function inChart(node: ReactNode, onChartMove = vi.fn()) {
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(150)
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(60)
    const view = render(
      <div data-chart-cursor="idle" onMouseMove={onChartMove} onTouchMove={onChartMove}>
        <div className="recharts-wrapper">{node}</div>
      </div>,
    )
    const wrapper = view.container.querySelector('.recharts-wrapper') as HTMLElement
    const move = (x: number, y: number, on: HTMLElement = wrapper) => fireEvent.mouseMove(on, { clientX: x, clientY: y })
    return { ...view, wrapper, move, onChartMove }
  }

  /** From the day's bar (x 300-314), up and right, onto the box (x 326-476, y 16-76). */
  const APPROACH: [number, number][] = [
    [310, 150],
    [316, 110],
    [322, 80],
    [330, 60],
  ]

  it('holds a pointer that came to it from its mark: its moves on the box never reach the chart', () => {
    const { container, move, onChartMove } = inChart(<PinnedTip content={CONTENT} mark={mark} sides={COLUMN_SIDES} align="start" chartBox={box} />)
    const tip = container.querySelector('[data-chart-tooltip]') as HTMLElement
    expect(tip.style.pointerEvents).toBe('auto')
    expect({ left: tip.style.left, top: tip.style.top }).toEqual({ left: '326px', top: '16px' })
    for (const [x, y] of APPROACH) move(x, y)
    onChartMove.mockClear()
    move(360, 40, tip)
    fireEvent.touchMove(tip)
    expect(onChartMove).not.toHaveBeenCalled()
  })

  it('keeps the moves of a pointer on its way from the mark from the chart, so the mark cannot change under it', () => {
    const { move, onChartMove } = inChart(<PinnedTip content={CONTENT} mark={mark} sides={COLUMN_SIDES} align="start" chartBox={box} />)
    move(310, 150)
    onChartMove.mockClear()
    // Heading for the box, across where the next days are.
    move(316, 110)
    move(322, 80)
    expect(onChartMove).not.toHaveBeenCalled()
    // Turned away: the chart has the pointer again.
    move(340, 200)
    expect(onChartMove).toHaveBeenCalledTimes(1)
  })

  it('does NOT hold a pointer that ran into it from anywhere else: those moves reach the chart (Wave 2.4 A2/F3)', () => {
    const { container, move, onChartMove } = inChart(<PinnedTip content={CONTENT} mark={mark} sides={COLUMN_SIDES} align="start" chartBox={box} />)
    const tip = container.querySelector('[data-chart-tooltip]') as HTMLElement
    // Along the top of the plot from the right, onto the box.
    move(520, 40)
    move(470, 40, tip)
    move(430, 40, tip)
    // Straight down onto it from above the plot.
    move(400, 2)
    move(400, 20, tip)
    expect(onChartMove).toHaveBeenCalledTimes(5)
  })

  it("is kept off the pointer's line: placed when its mark became active, moved only when the pointer comes onto its line", () => {
    const { container, move, rerender } = inChart(
      <PinnedTip content={CONTENT} mark={mark} sides={COLUMN_SIDES} align="start" chartBox={box} sweep={{ axis: 'x', at: 40 }} />,
    )
    const tip = () => container.querySelector('[data-chart-tooltip]') as HTMLElement
    // The pointer is at y 40, near the plot's top: the box goes to the plot's bottom (216 - 60).
    expect(tip().style.top).toBe('156px')
    // Recharts hands a new line on every move — it is NOT followed (the pointer may be heading for the box) …
    rerender(
      <div data-chart-cursor="idle">
        <div className="recharts-wrapper">
          <PinnedTip content={CONTENT} mark={mark} sides={COLUMN_SIDES} align="start" chartBox={box} sweep={{ axis: 'x', at: 170 }} />
        </div>
      </div>,
    )
    expect(tip().style.top).toBe('156px')
    // … but a pointer that moved down its day onto the box's line, not heading for it, moves the box off it.
    move(300, 170)
    expect(tip().style.top).toBe('16px')
  })

  it('fits nowhere beside its mark: drawn in the readout slot below the plot, in the flow, never over the plot', () => {
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(560)
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(60)
    const { container } = render(
      <div data-chart-cursor="idle">
        <div className="recharts-wrapper">
          <PinnedTip content={CONTENT} mark={mark} sides={COLUMN_SIDES} align="start" chartBox={box} />
        </div>
      </div>,
    )
    const shown = container.querySelectorAll('[data-chart-tooltip]')
    expect(shown).toHaveLength(1)
    const slot = shown[0] as HTMLElement
    expect(slot.getAttribute('data-tip-placement')).toBe('slot')
    expect(slot.getAttribute('data-tip-fits')).toBe('false')
    // Out of the chart wrapper, after it, in the cursor surface: below the plot.
    expect(slot.closest('.recharts-wrapper')).toBeNull()
    expect(slot.parentElement?.hasAttribute('data-chart-cursor')).toBe(true)
    expect(slot.style.position).toBe('sticky')
    expect(readTooltip(slot)).toEqual(readTooltip(buildTooltipNode(CONTENT)))
    // The copy left in the plot only measures: hidden, and not a tooltip to anyone.
    const measure = container.querySelector('.recharts-wrapper > .chart-tooltip') as HTMLElement
    expect(measure.style.visibility).toBe('hidden')
    expect(measure.getAttribute('aria-hidden')).toBe('true')
  })

  it('in the slot, it holds the pointer on it, and stays while the pointer is on the chart, off the plot', () => {
    vi.useFakeTimers()
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(560)
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(60)
    const onChartMove = vi.fn()
    const tree = (content: TooltipContent | null) => (
      <div data-chart-cursor="idle" onMouseMove={onChartMove}>
        <div className="recharts-wrapper">
          <PinnedTip content={content} mark={mark} sides={COLUMN_SIDES} align="start" chartBox={box} />
        </div>
      </div>
    )
    const { container, rerender } = render(tree(CONTENT))
    const surface = container.querySelector('[data-chart-cursor]') as HTMLElement
    // The pointer leaves the plot for the slot: the chart lets go …
    rerender(tree(null))
    act(() => {
      vi.advanceTimersByTime(TIP_LINGER_MS * 10)
    })
    // … and it is still there: the pointer is on the chart's surface.
    const slot = container.querySelector('[data-tip-placement="slot"]') as HTMLElement
    expect(slot).not.toBeNull()
    fireEvent.mouseMove(slot)
    expect(onChartMove).not.toHaveBeenCalled()
    // Off the chart altogether: it lingers, then goes.
    fireEvent.pointerLeave(slot)
    fireEvent.pointerLeave(surface)
    act(() => {
      vi.advanceTimersByTime(TIP_LINGER_MS + 1)
    })
    expect(container.querySelector('[data-chart-tooltip]')).toBeNull()
  })

  it('measures itself again only when something that places it changed — not on every render (review N1)', () => {
    const width = vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(150)
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(60)
    const tree = (at: typeof mark) => <PinnedTip content={CONTENT} mark={at} sides={COLUMN_SIDES} align="start" chartBox={{ ...box }} />
    const { rerender } = render(tree({ ...mark }))
    const first = width.mock.calls.length
    expect(first).toBeGreaterThan(0)
    // Recharts hands fresh (equal) objects on every pointer move over the same day.
    rerender(tree({ ...mark }))
    rerender(tree({ ...mark }))
    expect(width.mock.calls.length).toBe(first)
    // Another day: measured and placed again.
    rerender(tree({ ...mark, left: 100 }))
    expect(width.mock.calls.length).toBeGreaterThan(first)
  })

  it('lingers after the chart lets go, so the pointer can cross the gap onto it — then goes', () => {
    vi.useFakeTimers()
    const { container, rerender } = render(<PinnedTip content={CONTENT} mark={mark} chartBox={box} />)
    rerender(<PinnedTip content={null} mark={mark} chartBox={box} />)
    expect(container.querySelector('[data-chart-tooltip]')).not.toBeNull()
    act(() => {
      vi.advanceTimersByTime(TIP_LINGER_MS + 1)
    })
    expect(container.querySelector('[data-chart-tooltip]')).toBeNull()
  })

  it('holds for as long as the pointer is on it (SC 1.4.13 Hoverable), and goes once it leaves', () => {
    vi.useFakeTimers()
    const tree = (content: TooltipContent | null) => (
      <div className="recharts-wrapper">
        <PinnedTip content={content} mark={mark} sides={COLUMN_SIDES} align="start" chartBox={box} />
      </div>
    )
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(150)
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(60)
    const { container, rerender } = render(tree(CONTENT))
    const wrapper = container.querySelector('.recharts-wrapper') as HTMLElement
    const tip = container.querySelector('[data-chart-tooltip]') as HTMLElement
    // Onto it from its mark.
    for (const [x, y] of [
      [310, 150],
      [316, 110],
      [322, 80],
    ]) {
      fireEvent.mouseMove(wrapper, { clientX: x, clientY: y })
    }
    fireEvent.mouseMove(tip, { clientX: 340, clientY: 50 })
    rerender(tree(null))
    act(() => {
      vi.advanceTimersByTime(TIP_LINGER_MS * 10)
    })
    expect(readTooltip(container.querySelector('[data-chart-tooltip]') as HTMLElement)).toEqual(readTooltip(buildTooltipNode(CONTENT)))
    fireEvent.pointerLeave(container.querySelector('[data-chart-tooltip]') as HTMLElement)
    act(() => {
      vi.advanceTimersByTime(TIP_LINGER_MS + 1)
    })
    expect(container.querySelector('[data-chart-tooltip]')).toBeNull()
  })

  it('shows the NEW mark at once when the pointer moves to another one — lingering never delays it', () => {
    const next = tipContent('2026-03-03 (UTC)', [{ label: 'Pass rate %', value: '80.0%' }])
    const { container, rerender } = render(<PinnedTip content={CONTENT} mark={mark} chartBox={box} />)
    rerender(<PinnedTip content={next} mark={{ ...mark, left: 100 }} chartBox={box} />)
    const tip = container.querySelector('[data-chart-tooltip]') as HTMLElement
    expect(tip.querySelector('.chart-tooltip-title')?.textContent).toBe('2026-03-03 (UTC)')
    expect(tip.style.left).toBe('126px')
  })
})

describe('inside the chart, never portalled to <body> (full screen keeps it)', () => {
  it('Recharts renders the pinned tooltip inside the chart wrapper', () => {
    const data = [
      { x: 'a', y: 1 },
      { x: 'b', y: 2 },
    ]
    const { container } = render(
      <LineChart width={400} height={200} data={data} accessibilityLayer={false}>
        <XAxis dataKey="x" />
        <YAxis />
        <Line dataKey="y" isAnimationActive={false} />
        <Tooltip
          defaultIndex={1}
          active
          position={{ x: 0, y: 0 }}
          isAnimationActive={false}
          content={({ active }) => (
            <PinnedTip content={active ? CONTENT : null} mark={{ left: 200, top: 0, width: 0, height: 200 }} chartBox={{ left: 0, top: 0, width: 400, height: 200 }} />
          )}
        />
      </LineChart>,
    )
    const tip = document.querySelector('[data-chart-tooltip]') as HTMLElement
    expect(tip).not.toBeNull()
    expect(container.contains(tip)).toBe(true)
    expect(tip.closest('.recharts-wrapper')).not.toBeNull()
    expect(tip.parentElement).not.toBe(document.body)
  })
})
