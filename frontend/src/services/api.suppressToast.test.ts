/**
 * The per-request toast opt-out (VIZ-107), driven through the REAL response
 * interceptor via a stubbed axios adapter.
 *
 * Chart requests render their own error state, so they pass `suppressToast`;
 * six failing charts must not raise six toasts. The regression half matters
 * as much: a request that does NOT pass the flag keeps toasting exactly as
 * before — the opt-out must not leak into every other caller.
 */
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toastError = vi.hoisted(() => vi.fn())

vi.mock('@/store/authStore', () => ({
  useAuthStore: { getState: () => ({ token: 'access-token', refreshAccessToken: vi.fn() }) },
}))
vi.mock('react-hot-toast', () => ({ default: { error: toastError, success: vi.fn() } }))

const { api } = await import('./api')

function stubStatus(status: number) {
  api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
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

describe('suppressToast request opt-out', () => {
  beforeEach(() => {
    toastError.mockReset()
  })
  afterEach(() => {
    api.defaults.adapter = undefined
  })

  it.each([500, 403, 422, 429])('an ordinary request still toasts a %s (existing behaviour)', async (status) => {
    stubStatus(status)
    await expect(api.get('/api/v1/analytics/x')).rejects.toBeInstanceOf(AxiosError)
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(toastError).toHaveBeenCalledWith('Server exploded')
  })

  it('an ordinary request with suppressToast: false still toasts', async () => {
    stubStatus(500)
    await expect(api.get('/api/v1/analytics/x', { suppressToast: false })).rejects.toBeInstanceOf(AxiosError)
    expect(toastError).toHaveBeenCalledTimes(1)
  })

  it.each([500, 403, 422, 429])('a suppressToast request rejects with the error but raises no toast (%s)', async (status) => {
    stubStatus(status)
    const failure = api.get('/api/v1/analytics/x', { suppressToast: true })
    await expect(failure).rejects.toBeInstanceOf(AxiosError)
    // The caller still gets the full response to render (status, request id).
    const error = (await failure.catch((e: unknown) => e)) as AxiosError
    expect(error.response?.status).toBe(status)
    expect(toastError).not.toHaveBeenCalled()
  })

  it('401 and 404 stay quiet with or without the flag', async () => {
    stubStatus(404)
    await expect(api.get('/api/v1/x')).rejects.toBeInstanceOf(AxiosError)
    expect(toastError).not.toHaveBeenCalled()
  })
})
