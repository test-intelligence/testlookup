/**
 * The single most likely way MFA ships broken.
 *
 * `services/api.ts` retries once on 401 by calling `refreshAccessToken`. Every
 * wrong TOTP code and every expired MFA challenge comes back as a 401, and the
 * verify call happens on the LOGIN screen where there is no session to refresh
 * — so without an exclusion a typo triggers a refresh, the refresh fails,
 * `refreshAccessToken` calls `logout()`, and the feature looks broken.
 *
 * These tests drive the REAL interceptor (via a stubbed axios adapter) and
 * assert on whether the refresh was attempted.
 */
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const refreshAccessToken = vi.fn()

vi.mock('@/store/authStore', () => ({
  useAuthStore: {
    getState: () => ({ token: 'stale-access-token', refreshAccessToken }),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}))

const { api, shouldAttemptTokenRefresh } = await import('./api')

/** Every request fails with `status`, exactly like a real backend rejection. */
function stubStatus(status: number) {
  api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
    const response = {
      status,
      statusText: 'Error',
      data: { detail: 'Invalid verification code.' },
      headers: {},
      config,
    } as AxiosResponse
    throw new AxiosError(
      `Request failed with status code ${status}`,
      'ERR_BAD_REQUEST',
      config,
      {},
      response,
    )
  }
}

describe('401 refresh exclusions', () => {
  beforeEach(() => {
    refreshAccessToken.mockReset()
    // A successful refresh would otherwise loop the retry forever.
    refreshAccessToken.mockResolvedValue(null)
    stubStatus(401)
  })

  afterEach(() => {
    api.defaults.adapter = undefined
  })

  it('does NOT attempt a token refresh when /auth/mfa/verify returns 401', async () => {
    await expect(
      api.post('/api/v1/auth/mfa/verify', {
        challenge_token: 'ct',
        code: '000000',
      }),
    ).rejects.toMatchObject({ response: { status: 401 } })

    expect(refreshAccessToken).not.toHaveBeenCalled()
  })

  it.each([
    '/api/v1/auth/mfa/enroll/start',
    '/api/v1/auth/mfa/enroll/confirm',
    '/api/v1/auth/mfa/status',
    '/api/v1/auth/login',
    '/api/v1/auth/refresh',
  ])('does NOT attempt a token refresh for %s', async (url) => {
    await expect(api.post(url, {})).rejects.toMatchObject({ response: { status: 401 } })
    expect(refreshAccessToken).not.toHaveBeenCalled()
  })

  it('STILL refreshes on 401 for an ordinary API call (control)', async () => {
    await expect(api.get('/api/v1/projects')).rejects.toBeDefined()
    expect(refreshAccessToken).toHaveBeenCalledTimes(1)
  })

  it('does not refresh on a non-401 MFA failure either (400 wrong code on confirm)', async () => {
    stubStatus(400)
    await expect(api.post('/api/v1/auth/mfa/enroll/confirm', { code: '000000' })).rejects
      .toBeDefined()
    expect(refreshAccessToken).not.toHaveBeenCalled()
  })
})

describe('shouldAttemptTokenRefresh', () => {
  it.each([
    '/api/v1/auth/mfa/verify',
    '/api/v1/auth/mfa/enroll/start',
    '/api/v1/auth/mfa/enroll/confirm',
    '/api/v1/auth/mfa/disable',
    '/api/v1/auth/mfa/recovery-codes',
    '/api/v1/auth/mfa/status',
    '/api/v1/auth/login',
    '/api/v1/auth/refresh',
    '/api/v1/auth/register',
    '/api/v1/auth/dev-login',
  ])('excludes %s', (url) => {
    expect(shouldAttemptTokenRefresh(url)).toBe(false)
  })

  it.each([
    '/api/v1/projects',
    '/api/v1/auth/me',
    '/api/v1/auth/change-password',
    '/api/v1/settings/mfa-policy',
  ])('allows %s', (url) => {
    expect(shouldAttemptTokenRefresh(url)).toBe(true)
  })

  it('ignores query strings and absolute URLs', () => {
    expect(shouldAttemptTokenRefresh('/api/v1/auth/mfa/verify?x=1')).toBe(false)
    expect(shouldAttemptTokenRefresh('https://app.example.com/api/v1/auth/mfa/verify')).toBe(
      false,
    )
    expect(shouldAttemptTokenRefresh('https://app.example.com/api/v1/projects')).toBe(true)
  })

  it('does not let a lookalike path slip through, and does not over-match', () => {
    // The MFA policy page is a settings resource, not an /auth/mfa/ endpoint.
    expect(shouldAttemptTokenRefresh('/api/v1/settings/mfa-policy')).toBe(true)
    expect(shouldAttemptTokenRefresh(undefined)).toBe(true)
  })
})
