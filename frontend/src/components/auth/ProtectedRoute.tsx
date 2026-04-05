import { useEffect, useState } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuthStore } from '../../store/authStore';
import LoadingSpinner from '../ui/LoadingSpinner';

export default function ProtectedRoute() {
  const hasHydrated = useAuthStore((s) => s._hasHydrated);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const token = useAuthStore((s) => s.token);
  const fetchUser = useAuthStore((s) => s.fetchUser);
  const location = useLocation();

  // Pre-initialise to true when a token exists but isAuthenticated hasn't been
  // restored yet (page-refresh scenario).  This prevents the first render from
  // immediately redirecting to /login before fetchUser() has a chance to run.
  const [validating, setValidating] = useState(
    () => !!useAuthStore.getState().token && !useAuthStore.getState().isAuthenticated,
  );

  useEffect(() => {
    if (hasHydrated && token && !isAuthenticated) {
      setValidating(true);
      fetchUser().finally(() => setValidating(false));
    }
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

  return <Outlet />;
}
