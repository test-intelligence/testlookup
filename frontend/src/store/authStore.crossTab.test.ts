/**
 * D-0019 (GPT-6): `auth-storage` is one entry shared by every tab of a browser profile, and each tab works from its
 * own copy in memory. Tab A (an admin) kept its session after tab B signed in as a viewer, and a write from A after
 * the switch put the admin back in storage, so the next tab or reload opened as the admin.
 *
 * One account per browser profile (owner decision Q3): another tab's sign-in as someone else, or sign-out, reloads
 * this tab (with a note), and this tab never writes its old account over the new one. Another tab refreshing the
 * same account's tokens is adopted without a reload. Each case loads the module fresh, as a page load does.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockGet = vi.fn()
const mockPost = vi.fn()
vi.mock('../services/api', () => ({
  api: { get: (...args: unknown[]) => mockGet(...args), post: (...args: unknown[]) => mockPost(...args) },
}))
const toast = vi.hoisted(() => vi.fn())
vi.mock('react-hot-toast', () => ({ default: toast }))

const KEY = 'auth-storage'
const person = (id: string, username: string, role: string) => ({
  id, email: `${username}@example.test`, username, full_name: username, role,
  is_active: true, must_change_password: false, avatar_color: null,
})
const ADMIN = person('u-admin', 'admin', 'ADMIN')
const VIEWER = person('u-viewer', 'viewer', 'VIEWER')

/** The entry a tab of this build writes: the store's persisted fields, in `partialize`'s order. */
const entry = (user: typeof ADMIN | null, token: string | null, refreshToken: string | null = token && `${token}-refresh`) =>
  JSON.stringify({
    state: {
      token, refreshToken, user, isAuthenticated: user !== null, refreshRequiresReauth: false, refreshError: null,
      refreshRetryAt: null, refreshFailureCount: 0, refreshRetryExhausted: false, demoSession: false,
    },
    version: 0,
  })

/**
 * A page load signed in as `user`: the stored session, a fresh store with the reload stubbed, then the cross-tab
 * module main.tsx installs after the first render.
 */
async function loadAs(user: typeof ADMIN | null, token: string | null = user && `${user.username}-access`) {
  localStorage.setItem(KEY, entry(user, token))
  vi.resetModules()
  const store = await import('./authStore')
  const crossTab = await import('./authCrossTab')
  const reload = vi.spyOn(crossTab.sessionReload, 'reload').mockImplementation(() => {})
  crossTab.installAuthCrossTab()
  return { ...store, ...crossTab, reload }
}

/** Another tab's write to storage (this document gets no event for it). */
function otherTabStores(value: string | null) {
  if (value === null) localStorage.removeItem(KEY)
  else localStorage.setItem(KEY, value)
}

/** The `storage` event this tab receives for another tab's write. */
function storageEvent(value: string | null, key: string | null = KEY) {
  window.dispatchEvent(new StorageEvent('storage', { key, newValue: value, storageArea: localStorage }))
}

/** Another tab's write (storage first, then the event this tab receives), or a silent write with no event. */
function otherTabWrites(value: string | null, options: { key?: string | null; event?: boolean } = {}) {
  const key = options.key === undefined ? KEY : options.key
  if (key === KEY) otherTabStores(value)
  if (options.event !== false) storageEvent(value, key)
}

