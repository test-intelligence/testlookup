/**
 * B0 (Wave 3): the sections' "ever had a run" probe failing raised the
 * page-wide error toast ("Network Error") on Suite detail, Coverage and
 * Failures — a section's private question reaching the whole page. The probe
 * is quiet: through the REAL runs service and response interceptor (stubbed
 * adapter), a 500 or a dropped connection raises no toast, and the probe
 * still falls back to `true` ("nothing matches", never "no data yet"). An
 * ordinary `/runs` read keeps toasting (the opt-out does not leak).
 */
import { renderHook, waitFor } from '@testing-library/react'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toastError = vi.hoisted(() => vi.fn())
vi.mock('react-hot-toast', () => ({ default: { error: toastError, success: vi.fn() } }))
vi.mock('@/store/authStore', () => ({
  useAuthStore: { getState: () => ({ token: 'access-token', refreshAccessToken: vi.fn() }) },
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) => sel({ activeProjectId: 'proj-1' }),
}))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => null }))

const { api } = await import('@/services/api')
const { useRuns } = await import('@/hooks/useRuns')
const { EVER_HAD_RUN_PARAMS, useEverHadRun } = await import('./useEverHadRun')

const asked: { url?: string; suppressToast?: boolean }[] = []
function failWith(status: number | 'network') {
  api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
    asked.push({ url: config.url, suppressToast: config.suppressToast })
    if (status === 'network') throw new AxiosError('Network Error', 'ERR_NETWORK', config, {})
    const response = {
      status,
      statusText: 'Error',
      data: { code: 'boom', message: 'Server error', request_id: 'req-1', detail: 'Server exploded' },
      headers: { 'x-request-id': 'req-1' },
      config,
    } as AxiosResponse
    throw new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_RESPONSE', config, {}, response)
  }
}

const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>{children}</SWRConfig>
)

beforeEach(() => {
  toastError.mockReset()
  asked.length = 0
})
afterEach(() => {
  api.defaults.adapter = undefined
})

describe('the sections’ existence probe is quiet', () => {
  it.each([500, 'network'] as const)('a failed probe (%s) raises no toast and still answers true', async (status) => {
    failWith(status)
    const { result } = renderHook(() => useEverHadRun(true), { wrapper })
    await waitFor(() => expect(result.current).toBe(true))
    expect(asked[0]).toMatchObject({ url: '/api/v1/runs', suppressToast: true })
    expect(toastError).not.toHaveBeenCalled()
  })

  // R1B-7: Overview asks the SAME question ({page:1,size:1}, no release) loudly for its first-run
  // wizard while the catalogue's probe asks it quietly. One shared cache entry let whichever fetcher
  // fired first decide for both: the page's failure lost its toast, or the probe toasted again.
  it.each([
    ['the page first, then the quiet probe', ['loud', 'quiet']],
    ['the quiet probe first, then the page', ['quiet', 'loud']],
  ] as const)('Overview + catalogue (%s): the page read toasts once, the probe never', async (_name, order) => {
    failWith(500)
    const hooks = {
      loud: function useLoud() {
        return useRuns(EVER_HAD_RUN_PARAMS, { ignoreGlobalRelease: true })
      },
      quiet: function useQuiet() {
        return useEverHadRun(true)
      },
    }
    renderHook(
      () => {
        hooks[order[0]]()
        hooks[order[1]]()
      },
      { wrapper },
    )
    await waitFor(() => expect(asked).toHaveLength(2))
    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1))
    // Each read its own request, with its own toast rule, whichever mounted first.
    expect(asked.map((a) => Boolean(a.suppressToast)).sort()).toEqual([false, true])
  })

  it('an ordinary runs read still toasts its failure (the opt-out stays with the probe)', async () => {
    failWith(500)
    renderHook(() => useRuns({ page: 1, size: 20 }), { wrapper })
    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(asked[0]?.suppressToast).toBeUndefined()
  })
})
