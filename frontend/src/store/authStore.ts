import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api } from '../services/api';

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
  retryRefresh: () => Promise<string | null>;
  _hasHydrated: boolean;
  setHasHydrated: (v: boolean) => void;
  setAuth: (token: string, refreshToken: string, user: User) => void;
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
      retryRefresh: async () => { set({ refreshRetryAt: null, refreshFailureCount: 0, refreshError: null, refreshRequiresReauth: false, refreshRetryExhausted: false }); return get().refreshAccessToken(); },
      _hasHydrated: false,

      setHasHydrated: (v) => set({ _hasHydrated: v }),

      setAuth: (token, refreshToken, user) =>
        set((state) => ({ token, refreshToken, user: normalizeUser(user), isAuthenticated: true, refreshRetryAt: null, refreshFailureCount: 0, refreshError: null, refreshRequiresReauth: false, refreshRetryExhausted: false, sessionGeneration: state.sessionGeneration + 1 })),

      clearMustChangePassword: () =>
        set((state) =>
          state.user ? { user: { ...state.user, must_change_password: false } } : {}
        ),

      logout: () => set((state) => ({ token: null, refreshToken: null, user: null, isAuthenticated: false, refreshRetryAt: null, refreshFailureCount: 0, refreshError: null, refreshRequiresReauth: false, refreshRetryExhausted: false, sessionGeneration: state.sessionGeneration + 1 })),
      logoutServer: async () => {
        try { await api.post('/api/v1/auth/logout'); }
        catch { /* local cleanup still completes when the server is unavailable */ }
        finally { get().logout(); }
      },

      fetchUser: async () => {
        const { token, logout, sessionGeneration } = get();
        if (!token) return;

        try {
          const res = await api.get<User>('/api/v1/auth/me');
          if (get().token === token && get().sessionGeneration === sessionGeneration) {
            set({ user: normalizeUser(res.data), isAuthenticated: true });
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
        }
      },

      refreshAccessToken: async (): Promise<string | null> => {
        if (refreshInFlight) return refreshInFlight;
        refreshInFlight = (async (): Promise<string | null> => {
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
        })();
        try { return await refreshInFlight; } finally { refreshInFlight = null; }
      },
    }),
    {
      name: 'auth-storage',
      // Persist user + isAuthenticated alongside tokens so that a hard page
      // refresh never clears auth state while valid tokens are still present.
      // fetchUser() still runs in the background on every mount to silently
      // re-validate the token; logout only happens on explicit 401/403.
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
      }),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true);
      },
    }
  )
);
