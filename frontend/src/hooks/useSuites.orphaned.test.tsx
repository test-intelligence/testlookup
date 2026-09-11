/**
 * N16: the orphaned-cases request must carry the page the panel is showing.
 *
 * Real hook, real suitesService; only the HTTP helper is faked, so the
 * params asserted here are the query string the backend receives. Before
 * this, the request carried no page/size at all and the backend's first 25
 * were the only rows the panel could ever show.
 */
import type { ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/services/http', () => ({
  getData: vi.fn(async () => ({ items: [], total: 0 })),
  postData: vi.fn(),
  patchData: vi.fn(),
  deleteData: vi.fn(),
}))
vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  const state = { activeProjectId: 'project-1' }
  return {
    ...actual,
    useProjectStore: (selector?: (s: typeof state) => unknown) => (selector ? selector(state) : state),
  }
})

import { getData } from '@/services/http'
import { useOrphanedCanonicalCases } from './useSuites'

const get = vi.mocked(getData)

function wrapper({ children }: { children: ReactNode }) {
  return <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
}

const orphanedCalls = () =>
  get.mock.calls.filter(([url]) => url === '/api/v1/canonical-test-cases/orphaned')
const paramsOf = (call: unknown[]) => (call[1] as { params: Record<string, unknown> }).params

describe('useOrphanedCanonicalCases', () => {
  beforeEach(() => get.mockClear())

  it('asks for the page and size it shows', async () => {
    renderHook(() => useOrphanedCanonicalCases(), { wrapper })
    await waitFor(() => expect(orphanedCalls()).toHaveLength(1))
    expect(paramsOf(orphanedCalls()[0])).toEqual({ project_id: 'project-1', page: 1, size: 25 })
  })

  it('fetches the next page when the page changes', async () => {
    const { rerender } = renderHook(({ page }) => useOrphanedCanonicalCases(page), {
      wrapper,
      initialProps: { page: 1 },
    })
    await waitFor(() => expect(orphanedCalls()).toHaveLength(1))

    rerender({ page: 2 })
    await waitFor(() => expect(orphanedCalls()).toHaveLength(2))
    expect(paramsOf(orphanedCalls()[1])).toEqual({ project_id: 'project-1', page: 2, size: 25 })
  })
})
