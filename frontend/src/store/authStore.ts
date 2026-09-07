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
  _hasHydrated: boolean;
  setHasHydrated: (v: boolean) => void;
  setAuth: (token: string, refreshToken: string, user: User) => void;
  clearMustChangePassword: () => void;
  logout: () => void;
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
      _hasHydrated: false,

      setHasHydrated: (v) => set({ _hasHydrated: v }),

      setAuth: (token, refreshToken, user) =>
        set({ token, refreshToken, user: normalizeUser(user), isAuthenticated: true, refreshRetryAt: null, refreshFailureCount: 0, refreshError: null, refreshRequiresReauth: false }),

      clearMustChangePassword: () =>
        set((state) =>
          state.user ? { user: { ...state.user, must_change_password: false } } : {}
        ),

      logout: () => set({ token: null, refreshToken: null, user: null, isAuthenticated: false, refreshRetryAt: null, refreshFailureCount: 0, refreshError: null, refreshRequiresReauth: false }),

      fetchUser: async () => {
        const { token, logout } = get();
        if (!token) return;

        try {
          const res = await api.get<User>('/api/v1/auth/me');
          set({ user: normalizeUser(res.data), isAuthenticated: true });
        } catch (err) {
          // Only clear auth on explicit auth rejections (401/403).
          // Network errors (status undefined) or server errors (5xx) should not
          // log the user out — they may be a transient connectivity issue and
          // the stored token may still be valid once the backend recovers.
          const status = (err as { response?: { status?: number } })?.response?.status;
          if ((status === 401 || status === 403) && !get().refreshRequiresReauth && !get().refreshError) {
            logout();
          }
        }
      },

      refreshAccessToken: async (): Promise<string | null> => {
        const { refreshToken, logout, refreshRetryAt, refreshRequiresReauth } = get();
        if (refreshRequiresReauth || (refreshRetryAt && Date.now() < refreshRetryAt)) return null;
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
          set({ token: access_token, refreshToken: refresh_token, refreshRetryAt: null, refreshFailureCount: 0, refreshError: null, refreshRequiresReauth: false });
          return access_token;
        } catch (err) {
          const status = (err as { response?: { status?: number } })?.response?.status;
          // A refresh failure is ambiguous for transient/network/server errors:
          // the rotated token may already have been committed server-side.
          // Preserve credentials and let the next request retry. Only an
          // explicit credential rejection means the session is invalid.
          if (status === 401 || status === 403) logout();
          else {
            const failures = get().refreshFailureCount + 1;
            // The backend rotates the refresh token before responding. Any
            // non-auth response may therefore be post-commit and unsafe to
            // replay; preserve the shell but require explicit sign-in.
            set({ refreshFailureCount: failures, refreshRetryAt: Number.POSITIVE_INFINITY, refreshRequiresReauth: true, refreshError: 'Session refresh could not be confirmed. Sign in again to continue.' });
          }
          return null;
        }
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
      }),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true);
      },
    }
  )
);
