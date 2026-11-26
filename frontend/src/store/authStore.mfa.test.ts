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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockGet = vi.fn()
const mockPost = vi.fn()
vi.mock('../services/api', () => ({
  api: { get: (...args: unknown[]) => mockGet(...args), post: (...args: unknown[]) => mockPost(...args) },
}))

const { AUTH_STORAGE_KEY, useAuthStore } = await import('./authStore')
const { withOriginRefreshLock } = await import('./refreshCoordinator')
const originalLocks = Object.getOwnPropertyDescriptor(navigator, 'locks')

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
  return { response: { status, data: { detail: 'nope' }, headers: status === 503 ? { 'x-refresh-retry-safe': '1' } : {} } }
}

function immediateBrowserLocks() {
  return { request: vi.fn(async (_name: string, callback: () => Promise<unknown>) => callback()) }
}

function deferredPost() {
  let resolve!: (value: unknown) => void
  let reject!: (reason: unknown) => void
  let markStarted!: () => void
  const started = new Promise<void>((done) => { markStarted = done })
  const response = new Promise<unknown>((done, fail) => {
    resolve = done
    reject = fail
  })
  mockPost.mockImplementationOnce(() => {
    markStarted()
    return response
  })
  return { started, resolve, reject }
}

function mockedRefreshLockStorage() {
  const records = new Map<string, string>()
  let waiters: Array<() => void> = []
  const wake = () => {
    const pending = waiters
    waiters = []
    for (const resolve of pending) resolve()
  }
  return {
    backend: {
      claim: vi.fn(async (credential: string, owner: string) => {
        if (!records.has(credential)) {
          records.set(credential, owner)
          return 'claimed' as const
        }
        return 'held' as const
      }),
      release: vi.fn(async (credential: string, owner: string) => {
        if (records.get(credential) === owner) {
          records.delete(credential)
          wake()
        }
      }),
    },
    wait: vi.fn(() => new Promise<void>((resolve) => { waiters.push(resolve) })),
  }
}

