/**
 * Wave 3 (FK0 M0b): Enter on a canvas chart's highlighted point. Without
 * `onEnter`, Enter is not handled and keys from inside the container behave
 * as they always did.
 */
import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useChartKeyboard } from './useChartKeyboard'

function Harness({ onEnter, onActivate = vi.fn() }: { onEnter?: (i: number, m: { shiftKey: boolean }) => void; onActivate?: (i: number) => void }) {
  const { containerRef, containerProps, activeIndex } = useChartKeyboard({
    count: 3,
    move: (current, { key }) => (key === 'ArrowRight' ? Math.min((current ?? -1) + 1, 2) : current),
    onActivate,
    onClear: () => {},
    describe: (i) => `point ${i}`,
    onEnter,
  })
  return (
    <div ref={containerRef} data-surface="" data-active={activeIndex ?? ''} {...containerProps}>
      <button type="button" data-inner="">View rows</button>
    </div>
  )
}

const surfaceOf = (container: HTMLElement) => container.querySelector('[data-surface]') as HTMLElement

describe('useChartKeyboard Enter', () => {
  it('without onEnter, Enter is not handled', () => {
    const { container } = render(<Harness />)
    const surface = surfaceOf(container)
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(fireEvent.keyDown(surface, { key: 'Enter' })).toBe(true)
  })

  it('without onEnter, an arrow pressed inside the container still moves (the old behaviour)', () => {
    const { container } = render(<Harness />)
    fireEvent.keyDown(container.querySelector('[data-inner]') as HTMLElement, { key: 'ArrowRight' })
    expect(surfaceOf(container).getAttribute('data-active')).toBe('0')
  })

  it('hands the highlighted index and Shift to the chart', () => {
    const onEnter = vi.fn()
    const { container } = render(<Harness onEnter={onEnter} />)
    const surface = surfaceOf(container)
    // Nothing highlighted: nothing to activate.
    expect(fireEvent.keyDown(surface, { key: 'Enter' })).toBe(true)
    expect(onEnter).not.toHaveBeenCalled()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(fireEvent.keyDown(surface, { key: 'Enter' })).toBe(false)
    expect(onEnter).toHaveBeenLastCalledWith(1, { shiftKey: false })
    fireEvent.keyDown(surface, { key: 'Enter', shiftKey: true })
    expect(onEnter).toHaveBeenLastCalledWith(1, { shiftKey: true })
    fireEvent.keyDown(surface, { key: 'Enter', ctrlKey: true })
    expect(onEnter).toHaveBeenCalledTimes(2)
  })

  it('a key on an action button inside the container is the button’s; Escape still clears and refocuses', () => {
    const onEnter = vi.fn()
    const onActivate = vi.fn()
    const { container } = render(<Harness onEnter={onEnter} onActivate={onActivate} />)
    const surface = surfaceOf(container)
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const inner = container.querySelector('[data-inner]') as HTMLElement
    inner.focus()
    expect(fireEvent.keyDown(inner, { key: 'Enter' })).toBe(true)
    fireEvent.keyDown(inner, { key: 'ArrowRight' })
    expect(onEnter).not.toHaveBeenCalled()
    expect(onActivate).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(inner, { key: 'Escape' })
    expect(surface.getAttribute('data-active')).toBe('')
    expect(document.activeElement).toBe(surface)
  })
})
