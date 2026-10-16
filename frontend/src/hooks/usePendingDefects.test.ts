/**
 * W06 DEF-06: the defect approval queue is asked for only when the caller
 * says so (a QA lead) and only for one project, with the exact request the
 * hermetic Inbox spec pins; its count is the server's `total`, not the page's
 * length.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// The shared Axios base: the real service and hook run on top of it.
const api = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/services/api', () => ({ api }))

import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { usePendingDefects } from './usePendingDefects'

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

const PENDING = {
  defect_id: 'd-1', project_id: 'p-1', title: 'Checkout fails', severity: 'CRITICAL', component: 'checkout',
  owner_team: 'payments', created_at: '2026-10-12T00:00:00Z', policy_reasons: ['CRITICAL severity'],
  jira_project_key: 'QA', run_id: 'r-1', cluster_id: 'c-1', ai_confidence_score: 91,
}

beforeEach(() => {
  api.get.mockReset()
  api.get.mockResolvedValue({ data: { items: [PENDING], total: 120, page: 1, page_size: 100 } })
})

describe('usePendingDefects', () => {
  it("asks for one project's first page of 100, and reports the server's total", async () => {
    const { result } = renderHook(() => usePendingDefects('p-1', true), { wrapper })
    await waitFor(() => expect(result.current.total).toBe(120))
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledWith('/api/v1/deep-investigate/defects/pending-review', {
      params: { project_id: 'p-1', page: 1, page_size: 100 },
    })
    // The params in this order make `?project_id=…&page=1&page_size=100`.
    expect(Object.keys(api.get.mock.calls[0][1].params)).toEqual(['project_id', 'page', 'page_size'])
    expect(result.current.items).toEqual([PENDING])
  })

  it('asks for nothing when not enabled (not a QA lead)', async () => {
    const { result } = renderHook(() => usePendingDefects('p-1', false), { wrapper })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(api.get).not.toHaveBeenCalled()
    expect(result.current.items).toEqual([])
    expect(result.current.total).toBeUndefined()
  })

  it.each([null, ALL_PROJECTS_ID])('asks for nothing without one project (%s)', async (projectId) => {
    renderHook(() => usePendingDefects(projectId, true), { wrapper })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(api.get).not.toHaveBeenCalled()
  })

  it('revalidates on mutate()', async () => {
    const { result } = renderHook(() => usePendingDefects('p-1', true), { wrapper })
    await waitFor(() => expect(result.current.total).toBe(120))
    api.get.mockResolvedValue({ data: { items: [], total: 0, page: 1, page_size: 100 } })
    await result.current.mutate()
    await waitFor(() => expect(result.current.total).toBe(0))
    expect(api.get).toHaveBeenCalledTimes(2)
  })
})
