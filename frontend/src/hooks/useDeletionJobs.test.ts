/**
 * W22b: the deletion-jobs list polls while something can still change on its own — every 5 s while a job is
 * running / queued, every 30 s while an archive awaits collection (so `expired` appears without a reload), and not
 * at all once every job has settled.
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DELETION_JOBS_ACTIVE_POLL_MS,
  DELETION_JOBS_AWAITING_POLL_MS,
  deletionJobsRefreshInterval,
  useDeletionJobs,
} from './useDeletionJobs'
import { awaitingJobRow, expiredJobRow, failedExportJobRow, jobList, jobRow, retainedJobRow } from '@/test/retentionExportMocks'

const swrSpy = vi.fn()
/** The SWR cache the hook reads its poll from, by serialized key. */
const cached = vi.hoisted(() => new Map<string, unknown>())
vi.mock('swr', () => ({
  default: (...args: unknown[]) => swrSpy(...args),
  unstable_serialize: (key: unknown) => (typeof key === 'string' ? key : JSON.stringify(key)),
  useSWRConfig: () => ({ cache: { get: (k: string) => (cached.has(k) ? { data: cached.get(k) } : undefined) } }),
}))
vi.mock('@/services/retentionService', () => ({ criteriaDeletionService: { jobs: vi.fn() } }))

beforeEach(() => {
  swrSpy.mockReset()
  swrSpy.mockReturnValue({ data: undefined, error: undefined, isLoading: true, mutate: vi.fn() })
})

describe('deletionJobsRefreshInterval', () => {
  it('polls every 5 s while a job is running or queued', () => {
    expect(DELETION_JOBS_ACTIVE_POLL_MS).toBe(5000)
    expect(deletionJobsRefreshInterval(jobList([jobRow({ status: 'running' })]))).toBe(5000)
    expect(deletionJobsRefreshInterval(jobList([jobRow(), jobRow({ status: 'queued' })]))).toBe(5000)
  })

  it('polls every 30 s while an archive awaits collection and nothing runs', () => {
    expect(DELETION_JOBS_AWAITING_POLL_MS).toBe(30000)
    expect(deletionJobsRefreshInterval(jobList([awaitingJobRow(), jobRow()]))).toBe(30000)
  })

  it('prefers the faster poll when a job runs beside one awaiting collection', () => {
    expect(deletionJobsRefreshInterval(jobList([awaitingJobRow(), jobRow({ status: 'running' })]))).toBe(5000)
  })

  it('stops once every job has settled (completed, partial, failed, expired, previewed) or there is no data', () => {
    const settled = [
      jobRow(),
      jobRow({ status: 'partial' }),
      failedExportJobRow(),
      expiredJobRow(),
      retainedJobRow(),
      jobRow({ status: 'previewed' }),
    ]
    expect(deletionJobsRefreshInterval(jobList(settled))).toBe(0)
    expect(deletionJobsRefreshInterval(jobList([]))).toBe(0)
    expect(deletionJobsRefreshInterval(undefined)).toBe(0)
  })
})

describe('useDeletionJobs', () => {
  it('keys the project’s jobs path and polls with a NUMBER from the cached list, never the function form', () => {
    // The function form never re-armed: a settled list (0) that gained a queued job sat on "Queued" in the W22b
    // local demo. New data re-renders the hook, the number changes, SWR re-arms its timer.
    const project = '00000000-0000-4000-8000-00000000e0a1'
    const key = `/api/v1/projects/${project}/deletion/jobs`
    const intervalFor = (list: ReturnType<typeof jobList> | undefined) => {
      cached.clear()
      if (list) cached.set(key, list)
      renderHook(() => useDeletionJobs(project))
      return (swrSpy.mock.lastCall as [unknown, unknown, Record<string, unknown>])[2].refreshInterval
    }
    expect(intervalFor(undefined)).toBe(0)
    expect(intervalFor(jobList([jobRow()]))).toBe(0)
    expect(intervalFor(jobList([jobRow({ status: 'queued' })]))).toBe(DELETION_JOBS_ACTIVE_POLL_MS)
    expect(intervalFor(jobList([awaitingJobRow()]))).toBe(DELETION_JOBS_AWAITING_POLL_MS)
    const [k, fetcher, options] = swrSpy.mock.lastCall as [unknown, unknown, Record<string, unknown>]
    expect(k).toBe(key)
    expect(typeof fetcher).toBe('function')
    expect(typeof options.refreshInterval).toBe('number')
    expect(options.revalidateOnFocus).toBe(false)
    cached.clear()
  })

  it('issues no request without a project or when disabled', () => {
    renderHook(() => useDeletionJobs(null))
    expect(swrSpy.mock.lastCall?.slice(0, 2)).toEqual([null, null])
    renderHook(() => useDeletionJobs('00000000-0000-4000-8000-00000000e0a1', false))
    expect(swrSpy.mock.lastCall?.slice(0, 2)).toEqual([null, null])
  })
})
