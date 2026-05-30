import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { usePageVisibility, useVisibilityAwareInterval } from './usePageVisibility'

describe('usePageVisibility', () => {
  let hidden: boolean

  beforeEach(() => {
    hidden = false
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => hidden,
    })
  })

  afterEach(() => {
    // Restore default
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => false,
    })
  })

  it('returns true when tab is visible', () => {
    hidden = false
    const { result } = renderHook(() => usePageVisibility())
    expect(result.current).toBe(true)
  })

  it('returns false after visibility change to hidden', () => {
    hidden = false
    const { result } = renderHook(() => usePageVisibility())

    act(() => {
      hidden = true
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(result.current).toBe(false)
  })

  it('returns true after visibility change back to visible', () => {
    hidden = true
    const { result } = renderHook(() => usePageVisibility())

    act(() => {
      hidden = false
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(result.current).toBe(true)
  })
})

describe('useVisibilityAwareInterval', () => {
  beforeEach(() => {
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => false,
    })
  })

  it('returns the interval when visible', () => {
    const { result } = renderHook(() => useVisibilityAwareInterval(15_000))
    expect(result.current).toBe(15_000)
  })

  it('returns 0 when hidden', () => {
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => true,
    })
    const { result } = renderHook(() => useVisibilityAwareInterval(15_000))

    // Need to trigger the visibility change for the hook to pick it up
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(result.current).toBe(0)
  })
})
