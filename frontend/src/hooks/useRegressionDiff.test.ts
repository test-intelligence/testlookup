/**
 * W10 FAIL-09 (§0.26): the Changes tab's inline `useSWR` moved into this hook
 * with the SWR key BYTE-IDENTICAL (`regression-diff-${runId}`):
 * `RunDetailPage.tabs.test.tsx` answers that string and `fold-run.spec.ts`
 * counts the tab's reads.
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const swrSpy = vi.hoisted(() => vi.fn())
vi.mock('swr', () => ({ default: swrSpy }))
const getData = vi.fn()
vi.mock('@/services/http', () => ({ getData: (...a: unknown[]) => getData(...a) }))

import { useRegressionDiff } from './useRegressionDiff'

describe('useRegressionDiff', () => {
  beforeEach(() => {
    swrSpy.mockReset()
    getData.mockReset()
  })

  it('keys on `regression-diff-${runId}` exactly, without focus revalidation', () => {
    swrSpy.mockReturnValue({ data: undefined, isLoading: true, error: undefined })
    renderHook(() => useRegressionDiff('run-1'))
    expect(swrSpy).toHaveBeenCalledTimes(1)
    const [key, , opts] = swrSpy.mock.calls[0]
    expect(key).toBe('regression-diff-run-1')
    expect(opts).toEqual({ revalidateOnFocus: false })
  })

  it('fetches the run\'s regression diff and nothing else', async () => {
    swrSpy.mockReturnValue({ data: undefined, isLoading: true, error: undefined })
    getData.mockResolvedValue({ baseline_available: false })
    renderHook(() => useRegressionDiff('run-7'))
    const fetcher = swrSpy.mock.calls[0][1] as () => Promise<unknown>
    await expect(fetcher()).resolves.toEqual({ baseline_available: false })
    expect(getData).toHaveBeenCalledTimes(1)
    expect(getData).toHaveBeenCalledWith('/api/v1/runs/run-7/regression-diff')
  })

  it('returns data, isLoading and error as SWR gives them', () => {
    const error = new Error('boom')
    swrSpy.mockReturnValue({ data: { baseline_available: true }, isLoading: false, error })
    const { result } = renderHook(() => useRegressionDiff('run-1'))
    expect(result.current).toEqual({ data: { baseline_available: true }, isLoading: false, error })
  })
})
