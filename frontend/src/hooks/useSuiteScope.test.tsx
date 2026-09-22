/**
 * VIZ-303: the page-local `selectedSuite` state becomes the global suite store
 * — but only with `viz_multi_filters` on.
 */
import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { usePageSuiteFilter, useSuiteScope } from './useSuiteScope'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { useSuiteStore } from '@/store/suiteStore'
import { settleScopeNow } from '@/store/settledScope'

describe('usePageSuiteFilter', () => {
  beforeEach(() => {
    localStorage.clear()
    useSuiteStore.setState({ activeSuiteNames: [], scopedProjectId: null })
    useMultiFiltersFlagStore.setState({ enabled: false })
    settleScopeNow()
  })

  it('flag off: page-local state — the suite does not follow to another page', () => {
    const trends = renderHook(() => usePageSuiteFilter())
    act(() => trends.result.current.setSelectedSuite('payments'))
    expect(trends.result.current.selectedSuite).toBe('payments')
    expect(trends.result.current.suiteFilter).toBe('payments')

    const coverage = renderHook(() => usePageSuiteFilter())
    expect(coverage.result.current.selectedSuite).toBe('')
    expect(coverage.result.current.suiteFilter).toBeNull()
    expect(useSuiteStore.getState().activeSuiteNames).toEqual([])
  })

  it('flag off: ignores whatever the global store holds', () => {
    useSuiteStore.setState({ activeSuiteNames: ['payments', 'cart'] })
    const { result } = renderHook(() => usePageSuiteFilter())
    expect(result.current.suiteFilter).toBeNull()
    expect(renderHook(() => useSuiteScope()).result.current).toBeNull()
  })

  it('flag on: the selection is global — set on one page, read on the next', () => {
    useMultiFiltersFlagStore.setState({ enabled: true })
    const trends = renderHook(() => usePageSuiteFilter())
    act(() => trends.result.current.setSelectedSuite('payments'))
    const coverage = renderHook(() => usePageSuiteFilter())
    // Two clocks: the control follows the click at once; the DATA filter
    // follows the settled scope (store/settledScope.ts).
    expect(coverage.result.current.selectedSuite).toBe('payments')
    expect(coverage.result.current.suiteFilter).toBeNull()
    act(() => settleScopeNow())
    expect(coverage.result.current.suiteFilter).toBe('payments')
  })

  it('flag on: several suites are a sorted list for data and "N suites" for the legacy dropdown', () => {
    useMultiFiltersFlagStore.setState({ enabled: true })
    useSuiteStore.setState({ activeSuiteNames: ['payments', 'cart'] })
    settleScopeNow()
    const { result, rerender } = renderHook(() => usePageSuiteFilter())
    expect(result.current.suiteFilter).toEqual(['cart', 'payments'])
    expect(result.current.selectedSuite).toBe('')
    expect(result.current.multiLabel).toBe('2 suites')
    expect(result.current.suiteLabel).toBe('cart, payments')
    const first = result.current.suiteFilter
    rerender()
    expect(result.current.suiteFilter).toBe(first)
  })

  it('flag on: clearing from the dropdown clears the global selection', () => {
    useMultiFiltersFlagStore.setState({ enabled: true })
    useSuiteStore.setState({ activeSuiteNames: ['payments', 'cart'] })
    const { result } = renderHook(() => usePageSuiteFilter())
    act(() => result.current.setSelectedSuite(''))
    expect(useSuiteStore.getState().activeSuiteNames).toEqual([])
  })
})
