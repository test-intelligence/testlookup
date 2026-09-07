/**
 * `fetchUser` logs the user out on failure — but only on an explicit auth
 * rejection.
 *
 * MFA gives the auth endpoints a new way to fail: 503, raised when the stored
 * TOTP seed will not decrypt or when the token revocation store is down. That
 * is a transient server problem, and treating it as "log out" would evict
 * every signed-in user during a Redis blip. This pins the existing 401/403-only
 * behaviour so the MFA work cannot quietly widen it.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockGet = vi.fn()
const mockPost = vi.fn()
vi.mock('../services/api', () => ({
  api: { get: (...args: unknown[]) => mockGet(...args), post: (...args: unknown[]) => mockPost(...args) },
}))

const { useAuthStore } = await import('./authStore')

const USER = {
  id: 'u1',
  email: 'a@b.c',
  username: 'alice',
  full_name: 'Alice',
  role: 'ADMIN',
  is_active: true,
  must_change_password: false,
  avatar_color: 'blue',
}

function httpError(status: number) {
  return { response: { status, data: { detail: 'nope' } } }
}

describe('authStore.fetchUser under MFA-era failures', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockPost.mockReset()
    useAuthStore.setState({
      token: 'acc',
      refreshToken: 'ref',
      user: USER,
      isAuthenticated: true,
      refreshRetryAt: null,
      refreshFailureCount: 0,
      refreshError: null,
      refreshRequiresReauth: false,
    })
  })

  it('preserves credentials and enters cooldown on refresh 503', async () => {
    mockPost.mockRejectedValueOnce(httpError(503))
    await useAuthStore.getState().refreshAccessToken()
    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(useAuthStore.getState().isAuthenticated).toBe(true)
    expect(useAuthStore.getState().refreshRetryAt).toBeGreaterThan(Date.now())
  })

  it('requires reauthentication when refresh outcome is ambiguous', async () => {
    mockPost.mockRejectedValueOnce(new Error('Network Error'))
    await useAuthStore.getState().refreshAccessToken()
    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(useAuthStore.getState().isAuthenticated).toBe(true)
    expect(useAuthStore.getState().refreshRequiresReauth).toBe(true)
    await useAuthStore.getState().refreshAccessToken()
    expect(mockPost).toHaveBeenCalledTimes(1)
  })

  it('clears refresh error state after a successful retry', async () => {
    useAuthStore.setState({ refreshRetryAt: null, refreshFailureCount: 1, refreshError: 'temporary', refreshRequiresReauth: false })
    mockPost.mockResolvedValueOnce({ data: { access_token: 'new-acc', refresh_token: 'new-ref' } })
    await useAuthStore.getState().refreshAccessToken()
    expect(useAuthStore.getState().refreshError).toBeNull()
    expect(useAuthStore.getState().refreshRequiresReauth).toBe(false)
  })

  it.each([401, 403])('clears credentials on explicit refresh rejection %i', async (status) => {
    mockPost.mockRejectedValue(httpError(status))
    await useAuthStore.getState().refreshAccessToken()
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
    expect(useAuthStore.getState().token).toBeNull()
  })

  it('keeps the session on a 503 — "we cannot verify right now" is not "log out"', async () => {
    mockGet.mockRejectedValue(httpError(503))
    await useAuthStore.getState().fetchUser()

    expect(useAuthStore.getState().isAuthenticated).toBe(true)
    expect(useAuthStore.getState().token).toBe('acc')
    expect(useAuthStore.getState().user).toEqual(USER)
  })

  it('keeps the session on a network error (no response at all)', async () => {
    mockGet.mockRejectedValue(new Error('Network Error'))
    await useAuthStore.getState().fetchUser()

    expect(useAuthStore.getState().isAuthenticated).toBe(true)
  })

  it.each([401, 403])('still logs out on an explicit %i', async (status) => {
    mockGet.mockRejectedValue(httpError(status))
    await useAuthStore.getState().fetchUser()

    expect(useAuthStore.getState().isAuthenticated).toBe(false)
    expect(useAuthStore.getState().token).toBeNull()
  })

  it('preserves the session when an original 401 follows an ambiguous refresh failure', async () => {
    useAuthStore.setState({ refreshRequiresReauth: true, refreshError: 'Session refresh could not be confirmed.' })
    mockGet.mockRejectedValue(httpError(401))
    await useAuthStore.getState().fetchUser()
    expect(useAuthStore.getState().isAuthenticated).toBe(true)
    expect(useAuthStore.getState().refreshRequiresReauth).toBe(true)
  })
})
