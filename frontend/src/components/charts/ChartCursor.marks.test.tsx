/**
 * Wave 3 (FK0 M0b): mark activation through the shared keyboard cursor.
 *
 * Without `onMarkActivate` the cursor is exactly the pre-Wave-3 cursor (the
 * flag-off pages are byte-identical): same name, Enter ignored, no buttons.
 * With it, Enter / Shift+Enter activate the focused mark and the readout grows
 * real buttons that Tab reaches.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import axe from 'axe-core'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import SidePanel from '@/components/ui/SidePanel'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { CURSOR_ACTIVATE_HINT, CURSOR_HINT, useChartCursor, type ChartCursorPoint } from './ChartCursor'
import { MARK_BUTTON_LABEL_MAX, MarkActions } from './MarkActions'
import type { ChartMark, MarkActivateHandler, MarkIntentsFor, MarkKit } from './marks'
import { MARK_KIT } from './markKit'

const payments: ChartMark = { dimension: 'suite', value: 'suite-key-1', label: 'payments', y: 42, n: 50 }
const hostile: ChartMark = {
  dimension: 'suite',
  value: '__proto__',
  label: `<img src=x onerror="window.__xss=1">${'x'.repeat(200)}`,
  y: 3,
  n: 3,
}
const POINTS: ChartCursorPoint[] = [
  { key: 'a', text: 'payments: 42 failures', mark: payments },
  { key: 'b', text: 'hostile: 3 failures', mark: hostile },
  { key: 'c', text: 'no mark here' },
]

interface ProbeProps {
  onMarkActivate?: MarkActivateHandler
  markIntents?: MarkIntentsFor
  points?: ChartCursorPoint[]
  expose?: (cursor: ReturnType<typeof useChartCursor>) => void
  /** The activation code (`MARK_KIT`), handed in by the host; `null` = a host that gave a handler and no kit. */
  kit?: MarkKit | null
}

function Probe({ onMarkActivate, markIntents, points = POINTS, expose, kit = MARK_KIT }: ProbeProps) {
  const cursor = useChartCursor({
    title: 'Failures by suite',
    chartType: 'bar chart',
    points,
    noun: 'bar',
    onMarkActivate,
    markIntents,
    markKit: kit ?? undefined,
  })
  expose?.(cursor)
  return (
    <>
      <div data-probe="" {...cursor.surfaceProps}>
        {cursor.readout}
      </div>
      <button type="button" data-outside="">outside</button>
    </>
  )
}

function mount(props: ProbeProps = {}) {
  const view = render(
    <ChartAnnouncerProvider>
      <Probe {...props} />
    </ChartAnnouncerProvider>,
  )
  const surface = view.container.querySelector('[data-probe]') as HTMLElement
  return { ...view, surface }
}

describe('no handler: the pre-Wave-3 cursor, unchanged', () => {
  it('keeps the old name, ignores Enter, draws no buttons', () => {
    const { surface, container } = mount()
    expect(surface.getAttribute('aria-label')).toBe(`Failures by suite, bar chart, 3 bars. ${CURSOR_HINT}`)
    surface.focus()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const enter = fireEvent.keyDown(surface, { key: 'Enter' })
    // Not handled: default not prevented.
    expect(enter).toBe(true)
    expect(container.querySelector('[data-mark-actions]')).toBeNull()
    expect(container.querySelectorAll('button')).toHaveLength(1)
  })

  it('a pointer activation is a no-op', () => {
    let cursor!: ReturnType<typeof useChartCursor>
    mount({ expose: (c) => (cursor = c) })
    expect(cursor.activatable).toBe(false)
    expect(() => cursor.activatePointer(0, {})).not.toThrow()
  })

  it('a handler with no marked point changes nothing either', () => {
    const onMarkActivate = vi.fn()
    const { surface } = mount({ onMarkActivate, points: [{ key: 'c', text: 'plain' }] })
    expect(surface.getAttribute('aria-label')).toBe(`Failures by suite, bar chart, 1 bar. ${CURSOR_HINT}`)
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    fireEvent.keyDown(surface, { key: 'Enter' })
    expect(onMarkActivate).not.toHaveBeenCalled()
  })

  // The kit comes from the host so the default chart path never loads it: a
  // handler on its own (no kit) must not half-activate the chart.
  it('a handler WITHOUT the kit is not activatable: old name, Enter ignored, no buttons, pointer a no-op', () => {
    const onMarkActivate = vi.fn()
    let cursor!: ReturnType<typeof useChartCursor>
    const { surface, container } = mount({ onMarkActivate, kit: null, expose: (c) => (cursor = c) })
    expect(surface.getAttribute('aria-label')).toBe(`Failures by suite, bar chart, 3 bars. ${CURSOR_HINT}`)
    surface.focus()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(fireEvent.keyDown(surface, { key: 'Enter' })).toBe(true)
    expect(container.querySelector('[data-mark-actions]')).toBeNull()
    expect(cursor.activatable).toBe(false)
    cursor.activatePointer(0, {})
    expect(cursor.activateMark(payments, {})).toBe(false)
    expect(onMarkActivate).not.toHaveBeenCalled()
  })
})

