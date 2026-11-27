import { create } from 'zustand';
import { createJSONStorage, persist, type StateStorage } from 'zustand/middleware';
import { api } from '../services/api';
import { resetReportScopeOnLogout } from './logoutReset';

/** The session's `localStorage` key: one entry shared by every tab of this browser profile. */
export const AUTH_STORAGE_KEY = 'auth-storage';

// ── One account per browser profile (D-0019) ───────────────────────────────
// Every tab hydrates `auth-storage` once and then works from its own copy in memory. A sign-in as someone else,
// or a sign-out, in another tab applies to this tab too: it reloads, and until then it never writes its old
// account back over the new one (the next tab or reload would hydrate it). Another tab refreshing the same
// account's tokens is adopted without a reload. Only the write guard is here (it must be in place before the
// store's first write); the reload, the listeners and the note are `authCrossTab.ts`, loaded after the first
// render (the eager bundle budget), which takes `sessionHooks.superseded` and, on install, catches up on a write
// the guard skipped before it loaded.

/** The user id this tab last read from or wrote to `auth-storage`; `null` when signed out. */
let tabUserId: string | null = null;
/** True only during `setAuth`'s write when it brings a new token: a sign-in here, which replaces any account. */
let signingIn = false;
/** What the guard calls when it skips a write: `authCrossTab` reloads the tab. */
export const sessionHooks: { superseded?: () => void } = {};

/** The user id an `auth-storage` value names; `null` when signed out, empty or unreadable. */
export function storedUserId(raw: string | null): string | null {
  try {
    return (raw && JSON.parse(raw).state?.user?.id) || null;
  } catch {
    return null;
  }
}

interface StoredSession {
  userId: string | null;
  refreshToken: string | null;
}

/** The persisted token pair another tab may have rotated while this tab waited. */
function storedSession(raw: string | null): StoredSession | null {
  try {
    const state = raw ? JSON.parse(raw).state : null;
    if (!state) return null;
    return {
      userId: state.user?.id ?? null,
      refreshToken: state.refreshToken ?? null,
    };
  } catch {
    return null;
  }
}

const REFRESH_LOCK_NAME = 'testlookup.auth.refresh';
interface BrowserLockManager {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
}

/** Serialize refresh-token use across tabs when the browser supports Web Locks. */
function withBrowserRefreshLock<T>(callback: () => Promise<T>): Promise<T> {
  const locks = typeof navigator === 'undefined'
    ? undefined
    : (navigator as unknown as { locks?: BrowserLockManager }).locks;
  return locks?.request ? locks.request(REFRESH_LOCK_NAME, callback) : callback();
}

/** `auth-storage`, guarded: a tab never writes over an entry another tab gave to a different account. */
const guardedLocalStorage: StateStorage = {
  getItem: (name) => {
    const raw = localStorage.getItem(name);
    tabUserId = storedUserId(raw);
    return raw;
  },
  setItem: (name, value) => {
    const current = localStorage.getItem(name);
    if (!signingIn && current !== null && storedUserId(current) !== tabUserId) return sessionHooks.superseded?.();
    localStorage.setItem(name, value);
    tabUserId = storedUserId(value);
  },
  removeItem: (name) => localStorage.removeItem(name),
};

export interface User {
  id: string;
  email: string;
  username: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  must_change_password: boolean;
  avatar_color: string | null;
}

interface AuthState {
  token: string | null;
  refreshToken: string | null;
  user: User | null;
  isAuthenticated: boolean;
  refreshRetryAt: number | null;
  refreshFailureCount: number;
  refreshError: string | null;
  refreshRequiresReauth: boolean;
  refreshRetryExhausted: boolean;
  sessionGeneration: number;
  /**
   * When the server last confirmed this session (W25a HYG-07): stamped by
   * `setAuth` (every sign-in tail fetched `/auth/me` just before it) and by a
   * successful `fetchUser`; cleared by `logout`. NOT persisted, so a hard
   * reload has no stamp and re-verifies once. `ProtectedRoute` skips its
   * re-verify while the stamp is fresh.
   */
  lastVerifiedAt: number | null;
  /**
   * This session was opened by the public read-only demo's one-click sign-in
   * (`POST /auth/demo-login`), the only way into that deployment. The shell
   * shows the demo banner from it, so no deployment pays a request per page to
   * ask "is this the demo?". False for every ordinary sign-in; cleared by `logout`.
   */
  demoSession: boolean;
  retryRefresh: () => Promise<string | null>;
  _hasHydrated: boolean;
  setHasHydrated: (v: boolean) => void;
  setAuth: (token: string, refreshToken: string, user: User, options?: { demo?: boolean }) => void;
  clearMustChangePassword: () => void;
  logout: () => void;
  logoutServer: () => Promise<void>;
  fetchUser: () => Promise<void>;
  refreshAccessToken: () => Promise<string | null>;
}

