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
}

interface AuthState {
  token: string | null;
  refreshToken: string | null;
  user: User | null;
  isAuthenticated: boolean;
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
      _hasHydrated: false,

      setHasHydrated: (v) => set({ _hasHydrated: v }),

      setAuth: (token, refreshToken, user) =>
        set({ token, refreshToken, user: normalizeUser(user), isAuthenticated: true }),

      clearMustChangePassword: () =>
        set((state) =>
          state.user ? { user: { ...state.user, must_change_password: false } } : {}
        ),

      logout: () => set({ token: null, refreshToken: null, user: null, isAuthenticated: false }),

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
          if (status === 401 || status === 403) {
            logout();
          }
        }
      },

      refreshAccessToken: async (): Promise<string | null> => {
        const { refreshToken, logout } = get();
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
          set({ token: access_token, refreshToken: refresh_token });
          return access_token;
        } catch {
          logout();
          return null;
        }
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({
        token: state.token,
        refreshToken: state.refreshToken,
      }),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true);
      },
    }
  )
);
