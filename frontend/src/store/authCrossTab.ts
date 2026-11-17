/**
 * One account per browser profile, across tabs (D-0019): the listeners, the account check and the note. `authStore.ts`
 * holds the write guard (a tab never writes its old account over another tab's); this module is loaded after the
 * first render (main.tsx), so it costs the eager bundle little, and on install it compares the stored account once,
 * so a change made before it loaded is not missed.
 */
import toast from 'react-hot-toast'
import { AUTH_STORAGE_KEY, sessionHooks, storedUserId, useAuthStore } from './authStore'

/** Set by a tab just before it reloads because another tab changed the account; read once after the reload. */
const SESSION_NOTE_KEY = 'testlookup.session-note'

/** The reload, behind an object so a test can stub it (jsdom has no navigation). */
export const sessionReload = { reload: (): void => window.location.reload() }

let superseded = false

/** Another tab signed in as someone else, or signed out: drop everything this tab holds for the old account. */
export function onSessionSuperseded(): void {
  if (superseded) return
  superseded = true
  try {
    sessionStorage.setItem(SESSION_NOTE_KEY, '1')
  } catch {
    /* the note is a courtesy: storage may be unavailable */
  }
  sessionReload.reload()
}

/**
 * The stored account against this tab's. Another account, or none: reload. The same account (another tab refreshed
 * its tokens): adopt them when `adopt` (`rehydrate()` sets the store with zustand's raw `set`, so it writes nothing
 * back, and the `_hasHydrated` write that follows stores an identical value, which fires no `storage` event).
 */
export function checkStoredAccount(adopt: boolean): void {
  const stored = storedUserId(localStorage.getItem(AUTH_STORAGE_KEY))
  const mine = useAuthStore.getState().user?.id ?? null
  if (stored !== mine) onSessionSuperseded()
  else if (adopt && stored !== null) void useAuthStore.persist.rehydrate()
}

/** A `storage` event: another tab wrote `auth-storage`, or cleared this browser's storage (`key === null`). */
export function syncAuthFromStorage(event: StorageEvent): void {
  if (event.key !== AUTH_STORAGE_KEY && event.key !== null) return
  if (event.storageArea !== null && event.storageArea !== localStorage) return
  checkStoredAccount(true)
}

/** Why this tab reloaded, if it was because another tab changed the account (read once, after the reload). */
export function consumeSessionNote(): string | null {
  try {
    if (sessionStorage.getItem(SESSION_NOTE_KEY) === null) return null
    sessionStorage.removeItem(SESSION_NOTE_KEY)
  } catch {
    return null
  }
  const who = useAuthStore.getState().user?.username
  return who
    ? `Another tab of this browser signed in as ${who}, so this tab reloaded.`
    : 'Another tab of this browser signed out, so this tab reloaded.'
}

/** One set of listeners per window, whatever re-evaluates this module (HMR, `vi.resetModules`). */
const AUTH_SYNC = Symbol.for('testlookup.auth.storage-sync')
interface AuthSync {
  storage: (event: StorageEvent) => void
  pageshow: (event: PageTransitionEvent) => void
  visibility: () => void
}

/** Listen for another tab's account changes, catch up on any made before now, and say why this tab reloaded. */
export function installAuthCrossTab(): void {
  const slot = window as unknown as Record<symbol, AuthSync | undefined>
  const previous = slot[AUTH_SYNC]
  if (previous) {
    window.removeEventListener('storage', previous.storage)
    window.removeEventListener('pageshow', previous.pageshow)
    document.removeEventListener('visibilitychange', previous.visibility)
  }
  // A tab restored from the back-forward cache, or thawed, missed the events: compare once on its return.
  const sync: AuthSync = {
    storage: syncAuthFromStorage,
    pageshow: (event) => { if (event.persisted) checkStoredAccount(false) },
    visibility: () => { if (document.visibilityState === 'visible') checkStoredAccount(false) },
  }
  slot[AUTH_SYNC] = sync
  sessionHooks.superseded = onSessionSuperseded
  window.addEventListener('storage', sync.storage)
  window.addEventListener('pageshow', sync.pageshow)
  document.addEventListener('visibilitychange', sync.visibility)
  checkStoredAccount(false)
  const note = consumeSessionNote()
  if (note) toast(note, { icon: 'ℹ️', duration: 8000 })
}