const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0))
const storedUsername = () => JSON.parse(localStorage.getItem(KEY) ?? '{}')?.state?.user?.username ?? null

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  mockGet.mockReset()
  mockPost.mockReset()
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('authStore across tabs: another tab changes the account (D-0019)', () => {
  it('another tab signs in as someone else: this tab reloads, with a note, and writes nothing', async () => {
    const { reload } = await loadAs(ADMIN)
    otherTabStores(entry(VIEWER, 'viewer-access'))
    const writes = vi.spyOn(Storage.prototype, 'setItem')
    storageEvent(entry(VIEWER, 'viewer-access'))
    await settle()
    expect(reload).toHaveBeenCalledTimes(1)
    expect(writes.mock.calls.filter(([key]) => key === KEY)).toEqual([])
    expect(storedUsername()).toBe('viewer')
    // The reload: the tab hydrates the viewer and says why it reloaded, once.
    expect(sessionStorage.getItem('testlookup.session-note')).toBe('1')
    toast.mockClear()
    const after = await loadAs(VIEWER)
    expect(toast.mock.calls.map(([text]) => text)).toEqual(['Another tab of this browser signed in as viewer, so this tab reloaded.'])
    expect(after.consumeSessionNote()).toBeNull()
  })

  it('another tab signs out, or clears the browser storage: this tab reloads', async () => {
    const signedOut = await loadAs(ADMIN)
    otherTabWrites(entry(null, null))
    await settle()
    expect(signedOut.reload).toHaveBeenCalledTimes(1)
    toast.mockClear()
    await loadAs(null)
    expect(toast.mock.calls.map(([text]) => text)).toEqual(['Another tab of this browser signed out, so this tab reloaded.'])

    const cleared = await loadAs(ADMIN)
    localStorage.clear()
    otherTabWrites(null, { key: null })
    await settle()
    expect(cleared.reload).toHaveBeenCalledTimes(1)
  })

  it('another tab refreshes the same account\'s tokens: this tab adopts them, with no reload and no echo', async () => {
    const { useAuthStore, reload } = await loadAs(ADMIN, 'admin-access')
    const rotated = entry(ADMIN, 'admin-access-2', 'admin-refresh-2')
    otherTabStores(rotated)
    const writes = vi.spyOn(Storage.prototype, 'setItem')
    storageEvent(rotated)
    await settle()
    expect(reload).not.toHaveBeenCalled()
    expect(useAuthStore.getState().token).toBe('admin-access-2')
    expect(useAuthStore.getState().refreshToken).toBe('admin-refresh-2')
    // Whatever this tab writes back is the stored string itself, and a browser fires no event for an unchanged
    // value: the other tab hears nothing, so there is no loop.
    for (const [, value] of writes.mock.calls.filter(([key]) => key === KEY)) expect(value).toBe(rotated)
  })

  it('another key changing does nothing', async () => {
    const { reload, useAuthStore } = await loadAs(ADMIN)
    otherTabWrites(JSON.stringify({ state: { theme: 'lab' }, version: 0 }), { key: 'testlookup-theme' })
    await settle()
    expect(reload).not.toHaveBeenCalled()
    expect(useAuthStore.getState().user?.username).toBe('admin')
  })
})

