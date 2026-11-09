/**
 * The public demo's write refusal (`403 demo_read_only`, server DEMO_MODE),
 * driven through the REAL response interceptor via a stubbed axios adapter.
 *
 * Every save on the demo is refused, so the refusal must read as one reused
 * toast (stable id), stay quiet for callers that opted out, and leave every
 * other 403 exactly as it was.
 */
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toastError = vi.hoisted(() => vi.fn())

vi.mock('@/store/authStore', () => ({
  useAuthStore: { getState: () => ({ token: 'access-token', refreshAccessToken: vi.fn() }) },
}))
vi.mock('react-hot-toast', () => ({ default: { error: toastError, success: vi.fn() } }))

const { api, DEMO_READ_ONLY_TOAST_ID } = await import('./api')
const { isDemoReadOnly } = await import('./apiErrors')

const DEMO_DETAIL = {
  code: 'demo_read_only',
  message: 'This is a read-only demo, so changes are turned off. Install TestLookup to try everything.',
}

function stub(status: number, detail: unknown) {
  api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
    const response = { status, statusText: 'Forbidden', data: { detail }, headers: {}, config } as AxiosResponse
    throw new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_RESPONSE', config, {}, response)
  }
}

describe('demo read-only refusal', () => {
  beforeEach(() => {
    toastError.mockReset()
  })
  afterEach(() => {
    api.defaults.adapter = undefined
  })

  it('raises the server sentence as one toast, reused by id across refusals', async () => {
    stub(403, DEMO_DETAIL)
    await expect(api.post('/api/v1/projects', {})).rejects.toBeInstanceOf(AxiosError)
    await expect(api.patch('/api/v1/projects/p1', {})).rejects.toBeInstanceOf(AxiosError)
    expect(toastError).toHaveBeenCalledTimes(2)
    for (const [message, options] of toastError.mock.calls) {
      expect(message).toBe(DEMO_DETAIL.message)
      expect(options).toEqual({ id: DEMO_READ_ONLY_TOAST_ID })
    }
  })

  it('stays quiet for a request that opted out, and still rejects', async () => {
    stub(403, DEMO_DETAIL)
    const failure = api.post('/api/v1/onboarding/track', {}, { suppressToast: true })
    const error = (await failure.catch((e: unknown) => e)) as AxiosError
    expect(error.response?.status).toBe(403)
    expect(toastError).not.toHaveBeenCalled()
  })

  it('leaves any other 403 on the ordinary toast (no shared id)', async () => {
    stub(403, 'Not enough permissions')
    await expect(api.post('/api/v1/projects', {})).rejects.toBeInstanceOf(AxiosError)
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(toastError).toHaveBeenCalledWith('Not enough permissions')
  })
})

describe('isDemoReadOnly', () => {
  it.each([
    [DEMO_DETAIL, true],
    [{ code: 'demo_read_only' }, true],
    [{ code: 'password_change_required' }, false],
    ['demo_read_only', false],
    [null, false],
    [undefined, false],
  ])('%j → %s', (detail, expected) => {
    expect(isDemoReadOnly(detail)).toBe(expected)
  })
})
