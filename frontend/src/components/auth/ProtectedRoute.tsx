import { useEffect, useState } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuthStore } from '../../store/authStore';
import LoadingSpinner from '../ui/LoadingSpinner';

export default function ProtectedRoute() {
  const hasHydrated = useAuthStore((s) => s._hasHydrated);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const token = useAuthStore((s) => s.token);
  const refreshError = useAuthStore((s) => s.refreshError);
  const refreshRequiresReauth = useAuthStore((s) => s.refreshRequiresReauth);
  const fetchUser = useAuthStore((s) => s.fetchUser);
  const logout = useAuthStore((s) => s.logout);
  const location = useLocation();

  // Show a spinner only when we have a token but no cached auth state yet
  // (first-ever load before localStorage is seeded).  Once user + isAuthenticated
  // are persisted, this is false on every subsequent page refresh.
  const [validating, setValidating] = useState(
    () => !!useAuthStore.getState().token && !useAuthStore.getState().isAuthenticated,
  );

  useEffect(() => {
    if (!hasHydrated || !token) return;

    // Always re-verify the token silently on every mount so expired tokens are
    // caught promptly.  If we already have cached auth state (isAuthenticated=true
    // from localStorage) we don't block rendering — the verification runs in the
    // background and only logs out on an explicit 401/403 from the server.
    const hasCachedAuth = useAuthStore.getState().isAuthenticated;
    // Genuine async loading flag around a network token re-verification (not a
    // derive-during-render case): show the spinner only when there's no cached
    // auth state to fall back on.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!hasCachedAuth) setValidating(true);
    fetchUser().finally(() => {
      if (!hasCachedAuth) setValidating(false);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasHydrated]);

  if (!hasHydrated || validating) {
    return (
      <div className="flex items-center justify-center h-screen bg-[var(--color-bg)]">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Self-registered users must set a permanent password before accessing the app.
  // Allow /reset-password itself to avoid an infinite redirect loop.
  const user = useAuthStore.getState().user;
  if (user?.must_change_password && location.pathname !== '/reset-password') {
    return <Navigate to="/reset-password" replace />;
  }

  return (
    <>
      {refreshError && (
        <div role="alert" className="fixed inset-x-0 top-0 z-50 bg-[var(--status-broken-bg)] px-4 py-2 text-center text-sm text-[var(--status-broken)]">
          {refreshError}
          {refreshRequiresReauth && (
            <button className="ml-2 font-semibold underline" onClick={logout}>Sign in</button>
          )}
        </div>
      )}
      <Outlet />
    </>
  );
}
