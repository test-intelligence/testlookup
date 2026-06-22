/**
 * Tests for useReassignOptions hook.
 *
 * Regression guard for the MyFailuresPage ReassignModal migration off a
 * load-on-mount `useEffect` (set-state-in-effect): verifies the SWR hook
 * fetches the reassignment picker payload keyed on the test-case id,
 * surfaces it declaratively, skips the request entirely for a null id, and
 * exposes the thrown error (the old `.catch` that drove the modal's
 * permission/empty message).
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/myFailuresService', () => ({
  myFailuresService: {
    getReassignOptions: vi.fn(),
  },
}))

// Each test gets its own SWR cache so a resolved/rejected value from a
// previous test doesn't bleed across the shared key.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

const OPTIONS = {
  project_id: 'p1',
  suite_name: 'checkout',
  suite_owner: { user_id: 'u-owner', email: 'o@x.io', username: 'owner', full_name: 'Owner' },
  qa_engineers: [
    { user_id: 'u-eng', email: 'e@x.io', username: 'eng', full_name: 'Eng' },
  ],
}

describe('useReassignOptions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches the reassignment options for a test case', async () => {
    const { myFailuresService } = await import('@/services/myFailuresService')
    ;(myFailuresService.getReassignOptions as ReturnType<typeof vi.fn>).mockResolvedValue(OPTIONS)

    const { useReassignOptions } = await import('./useMyFailures')
    const { result } = renderHook(() => useReassignOptions('tc-1'), { wrapper })

    await waitFor(() => expect(result.current.data).toEqual(OPTIONS))
    expect(myFailuresService.getReassignOptions).toHaveBeenCalledWith('tc-1')
    expect(result.current.error).toBeUndefined()
  })

  it('skips the request entirely when the id is null', async () => {
    const { myFailuresService } = await import('@/services/myFailuresService')

    const { useReassignOptions } = await import('./useMyFailures')
    const { result } = renderHook(() => useReassignOptions(null), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data).toBeUndefined()
    expect(myFailuresService.getReassignOptions).not.toHaveBeenCalled()
  })

  it('surfaces the thrown error when the load fails', async () => {
    const { myFailuresService } = await import('@/services/myFailuresService')
    const boom = { response: { data: { detail: 'Forbidden' } } }
    ;(myFailuresService.getReassignOptions as ReturnType<typeof vi.fn>).mockRejectedValue(boom)

    const { useReassignOptions } = await import('./useMyFailures')
    const { result } = renderHook(() => useReassignOptions('tc-1'), { wrapper })

    await waitFor(() => expect(result.current.error).toEqual(boom))
    expect(result.current.data).toBeUndefined()
  })
})
