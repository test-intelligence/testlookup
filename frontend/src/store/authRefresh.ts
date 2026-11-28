import { api } from '../services/api';
import { AUTH_STORAGE_KEY, sessionHooks, storedSession, storedUserId, useAuthStore } from './authStore';
import { withOriginRefreshLock } from './refreshCoordinator';

export async function refreshSession(
  refreshTokenBeforeLock: string | null,
  userBeforeLock: string | null,
  sessionBeforeLoad: number,
  manualRetry: boolean,
): Promise<string | null> {
  const { getState: get, setState: set } = useAuthStore;
  if (get().sessionGeneration !== sessionBeforeLoad) return null;

  // Notice an account switch even when browser coordination storage is
  // unavailable; no refresh credential is submitted in that state.
  try {
    const persisted = storedSession(localStorage.getItem(AUTH_STORAGE_KEY));
    if (persisted.userId !== userBeforeLock) {
      sessionHooks.superseded?.();
      return null;
    }
    if (persisted.refreshToken !== refreshTokenBeforeLock) {
      await useAuthStore.persist.rehydrate();
      if ((get().user?.id ?? null) !== userBeforeLock) {
        sessionHooks.superseded?.();
        return null;
      }
      return get().token;
    }
  } catch {
    return null;
  }
  if (!refreshTokenBeforeLock) {
    get().logout();
    return null;
  }

  return withOriginRefreshLock(refreshTokenBeforeLock, async () => {
    // Another tab may have completed rotation while this one waited for
    // the origin-wide lock. Adopt its persisted pair instead of redeeming
    // the now-spent token and triggering replay containment.
    try {
      if (storedUserId(localStorage.getItem(AUTH_STORAGE_KEY)) !== userBeforeLock) {
        sessionHooks.superseded?.();
        return null;
      }
    } catch {
      return null;
    }
    await useAuthStore.persist.rehydrate();
    if ((get().user?.id ?? null) !== userBeforeLock) {
      sessionHooks.superseded?.();
      return null;
    }
    if (get().refreshToken !== refreshTokenBeforeLock) return get().token;
    if (get().refreshRequiresReauth) return null;
    if (manualRetry) {
      // Clear retryable failure state only after adopting the latest
      // persisted session under the origin lock. A stale tab must not
      // overwrite another tab's authoritative reauthentication state.
      set({ refreshRetryAt: null, refreshFailureCount: 0, refreshError: null, refreshRetryExhausted: false });
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
      const persisted = storedSession(localStorage.getItem(AUTH_STORAGE_KEY));
      if (get().refreshToken !== refreshToken || get().sessionGeneration !== sessionGeneration || persisted.userId !== userBeforeLock || persisted.refreshToken !== refreshToken) return null;
      set({ token: access_token, refreshToken: refresh_token, refreshRetryAt: null, refreshFailureCount: 0, refreshError: null, refreshRequiresReauth: false, refreshRetryExhausted: false });
      return access_token;
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      // A refresh failure is ambiguous for transient/network/server errors:
      // the rotated token may already have been committed server-side.
      // Only an explicit credential rejection invalidates the session,
      // and only a server-marked retry-safe response permits resubmission.
      const persisted = storedSession(localStorage.getItem(AUTH_STORAGE_KEY));
      if (get().refreshToken !== refreshToken || get().sessionGeneration !== sessionGeneration || persisted.userId !== userBeforeLock || persisted.refreshToken !== refreshToken) return null;
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
  }, null);
}
