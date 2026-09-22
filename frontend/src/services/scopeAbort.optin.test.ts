/**
 * Superseded-scope aborting is OPT-IN (E3 review, proof C; security N4).
 *
 * It used to track every GET whose params happened to equal the settled
 * scope — which included a user-initiated PDF export (same `release_id` as
 * the page), so moving the picker mid-download cancelled the download and
 * toasted. Only requests an SWR data fetcher of a scoped hook marks (through
 * `scopedFetch`) are tracked now, and never a blob, a non-GET, or a request
 * that brought its own signal. A tracked request retried after a 401 refresh
 * stays tracked. Turning tracking off (flag off, or a flag flicker) never
 * aborts anything.
 */
import { CanceledError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))

import { api } from '@/services/api'
import { getData } from '@/services/http'
import {
  isScopeSuperseded,
  scopedFetch,
  setSettledScopeFingerprint,
  trackScopeRequest,
  trackedScopeRequestCount,
} from '@/services/scopeAbort'
import { summaryReportQueryParams } from '@/services/summaryReportService'
import { useAuthStore } from '@/store/authStore'

const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'

function config(extra: Partial<InternalAxiosRequestConfig> = {}): InternalAxiosRequestConfig {
  return {
    method: 'get',
    url: '/api/v1/metrics/summary',
    params: { release_id: R1 },
    headers: {} as never,
    ...extra,
  } as InternalAxiosRequestConfig
}

beforeEach(() => {
  setSettledScopeFingerprint({ release: R1, suite: null })
})
afterEach(() => {
  setSettledScopeFingerprint(null)
})

describe('scopeAbort is opt-in', () => {
  it('a summary PDF download is never tracked, even with the settled scope in its params (proof C)', () => {
    const pdf = config({
      url: '/api/v1/reports/summary/pdf',
      responseType: 'blob',
      params: summaryReportQueryParams({ project_id: 'p', days: 7, mode: 'summary' as never, release_id: R1 }),
    })
    const out = trackScopeRequest(pdf)
    expect(out.signal).toBeUndefined()
    // Even if someone marked it.
    const marked = trackScopeRequest(config({ responseType: 'blob', scopeTracked: true }))
    expect(marked.signal).toBeUndefined()
  })

  it('an UNMARKED GET whose params equal the settled scope is not tracked', () => {
    expect(trackScopeRequest(config()).signal).toBeUndefined()
  })

  it('a MARKED GET is tracked and aborted when the scope moves on', () => {
    const cfg = trackScopeRequest(config({ scopeTracked: true }))
    expect(cfg.signal).toBeDefined()
    setSettledScopeFingerprint({ release: R2, suite: null })
    expect((cfg.signal as AbortSignal).aborted).toBe(true)
  })

  it('turning tracking off (flag off / flicker) aborts NOTHING in flight (proof C, part 2)', () => {
    const cfg = trackScopeRequest(config({ scopeTracked: true }))
    setSettledScopeFingerprint(null)
    expect((cfg.signal as AbortSignal).aborted).toBe(false)
    expect(trackedScopeRequestCount()).toBe(0)
  })

  it('never a non-GET, never a caller-owned signal', () => {
    expect(trackScopeRequest(config({ method: 'post', scopeTracked: true })).signal).toBeUndefined()
    const own = new AbortController().signal
    expect(trackScopeRequest(config({ scopeTracked: true, signal: own })).signal).toBe(own)
  })

  it('scopedFetch marks exactly the GETs issued synchronously inside it', () => {
    const seen: Array<boolean | undefined> = []
    const originalAdapter = api.defaults.adapter
    api.defaults.adapter = (async (c: InternalAxiosRequestConfig) => {
      seen.push(c.scopeTracked)
      return { data: 'ok', status: 200, statusText: 'OK', headers: {}, config: c }
    }) as AxiosAdapter
    return (async () => {
      try {
        await scopedFetch(() => getData('/api/v1/metrics/summary', { params: { release_id: R1 } }))
        await getData('/api/v1/metrics/summary', { params: { release_id: R1 } })
        expect(seen).toEqual([true, undefined])
      } finally {
        api.defaults.adapter = originalAdapter
      }
    })()
  })
})

describe('a tracked request retried after a 401 refresh stays tracked (N4)', () => {
  const originalAdapter = api.defaults.adapter
  afterEach(() => {
    api.defaults.adapter = originalAdapter
    vi.restoreAllMocks()
  })

  it('the retry is aborted when the scope moves during it, and the rejection is marked superseded', async () => {
    const realRefresh = useAuthStore.getState().refreshAccessToken
    onTestFinished(() => { useAuthStore.setState({ refreshAccessToken: realRefresh }) })
    useAuthStore.setState({ token: 'old', refreshToken: 'refresh', isAuthenticated: true })
    useAuthStore.setState({ refreshAccessToken: vi.fn(async () => 'new-token') })

    let calls = 0
    let retryConfig: InternalAxiosRequestConfig | null = null
    api.defaults.adapter = ((c: InternalAxiosRequestConfig) => {
      calls += 1
      if (calls === 1) {
        const err = Object.assign(new Error('401'), {
          isAxiosError: true,
          config: c,
          response: { status: 401, data: {}, headers: {}, config: c, statusText: 'Unauthorized' },
        })
        return Promise.reject(err)
      }
      retryConfig = c
      // The retry hangs until its signal fires.
      return new Promise((_resolve, reject) => {
        ;(c.signal as AbortSignal).addEventListener('abort', () => reject(new CanceledError('aborted', c)))
      })
    }) as AxiosAdapter

    const request = scopedFetch(() => getData('/api/v1/metrics/summary', { params: { release_id: R1 } }))
    const caught = request.catch((e: unknown) => e)
    await vi.waitFor(() => expect(retryConfig).not.toBeNull())
    expect((retryConfig as InternalAxiosRequestConfig | null)?.signal, 'the retry carries a scope signal').toBeDefined()
    expect(trackedScopeRequestCount()).toBe(1)

    setSettledScopeFingerprint({ release: R2, suite: null })
    const error = await caught
    expect(isScopeSuperseded(error)).toBe(true)
  })
})
