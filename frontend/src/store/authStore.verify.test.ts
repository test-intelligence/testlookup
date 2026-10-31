/**
 * W25a HYG-07: one `GET /api/v1/auth/me` per sign-in.
 *
 * Every sign-in tail fetches the user and then calls `setAuth`, which stamps
 * `lastVerifiedAt` (so the protected shell does not ask again), and a second
 * `fetchUser()` while the first is pending for the same token shares it (the
 * dev server's StrictMode runs every mount effect twice). The stamp is never
 * persisted: a hard reload re-verifies once.
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
  role: 'QA_LEAD',
  is_active: true,
  must_change_password: false,
  avatar_color: 'blue',
}

/** A `GET` that answers only when the test says so. */
function deferredGet() {
  let resolve!: (value: unknown) => void
  mockGet.mockReturnValueOnce(new Promise((r) => { resolve = r }))
  return (data: unknown) => resolve({ data })
}

const meCalls = () => mockGet.mock.calls.filter(([path]) => path === '/api/v1/auth/me').length

describe('authStore: one session read per sign-in', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockPost.mockReset()
    localStorage.clear()
    useAuthStore.setState({
      token: 'acc',
      refreshToken: 'ref',
      user: USER,
      isAuthenticated: true,
      refreshRetryAt: null,
      refreshFailureCount: 0,
      refreshError: null,
      refreshRequiresReauth: false,
      lastVerifiedAt: null,
    })
  })

  it('two concurrent fetchUser() calls for one token share ONE GET /api/v1/auth/me', async () => {
    const answer = deferredGet()
    const first = useAuthStore.getState().fetchUser()
    const second = useAuthStore.getState().fetchUser()
    expect(second).toBe(first)

    answer({ ...USER, full_name: 'Alice Shared' })
    await Promise.all([first, second])

    expect(meCalls()).toBe(1)
    expect(useAuthStore.getState().user?.full_name).toBe('Alice Shared')
  })

  it('a call after the first settles reads again', async () => {
    mockGet.mockResolvedValue({ data: USER })
    await useAuthStore.getState().fetchUser()
    await useAuthStore.getState().fetchUser()

    expect(meCalls()).toBe(2)
  })

  it('a token change between the calls does NOT share the read', async () => {
    const answerFirst = deferredGet()
    const first = useAuthStore.getState().fetchUser()

    useAuthStore.setState({ token: 'acc-rotated' })
    const answerSecond = deferredGet()
    const second = useAuthStore.getState().fetchUser()
    expect(second).not.toBe(first)

    answerFirst(USER)
    answerSecond(USER)
    await Promise.all([first, second])
    expect(meCalls()).toBe(2)
  })

  it('a successful fetchUser stamps lastVerifiedAt; a failed one does not', async () => {
    mockGet.mockRejectedValueOnce(new Error('Network Error'))
    await useAuthStore.getState().fetchUser()
    expect(useAuthStore.getState().lastVerifiedAt).toBeNull()

    const before = Date.now()
    mockGet.mockResolvedValueOnce({ data: USER })
    await useAuthStore.getState().fetchUser()
    expect(useAuthStore.getState().lastVerifiedAt).toBeGreaterThanOrEqual(before)
  })

  it('setAuth stamps lastVerifiedAt and logout clears it', () => {
    const before = Date.now()
    useAuthStore.getState().setAuth('new-acc', 'new-ref', USER)
    const stamped = useAuthStore.getState().lastVerifiedAt
    expect(typeof stamped).toBe('number')
    expect(stamped).toBeGreaterThanOrEqual(before)

    useAuthStore.getState().logout()
    expect(useAuthStore.getState().lastVerifiedAt).toBeNull()
  })

  it('the stamp is absent from the persisted session (a hard reload re-verifies)', () => {
    useAuthStore.getState().setAuth('new-acc', 'new-ref', USER)
    expect(useAuthStore.getState().lastVerifiedAt).not.toBeNull()

    const saved = localStorage.getItem('auth-storage')
    expect(saved).not.toBeNull()
    const persisted = JSON.parse(saved as string) as { state: Record<string, unknown> }
    expect(persisted.state.token).toBe('new-acc')
    expect('lastVerifiedAt' in persisted.state).toBe(false)
  })
})
