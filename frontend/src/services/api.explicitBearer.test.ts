/**
 * D-0019 (GPT-6): a sign-in in a tab that already holds a session fetches the new account's user with the new token
 * (`fetchCurrentUser(accessToken)`), while the store still holds the previous session's. The request interceptor
 * overwrote that header with the stored token, so `/auth/me` answered the previous account and the tab kept the new
 * token with the old user. A request that names its own bearer keeps it; every other same-origin request still
 * takes the stored token, and a foreign origin gets none.
 */
import { describe, expect, it, vi } from 'vitest'
import type { InternalAxiosRequestConfig } from 'axios'

vi.mock('@/store/authStore', () => ({
  useAuthStore: { getState: () => ({ token: 'stored-access', refreshAccessToken: vi.fn() }) },
}))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

const { api } = await import('./api')

/** The Authorization header a request leaves with (no network: the adapter answers). */
async function bearerOf(url: string, headers?: Record<string, string>): Promise<unknown> {
  let sent: unknown
  await api.get(url, {
    headers,
    adapter: async (config: InternalAxiosRequestConfig) => {
      sent = config.headers.Authorization
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
    },
  })
  return sent
}

describe('the API client attaches the bearer', () => {
  it('keeps a bearer the request names itself (the sign-in\'s /auth/me with the new token)', async () => {
    expect(await bearerOf('/api/v1/auth/me', { Authorization: 'Bearer new-access' })).toBe('Bearer new-access')
  })

  it('gives every other same-origin request the stored token', async () => {
    expect(await bearerOf('/api/v1/runs')).toBe('Bearer stored-access')
  })

  it('gives a foreign origin none', async () => {
    expect(await bearerOf('https://elsewhere.example/api')).toBeUndefined()
  })
})
