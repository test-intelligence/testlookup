/**
 * The Runs list's batch reads (FAIL-04 / FAIL-03 / FAIL-05): ONE request for
 * the verdicts of the runs on screen, ONE for the failure signatures of up to
 * 50 failed builds, and the branch list only with one project selected.
 * (The per-run `/intelligence` fetches, their throttle and their hook are
 * gone: `aiVerdict.test.ts` pins that they no longer exist.)
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { RunSignatureBatch, RunVerdictBatch, TestRun } from '@/types/runs'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'

const svc = vi.hoisted(() => ({
  getAiVerdicts: vi.fn(),
  getFailureSignatures: vi.fn(),
  getBranches: vi.fn(),
  searchRuns: vi.fn(),
  getRun: vi.fn(),
}))
vi.mock('@/services/runListService', () => ({ runListService: svc }))

import { MAX_VERDICT_RUN_IDS, useRunAiVerdicts, useRunBranches, useRunSignatures, verdictRunIds } from './useRunListInsights'

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

function run(i: number, status = 'FAILED'): TestRun {
  return {
    id: `run-${String(i).padStart(2, '0')}`, build_number: i, status,
    passed_tests: 1, failed_tests: 1, skipped_tests: 0, total_tests: 2, pass_rate: 50,
    created_at: `2026-10-${String(1 + (i % 28)).padStart(2, '0')}T00:00:00Z`,
  }
}

const batch = (ids: string[]): RunVerdictBatch => ({
  items: ids.map((id) => ({
    run_id: id, snapshot: true, stale: false, generated_at: null, analysed: true,
    verdict_inputs: { release_decision: { recommendation: 'GO' }, structured_summary: null, intelligence_available: true, top_analyses: null },
    review: { state: 'accepted' },
  })),
  unavailable: [],
  max_ids: 25,
})

beforeEach(() => {
  for (const fn of Object.values(svc)) fn.mockReset()
  useProjectStore.setState({ activeProjectId: 'p1' })
})
afterEach(() => {
  useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID })
})

describe('useRunAiVerdicts — ONE request for the runs on screen', () => {
  it('asks once for 25 runs, the ids sorted, and answers each run from that one batch', async () => {
    const runs = Array.from({ length: 25 }, (_, i) => run(25 - i))  // newest first, as the table lists them
    svc.getAiVerdicts.mockImplementation(async (ids: string[]) => batch(ids))
    const { result } = renderHook(() => useRunAiVerdicts(runs), { wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(svc.getAiVerdicts).toHaveBeenCalledTimes(1)
    const asked = svc.getAiVerdicts.mock.calls[0][0] as string[]
    expect(asked).toHaveLength(25)
    expect(asked).toEqual([...asked].sort())
    for (const r of runs) expect(result.current.verdictOf(r)).toEqual({ recommendation: 'GO', review: null })
  })

  it('never asks for a run in flight (it reads "—", in progress), and asks nothing for a page of them', async () => {
    const page = [run(1, 'IN_PROGRESS'), run(2, 'RUNNING')]
    const { result } = renderHook(() => useRunAiVerdicts(page), { wrapper })
    expect(result.current.isLoading).toBe(false)
    expect(result.current.verdictOf(page[0])).toEqual({ recommendation: null, reason: 'in-progress' })
    await new Promise((r) => setTimeout(r, 0))
    expect(svc.getAiVerdicts).not.toHaveBeenCalled()

    expect(verdictRunIds([run(3), run(4, 'IN_PROGRESS'), run(1)])).toEqual(['run-01', 'run-03'])
  })

  it('caps the request at the route\'s 25 ids', () => {
    expect(MAX_VERDICT_RUN_IDS).toBe(25)
    expect(verdictRunIds(Array.from({ length: 30 }, (_, i) => run(i)))).toHaveLength(25)
  })

  it('an id the batch lists as unavailable and a failed request both read "unavailable", with different titles', async () => {
    svc.getAiVerdicts.mockResolvedValueOnce({ items: [], unavailable: ['run-01'], max_ids: 25 })
    const { result } = renderHook(() => useRunAiVerdicts([run(1)]), { wrapper })
    await waitFor(() => expect(result.current.verdictOf(run(1))).toMatchObject({ reason: 'unavailable', title: 'AI verdict unavailable: the run is not available to you' }))

    svc.getAiVerdicts.mockRejectedValueOnce(new Error('503'))
    const failing = renderHook(() => useRunAiVerdicts([run(2)]), { wrapper })
    await waitFor(() => expect(failing.result.current.verdictOf(run(2))).toEqual({ recommendation: null, reason: 'unavailable' }))
  })

  it('loading: no verdict yet (the cell draws its skeleton)', () => {
    svc.getAiVerdicts.mockReturnValue(new Promise(() => undefined))
    const { result } = renderHook(() => useRunAiVerdicts([run(1)]), { wrapper })
    expect(result.current.isLoading).toBe(true)
    expect(result.current.verdictOf(run(1))).toBeUndefined()
  })
})

describe('useRunSignatures — ONE request per failed-build id set', () => {
  it('asks once with the ids sorted; the same set in another order is the same request', async () => {
    const answer: RunSignatureBatch = { items: [], unavailable: [], max_ids: 50, rows_per_run_cap: 500 }
    svc.getFailureSignatures.mockResolvedValue(answer)
    const { result, rerender } = renderHook(({ ids }) => useRunSignatures(ids), {
      wrapper, initialProps: { ids: ['b', 'a', 'c'] },
    })
    await waitFor(() => expect(result.current.data).toEqual(answer))
    rerender({ ids: ['c', 'b', 'a'] })
    await new Promise((r) => setTimeout(r, 0))
    expect(svc.getFailureSignatures).toHaveBeenCalledTimes(1)
    expect(svc.getFailureSignatures).toHaveBeenCalledWith(['a', 'b', 'c'])
  })

  it('asks nothing for no failed builds', async () => {
    const { result } = renderHook(() => useRunSignatures([]), { wrapper })
    await new Promise((r) => setTimeout(r, 0))
    expect(result.current.isLoading).toBe(false)
    expect(svc.getFailureSignatures).not.toHaveBeenCalled()
  })
})

describe('useRunBranches — the active project\'s branches, never in All Projects', () => {
  it('asks for the project and the window', async () => {
    svc.getBranches.mockResolvedValue({ items: [{ branch: 'main', runs: 3, last_run_at: null }], total: 1, truncated: false, window_days: 14 })
    const { result } = renderHook(() => useRunBranches(14), { wrapper })
    await waitFor(() => expect(result.current.data?.items[0].branch).toBe('main'))
    expect(svc.getBranches).toHaveBeenCalledWith('p1', 14)
  })

  it('makes no request in All Projects', async () => {
    useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID })
    const { result } = renderHook(() => useRunBranches(30), { wrapper })
    await new Promise((r) => setTimeout(r, 0))
    expect(result.current.data).toBeUndefined()
    expect(svc.getBranches).not.toHaveBeenCalled()
  })
})
