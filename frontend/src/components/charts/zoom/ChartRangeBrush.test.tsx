/**
 * VIZ-407 — the range brush: two keyboard range handles that cannot cross,
 * drag-to-select with a mouse (and not with a finger), Reset zoom, and the
 * "Apply as time filter" action with its visible reason.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { addUtcDays } from '../seriesAlignment'
import FilterChips from '@/components/filters/FilterChips'
import ChartRangeBrush, { APPLY_AS_FILTER_LABEL, RESET_ZOOM_LABEL } from './ChartRangeBrush'
import { applyAsWindowLabel, windowAppliedAnnouncement } from './windowWords'
import { PROMOTE_NOT_LATEST_REASON, RELATIVE_DAY_WORDS, type PromoteDecision, type ZoomRange } from './zoomModel'

const XS = Array.from({ length: 30 }, (_, i) => addUtcDays('2026-09-01', i))

function Harness({
  initial = null,
  xs = XS,
  onChange,
  promote,
  onPromote,
  words,
}: {
  initial?: ZoomRange | null
  xs?: readonly string[]
  onChange?: (range: ZoomRange | null) => void
  promote?: PromoteDecision | null
  onPromote?: (days: number) => void
  words?: typeof RELATIVE_DAY_WORDS
}) {
  const [range, setRange] = useState<ZoomRange | null>(initial)
  return (
    <div data-chart-body="" tabIndex={-1}>
      <ChartRangeBrush
        xs={xs}
        range={range}
        onRangeChange={(next) => {
          onChange?.(next)
          setRange(next)
        }}
        title="Pass rate trend"
        promote={promote}
        onPromote={onPromote}
        words={words}
      />
    </div>
  )
}

const start = () => screen.getByRole('slider', { name: 'Start of zoom range' })
const end = () => screen.getByRole('slider', { name: 'End of zoom range' })
const key = (el: HTMLElement, k: string) => fireEvent.keyDown(el, { key: k })

describe('ChartRangeBrush — keyboard range handles', () => {
  it('names each handle and states its day in words, with the other handle as its bound', () => {
    render(<Harness initial={{ start: 4, end: 11 }} />)
    expect(screen.getByRole('group', { name: 'Zoom Pass rate trend' })).toBeInTheDocument()
    expect(start()).toHaveAttribute('aria-valuemin', '0')
    expect(start()).toHaveAttribute('aria-valuemax', '11')
    expect(start()).toHaveAttribute('aria-valuenow', '4')
    expect(start()).toHaveAttribute('aria-valuetext', 'September 5, 2026')
    expect(end()).toHaveAttribute('aria-valuemin', '4')
    expect(end()).toHaveAttribute('aria-valuemax', '29')
    expect(end()).toHaveAttribute('aria-valuenow', '11')
    expect(end()).toHaveAttribute('aria-valuetext', 'September 12, 2026')
    expect(start()).toHaveAttribute('tabindex', '0')
    expect(start().getAttribute('aria-describedby')).toBeTruthy()
  })

  it('moves a day with the arrows and a week with Page Up / Page Down', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    key(start(), 'ArrowRight')
    expect(onChange).toHaveBeenLastCalledWith({ start: 1, end: 29 })
    key(start(), 'ArrowUp')
    expect(start()).toHaveAttribute('aria-valuenow', '2')
    key(start(), 'PageUp')
    expect(start()).toHaveAttribute('aria-valuenow', '9')
    key(start(), 'ArrowLeft')
    key(start(), 'ArrowDown')
    expect(start()).toHaveAttribute('aria-valuenow', '7')
    key(start(), 'PageDown')
    expect(start()).toHaveAttribute('aria-valuenow', '0')
    key(end(), 'PageDown')
    expect(end()).toHaveAttribute('aria-valuenow', '22')
    key(end(), 'ArrowLeft')
    expect(end()).toHaveAttribute('aria-valuenow', '21')
    key(end(), 'PageUp')
    expect(end()).toHaveAttribute('aria-valuenow', '28')
  })

  it('Home and End go to the ends — each handle’s end being the other handle', () => {
    render(<Harness initial={{ start: 10, end: 20 }} />)
    key(start(), 'Home')
    expect(start()).toHaveAttribute('aria-valuenow', '0')
    key(start(), 'End')
    expect(start()).toHaveAttribute('aria-valuenow', '20')
    key(end(), 'End')
    expect(end()).toHaveAttribute('aria-valuenow', '29')
    key(end(), 'Home')
    // Meets the start handle: one day, not a crossing.
    expect(end()).toHaveAttribute('aria-valuenow', '20')
    expect(start()).toHaveAttribute('aria-valuenow', '20')
  })

  it('the handles never cross, and never leave the axis', () => {
    render(<Harness initial={{ start: 10, end: 12 }} />)
    key(start(), 'PageUp')
    expect(start()).toHaveAttribute('aria-valuenow', '12')
    expect(end()).toHaveAttribute('aria-valuenow', '12')
    key(end(), 'PageDown')
    expect(end()).toHaveAttribute('aria-valuenow', '12')
    key(end(), 'PageUp')
    key(end(), 'PageUp')
    key(end(), 'PageUp')
    expect(end()).toHaveAttribute('aria-valuenow', '29')
    key(start(), 'Home')
    key(start(), 'ArrowLeft')
    expect(start()).toHaveAttribute('aria-valuenow', '0')
  })

  it('ignores other keys (Tab keeps moving focus)', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    const event = fireEvent.keyDown(start(), { key: 'Tab' })
    expect(event).toBe(true)
    key(start(), 'a')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('moving a handle back to the whole axis resets the zoom', () => {
    const onChange = vi.fn()
    render(<Harness initial={{ start: 1, end: 29 }} onChange={onChange} />)
    key(start(), 'ArrowLeft')
    expect(onChange).toHaveBeenLastCalledWith(null)
    expect(screen.queryByRole('button', { name: RESET_ZOOM_LABEL })).not.toBeInTheDocument()
  })

  it('speaks relative days on a release-aligned axis', () => {
    render(<Harness xs={['0', '1', '2', '3']} words={RELATIVE_DAY_WORDS} />)
    expect(end()).toHaveAttribute('aria-valuetext', 'Day 3 since release start')
  })

  it('draws nothing for an axis that cannot be zoomed', () => {
    const { container } = render(<Harness xs={[XS[0]]} />)
    expect(container.querySelector('[data-chart-brush]')).toBeNull()
  })
})

describe('ChartRangeBrush — pointer', () => {
  function withTrack() {
    const track = document.querySelector('[data-chart-brush-track]') as HTMLElement
    // 30 days over 300 px: day i owns [10i, 10i + 10).
    track.getBoundingClientRect = () => ({ left: 0, width: 300, top: 0, height: 32, right: 300, bottom: 32, x: 0, y: 0, toJSON: () => ({}) })
    return track
  }

  it('a mouse drag across the strip zooms to the days it covered, on release', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    const track = withTrack()
    fireEvent.pointerDown(track, { clientX: 125, pointerId: 1, button: 0, pointerType: 'mouse' })
    fireEvent.pointerMove(track, { clientX: 80, pointerId: 1, pointerType: 'mouse' })
    // Previewed, not yet committed.
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByText(/Showing Sep 9–13, 2026/)).toBeInTheDocument()
    fireEvent.pointerUp(track, { clientX: 80, pointerId: 1, pointerType: 'mouse' })
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenLastCalledWith({ start: 8, end: 12 })
  })

  const click = (track: HTMLElement, clientX: number, pointerType = 'mouse', pointerId = 1) => {
    fireEvent.pointerDown(track, { clientX, clientY: 10, pointerId, button: 0, pointerType })
    // A wobble under the drag threshold is still a click.
    fireEvent.pointerMove(track, { clientX: clientX + 1, clientY: 10, pointerId, pointerType })
    fireEvent.pointerUp(track, { clientX: clientX + 1, clientY: 10, pointerId, pointerType })
  }

  // SC 2.5.7 (review A3): dragging is not the only pointer way to zoom.
  it('a single click picks a day and does not zoom yet; a second click zooms to the two days', () => {
    const onChange = vi.fn()
    const { container } = render(<Harness onChange={onChange} />)
    const track = withTrack()
    click(track, 125) // day 12
    expect(onChange).not.toHaveBeenCalled()
    // The picked day is marked, and the label says the range waits for its other end.
    const marker = container.querySelector('[data-chart-brush-picked]') as HTMLElement
    expect(marker.style.left).toBe('40%')
    expect(screen.getByText('From September 13, 2026: click the day the range ends')).toBeVisible()
    click(track, 184) // day 18
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenLastCalledWith({ start: 12, end: 18 })
    expect(container.querySelector('[data-chart-brush-picked]')).toBeNull()
    expect(screen.getByText(/Showing Sep 13–19, 2026 \(7 of 30 days\)/)).toBeInTheDocument()
  })

  it('the two clicks may come in either order, and the same day twice is a one-day zoom', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    const track = withTrack()
    click(track, 205)
    click(track, 35)
    expect(onChange).toHaveBeenLastCalledWith({ start: 3, end: 20 })
    click(track, 95)
    click(track, 95)
    expect(onChange).toHaveBeenLastCalledWith({ start: 9, end: 9 })
  })

  it('reaches ANY range in two clicks, from any zoom (two days near the end of a wide range)', () => {
    const onChange = vi.fn()
    render(<Harness initial={{ start: 0, end: 25 }} onChange={onChange} />)
    const track = withTrack()
    click(track, 215)
    click(track, 225)
    expect(onChange).toHaveBeenLastCalledWith({ start: 21, end: 22 })
  })

  it('a handle, a key or a drag drops the picked day', () => {
    const onChange = vi.fn()
    const { container } = render(<Harness initial={{ start: 2, end: 20 }} onChange={onChange} />)
    const track = withTrack()
    click(track, 125)
    key(start(), 'ArrowRight')
    expect(container.querySelector('[data-chart-brush-picked]')).toBeNull()
    // The next click is a FIRST click again.
    onChange.mockClear()
    click(track, 155)
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.pointerDown(end(), { clientX: 210, pointerId: 5, button: 0, pointerType: 'mouse' })
    expect(container.querySelector('[data-chart-brush-picked]')).toBeNull()
    fireEvent.pointerUp(track, { clientX: 210, pointerId: 5, pointerType: 'mouse' })
    click(track, 125)
    fireEvent.pointerDown(track, { clientX: 50, pointerId: 6, button: 0, pointerType: 'mouse' })
    fireEvent.pointerMove(track, { clientX: 90, pointerId: 6, pointerType: 'mouse' })
    fireEvent.pointerUp(track, { clientX: 90, pointerId: 6, pointerType: 'mouse' })
    expect(onChange).toHaveBeenLastCalledWith({ start: 5, end: 9 })
    expect(container.querySelector('[data-chart-brush-picked]')).toBeNull()
  })

  it('a finger sliding over the strip scrolls the page — it does not select or pick', () => {
    const onChange = vi.fn()
    const { container } = render(<Harness onChange={onChange} />)
    const track = withTrack()
    expect(track.style.touchAction).toBe('pan-y')
    fireEvent.pointerDown(track, { clientX: 125, clientY: 10, pointerId: 2, button: 0, pointerType: 'touch' })
    fireEvent.pointerMove(track, { clientX: 20, clientY: 10, pointerId: 2, pointerType: 'touch' })
    fireEvent.pointerUp(track, { clientX: 20, clientY: 10, pointerId: 2, pointerType: 'touch' })
    // The browser taking the finger for a vertical scroll cancels the pointer.
    fireEvent.pointerDown(track, { clientX: 125, clientY: 10, pointerId: 7, button: 0, pointerType: 'touch' })
    fireEvent.pointerCancel(track, { clientX: 125, clientY: 10, pointerId: 7, pointerType: 'touch' })
    expect(onChange).not.toHaveBeenCalled()
    expect(container.querySelector('[data-chart-brush-picked]')).toBeNull()
  })

  it('two TAPS zoom as two clicks do (a finger has a single-pointer way too)', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    const track = withTrack()
    click(track, 45, 'touch', 8)
    click(track, 75, 'touch', 9)
    expect(onChange).toHaveBeenLastCalledWith({ start: 4, end: 7 })
  })

  // SC 2.5.8 (review A8): centred handles overlapped by 10 px when one day
  // apart, leaving the start handle ~14 px. Each now sits OUTSIDE its edge.
  it('the start handle sits left of its edge and the end handle right of its own, so they never overlap', () => {
    render(<Harness initial={{ start: 9, end: 9 }} />)
    expect(start().className).toContain('-translate-x-full')
    expect(end().className).toContain('translate-x-0')
    expect(`${start().className} ${end().className}`).not.toContain('-translate-x-1/2')
    // The start handle's right edge is the start of day 9; the end handle's left edge is the end of day 9.
    expect(start().style.left).toBe(`${(9 / 30) * 100}%`)
    expect(end().style.left).toBe(`${(10 / 30) * 100}%`)
  })

  // Review N8: every aria value of a handle comes from the range SHOWN.
  it('during a drag preview a handle never reports a value outside its own bounds', () => {
    render(<Harness initial={{ start: 5, end: 9 }} />)
    const track = withTrack()
    fireEvent.pointerDown(track, { clientX: 125, pointerId: 1, button: 0, pointerType: 'mouse' })
    fireEvent.pointerMove(track, { clientX: 205, pointerId: 1, pointerType: 'mouse' })
    for (const slider of [start(), end()]) {
      const now = Number(slider.getAttribute('aria-valuenow'))
      expect(now).toBeGreaterThanOrEqual(Number(slider.getAttribute('aria-valuemin')))
      expect(now).toBeLessThanOrEqual(Number(slider.getAttribute('aria-valuemax')))
    }
    expect(start()).toHaveAttribute('aria-valuenow', '12')
    expect(start()).toHaveAttribute('aria-valuemax', '20')
    expect(end()).toHaveAttribute('aria-valuemin', '12')
    fireEvent.pointerCancel(track, { clientX: 205, pointerId: 1, pointerType: 'mouse' })
  })

  it('a finger drags a handle (touch-action none), which stops at the other handle', () => {
    const onChange = vi.fn()
    render(<Harness initial={{ start: 5, end: 9 }} onChange={onChange} />)
    const track = withTrack()
    expect(start().style.touchAction).toBe('none')
    fireEvent.pointerDown(start(), { clientX: 50, pointerId: 3, button: 0, pointerType: 'touch' })
    // Dragged far past the end handle: it stops there.
    fireEvent.pointerMove(track, { clientX: 250, pointerId: 3, pointerType: 'touch' })
    fireEvent.pointerUp(track, { clientX: 250, pointerId: 3, pointerType: 'touch' })
    expect(onChange).toHaveBeenLastCalledWith({ start: 9, end: 9 })
    fireEvent.pointerDown(end(), { clientX: 100, pointerId: 4, button: 0, pointerType: 'touch' })
    fireEvent.pointerMove(track, { clientX: 200, pointerId: 4, pointerType: 'touch' })
    fireEvent.pointerUp(track, { clientX: 200, pointerId: 4, pointerType: 'touch' })
    // The end handle lands on the edge after day 19.
    expect(onChange).toHaveBeenLastCalledWith({ start: 9, end: 19 })
  })

  it('a cancelled drag changes nothing', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    const track = withTrack()
    fireEvent.pointerDown(track, { clientX: 125, pointerId: 1, button: 0, pointerType: 'mouse' })
    fireEvent.pointerMove(track, { clientX: 20, pointerId: 1, pointerType: 'mouse' })
    fireEvent.pointerCancel(track, { clientX: 20, pointerId: 1, pointerType: 'mouse' })
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByText('Showing all 30 days')).toBeInTheDocument()
  })
})

describe('ChartRangeBrush — Reset zoom and Apply as time filter', () => {
  it('shows Reset zoom only while zoomed; resetting moves focus to the start handle', () => {
    render(<Harness />)
    expect(screen.queryByRole('button', { name: RESET_ZOOM_LABEL })).not.toBeInTheDocument()
    key(start(), 'PageUp')
    const reset = screen.getByRole('button', { name: RESET_ZOOM_LABEL })
    reset.focus()
    fireEvent.click(reset)
    expect(screen.queryByRole('button', { name: RESET_ZOOM_LABEL })).not.toBeInTheDocument()
    expect(start()).toHaveAttribute('aria-valuenow', '0')
    expect(end()).toHaveAttribute('aria-valuenow', '29')
    expect(document.activeElement).toBe(start())
  })

  it('an enabled Apply states the window it sets, applies exactly that, and parks focus on the chart body', () => {
    const onPromote = vi.fn()
    render(<Harness initial={{ start: 23, end: 29 }} promote={{ enabled: true, days: 7 }} onPromote={onPromote} />)
    const apply = screen.getByRole('button', { name: 'Apply as time filter: last 7 days' })
    expect(apply).not.toHaveAttribute('aria-disabled')
    fireEvent.click(apply)
    expect(onPromote).toHaveBeenCalledWith(7)
    expect(document.activeElement).toBe(document.querySelector('[data-chart-body]'))
  })

  it('a disabled Apply stays focusable, says why, and never applies', () => {
    const onPromote = vi.fn()
    render(
      <Harness
        initial={{ start: 3, end: 9 }}
        promote={{ enabled: false, reason: PROMOTE_NOT_LATEST_REASON }}
        onPromote={onPromote}
      />,
    )
    const apply = screen.getByRole('button', { name: APPLY_AS_FILTER_LABEL })
    expect(apply).toHaveAttribute('aria-disabled', 'true')
    expect(apply).not.toBeDisabled()
    expect(apply).toHaveAccessibleDescription(PROMOTE_NOT_LATEST_REASON)
    expect(screen.getByText(PROMOTE_NOT_LATEST_REASON)).toBeVisible()
    fireEvent.click(apply)
    expect(onPromote).not.toHaveBeenCalled()
  })

  // Review F8: "last 1 day" on the button, "last 24 hours" on the chip it writes to.
  it('names a one-day window exactly as the filter chip it sets does', () => {
    render(<Harness initial={{ start: 29, end: 29 }} promote={{ enabled: true, days: 1 }} />)
    const label = screen.getByRole('button', { name: /^Apply as time filter: / }).textContent ?? ''
    render(
      <FilterChips
        releases={[]}
        suites={[]}
        windowDays={1}
        defaultWindowDays={30}
        onRemoveRelease={() => {}}
        onRemoveSuite={() => {}}
        onResetWindow={() => {}}
        onClearAll={() => {}}
      />,
    )
    const chipWords = label.replace('Apply as time filter: ', '')
    expect(chipWords).toBe('last 24 hours')
    expect(screen.getByText(chipWords)).toBeInTheDocument()
    expect(windowAppliedAnnouncement(1)).toBe('Page window set to the last 24 hours')
    expect(applyAsWindowLabel(7)).toBe('Apply as time filter: last 7 days')
  })

  it('offers no Apply where the page did not ask for one', () => {
    render(<Harness initial={{ start: 23, end: 29 }} />)
    expect(screen.queryByRole('button', { name: /apply as time filter/i })).not.toBeInTheDocument()
  })

  it('adds no live region of its own', () => {
    const { container } = render(<Harness initial={{ start: 3, end: 9 }} />)
    expect(container.querySelector('[aria-live], [role="status"], [role="alert"]')).toBeNull()
  })
})
