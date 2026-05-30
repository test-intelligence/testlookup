import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import { useAuthStore } from '../store/authStore';
import LoadingSpinner from '../components/ui/LoadingSpinner';
import AppLogo from '@/components/ui/AppLogo';

/**
 * Forced first-time password reset.
 *
 * Shown automatically after a self-registered user's first login
 * (must_change_password=true on their account). Does not require the
 * registration password — just the new password twice.
 */
export default function ResetPasswordPage() {
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const user = useAuthStore((s) => s.user);
  const clearMustChangePassword = useAuthStore((s) => s.clearMustChangePassword);
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (newPassword !== confirmPassword) {
      toast.error('Passwords do not match');
      return;
    }
    if (newPassword.length < 8) {
      toast.error('Password must be at least 8 characters');
      return;
    }

    setIsSubmitting(true);
    try {
      await api.post('/api/v1/auth/first-time-reset', {
        new_password: newPassword,
        confirm_password: confirmPassword,
      });

      clearMustChangePassword();
      toast.success('Password updated successfully. Welcome!');
      navigate('/overview', { replace: true });
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? 'Failed to update password. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col justify-center py-12 sm:px-6 lg:px-8 theme-bg">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <div className="flex justify-center mb-6">
          <AppLogo className="text-[42px]" />
        </div>
        <h2 className="text-center text-3xl font-extrabold text-[var(--color-text)]">Set your password</h2>
        <p className="mt-2 text-center text-sm text-[var(--color-text-muted)]">
          Hi {user?.full_name ?? user?.username}! Please set a permanent password before continuing.
        </p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
        <div className="theme-bg-card py-8 px-4 shadow sm:rounded-lg sm:px-10 border theme-border">
          <div className="mb-5 rounded-md bg-[var(--color-bg-secondary)]/80 border border-[var(--color-border-light)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
            Your account was registered with a temporary password. Choose a strong permanent
            password to continue.
          </div>

          <form className="space-y-5" onSubmit={handleSubmit}>
            <div>
              <label htmlFor="new-password" className="block text-sm font-medium text-[var(--color-text-secondary)]">
                New Password
              </label>
              <input
                id="new-password"
                type="password"
                required
                minLength={8}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="mt-1 appearance-none block w-full px-3 py-2 border border-[var(--color-border)] rounded-md shadow-sm placeholder-[var(--color-text-faint)] text-[var(--color-text)] bg-[var(--color-bg-input)] focus:outline-none focus:ring-[var(--color-ring)] focus:border-[var(--color-ring)] sm:text-sm"
                placeholder="At least 8 characters"
                autoFocus
              />
            </div>

            <div>
              <label htmlFor="confirm-password" className="block text-sm font-medium text-[var(--color-text-secondary)]">
                Confirm Password
              </label>
              <input
                id="confirm-password"
                type="password"
                required
                minLength={8}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="mt-1 appearance-none block w-full px-3 py-2 border border-[var(--color-border)] rounded-md shadow-sm placeholder-[var(--color-text-faint)] text-[var(--color-text)] bg-[var(--color-bg-input)] focus:outline-none focus:ring-[var(--color-ring)] focus:border-[var(--color-ring)] sm:text-sm"
                placeholder="Repeat your new password"
              />
            </div>

            <button
              type="submit"
              disabled={isSubmitting}
              className="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-[var(--color-btn-primary-text)] bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--color-ring)] focus:ring-offset-[var(--color-bg)] transition-colors disabled:opacity-50"
            >
              {isSubmitting ? <LoadingSpinner size="sm" /> : 'Set Password & Continue'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