describe('authStore.fetchUser under MFA-era failures', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockPost.mockReset()
    Object.defineProperty(navigator, 'locks', { configurable: true, value: immediateBrowserLocks() })
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

  afterEach(() => {
    if (originalLocks) Object.defineProperty(navigator, 'locks', originalLocks)
    else Reflect.deleteProperty(navigator, 'locks')
  })

  it('recovers after a safe refresh 503 cooldown', async () => {
    mockPost.mockRejectedValueOnce(httpError(503))
    await useAuthStore.getState().refreshAccessToken()
    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(useAuthStore.getState().isAuthenticated).toBe(true)
    expect(useAuthStore.getState().refreshRetryAt).toBeGreaterThan(Date.now())
    useAuthStore.setState({ refreshRetryAt: null })
    mockPost.mockResolvedValueOnce({ data: { access_token: 'recovered', refresh_token: 'rotated' } })
    await useAuthStore.getState().refreshAccessToken()
    expect(mockPost).toHaveBeenCalledTimes(2)
    expect(useAuthStore.getState().token).toBe('recovered')
  })

  it('requires reauthentication when refresh outcome is ambiguous', async () => {
    mockPost.mockRejectedValueOnce(new Error('Network Error'))
    await useAuthStore.getState().refreshAccessToken()
    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(useAuthStore.getState().isAuthenticated).toBe(true)
    expect(useAuthStore.getState().refreshRequiresReauth).toBe(true)
    await useAuthStore.getState().refreshAccessToken()
    await useAuthStore.getState().retryRefresh()
    expect(mockPost).toHaveBeenCalledTimes(1)
  })

  it('clears refresh error state after a successful retry', async () => {
    useAuthStore.setState({ refreshRetryAt: null, refreshFailureCount: 1, refreshError: 'temporary', refreshRequiresReauth: false })
    mockPost.mockResolvedValueOnce({ data: { access_token: 'new-acc', refresh_token: 'new-ref' } })
    await useAuthStore.getState().refreshAccessToken()
    expect(useAuthStore.getState().refreshError).toBeNull()
    expect(useAuthStore.getState().refreshRequiresReauth).toBe(false)
  })

  it('single-flights concurrent refresh calls', async () => {
    const request = deferredPost()
    const first = useAuthStore.getState().refreshAccessToken()
    const second = useAuthStore.getState().refreshAccessToken()
    await request.started
    request.resolve({ data: { access_token: 'shared', refresh_token: 'shared-ref' } })
    await Promise.all([first, second])
    expect(mockPost).toHaveBeenCalledTimes(1)
  })

  it('adopts a refresh completed by another tab while waiting for the browser lock', async () => {
    const stored = JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY) ?? '{}')
    stored.state.token = 'other-tab-access'
    stored.state.refreshToken = 'other-tab-refresh'
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: {
        request: vi.fn(async (_name: string, callback: () => Promise<unknown>) => {
          localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(stored))
          return callback()
        }),
      },
    })

    const token = await useAuthStore.getState().refreshAccessToken()

    expect(token).toBe('other-tab-access')
    expect(useAuthStore.getState().refreshToken).toBe('other-tab-refresh')
    expect(mockPost).not.toHaveBeenCalled()
  })

  it('shares one successful refresh through mocked storage without Web Locks', async () => {
    Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined })
    const storage = mockedRefreshLockStorage()
    const request = deferredPost()

    const refresh = (owner: string) => withOriginRefreshLock<string | null>('ref', async () => {
      const stored = JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY) ?? '{}')
      if (stored.state.refreshToken !== 'ref') return stored.state.token as string
      const response = await mockPost('/api/v1/auth/refresh', { refresh_token: 'ref' }) as {
        data: { access_token: string; refresh_token: string }
      }
      stored.state.token = response.data.access_token
      stored.state.refreshToken = response.data.refresh_token
      localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(stored))
      return response.data.access_token
    }, null, { backend: storage.backend, owner, wait: storage.wait })

    const tabA = refresh('tab-a')
    const tabB = refresh('tab-b')
    await request.started
    expect(mockPost).toHaveBeenCalledTimes(1)

    request.resolve({ data: { access_token: 'shared', refresh_token: 'shared-ref' } })

    await expect(Promise.all([tabA, tabB])).resolves.toEqual(['shared', 'shared'])
    expect(mockPost).toHaveBeenCalledTimes(1)
  })

  it('keeps replacement credential claims separate and releases only their owner', async () => {
    Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined })
    const storage = mockedRefreshLockStorage()
    let finishOld!: () => void
    let finishNew!: () => void
    let markOldStarted!: () => void
    let markNewStarted!: () => void
    const oldDone = new Promise<void>((resolve) => { finishOld = resolve })
    const newDone = new Promise<void>((resolve) => { finishNew = resolve })
    const oldStarted = new Promise<void>((resolve) => { markOldStarted = resolve })
    const newStarted = new Promise<void>((resolve) => { markNewStarted = resolve })

    const oldRefresh = withOriginRefreshLock('old-ref', async () => {
      markOldStarted()
      await oldDone
      return 'old-result'
    }, null, { backend: storage.backend, owner: 'old-owner', wait: storage.wait })
    await oldStarted

    useAuthStore.getState().setAuth('new-acc', 'new-ref', { ...USER, id: 'u2' })
    const newRefresh = withOriginRefreshLock('new-ref', async () => {
      markNewStarted()
      await newDone
      return 'new-result'
    }, null, { backend: storage.backend, owner: 'new-owner', wait: storage.wait })
    await newStarted

    finishOld()
    await expect(oldRefresh).resolves.toBe('old-result')
    expect(storage.backend.release).toHaveBeenCalledWith('old-ref', 'old-owner')

    const intruder = vi.fn(async () => 'unsafe')
    await expect(withOriginRefreshLock('new-ref', intruder, null, {
      backend: storage.backend,
      owner: 'old-owner',
      timeoutMs: 0,
    })).resolves.toBeNull()
    expect(intruder).not.toHaveBeenCalled()

    finishNew()
    await expect(newRefresh).resolves.toBe('new-result')
    expect(storage.backend.release).toHaveBeenCalledWith('new-ref', 'new-owner')
  })

  it('does not refresh when fallback storage is unavailable', async () => {
    Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined })
    const callback = vi.fn(async () => 'unsafe')
    const backend = {
      claim: vi.fn(async () => 'unavailable' as const),
      release: vi.fn(async () => {}),
    }

    await expect(withOriginRefreshLock<string | null>('ref', callback, null, { backend })).resolves.toBeNull()
    expect(callback).not.toHaveBeenCalled()
  })

  it('does not refresh after waiting for an unresolved owner times out', async () => {
    Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined })
    const storage = mockedRefreshLockStorage()
    await storage.backend.claim('ref', 'other-tab')
    const callback = vi.fn(async () => 'unsafe')

    await expect(withOriginRefreshLock<string | null>('ref', callback, null, {
      backend: storage.backend,
      owner: 'waiting-tab',
      timeoutMs: 0,
    })).resolves.toBeNull()
    expect(callback).not.toHaveBeenCalled()
  })

  it('revokes server session before clearing local state, even on failure', async () => {
    mockPost.mockRejectedValueOnce(new Error('offline'))
    await useAuthStore.getState().logoutServer()
    expect(mockPost).toHaveBeenCalledWith('/api/v1/auth/logout')
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
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

  it('does not let a stale manual retry overwrite persisted reauthentication state', async () => {
    const stored = JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY) ?? '{}')
    stored.state.refreshRequiresReauth = true
    stored.state.refreshError = 'Session refresh could not be confirmed.'
    stored.state.refreshRetryAt = Date.now() + 60_000
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(stored))

    await expect(useAuthStore.getState().retryRefresh()).resolves.toBeNull()

    const persisted = JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY) ?? '{}')
    expect(mockPost).not.toHaveBeenCalled()
    expect(useAuthStore.getState().refreshRequiresReauth).toBe(true)
    expect(persisted.state.refreshRequiresReauth).toBe(true)
  })

  it('does not resurrect a session from a late user response after logout', async () => {
    let resolve!: (value: unknown) => void
    mockGet.mockReturnValueOnce(new Promise((r) => { resolve = r }))
    const pending = useAuthStore.getState().fetchUser()

    useAuthStore.getState().logout()
    resolve({ data: USER })
    await pending

    expect(useAuthStore.getState().isAuthenticated).toBe(false)
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('does not apply a late refresh response after logout', async () => {
    const request = deferredPost()
    const pending = useAuthStore.getState().refreshAccessToken()

    await request.started
    useAuthStore.getState().logout()
    request.resolve({ data: { access_token: 'stale', refresh_token: 'stale-ref' } })
    await pending

    expect(useAuthStore.getState().isAuthenticated).toBe(false)
    expect(useAuthStore.getState().token).toBeNull()
    expect(useAuthStore.getState().refreshToken).toBeNull()
  })

  it('does not log out a replacement session on a late auth rejection', async () => {
    let reject!: (reason: unknown) => void
    mockGet.mockReturnValueOnce(new Promise((_resolve, r) => { reject = r }))
    const pending = useAuthStore.getState().fetchUser()

    useAuthStore.getState().setAuth('new-acc', 'new-ref', { ...USER, id: 'u2', email: 'new@example.com' })
    reject(httpError(401))
    await pending

    expect(useAuthStore.getState().isAuthenticated).toBe(true)
    expect(useAuthStore.getState().token).toBe('new-acc')
    expect(useAuthStore.getState().user?.id).toBe('u2')
  })

  it('does not update replacement-session retry state on a late refresh failure', async () => {
    const request = deferredPost()
    const pending = useAuthStore.getState().refreshAccessToken()

    await request.started
    useAuthStore.getState().setAuth('new-acc', 'new-ref', { ...USER, id: 'u2', email: 'new@example.com' })
    request.reject(new Error('network down'))
    await pending

    expect(useAuthStore.getState().token).toBe('new-acc')
    expect(useAuthStore.getState().refreshToken).toBe('new-ref')
    expect(useAuthStore.getState().refreshFailureCount).toBe(0)
    expect(useAuthStore.getState().refreshRequiresReauth).toBe(false)
  })
})
