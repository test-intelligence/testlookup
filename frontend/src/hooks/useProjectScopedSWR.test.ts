import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'

const mocked = vi.hoisted(() => ({
  useSWR: vi.fn(),
  activeProjectId: 'project-a' as string | null,
}))

vi.mock('swr', () => ({ default: mocked.useSWR }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (selector: (state: { activeProjectId: string | null }) => unknown) =>
    selector({ activeProjectId: mocked.activeProjectId }),
}))

import { useProjectScopedSWR } from './useProjectScopedSWR'

describe('useProjectScopedSWR project isolation', () => {
  beforeEach(() => {
    mocked.activeProjectId = 'project-a'
    mocked.useSWR.mockReset()
  })

  it('uses the project id in the cache key and changes it when project scope changes', () => {
    const fetcher = vi.fn()
    const { rerender } = renderHook(() => useProjectScopedSWR('/runs', fetcher, undefined, ['7d']))

    expect(mocked.useSWR).toHaveBeenLastCalledWith(
      ['/runs', 'project-a', '7d'],
      expect.any(Function),
      undefined,
    )

    mocked.activeProjectId = 'project-b'
    rerender()

    expect(mocked.useSWR).toHaveBeenLastCalledWith(
      ['/runs', 'project-b', '7d'],
      expect.any(Function),
      undefined,
    )
  })

  it('passes an unscoped fetch to all-projects mode while retaining that mode in the key', async () => {
    const fetcher = vi.fn().mockResolvedValue([])
    mocked.activeProjectId = 'all'
    renderHook(() => useProjectScopedSWR('/runs', fetcher))

    const lastCall = mocked.useSWR.mock.calls[mocked.useSWR.mock.calls.length - 1]
    const [, swrFetcher] = lastCall as [unknown, () => Promise<unknown>]
    expect(mocked.useSWR).toHaveBeenLastCalledWith(['/runs', 'all'], expect.any(Function), undefined)
    await swrFetcher()
    expect(fetcher).toHaveBeenCalledWith(null)
  })
})