describe('authStore across tabs: a tab whose account was replaced never writes it back (D-0019)', () => {
  /** Storage now holds the viewer, with no event delivered (the race, or a frozen tab). */
  const viewerSignedInSilently = () => otherTabWrites(entry(VIEWER, 'viewer-access'), { event: false })

  it('a late /auth/me answer for the old account', async () => {
    const { useAuthStore, reload } = await loadAs(ADMIN, 'admin-access')
    let answer!: (value: unknown) => void
    mockGet.mockReturnValueOnce(new Promise((resolve) => { answer = resolve }))
    const pending = useAuthStore.getState().fetchUser()
    viewerSignedInSilently()
    answer({ data: ADMIN })
    await pending
    expect(storedUsername()).toBe('viewer')
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('a token refresh for the old account', async () => {
    const { useAuthStore, reload } = await loadAs(ADMIN, 'admin-access')
    mockPost.mockResolvedValueOnce({ data: { access_token: 'admin-access-2', refresh_token: 'admin-refresh-2' } })
    viewerSignedInSilently()
    await useAuthStore.getState().refreshAccessToken()
    expect(storedUsername()).toBe('viewer')
    expect(JSON.parse(localStorage.getItem(KEY) ?? '{}').state.token).toBe('viewer-access')
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('a 401 sign-out of the old account does not sign the new one out', async () => {
    const { useAuthStore, reload } = await loadAs(ADMIN, 'admin-access')
    viewerSignedInSilently()
    useAuthStore.getState().logout()
    expect(storedUsername()).toBe('viewer')
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('a profile save that re-stores the same token', async () => {
    const { useAuthStore, reload } = await loadAs(ADMIN, 'admin-access')
    viewerSignedInSilently()
    useAuthStore.getState().setAuth('admin-access', 'admin-access-refresh', { ...ADMIN, full_name: 'Renamed Admin' })
    expect(storedUsername()).toBe('viewer')
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('a sign-in in this tab (a new token) replaces whatever is stored, with no reload', async () => {
    const { useAuthStore, reload } = await loadAs(ADMIN, 'admin-access')
    viewerSignedInSilently()
    useAuthStore.getState().setAuth('admin-access-new', 'admin-refresh-new', ADMIN)
    expect(storedUsername()).toBe('admin')
    expect(reload).not.toHaveBeenCalled()
  })

  it('a sign-out in this tab, of this tab\'s own account, is stored', async () => {
    const { useAuthStore, reload } = await loadAs(ADMIN, 'admin-access')
    useAuthStore.getState().logout()
    expect(JSON.parse(localStorage.getItem(KEY) ?? '{}').state.user).toBeNull()
    expect(reload).not.toHaveBeenCalled()
  })
})

describe('authStore across tabs: a tab that missed the events checks on its return (D-0019)', () => {
  it('back from the back-forward cache, or made visible, with another account stored: reload', async () => {
    const shown = await loadAs(ADMIN)
    otherTabWrites(entry(VIEWER, 'viewer-access'), { event: false })
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))
    expect(shown.reload).toHaveBeenCalledTimes(1)

    const visible = await loadAs(ADMIN)
    otherTabWrites(entry(VIEWER, 'viewer-access'), { event: false })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(visible.reload).toHaveBeenCalledTimes(1)
  })

  // Phase 3 (the TL-AUTH-003 release journey): the release harness seeds a session into a signed-out tab's storage
  // (`authenticatePage`) and then navigates; a catch-up reload of that tab aborted the navigation. A signed-out tab
  // holds no account that could be stale, so the catch-up checks leave it alone (the live listener still acts).
  it('a signed-out tab is not reloaded by a catch-up check when a session appears in its storage', async () => {
    localStorage.clear()
    vi.resetModules()
    await import('./authStore')
    const crossTab = await import('./authCrossTab')
    const reload = vi.spyOn(crossTab.sessionReload, 'reload').mockImplementation(() => {})
    localStorage.setItem(KEY, entry(ADMIN, 'admin-access'))  // seeded in this document: no storage event
    crossTab.installAuthCrossTab()
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))
    document.dispatchEvent(new Event('visibilitychange'))
    expect(reload).not.toHaveBeenCalled()
  })

  it('with the same account stored: nothing', async () => {
    const { reload } = await loadAs(ADMIN)
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))
    document.dispatchEvent(new Event('visibilitychange'))
    expect(reload).not.toHaveBeenCalled()
  })
})

describe('authStore across tabs: the module installed after the first render (D-0019)', () => {
  it('catches up on a change made before it loaded, and shows the note a reload left', async () => {
    localStorage.setItem(KEY, entry(ADMIN, 'admin-access'))
    vi.resetModules()
    const { useAuthStore } = await import('./authStore')
    otherTabWrites(entry(VIEWER, 'viewer-access'), { event: false })
    // Before the module loads, the guard still keeps the old account out of storage (and cannot reload yet).
    useAuthStore.getState().clearMustChangePassword()
    expect(storedUsername()).toBe('viewer')
    expect(useAuthStore.getState().user?.username).toBe('admin')
    const crossTab = await import('./authCrossTab')
    const reload = vi.spyOn(crossTab.sessionReload, 'reload').mockImplementation(() => {})
    crossTab.installAuthCrossTab()
    expect(reload).toHaveBeenCalledTimes(1)

    // After the reload: the note becomes one toast, once.
    toast.mockClear()
    sessionStorage.setItem('testlookup.session-note', '1')
    localStorage.setItem(KEY, entry(VIEWER, 'viewer-access'))
    vi.resetModules()
    await import('./authStore')
    ;(await import('./authCrossTab')).installAuthCrossTab()
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast.mock.calls[0][0]).toBe('Another tab of this browser signed in as viewer, so this tab reloaded.')
    expect(sessionStorage.getItem('testlookup.session-note')).toBeNull()
  })
})
