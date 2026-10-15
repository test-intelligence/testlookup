/**
 * W06 DEF-06 §2.6-9: the Approvals counts gain the defects queue for a QA
 * lead only. Anyone else never asks for it (the list is QA_LEAD-only: asking
 * would 403-toast every viewer's Inbox), counts it as 0, and their total does
 * not wait on it.
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  isQaLead: true,
  pending: { total: 3 as number | undefined, error: undefined as unknown, isLoading: false },
}))

vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isQaLead: state.isQaLead }) }))
const usePendingDefects = vi.hoisted(() => vi.fn())
vi.mock('@/hooks/usePendingDefects', () => ({ usePendingDefects }))
vi.mock('@/hooks/useReviews', () => ({
  useProjectReviews: () => ({ reviews: [{ id: 'r1' }, { id: 'r2' }], isLoading: false, isError: false }),
}))
vi.mock('@/hooks/useFlakyQuarantine', () => ({
  useQuarantineList: () => ({ requests: [{ id: 'q1', status: 'PROPOSED' }], isLoading: false, isError: false }),
}))
vi.mock('@/hooks/useTestManagement', () => ({
  useTestCases: (params: { status: string }) => ({
    data: { items: [{ id: params.status === 'review_requested' ? 'c1' : 'c2' }] },
    isLoading: false,
    error: undefined,
  }),
}))

import { useApprovalCounts } from './approvalQueues'

beforeEach(() => {
  state.isQaLead = true
  state.pending = { total: 3, error: undefined, isLoading: false }
  usePendingDefects.mockReset()
  usePendingDefects.mockImplementation(() => ({ items: [], mutate: vi.fn(), ...state.pending }))
})

describe('useApprovalCounts — the defects queue', () => {
  it("counts a QA lead's pending defects (the server's total) in the total", () => {
    const { result } = renderHook(() => useApprovalCounts('p-1'))
    expect(usePendingDefects).toHaveBeenCalledWith('p-1', true)
    expect(result.current).toEqual({ reports: 2, quarantine: 1, cases: 2, defects: 3, total: 8 })
  })

  it('never asks for the queue for anyone else, counts 0, and the total does not wait on it', () => {
    state.isQaLead = false
    state.pending = { total: undefined, error: undefined, isLoading: false }
    const { result } = renderHook(() => useApprovalCounts('p-1'))
    expect(usePendingDefects).toHaveBeenCalledWith('p-1', false)
    expect(usePendingDefects).not.toHaveBeenCalledWith('p-1', true)
    expect(result.current).toEqual({ reports: 2, quarantine: 1, cases: 2, defects: 0, total: 5 })
  })

  it("leaves a QA lead's total undefined until the defects queue has answered", () => {
    state.pending = { total: undefined, error: undefined, isLoading: true }
    const { result } = renderHook(() => useApprovalCounts('p-1'))
    expect(result.current.defects).toBeUndefined()
    expect(result.current.total).toBeUndefined()
  })

  it('reads a failed defects queue as unknown, not 0', () => {
    state.pending = { total: undefined, error: new Error('500'), isLoading: false }
    const { result } = renderHook(() => useApprovalCounts('p-1'))
    expect(result.current.defects).toBeUndefined()
    expect(result.current.total).toBeUndefined()
  })
})
