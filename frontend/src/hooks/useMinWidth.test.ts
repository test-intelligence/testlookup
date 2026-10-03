/**
 * `useMinWidth` (Wave 3, X2/X3): the report pages' body grid is two columns
 * from 768 px and one below. Without `matchMedia` it answers wide (desktop).
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BODY_GRID_MIN_WIDTH, BODY_GRID_ONE_COLUMN, BODY_GRID_TWO_COLUMNS, useMinWidth } from './useMinWidth'

function stubMatchMedia(initial: boolean) {
  let matches = initial
  const listeners = new Set<() => void>()
  const asked: string[] = []
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => {
      asked.push(query)
      return {
        get matches() {
          return matches
        },
        media: query,
        addEventListener: (_: string, fn: () => void) => listeners.add(fn),
        removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
      }
    }),
  )
  return {
    asked,
    set(next: boolean) {
      matches = next
      for (const fn of listeners) fn()
    },
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useMinWidth', () => {
  it('asks the min-width query and follows it as the window resizes', () => {
    const media = stubMatchMedia(false)
    const { result } = renderHook(() => useMinWidth(BODY_GRID_MIN_WIDTH))
    expect(media.asked).toContain('(min-width: 768px)')
    expect(result.current).toBe(false)
    act(() => media.set(true))
    expect(result.current).toBe(true)
  })

  it('without matchMedia (jsdom, SSR) it is wide: the desktop layout', () => {
    vi.stubGlobal('matchMedia', undefined)
    const { result } = renderHook(() => useMinWidth(768))
    expect(result.current).toBe(true)
  })

  it('the body grid columns: 1.65 : 1, or one column', () => {
    expect(BODY_GRID_TWO_COLUMNS).toBe('minmax(0, 1.65fr) minmax(0, 1fr)')
    expect(BODY_GRID_ONE_COLUMN).toBe('minmax(0, 1fr)')
    expect(BODY_GRID_MIN_WIDTH).toBe(768)
  })
})
