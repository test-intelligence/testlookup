/**
 * W24 ADM-05: the project-data hooks through the REAL SWR (the service is mocked).
 *
 * - `useProjectDataJob` polls while the job is queued / running and STOPS at a terminal status — with a NUMBER
 *   `refreshInterval` (SWR's function form never re-arms from 0: the W22b demo bug); never without a job id.
 * - `useProjectExports` / `useMyImports` read once, and not at all without a project / unless enabled.
 * JOB_POLL_MS is shortened to 40 ms here.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { jobFixture, jobList, PROJECT_DATA_PROJECT_ID } from '@/test/projectDataMocks'

vi.mock('@/types/projectData', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/types/projectData')>()
  return { ...actual, JOB_POLL_MS: 40 }
})
const service = vi.hoisted(() => ({ getJob: vi.fn(), listExports: vi.fn(), listMyImports: vi.fn() }))
vi.mock('@/services/projectDataService', () => ({ projectDataService: service }))

import {
  projectDataJobRefreshInterval,
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
})

describe('projectDataJobRefreshInterval', () => {
  it('is a number: the poll interval while active, 0 otherwise', () => {
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
