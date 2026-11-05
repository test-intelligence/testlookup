/**
 * W31 STRAT-12: the subject-rights hooks through the REAL SWR (the service is mocked).
 *
 * - `useSubjectJob` polls while the job is queued / running and STOPS at a terminal status or an erasure's
 *   `phase === 'done'` — with a NUMBER `refreshInterval` (SWR's function form never re-arms from 0: the W22b demo
 *   bug); never without a job id.
 * - `useMyExports` / `useUserJobs` read once, and `useUserJobs` not at all without a user id.
 * - `useErasurePreview` never fetches unless enabled, never polls, and re-reads on `mutate()`.
 * JOB_POLL_MS is shortened to 40 ms here.
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  erasurePreviewFixture,
  incompleteErasureFixture,
  SUBJECT_USER_ID,
  subjectJobFixture,
  subjectJobList,
} from '@/test/subjectRightsMocks'

vi.mock('@/types/subjectRights', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/types/subjectRights')>()
  return { ...actual, JOB_POLL_MS: 40 }
})
const service = vi.hoisted(() => ({
  getJob: vi.fn(), listMyExports: vi.fn(), listUserJobs: vi.fn(), getErasurePreview: vi.fn(),
}))
vi.mock('@/services/subjectRightsService', () => ({ subjectRightsService: service }))

import {
  subjectJobRefreshInterval,
  useErasurePreview,
  useMyExports,
  useSubjectJob,
  useUserJobs,
} from './useSubjectRights'

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function wrapper({ children }: { children: ReactNode }) {
  return <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
}

beforeEach(() => {
  service.getJob.mockReset()
  service.listMyExports.mockReset()
  service.listUserJobs.mockReset()
  service.getErasurePreview.mockReset()
})

describe('subjectJobRefreshInterval', () => {
  it('is a number: the poll interval while active, 0 at a terminal status or an erasure at done', () => {
    expect(subjectJobRefreshInterval(subjectJobFixture({ status: 'queued' }))).toBe(40)
    expect(subjectJobRefreshInterval(subjectJobFixture({ status: 'running' }))).toBe(40)
    expect(subjectJobRefreshInterval(subjectJobFixture({ kind: 'erasure', status: 'queued' }))).toBe(40)
    expect(subjectJobRefreshInterval(incompleteErasureFixture('objects'))).toBe(40)
    for (const status of ['succeeded', 'failed', 'expired'] as const) {
      expect(subjectJobRefreshInterval(subjectJobFixture({ status }))).toBe(0)
    }
    // the phase line can reach `done` a beat before the status flips: polling stops either way
    expect(subjectJobRefreshInterval(subjectJobFixture({ kind: 'erasure', status: 'running', phase: 'done' }))).toBe(0)
    expect(subjectJobRefreshInterval(undefined)).toBe(0)
    expect(subjectJobRefreshInterval(null)).toBe(0)
  })
})

describe('useSubjectJob (real SWR)', () => {
  it('polls an export while it runs and stops once it has finished', async () => {
    const id = '00000000-0000-4000-8000-0000000051a1'
    const answers = [
      subjectJobFixture({ id, status: 'queued' }),
      subjectJobFixture({ id, status: 'running' }),
      subjectJobFixture({ id, status: 'running' }),
      subjectJobFixture({ id, status: 'succeeded' }),
    ]
    service.getJob.mockImplementation(async () => answers.shift() ?? subjectJobFixture({ id, status: 'succeeded' }))

    const { result } = renderHook(() => useSubjectJob(id), { wrapper })
    await waitFor(() => expect(result.current.data?.status).toBe('succeeded'), { timeout: 3000 })
    expect(service.getJob).toHaveBeenCalledWith(id)
    const calls = service.getJob.mock.calls.length
    expect(calls).toBeGreaterThanOrEqual(4)
    await pause(250)                                  // > 6 poll intervals: nothing more is read
    expect(service.getJob.mock.calls.length).toBe(calls)
  })

  it('polls an erasure through its phases and stops at done', async () => {
    const id = '00000000-0000-4000-8000-0000000051a2'
    const answers = [
      subjectJobFixture({ id, kind: 'erasure', status: 'queued', phase: 'lockout' }),
      subjectJobFixture({ id, kind: 'erasure', status: 'running', phase: 'postgres' }),
      subjectJobFixture({ id, kind: 'erasure', status: 'running', phase: 'objects' }),
      subjectJobFixture({ id, kind: 'erasure', status: 'running', phase: 'done' }),
    ]
    service.getJob.mockImplementation(async () => answers.shift() ?? subjectJobFixture({ id, kind: 'erasure' }))

    const { result } = renderHook(() => useSubjectJob(id), { wrapper })
    await waitFor(() => expect(result.current.data?.phase).toBe('done'), { timeout: 3000 })
    const calls = service.getJob.mock.calls.length
    expect(calls).toBe(4)
    await pause(250)
    expect(service.getJob.mock.calls.length).toBe(calls)
  })

  it('reads a terminal job once and never polls it', async () => {
    service.getJob.mockResolvedValue(subjectJobFixture({ status: 'failed' }))
    const { result } = renderHook(() => useSubjectJob('00000000-0000-4000-8000-0000000051a3'), { wrapper })
    await waitFor(() => expect(result.current.data?.status).toBe('failed'))
    await pause(200)
    expect(service.getJob).toHaveBeenCalledTimes(1)
  })

  it('never reads without a job id', async () => {
    const { result } = renderHook(() => useSubjectJob(null), { wrapper })
    await pause(120)
    expect(service.getJob).not.toHaveBeenCalled()
    expect(result.current.data).toBeUndefined()
  })
})

describe('the list hooks', () => {
  it('read my exports once', async () => {
    service.listMyExports.mockResolvedValue(subjectJobList([subjectJobFixture({ status: 'running' })], 3))
    const { result } = renderHook(() => useMyExports(), { wrapper })
    await waitFor(() => expect(result.current.data?.total).toBe(3))
    await pause(150)
    expect(service.listMyExports).toHaveBeenCalledTimes(1)
  })

  it("read a person's jobs once, and nothing without a user id", async () => {
    service.listUserJobs.mockResolvedValue(subjectJobList([], 0))
    const { result } = renderHook(() => useUserJobs(SUBJECT_USER_ID), { wrapper })
    await waitFor(() => expect(result.current.data?.total).toBe(0))
    expect(service.listUserJobs).toHaveBeenCalledWith(SUBJECT_USER_ID)
    renderHook(() => useUserJobs(undefined), { wrapper })
    await pause(120)
    expect(service.listUserJobs).toHaveBeenCalledTimes(1)
  })
})

describe('useErasurePreview', () => {
  it('never fetches unless enabled', async () => {
    renderHook(() => useErasurePreview(SUBJECT_USER_ID, false), { wrapper })
    renderHook(() => useErasurePreview(null, true), { wrapper })
    await pause(120)
    expect(service.getErasurePreview).not.toHaveBeenCalled()
  })

  it('reads once, never polls, and re-reads on demand', async () => {
    service.getErasurePreview.mockResolvedValue(erasurePreviewFixture())
    const { result } = renderHook(() => useErasurePreview(SUBJECT_USER_ID, true), { wrapper })
    await waitFor(() => expect(result.current.data?.pseudonym).toBeTruthy())
    expect(service.getErasurePreview).toHaveBeenCalledWith(SUBJECT_USER_ID)
    await pause(150)
    expect(service.getErasurePreview).toHaveBeenCalledTimes(1)
    await act(async () => {
      await result.current.mutate()
    })
    expect(service.getErasurePreview).toHaveBeenCalledTimes(2)
  })
})