describe('with a handler', () => {
  const both: MarkIntentsFor = () => ['drill', 'rows', 'filter']

  it('says Enter works, in the chart’s name', () => {
    const { surface } = mount({ onMarkActivate: vi.fn() })
    expect(surface.getAttribute('aria-label')).toBe(`Failures by suite, bar chart, 3 bars. ${CURSOR_HINT} ${CURSOR_ACTIVATE_HINT}`)
  })

  it('Enter activates the focused mark with the first intent; Shift+Enter filters', () => {
    const onMarkActivate = vi.fn()
    const { surface } = mount({ onMarkActivate, markIntents: both })
    surface.focus()
    // Nothing focused yet: Enter does nothing.
    fireEvent.keyDown(surface, { key: 'Enter' })
    expect(onMarkActivate).not.toHaveBeenCalled()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const handled = fireEvent.keyDown(surface, { key: 'Enter' })
    expect(handled).toBe(false)
    expect(onMarkActivate).toHaveBeenLastCalledWith(payments, 'drill')
    fireEvent.keyDown(surface, { key: 'Enter', shiftKey: true })
    expect(onMarkActivate).toHaveBeenLastCalledWith(payments, 'filter')
  })

  it('Enter on a point with no mark does nothing', () => {
    const onMarkActivate = vi.fn()
    const { surface } = mount({ onMarkActivate })
    fireEvent.keyDown(surface, { key: 'End' })
    expect(fireEvent.keyDown(surface, { key: 'Enter' })).toBe(true)
    expect(onMarkActivate).not.toHaveBeenCalled()
  })

  it('Ctrl+Enter stays the reader’s', () => {
    const onMarkActivate = vi.fn()
    const { surface } = mount({ onMarkActivate })
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    fireEvent.keyDown(surface, { key: 'Enter', ctrlKey: true })
    expect(onMarkActivate).not.toHaveBeenCalled()
  })

  it('the readout offers each intent as a button that Tab reaches, and the cursor survives the Tab', () => {
    const onMarkActivate = vi.fn()
    const { surface, container } = mount({ onMarkActivate, markIntents: both })
    surface.focus()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const buttons = [...container.querySelectorAll<HTMLButtonElement>('[data-mark-actions] button')]
    expect(buttons.map((b) => b.textContent)).toEqual(['Drill into payments', 'View rows', 'Filter page by this'])
    expect(buttons.every((b) => b.type === 'button' && !b.closest('[aria-hidden="true"]'))).toBe(true)
    // Focus moves from the surface onto its own button: the cursor (and the buttons) stay.
    fireEvent.blur(surface, { relatedTarget: buttons[1] })
    buttons[1].focus()
    expect(surface.getAttribute('data-chart-cursor')).toBe('active')
    fireEvent.click(buttons[1])
    expect(onMarkActivate).toHaveBeenCalledWith(payments, 'rows')
  })

  it('a key on a button is the button’s: Enter there does not ALSO activate through the surface', () => {
    const onMarkActivate = vi.fn()
    const { surface, container } = mount({ onMarkActivate, markIntents: both })
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const button = container.querySelector<HTMLButtonElement>('[data-mark-intent="rows"]') as HTMLButtonElement
    expect(fireEvent.keyDown(button, { key: 'Enter' })).toBe(true)
    fireEvent.keyDown(button, { key: 'ArrowRight' })
    expect(onMarkActivate).not.toHaveBeenCalled()
    expect(surface.getAttribute('data-chart-cursor-index')).toBe('0')
  })

  it('Escape on a button closes the readout and returns focus to the chart', () => {
    const { surface, container } = mount({ onMarkActivate: vi.fn(), markIntents: both })
    surface.focus()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const button = container.querySelector<HTMLButtonElement>('[data-mark-intent="drill"]') as HTMLButtonElement
    button.focus()
    fireEvent.keyDown(button, { key: 'Escape' })
    expect(document.activeElement).toBe(surface)
    expect(surface.getAttribute('data-chart-cursor')).toBe('idle')
    expect(container.querySelector('[data-mark-actions]')).toBeNull()
  })

  it('a readout button hands focus to the chart BEFORE it acts, so the panel it opens returns focus there (R1B-4)', () => {
    // The real panel: it captures the opener in its opening render and hands
    // focus back on close only if that opener is still in the page. The
    // button is not (the cursor clears when focus enters the panel).
    function Host() {
      const [mark, setMark] = useState<ChartMark | null>(null)
      return (
        <>
          <Probe onMarkActivate={(m) => setMark(m)} markIntents={both} />
          <SidePanel open={mark !== null} onClose={() => setMark(null)} title="Rows" closeLabel="Close rows">
            <p>rows</p>
          </SidePanel>
        </>
      )
    }
    const view = render(
      <ChartAnnouncerProvider>
        <Host />
      </ChartAnnouncerProvider>,
    )
    const surface = view.container.querySelector('[data-probe]') as HTMLElement
    surface.focus()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const button = view.container.querySelector<HTMLButtonElement>('[data-mark-intent="rows"]') as HTMLButtonElement
    button.focus()
    fireEvent.click(button)
    const close = screen.getByRole('button', { name: 'Close rows' })
    expect(document.activeElement).toBe(close)
    expect(view.container.querySelector('[data-mark-actions]')).toBeNull()
    fireEvent.click(close)
    expect(document.activeElement).toBe(surface)
    view.unmount()
  })

  it('leaving the chart for something else still clears the cursor', () => {
    const { surface, container } = mount({ onMarkActivate: vi.fn() })
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    fireEvent.blur(surface, { relatedTarget: container.querySelector('[data-outside]') })
    expect(surface.getAttribute('data-chart-cursor')).toBe('idle')
  })

  it('a hostile label is text, middle-truncated on the button, whole in the readout', () => {
    const { surface, container } = mount({ onMarkActivate: vi.fn() })
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const button = container.querySelector('[data-mark-intent="drill"]') as HTMLButtonElement
    expect(button.textContent?.startsWith('Drill into <img src=x onerror=')).toBe(true)
    expect(button.textContent?.length).toBeLessThanOrEqual('Drill into '.length + MARK_BUTTON_LABEL_MAX)
    expect(container.querySelector('img')).toBeNull()
  })

  it('activatePointer: click = first intent, Shift-click = filter, a touch tap selects and shows the buttons', () => {
    const onMarkActivate = vi.fn()
    let cursor!: ReturnType<typeof useChartCursor>
    const { surface, container } = mount({ onMarkActivate, markIntents: both, expose: (c) => (cursor = c) })
    expect(cursor.activatable).toBe(true)
    cursor.activatePointer(0, { pointerType: 'mouse' })
    expect(onMarkActivate).toHaveBeenLastCalledWith(payments, 'drill')
    cursor.activatePointer(1, { shiftKey: true })
    expect(onMarkActivate).toHaveBeenLastCalledWith(hostile, 'filter')
    onMarkActivate.mockClear()
    fireEvent.pointerDown(surface)
    act(() => cursor.activatePointer(0, { pointerType: 'touch' }))
    expect(onMarkActivate).not.toHaveBeenCalled()
    expect(surface.getAttribute('data-chart-cursor-index')).toBe('0')
    expect(container.querySelector('[data-mark-actions]')).not.toBeNull()
    // A point with no mark: nothing.
    cursor.activatePointer(2, {})
    expect(onMarkActivate).not.toHaveBeenCalled()
  })
})

