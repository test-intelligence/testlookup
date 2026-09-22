import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { isNavKey, moveInMatrix, useChartKeyboard, type NavRequest } from './useChartKeyboard'

// 3 columns × 2 rows, y grows upward (y = 1 is the top row); (2,0) is missing.
const CELLS = [
  { x: 0, y: 0 },
  { x: 1, y: 0 },
  { x: 0, y: 1 },
  { x: 1, y: 1 },
  { x: 2, y: 1 },
]
const at = (x: number, y: number) => CELLS.findIndex((c) => c.x === x && c.y === y)
const key = (k: NavRequest['key'], whole = false): NavRequest => ({ key: k, whole })

describe('moveInMatrix', () => {
  it('starts at the top-left cell (End: the bottom-right one)', () => {
    expect(moveInMatrix(CELLS, null, key('ArrowRight'))).toBe(at(0, 1))
    expect(moveInMatrix(CELLS, null, key('ArrowDown'))).toBe(at(0, 1))
    expect(moveInMatrix(CELLS, null, key('End'))).toBe(at(1, 0))
    expect(moveInMatrix([], null, key('ArrowRight'))).toBeNull()
  })

  it('moves along a row and stops at its ends', () => {
    expect(moveInMatrix(CELLS, at(0, 1), key('ArrowRight'))).toBe(at(1, 1))
    expect(moveInMatrix(CELLS, at(2, 1), key('ArrowRight'))).toBe(at(2, 1))
    expect(moveInMatrix(CELLS, at(1, 0), key('ArrowLeft'))).toBe(at(0, 0))
    expect(moveInMatrix(CELLS, at(0, 0), key('ArrowLeft'))).toBe(at(0, 0))
  })

  it('moves between rows (up = larger y), to the nearest column when one is missing', () => {
    expect(moveInMatrix(CELLS, at(1, 1), key('ArrowDown'))).toBe(at(1, 0))
    expect(moveInMatrix(CELLS, at(2, 1), key('ArrowDown'))).toBe(at(1, 0))
    expect(moveInMatrix(CELLS, at(0, 0), key('ArrowUp'))).toBe(at(0, 1))
    expect(moveInMatrix(CELLS, at(0, 1), key('ArrowUp'))).toBe(at(0, 1))
  })

  it('Home / End jump within the row; with Ctrl, across the chart', () => {
    expect(moveInMatrix(CELLS, at(1, 1), key('Home'))).toBe(at(0, 1))
    expect(moveInMatrix(CELLS, at(0, 1), key('End'))).toBe(at(2, 1))
    expect(moveInMatrix(CELLS, at(1, 1), key('End', true))).toBe(at(1, 0))
    expect(moveInMatrix(CELLS, at(1, 0), key('Home', true))).toBe(at(0, 1))
  })

  it('recognises only navigation keys', () => {
    expect(isNavKey('ArrowLeft')).toBe(true)
    expect(isNavKey('Enter')).toBe(false)
  })
})

function Harness({ count, onActivate, onClear }: { count: number; onActivate: (i: number) => void; onClear: (i: number) => void }) {
  const { containerRef, containerProps, activeIndex, announcement } = useChartKeyboard({
    count,
    move: (current, request) => moveInMatrix(CELLS.slice(0, count), current, request),
    onActivate,
    onClear,
    describe: (i) => `cell ${CELLS[i].x},${CELLS[i].y}`,
  })
  return (
    <div>
      <div ref={containerRef} {...containerProps} data-testid="chart" data-active={activeIndex ?? ''}>
        <span data-testid="announce">{announcement}</span>
      </div>
      <button type="button">outside</button>
    </div>
  )
}

describe('useChartKeyboard', () => {
  it('arrows activate points, Escape clears and keeps focus on the container', () => {
    const onActivate = vi.fn()
    const onClear = vi.fn()
    render(<Harness count={CELLS.length} onActivate={onActivate} onClear={onClear} />)
    const chart = screen.getByTestId('chart')
    expect(chart).toHaveAttribute('tabindex', '0')
    chart.focus()

    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    expect(onActivate).toHaveBeenLastCalledWith(at(0, 1))
    expect(screen.getByTestId('announce')).toHaveTextContent('cell 0,1')

    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    expect(onClear).toHaveBeenLastCalledWith(at(0, 1))
    expect(onActivate).toHaveBeenLastCalledWith(at(1, 1))
    expect(chart).toHaveAttribute('data-active', String(at(1, 1)))

    // A key with nowhere to go does nothing.
    const calls = onActivate.mock.calls.length
    fireEvent.keyDown(chart, { key: 'ArrowUp' })
    expect(onActivate.mock.calls.length).toBe(calls)
    // Unrelated keys are left alone.
    fireEvent.keyDown(chart, { key: 'a' })
    expect(onActivate.mock.calls.length).toBe(calls)

    fireEvent.keyDown(chart, { key: 'Escape' })
    expect(onClear).toHaveBeenLastCalledWith(at(1, 1))
    expect(chart).toHaveAttribute('data-active', '')
    expect(screen.getByTestId('announce')).toHaveTextContent('')
    expect(document.activeElement).toBe(chart)
  })

  it('leaving the chart clears the highlight; new data resets it', () => {
    const onClear = vi.fn()
    const { rerender } = render(<Harness count={CELLS.length} onActivate={vi.fn()} onClear={onClear} />)
    const chart = screen.getByTestId('chart')
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    fireEvent.blur(chart, { relatedTarget: screen.getByRole('button', { name: 'outside' }) })
    expect(onClear).toHaveBeenCalledTimes(1)
    expect(chart).toHaveAttribute('data-active', '')

    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    expect(chart).toHaveAttribute('data-active', String(at(0, 1)))
    rerender(<Harness count={3} onActivate={vi.fn()} onClear={onClear} />)
    expect(screen.getByTestId('chart')).toHaveAttribute('data-active', '')
  })

  it('does nothing with no points', () => {
    const onActivate = vi.fn()
    render(<Harness count={0} onActivate={onActivate} onClear={vi.fn()} />)
    fireEvent.keyDown(screen.getByTestId('chart'), { key: 'ArrowRight' })
    expect(onActivate).not.toHaveBeenCalled()
  })
})
