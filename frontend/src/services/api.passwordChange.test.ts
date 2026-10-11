/**
 * A session on a temporary password, driven through the REAL response
 * interceptor (stubbed adapter).
 *
 * The API refuses such a session with 403 `password_change_required` until the
 * password is changed (enforced server-side since 2026-10-10). The client sends
 * the reader to the change form instead of toasting a refusal on every call --
 * and only for that code: any other 403 still toasts as before.
 */
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toastError = vi.hoisted(() => vi.fn())

vi.mock('@/store/authStore', () => ({
  useAuthStore: { getState: () => ({ token: 'access-token', refreshAccessToken: vi.fn() }) },
}))
vi.mock('react-hot-toast', () => ({ default: { error: toastError, success: vi.fn() } }))

const { api } = await import('./api')

function stub403(detail: unknown) {
  api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
    const response = { status: 403, statusText: 'Forbidden', data: { detail }, headers: {}, config } as AxiosResponse
    throw new AxiosError('Request failed with status code 403', 'ERR_BAD_REQUEST', config, {}, response)
  }
}

const original = window.location
let assign: ReturnType<typeof vi.fn>

function at(pathname: string) {
  assign = vi.fn()
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...original, pathname, assign },
  })
}

describe('403 password_change_required', () => {
  beforeEach(() => toastError.mockReset())
  afterEach(() => {
    api.defaults.adapter = undefined
    Object.defineProperty(window, 'location', { configurable: true, value: original })
  })

  it('sends the reader to the change form and raises no toast', async () => {
    at('/runs')
    stub403({ code: 'password_change_required', message: 'Change it to continue.' })
    await expect(api.get('/api/v1/runs')).rejects.toBeInstanceOf(AxiosError)
    expect(assign).toHaveBeenCalledWith('/reset-password')
    expect(toastError).not.toHaveBeenCalled()
  })

  it('does not loop when the reader is already on the change form', async () => {
    at('/reset-password')
    stub403({ code: 'password_change_required', message: 'x' })
    await expect(api.get('/api/v1/projects')).rejects.toBeInstanceOf(AxiosError)
    expect(assign).not.toHaveBeenCalled()
  })

  it('any other 403 still toasts and stays put', async () => {
    at('/runs')
    stub403('QA Engineer role required')
    await expect(api.get('/api/v1/runs')).rejects.toBeInstanceOf(AxiosError)
    expect(assign).not.toHaveBeenCalled()
    expect(toastError).toHaveBeenCalledWith('QA Engineer role required')
  })
})
