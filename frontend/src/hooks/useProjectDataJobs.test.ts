/**
 * W24 ADM-05: the project-data hooks through the REAL SWR (the service is mocked).
 *
 * - `useProjectDataJob` polls while the job is queued / running and STOPS at a terminal status — with a NUMBER
 *   `refreshInterval` (SWR's function form never re-arms from 0: the W22b demo bug); never without a job id.
 * - `useProjectExports` / `useMyImports` read once, and not at all without a project / unless enabled.
 * - W30: a parted upload polls while `uploading`; `useExportEstimate` never fetches while unticked, re-reads on a
 *   window change and never polls.
 * JOB_POLL_MS is shortened to 40 ms here.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  estimateFixture,
  historyJobFixture,
  jobFixture,
  jobList,
  PROJECT_DATA_PROJECT_ID,
  RELEASE_ID,
  uploadFixture,
} from '@/test/projectDataMocks'

vi.mock('@/types/projectData', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/types/projectData')>()
  return { ...actual, JOB_POLL_MS: 40 }
})
const service = vi.hoisted(() => ({
  getJob: vi.fn(), listExports: vi.fn(), listMyImports: vi.fn(), getExportEstimate: vi.fn(),
}))
vi.mock('@/services/projectDataService', () => ({ projectDataService: service }))

import {
  exportEstimateKey,
  projectDataJobRefreshInterval,
  useExportEstimate,
  useMyImports,
  useProjectDataJob,
  useProjectExports,
} from './useProjectDataJobs'

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)
}

beforeEach(() => {
  service.getJob.mockReset()
  service.listExports.mockReset()
  service.listMyImports.mockReset()
  service.getExportEstimate.mockReset()
})

describe('projectDataJobRefreshInterval', () => {
  it('is a number: the poll interval while active, 0 otherwise', () => {
    expect(projectDataJobRefreshInterval(jobFixture({ kind: 'import', status: 'uploading' }))).toBe(40)
    expect(projectDataJobRefreshInterval(jobFixture({ status: 'queued' }))).toBe(40)
    expect(projectDataJobRefreshInterval(jobFixture({ status: 'running' }))).toBe(40)
    for (const status of ['succeeded', 'failed', 'expired'] as const) {
      expect(projectDataJobRefreshInterval(jobFixture({ status }))).toBe(0)
    }
    expect(projectDataJobRefreshInterval(undefined)).toBe(0)
  })
})

describe('useProjectDataJob (real SWR)', () => {
  it('polls while the job runs and stops once it has finished', async () => {
    const id = '00000000-0000-4000-8000-00000000d1a1'
    const answers = [
      jobFixture({ id, status: 'queued' }),
      jobFixture({ id, status: 'running' }),
      jobFixture({ id, status: 'running', progress: { done: 29, total: 30 } }),
      jobFixture({ id, status: 'succeeded' }),
    ]
    service.getJob.mockImplementation(async () => answers.shift() ?? jobFixture({ id, status: 'succeeded' }))

    const { result } = renderHook(() => useProjectDataJob(id), { wrapper })
    await waitFor(() => expect(result.current.data?.status).toBe('succeeded'), { timeout: 3000 })
    expect(service.getJob).toHaveBeenCalledWith(id)
    const calls = service.getJob.mock.calls.length
    expect(calls).toBeGreaterThanOrEqual(4)

    await pause(250)                                  // > 6 poll intervals: nothing more is read
    expect(service.getJob.mock.calls.length).toBe(calls)
  })

  it('reads a terminal job once and never polls it', async () => {
    service.getJob.mockResolvedValue(jobFixture({ status: 'failed' }))
    const { result } = renderHook(() => useProjectDataJob('00000000-0000-4000-8000-00000000d1a2'), { wrapper })
    await waitFor(() => expect(result.current.data?.status).toBe('failed'))
    await pause(200)
    expect(service.getJob).toHaveBeenCalledTimes(1)
  })

  it('polls a parted upload while it is uploading, through queued, until it has finished (W30)', async () => {
    const id = '00000000-0000-4000-8000-00000000d1a3'
    const answers = [
      historyJobFixture({ id, kind: 'import', status: 'uploading', upload: uploadFixture({ received: [1] }) }),
      historyJobFixture({ id, kind: 'import', status: 'uploading', upload: uploadFixture({ received: [1, 2] }) }),
      historyJobFixture({ id, kind: 'import', status: 'queued' }),
      historyJobFixture({ id, kind: 'import', status: 'running', phase: 'documents' }),
      historyJobFixture({ id, kind: 'import', status: 'succeeded' }),
    ]
    service.getJob.mockImplementation(async () => answers.shift() ?? historyJobFixture({ id, kind: 'import', status: 'succeeded' }))

    const { result } = renderHook(() => useProjectDataJob(id), { wrapper })
    await waitFor(() => expect(result.current.data?.status).toBe('succeeded'), { timeout: 3000 })
    const calls = service.getJob.mock.calls.length
    expect(calls).toBeGreaterThanOrEqual(5)
    await pause(250)
    expect(service.getJob.mock.calls.length).toBe(calls)
  })

  it('never reads without a job id', async () => {
    const { result } = renderHook(() => useProjectDataJob(null), { wrapper })
    await pause(120)
    expect(service.getJob).not.toHaveBeenCalled()
    expect(result.current.data).toBeUndefined()
  })
})

describe('the list hooks', () => {
  it('read the project exports once, newest one by default', async () => {
    service.listExports.mockResolvedValue(jobList([jobFixture({ status: 'running' })], 4))
    const { result } = renderHook(() => useProjectExports(PROJECT_DATA_PROJECT_ID), { wrapper })
    await waitFor(() => expect(result.current.data?.total).toBe(4))
    expect(service.listExports).toHaveBeenCalledWith(PROJECT_DATA_PROJECT_ID, 1)
    await pause(150)
    expect(service.listExports).toHaveBeenCalledTimes(1)   // the job hook polls a live job, not the list
  })

  it('read nothing without a project, or for imports unless enabled', async () => {
    service.listMyImports.mockResolvedValue(jobList([]))
    renderHook(() => useProjectExports(null), { wrapper })
    renderHook(() => useMyImports(false), { wrapper })
    await pause(100)
    expect(service.listExports).not.toHaveBeenCalled()
    expect(service.listMyImports).not.toHaveBeenCalled()

    const { result } = renderHook(() => useMyImports(true), { wrapper })
    await waitFor(() => expect(result.current.data).toEqual({ items: [], total: 0 }))
    expect(service.listMyImports).toHaveBeenCalledWith(1)
  })
})

describe('useExportEstimate (W30)', () => {
  it('never fetches while "Include run history" is unticked, or without a project', async () => {
    service.getExportEstimate.mockResolvedValue(estimateFixture())
    renderHook(() => useExportEstimate(PROJECT_DATA_PROJECT_ID, { historyDays: 90 }, false), { wrapper })
    renderHook(() => useExportEstimate(null, { historyDays: 90 }, true), { wrapper })
    await pause(120)
    expect(service.getExportEstimate).not.toHaveBeenCalled()
  })

  it('reads once when enabled, again when the window changes, and never polls', async () => {
    service.getExportEstimate.mockImplementation(async (_p: string, o: { historyDays: number | null }) =>
      estimateFixture({ history_days: o.historyDays }),
    )
    const { result, rerender } = renderHook(
      ({ days }: { days: number | null }) => useExportEstimate(PROJECT_DATA_PROJECT_ID, { historyDays: days }, true),
      { wrapper, initialProps: { days: 90 as number | null } },
    )
    await waitFor(() => expect(result.current.data?.history_days).toBe(90))
    expect(service.getExportEstimate).toHaveBeenLastCalledWith(PROJECT_DATA_PROJECT_ID, {
      includeHistory: true, historyDays: 90,
    })
    rerender({ days: null })
    await waitFor(() => expect(result.current.data?.history_days).toBeNull())
    expect(service.getExportEstimate).toHaveBeenLastCalledWith(PROJECT_DATA_PROJECT_ID, {
      includeHistory: true, historyDays: null,
    })
    await pause(150)
    expect(service.getExportEstimate).toHaveBeenCalledTimes(2)
    expect(exportEstimateKey('p', 365)).not.toBe(exportEstimateKey('p', null))
  })

  it('W37: a release window reads the release (its own key, one read per release); a day window\'s call stays W30\'s', async () => {
    service.getExportEstimate.mockImplementation(async (_p: string, o: { historyReleaseId?: string }) =>
      estimateFixture({ history_days: null, history_release_id: o.historyReleaseId ?? null }),
    )
    const { result, rerender } = renderHook(
      ({ release }: { release: string }) => useExportEstimate(PROJECT_DATA_PROJECT_ID, { historyDays: null, historyReleaseId: release }, true),
      { wrapper, initialProps: { release: RELEASE_ID } },
    )
    await waitFor(() => expect(result.current.data?.history_release_id).toBe(RELEASE_ID))
    expect(service.getExportEstimate.mock.lastCall).toStrictEqual([
      PROJECT_DATA_PROJECT_ID, { includeHistory: true, historyDays: null, historyReleaseId: RELEASE_ID },
    ])
    rerender({ release: 'rel-other' })
    await waitFor(() => expect(result.current.data?.history_release_id).toBe('rel-other'))
    expect(service.getExportEstimate).toHaveBeenCalledTimes(2)
    expect(exportEstimateKey('p', null, RELEASE_ID)).not.toBe(exportEstimateKey('p', null))
    expect(exportEstimateKey('p', 90)).toBe('project-data/estimate/p?history_days=90')

    service.getExportEstimate.mockClear()
    renderHook(() => useExportEstimate(PROJECT_DATA_PROJECT_ID, { historyDays: 30 }, true), { wrapper })
    await waitFor(() => expect(service.getExportEstimate).toHaveBeenCalledTimes(1))
    expect(service.getExportEstimate.mock.lastCall, 'no historyReleaseId key for a day window').toStrictEqual([
      PROJECT_DATA_PROJECT_ID, { includeHistory: true, historyDays: 30 },
    ])
  })
})
