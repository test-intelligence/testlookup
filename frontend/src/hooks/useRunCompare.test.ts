/**
 * Tests for the useRunCompare / useLatestSuiteCompare hooks.
 *
 * Regression guard for the no-non-null-assertion ratchet: the
 * `useLatestSuiteCompare` fetcher used to call `compareLatestSuite(suiteName!.trim())`
 * behind a `Boolean(suiteName?.trim())` gate the type-checker could not relate
 * back to the nullable `suiteName`. The assertion is now an explicit
 * `if (!suiteName) throw` guard, so these verify the fetcher still trims the
 * suite name, skips the request entirely when no suite is resolved, and that the
 * sibling two-run `useRunCompare` keeps gating on both ids being present.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'
import type { RunCompareResponse } from '@/services/runCompareService'

vi.mock('@/services/runCompareService', () => ({
  runCompareService: {
    compare: vi.fn(),
    compareLatestSuite: vi.fn(),
  },
}))

// Each test needs its own SWR cache so a previous test's resolved value for a
// shared key does not bleed across cases.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

const RESPONSE = { truncated: false } as unknown as RunCompareResponse

describe('useLatestSuiteCompare', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches with the trimmed suite name when a suite is resolved', async () => {
    const { runCompareService } = await import('@/services/runCompareService')
    ;(runCompareService.compareLatestSuite as ReturnType<typeof vi.fn>).mockResolvedValue(RESPONSE)

    const { useLatestSuiteCompare } = await import('./useRunCompare')
    const { result } = renderHook(() => useLatestSuiteCompare('  api-suite  ', 'p1'), { wrapper })

    await waitFor(() => expect(result.current.compare).toEqual(RESPONSE))
    expect(runCompareService.compareLatestSuite).toHaveBeenCalledWith('api-suite', 'p1')
  })

  it('skips the fetch entirely when the suite name is blank', async () => {
    const { runCompareService } = await import('@/services/runCompareService')

    const { useLatestSuiteCompare } = await import('./useRunCompare')
    const { result } = renderHook(() => useLatestSuiteCompare('   '), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.compare).toBeUndefined()
    expect(runCompareService.compareLatestSuite).not.toHaveBeenCalled()
  })

  it('skips the fetch when no suite name is resolved', async () => {
    const { runCompareService } = await import('@/services/runCompareService')

    const { useLatestSuiteCompare } = await import('./useRunCompare')
    const { result } = renderHook(() => useLatestSuiteCompare(null), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(runCompareService.compareLatestSuite).not.toHaveBeenCalled()
  })
})

describe('useRunCompare', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches when both run ids are present and distinct', async () => {
    const { runCompareService } = await import('@/services/runCompareService')
    ;(runCompareService.compare as ReturnType<typeof vi.fn>).mockResolvedValue(RESPONSE)

    const { useRunCompare } = await import('./useRunCompare')
    const { result } = renderHook(() => useRunCompare('left', 'right', 'api-suite'), { wrapper })

    await waitFor(() => expect(result.current.compare).toEqual(RESPONSE))
    expect(runCompareService.compare).toHaveBeenCalledWith('left', 'right', 'api-suite')
  })

  it('skips the fetch when either run id is missing', async () => {
    const { runCompareService } = await import('@/services/runCompareService')

    const { useRunCompare } = await import('./useRunCompare')
    const { result } = renderHook(() => useRunCompare('left', null), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(runCompareService.compare).not.toHaveBeenCalled()
  })

  it('skips the fetch when both ids are identical', async () => {
    const { runCompareService } = await import('@/services/runCompareService')

    const { useRunCompare } = await import('./useRunCompare')
    const { result } = renderHook(() => useRunCompare('same', 'same'), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(runCompareService.compare).not.toHaveBeenCalled()
  })
})