function normalizeRole(role: string | null | undefined): string {
  const rawRole = String(role ?? 'VIEWER').trim();
  return rawRole.startsWith('UserRole.') ? rawRole.slice('UserRole.'.length) : rawRole;
}

function normalizeUser(user: User): User {
  return {
    ...user,
    role: normalizeRole(user.role),
  };
}

let refreshInFlight: Promise<string | null> | null = null;
/**
 * One `GET /api/v1/auth/me` per token at a time: a second `fetchUser()` while
 * the first is pending for the SAME token returns the same promise (the dev
 * server's StrictMode runs every mount effect twice). A different token never
 * shares. Cleared when the read settles.
 */
let fetchUserInFlight: { token: string; promise: Promise<void> } | null = null;

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      refreshToken: null,
      user: null,
      isAuthenticated: false,
      refreshRetryAt: null,
      refreshFailureCount: 0,
      refreshError: null,
      refreshRequiresReauth: false,
      refreshRetryExhausted: false,
      sessionGeneration: 0,
      lastVerifiedAt: null,
      demoSession: false,
      retryRefresh: async () => { set({ refreshRetryAt: null, refreshFailureCount: 0, refreshError: null, refreshRequiresReauth: false, refreshRetryExhausted: false }); return get().refreshAccessToken(); },
      _hasHydrated: false,

      setHasHydrated: (v) => set({ _hasHydrated: v }),

      setAuth: (token, refreshToken, user, options) => {
        // D-0019: a new token is a sign-in here, which replaces whatever account is stored; the same token (a profile
        // save re-storing the user) stays under the guard.
        signingIn = token !== get().token;
        set((state) => ({ token, refreshToken, user: normalizeUser(user), isAuthenticated: true, refreshRetryAt: null, refreshFailureCount: 0, refreshError: null, refreshRequiresReauth: false, refreshRetryExhausted: false, sessionGeneration: state.sessionGeneration + 1, lastVerifiedAt: Date.now(), demoSession: options?.demo === true }));
        signingIn = false;
      },

      clearMustChangePassword: () =>
        set((state) =>
          state.user ? { user: { ...state.user, must_change_password: false } } : {}
        ),

      logout: () => {
        set((state) => ({ token: null, refreshToken: null, user: null, isAuthenticated: false, refreshRetryAt: null, refreshFailureCount: 0, refreshError: null, refreshRequiresReauth: false, refreshRetryExhausted: false, sessionGeneration: state.sessionGeneration + 1, lastVerifiedAt: null, demoSession: false }));
        // Security M1: the saved release filter (`tl.release-filter`, and a
        // `tl.suite-filter` an older build may have left) belongs to THIS
        // user's session — the next sign-in on this browser must not inherit
        // it. Through a registry (store/logoutReset.ts), not an import of the
        // scope store: this store is eager.
        resetReportScopeOnLogout();
      },
      logoutServer: async () => {
        try { await api.post('/api/v1/auth/logout'); }
        catch { /* local cleanup still completes when the server is unavailable */ }
        finally { get().logout(); }
      },

      fetchUser: () => {
        const { token, logout, sessionGeneration } = get();
        if (!token) return Promise.resolve();
        if (fetchUserInFlight && fetchUserInFlight.token === token) return fetchUserInFlight.promise;

        // Registered BEFORE the read starts, so a read that settles at once
        // still clears its own entry (and only its own).
        const entry: { token: string; promise: Promise<void> } = { token, promise: Promise.resolve() };
        fetchUserInFlight = entry;
        entry.promise = (async () => {
          try {
            const res = await api.get<User>('/api/v1/auth/me');
            if (get().token === token && get().sessionGeneration === sessionGeneration) {
              set({ user: normalizeUser(res.data), isAuthenticated: true, lastVerifiedAt: Date.now() });
            }
          } catch (err) {
            // Only clear auth on explicit auth rejections (401/403).
            // Network errors (status undefined) or server errors (5xx) should not
            // log the user out — they may be a transient connectivity issue and
            // the stored token may still be valid once the backend recovers.
            const status = (err as { response?: { status?: number } })?.response?.status;
            if ((status === 401 || status === 403) && get().token === token && get().sessionGeneration === sessionGeneration && !get().refreshRequiresReauth && !get().refreshError) {
              logout();
            }
          } finally {
            if (fetchUserInFlight === entry) fetchUserInFlight = null;
          }
        })();
        return entry.promise;
      },

      refreshAccessToken: async (): Promise<string | null> => {
        if (refreshInFlight) return refreshInFlight;
        refreshInFlight = withBrowserRefreshLock(async (): Promise<string | null> => {
          // A tab that rotated while this one waited owns the only live refresh
          // token. Adopt its pair instead of replaying the now-spent token.
          const beforeLock = get();
          const persisted = storedSession(localStorage.getItem(AUTH_STORAGE_KEY));
          if (persisted && beforeLock.user && persisted.userId !== beforeLock.user.id) {
            sessionHooks.superseded?.();
            return null;
          }
          if (
            persisted?.userId === beforeLock.user?.id
            && persisted.refreshToken
            && persisted.refreshToken !== beforeLock.refreshToken
          ) {
            await useAuthStore.persist.rehydrate();
            const adopted = get();
            if (adopted.refreshToken === persisted.refreshToken) return adopted.token;
            return null;
          }

          const { refreshToken, logout, refreshRetryAt, refreshRequiresReauth, refreshRetryExhausted, sessionGeneration } = get();
          if (refreshRequiresReauth || refreshRetryExhausted || (refreshRetryAt && Date.now() < refreshRetryAt)) return null;
          if (!refreshToken) {
            logout();
            return null;
          }

          try {
            const res = await api.post<{ access_token: string; refresh_token: string }>(
              '/api/v1/auth/refresh',
              { refresh_token: refreshToken },
            );
            const { access_token, refresh_token } = res.data;
            if (get().refreshToken !== refreshToken || get().sessionGeneration !== sessionGeneration) return null;
            set({ token: access_token, refreshToken: refresh_token, refreshRetryAt: null, refreshFailureCount: 0, refreshError: null, refreshRequiresReauth: false, refreshRetryExhausted: false });
            return access_token;
          } catch (err) {
            const status = (err as { response?: { status?: number } })?.response?.status;
            // A refresh failure is ambiguous for transient/network/server errors:
            // the rotated token may already have been committed server-side.
            // Preserve credentials and let the next request retry. Only an
            // explicit credential rejection means the session is invalid.
            if (get().refreshToken !== refreshToken || get().sessionGeneration !== sessionGeneration) return null;
            if (status === 401 || status === 403) logout();
            else {
              const failures = get().refreshFailureCount + 1;
              const headers = (err as { response?: { headers?: { get?: (name: string) => string | undefined } & Record<string, string> } })?.response?.headers;
              const header = (name: string) => headers?.get?.(name) ?? headers?.[name.toLowerCase()] ?? headers?.[name];
              const retryAfter = Number(header('retry-after'));
              const retrySafe = header('x-refresh-retry-safe') === '1';
              if (status === 429 || retrySafe) {
                if (failures >= 6) {
                  set({ refreshFailureCount: failures, refreshRetryAt: Number.POSITIVE_INFINITY, refreshRetryExhausted: true, refreshError: 'Session refresh is still unavailable. Try again when the service recovers.' });
                  return null;
                }
                const delay = Number.isFinite(retryAfter) && retryAfter > 0
                  ? Math.min(60_000, Math.max(1_000, retryAfter * 1000))
                  : Math.min(60_000, 1000 * 2 ** Math.min(failures - 1, 6));
                set({ refreshFailureCount: failures, refreshRetryAt: Date.now() + delay, refreshRetryExhausted: false, refreshError: 'Session refresh is temporarily unavailable. Retry after the cooldown.' });
              } else {
                // Other responses may be post-commit and unsafe to replay.
                set({ refreshFailureCount: failures, refreshRetryAt: Number.POSITIVE_INFINITY, refreshRequiresReauth: true, refreshError: 'Session refresh could not be confirmed. Sign in again to continue.' });
              }
            }
            return null;
          }
        });
        try { return await refreshInFlight; } finally { refreshInFlight = null; }
      },
    }),
    {
      name: AUTH_STORAGE_KEY,
      storage: createJSONStorage(() => guardedLocalStorage),
      // Persist user + isAuthenticated alongside tokens so that a hard page
      // refresh never clears auth state while valid tokens are still present.
      // fetchUser() still runs in the background on every protected mount to
      // silently re-validate the token (skipped within 30 s of a verification,
      // `lastVerifiedAt`, which is deliberately NOT persisted); logout only
      // happens on explicit 401/403.
      partialize: (state) => ({
        token: state.token,
        refreshToken: state.refreshToken,
        user: state.user,
        isAuthenticated: state.isAuthenticated,
        refreshRequiresReauth: state.refreshRequiresReauth,
        refreshError: state.refreshError,
        refreshRetryAt: state.refreshRetryAt,
        refreshFailureCount: state.refreshFailureCount,
        refreshRetryExhausted: state.refreshRetryExhausted,
        demoSession: state.demoSession,
      }),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true);
      },
    }
  )
);
