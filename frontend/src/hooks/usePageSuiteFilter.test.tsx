/**
 * The page-local suite filter (VIZ-303's flag-off behaviour, the only one
 * since Phase D removed the multi-filter mode).
 */
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { usePageSuiteFilter } from './usePageSuiteFilter'

describe('usePageSuiteFilter', () => {
  it('starts empty: no suite, no filter', () => {
    const { result } = renderHook(() => usePageSuiteFilter())
    expect(result.current.selectedSuite).toBe('')
    expect(result.current.suiteFilter).toBeNull()
    expect(result.current.suiteNames).toEqual([])
    expect(result.current.suiteLabel).toBe('')
  })

  it('is page-local: the suite does not follow to another page', () => {
    const trends = renderHook(() => usePageSuiteFilter())
    act(() => trends.result.current.setSelectedSuite('payments'))
    expect(trends.result.current.selectedSuite).toBe('payments')
    expect(trends.result.current.suiteFilter).toBe('payments')
    expect(trends.result.current.suiteNames).toEqual(['payments'])
    expect(trends.result.current.suiteLabel).toBe('payments')

    const coverage = renderHook(() => usePageSuiteFilter())
    expect(coverage.result.current.selectedSuite).toBe('')
    expect(coverage.result.current.suiteFilter).toBeNull()
  })

  it('clears with an empty value', () => {
    const { result } = renderHook(() => usePageSuiteFilter())
    act(() => result.current.setSelectedSuite('payments'))
    act(() => result.current.setSelectedSuite(''))
    expect(result.current.suiteFilter).toBeNull()
    expect(result.current.suiteNames).toEqual([])
  })

  it('keeps one identity until the suite changes (a dep-list safe value)', () => {
    const { result, rerender } = renderHook(() => usePageSuiteFilter())
    const first = result.current
    rerender()
    expect(result.current).toBe(first)
  })
})