describe('axe', () => {
  it('finds nothing on a focused, activatable cursor with its buttons', async () => {
    const { surface, container } = mount({ onMarkActivate: vi.fn(), markIntents: () => ['drill', 'rows', 'filter'] })
    surface.focus()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(container.querySelector('[data-mark-actions]')).not.toBeNull()
    const results = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(results.violations.map((v) => v.id)).toEqual([])
  })
})

describe('MarkActions (for canvas charts)', () => {
  it('renders nothing for no intents, and one button per intent otherwise', () => {
    const onActivate = vi.fn()
    const { container, rerender } = render(<MarkActions mark={payments} intents={[]} onActivate={onActivate} />)
    expect(container.innerHTML).toBe('')
    rerender(<MarkActions mark={payments} intents={['rows']} onActivate={onActivate} />)
    fireEvent.click(screen.getByRole('button', { name: 'View rows' }))
    expect(onActivate).toHaveBeenCalledWith(payments, 'rows')
    expect(screen.getByRole('group', { name: 'Actions for the focused value' })).toBeTruthy()
  })

  it('returnFocus: the element it names takes focus before the action runs; none, nothing moves', () => {
    const order: string[] = []
    const chart = document.createElement('div')
    chart.tabIndex = 0
    document.body.appendChild(chart)
    const onActivate = vi.fn(() => order.push(`act:${document.activeElement === chart ? 'chart' : 'other'}`))
    const returnFocus = vi.fn((button: HTMLButtonElement) => {
      order.push(`focus:${button.dataset.markIntent}`)
      return chart
    })
    const { rerender } = render(<MarkActions mark={payments} intents={['rows']} onActivate={onActivate} returnFocus={returnFocus} />)
    fireEvent.click(screen.getByRole('button', { name: 'View rows' }))
    expect(order).toEqual(['focus:rows', 'act:chart'])
    // A host that names nothing (the chart is gone) still acts.
    rerender(<MarkActions mark={payments} intents={['rows']} onActivate={onActivate} returnFocus={() => null} />)
    fireEvent.click(screen.getByRole('button', { name: 'View rows' }))
    expect(onActivate).toHaveBeenCalledTimes(2)
    chart.remove()
  })
})
