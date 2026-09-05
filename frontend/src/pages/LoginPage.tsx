import { useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import toast from 'react-hot-toast';
import { Zap, UserPlus, LogIn, Fingerprint, ShieldAlert } from 'lucide-react';
import { api } from '../services/api';
import { useAuthStore } from '../store/authStore';
import AppLogo from '@/components/ui/AppLogo';
import LoadingSpinner from '../components/ui/LoadingSpinner';
import { type SSOStatus, getSSOStatus } from '../services/ssoService';
import { isSafeExternalUrl } from '@/utils/safeUrl';
import { fetchCurrentUser, loginWithPassword } from '@/services/authService';
import { startMfaEnrollment } from '@/hooks/useMfaStatus';
import MfaChallengePanel from '@/components/mfa/MfaChallengePanel';
import MfaEnrollPanel from '@/components/mfa/MfaEnrollPanel';
import {
  isMfaChallenge,
  isMfaEnrollmentRequired,
  type MfaChallengeResponse,
  type MfaEnrollStartResponse,
  type MfaEnrollmentRequiredResponse,
  type TokenResponse,
} from '@/types/mfa';
import { describeMfaError, formatRetryAfter } from '@/utils/mfaErrors';

const DEV_ROLES = [
  { label: 'Admin',       value: 'admin',       colour: 'text-[var(--status-failed)]' },
  { label: 'QA Lead',     value: 'qa_lead',     colour: 'text-[var(--status-flaky)]' },
  { label: 'QA Engineer', value: 'qa_engineer', colour: 'text-[var(--color-text)]' },
  { label: 'Tester',      value: 'tester',      colour: 'text-[var(--status-passed)]' },
  { label: 'Viewer',      value: 'viewer',      colour: 'text-[var(--color-text-muted)]' },
];

/**
 * `POST /auth/login` now has three possible 200 bodies, so the page's mode
 * machine grew two steps to match them: `mfa-challenge` (the user has MFA on)
 * and `mfa-enroll` (workspace policy requires MFA and they have not set it up
 * yet — they cannot get in until they do).
 */
type LoginMode = 'login' | 'register' | 'mfa-challenge' | 'mfa-enroll';

export default function LoginPage() {
  const [mode, setMode] = useState<LoginMode>('login');

  // Login state
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [devLoggingInAs, setDevLoggingInAs] = useState<string | null>(null);
  const [loginNotice, setLoginNotice] = useState<string | null>(null);

  // MFA interstitial state
  const [challenge, setChallenge] = useState<MfaChallengeResponse | null>(null);
  const [enrollHandoff, setEnrollHandoff] = useState<MfaEnrollmentRequiredResponse | null>(null);
  const [enrollment, setEnrollment] = useState<MfaEnrollStartResponse | null>(null);

  // Register state
  const [regEmail, setRegEmail] = useState('');
  const [regUsername, setRegUsername] = useState('');
  const [regFullName, setRegFullName] = useState('');
  const [regPassword, setRegPassword] = useState('');
  const [regConfirmPassword, setRegConfirmPassword] = useState('');
  const [isRegistering, setIsRegistering] = useState(false);

  // SSO status
  const [ssoStatus, setSsoStatus] = useState<SSOStatus | null>(null);

  const setAuth = useAuthStore((state) => state.setAuth);
  const navigate = useNavigate();
  const location = useLocation();

  const from = location.state?.from?.pathname || '/overview';
  const isDev = import.meta.env.DEV;

  // Live confirm-password check. The submit handler still blocks on a mismatch,
  // but a toast is ephemeral and detached from the field — a screen-reader user
  // (or anyone who blinks) can miss why registration refused. Surface it inline
  // and tie it to the input so assistive tech announces it and points at the
  // field that needs fixing. Only flag once they've started confirming.
  const passwordsMismatch =
    mode === 'register' &&
    regConfirmPassword.length > 0 &&
    regPassword !== regConfirmPassword;

  useEffect(() => {
    getSSOStatus().then(setSsoStatus).catch(() => { /* SSO not available */ });
  }, []);

  /** Shared tail of every successful sign-in — normal, post-challenge, or post-enrollment. */
  const completeSignIn = async (tokens: TokenResponse) => {
    const user = await fetchCurrentUser(tokens.access_token);
    setAuth(tokens.access_token, tokens.refresh_token, user);
    setChallenge(null);
    setEnrollHandoff(null);
    setEnrollment(null);
    setPassword('');
    setMode('login');
    toast.success('Logged in successfully');
    // ProtectedRoute will redirect to /reset-password if must_change_password=true
    navigate(from, { replace: true });
  };

  /** Return to the password step and say why. */
  const backToPassword = (reason: string | null) => {
    setChallenge(null);
    setEnrollHandoff(null);
    setEnrollment(null);
    setPassword('');
    setLoginNotice(reason);
    setMode('login');
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) return;

    setIsSubmitting(true);
    setLoginNotice(null);
    try {
      const res = await loginWithPassword(username, password);

      if (isMfaChallenge(res)) {
        setChallenge(res);
        setMode('mfa-challenge');
        return;
      }

      if (isMfaEnrollmentRequired(res)) {
        // Fetch the secret up front so the enrollment panel is a pure render —
        // it never has to kick off a request from an effect.
        const started = await startMfaEnrollment(res.enrollment_token);
        setEnrollHandoff(res);
        setEnrollment(started);
        setMode('mfa-enroll');
        return;
      }

      await completeSignIn(res);
    } catch (err) {
      // Distinguish the failures that are NOT "wrong password": a locked
      // account (429 with Retry-After) and a backend that cannot verify at all
      // (503). Telling those users their password is wrong sends them off to
      // reset a credential that was fine.
      const info = describeMfaError(err);
      if (info.kind === 'locked_out') {
        const wait =
          info.retryAfterSeconds !== undefined
            ? ` Try again in about ${formatRetryAfter(info.retryAfterSeconds)}.`
            : '';
        setLoginNotice(`${info.message}${wait}`);
        toast.error('Account temporarily locked');
      } else if (info.kind === 'rate_limited' || info.kind === 'unavailable') {
        setLoginNotice(info.message);
        toast.error('Sign-in unavailable right now');
      } else if (info.status === 403) {
        setLoginNotice(info.message);
        toast.error(info.message);
      } else {
        toast.error('Invalid username or password');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (regPassword !== regConfirmPassword) {
      toast.error('Passwords do not match');
      return;
    }

    setIsRegistering(true);
    try {
      await api.post('/api/v1/auth/register', {
        email: regEmail,
        username: regUsername,
        full_name: regFullName || undefined,
        password: regPassword,
      });

      toast.success('Account created! You will be prompted to set a new password after your first login.');
      // Switch to login and pre-fill email
      setUsername(regEmail);
      setPassword('');
      setMode('login');
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? 'Registration failed. Please try again.');
    } finally {
      setIsRegistering(false);
    }
  };

  /** Dev-only: log in as a seeded role without credentials. */
  const handleDevLogin = async (role: string) => {
    if (!isDev) return;
    setDevLoggingInAs(role);
    try {
      const res = await api.post(`/api/v1/auth/dev-login?role=${encodeURIComponent(role)}`, {});
      const { access_token, refresh_token } = res.data;
      const userRes = await api.get('/api/v1/auth/me', {
        headers: { Authorization: `Bearer ${access_token}` },
      });
      setAuth(access_token, refresh_token, userRes.data);
      toast.success(`Logged in as ${role.replace('_', ' ')}`);
      navigate(from, { replace: true });
    } catch {
      toast.error('Dev login unavailable — ensure APP_ENV=development and DEV_AUTO_LOGIN_ENABLED=true in backend');
    } finally {
      setDevLoggingInAs(null);
    }
  };

  return (
    <div className="min-h-screen flex flex-col justify-center py-12 sm:px-6 lg:px-8 theme-bg">
      <div className="sm:mx-auto sm:w-full sm:max-w-md flex flex-col items-center">
        <div className="mb-6">
          <AppLogo className="text-[42px]" />
        </div>
        <h2 className="mt-2 text-center text-3xl font-extrabold text-[var(--color-text)]">
          {mode === 'register'
            ? 'Create an account'
            : mode === 'mfa-challenge'
              ? 'One more step'
              : mode === 'mfa-enroll'
                ? 'Set up two-factor authentication'
                : 'Sign in to TestLookup'}
        </h2>
        <p className="mt-2 text-center text-sm text-[var(--color-text-muted)]">
          Instant Answers from Your Test Results
        </p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md space-y-4">

        {/* ── Dev quick-login panel (Vite dev mode only) ── */}
        {isDev && mode === 'login' && (
          <div className="bg-[var(--status-broken-bg)]/40 border border-[var(--status-broken-bd)]/50 rounded-lg px-4 py-3">
            <p className="text-xs font-semibold text-[var(--status-broken)] uppercase tracking-wide mb-2">
              Dev environment — quick login (no password required)
            </p>
            <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
              {DEV_ROLES.map(({ label, value, colour }) => (
                <button
                  key={value}
                  type="button"
                  disabled={devLoggingInAs !== null}
                  onClick={() => handleDevLogin(value)}
                  className="flex items-center justify-center gap-1.5 text-xs px-2 py-1.5 rounded bg-[var(--status-broken-bg)]/30 hover:bg-[var(--status-broken-bg)]/60 border border-[var(--status-broken-bd)]/40 transition-colors disabled:opacity-50"
                >
                  {devLoggingInAs === value ? (
                    <LoadingSpinner size="sm" />
                  ) : (
                    <>
                      <Zap className="h-3 w-3 text-[var(--status-broken)]" />
                      <span className={colour}>{label}</span>
                    </>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="theme-bg-card py-8 px-4 shadow sm:rounded-lg sm:px-10 border theme-border">

          {/* ── Carry-over notice (lockout, expired challenge, SSO-required …) ── */}
          {mode === 'login' && loginNotice && (
            <div
              role="alert"
              className="mb-5 flex items-start gap-2 rounded-md border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg-soft)] px-3 py-2 text-xs text-[var(--color-text)]"
            >
              <ShieldAlert className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-[var(--status-broken)]" />
              <span>{loginNotice}</span>
            </div>
          )}

          {/* ── MFA challenge step ── */}
          {mode === 'mfa-challenge' && challenge && (
            <MfaChallengePanel
              challenge={challenge}
              accountLabel={username}
              onVerified={completeSignIn}
              onExpired={backToPassword}
              onCancel={() => backToPassword(null)}
            />
          )}

          {/* ── Forced enrollment step ── */}
          {mode === 'mfa-enroll' && enrollment && enrollHandoff && (
            <div className="space-y-5">
              <div className="rounded-md border border-[var(--color-border-light)] bg-[var(--color-bg-secondary)]/80 px-3 py-2 text-xs text-[var(--color-text-secondary)]">
                {enrollHandoff.required_for_role ? (
                  <>
                    This workspace requires two-factor authentication for{' '}
                    <strong>{enrollHandoff.required_for_role.replace(/_/g, ' ').toLowerCase()}</strong>{' '}
                    accounts and above. Finish setting it up to sign in — you only do this
                    once.
                  </>
                ) : (
                  <>
                    This workspace requires two-factor authentication for every account.
                    Finish setting it up to sign in — you only do this once.
                  </>
                )}
              </div>

              <MfaEnrollPanel
                enrollment={enrollment}
                enrollmentToken={enrollHandoff.enrollment_token}
                cancelLabel="Back to sign in"
                onCancel={() => backToPassword(null)}
                onTokenExpired={(info) => backToPassword(info.message)}
                onAlreadyEnrolled={() =>
                  backToPassword(
                    'This account already has two-factor authentication enabled. Sign in again and enter a code from your authenticator.',
                  )
                }
                onComplete={(result) => {
                  // The forced path is the only one where `tokens` is non-null —
                  // it is how the user finishes signing in without re-entering
                  // their password.
                  if (result.tokens) {
                    void completeSignIn(result.tokens);
                  } else {
                    backToPassword(
                      'Two-factor authentication is enabled. Sign in again to continue.',
                    );
                  }
                }}
              />
            </div>
          )}

          {/* ── Login form ── */}
          {mode === 'login' && (
            <form className="space-y-6" onSubmit={handleLogin}>
              <div>
                <label htmlFor="username" className="block text-sm font-medium text-[var(--color-text-secondary)]">
                  Email or Username
                </label>
                <div className="mt-1">
                  <input
                    id="username"
                    name="username"
                    type="text"
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className="appearance-none block w-full px-3 py-2 border border-[var(--color-border)] rounded-md shadow-sm placeholder-[var(--color-text-faint)] text-[var(--color-text)] bg-[var(--color-bg-input)] focus:outline-none focus:ring-[var(--color-ring)] focus:border-[var(--color-ring)] sm:text-sm"
                    placeholder="admin"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="password" className="block text-sm font-medium text-[var(--color-text-secondary)]">
                  Password
                </label>
                <div className="mt-1">
                  <input
                    id="password"
                    name="password"
                    type="password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="appearance-none block w-full px-3 py-2 border border-[var(--color-border)] rounded-md shadow-sm placeholder-[var(--color-text-faint)] text-[var(--color-text)] bg-[var(--color-bg-input)] focus:outline-none focus:ring-[var(--color-ring)] focus:border-[var(--color-ring)] sm:text-sm"
                    placeholder="••••••••"
                  />
                </div>
              </div>

              <div>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-[var(--color-btn-primary-text)] bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--color-ring)] focus:ring-offset-[var(--color-bg)] transition-colors disabled:opacity-50"
                >
                  {isSubmitting ? <LoadingSpinner size="sm" /> : (
                    <span className="flex items-center gap-2"><LogIn className="w-4 h-4" />Log In</span>
                  )}
                </button>
              </div>

              {/* SSO Login button */}
              {ssoStatus?.sso_enabled && ssoStatus.has_active_config && (
                <div className="pt-3 border-t border-[var(--color-border)]">
                  <button
                    type="button"
                    onClick={async () => {
                      try {
                        const { data } = await api.get('/api/v1/sso/login-url');
                        if (!isSafeExternalUrl(data?.redirect_url)) {
                          toast.error('SSO login URL rejected');
                          return;
                        }
                        window.location.href = data.redirect_url;
                      } catch {
                        toast.error('SSO login unavailable');
                      }
                    }}
                    className="w-full flex justify-center items-center gap-2 py-2 px-4 border border-[var(--color-border-light)] rounded-md shadow-sm text-sm font-medium text-[var(--color-text)] bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--color-ring)] focus:ring-offset-[var(--color-bg)] transition-colors"
                  >
                    <Fingerprint className="w-4 h-4" />
                    Sign in with SSO
                  </button>
                  {ssoStatus.enforcement_mode === 'SSO_REQUIRED' && (
                    <p className="text-xs text-[var(--status-broken)] mt-1.5 text-center">
                      SSO is required. Password login is only available for admin accounts.
                    </p>
                  )}
                </div>
              )}
            </form>
          )}

          {/* ── Register form ── */}
          {mode === 'register' && (
            <form className="space-y-5" onSubmit={handleRegister}>
              <div className="rounded-md bg-[var(--color-bg-secondary)]/80 border border-[var(--color-border-light)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
                New accounts have <strong>read-only (Viewer)</strong> access. An admin can promote
                your role after registration.
              </div>

              <div>
                <label htmlFor="reg-email" className="block text-sm font-medium text-[var(--color-text-secondary)]">
                  Email <span className="text-[var(--status-failed)]">*</span>
                </label>
                <input
                  id="reg-email"
                  type="email"
                  required
                  value={regEmail}
                  onChange={(e) => setRegEmail(e.target.value)}
                  className="mt-1 appearance-none block w-full px-3 py-2 border border-[var(--color-border)] rounded-md shadow-sm placeholder-[var(--color-text-faint)] text-[var(--color-text)] bg-[var(--color-bg-input)] focus:outline-none focus:ring-[var(--color-ring)] focus:border-[var(--color-ring)] sm:text-sm"
                  placeholder="you@example.com"
                />
              </div>

              <div>
                <label htmlFor="reg-username" className="block text-sm font-medium text-[var(--color-text-secondary)]">
                  Username <span className="text-[var(--status-failed)]">*</span>
                </label>
                <input
                  id="reg-username"
                  type="text"
                  required
                  minLength={3}
                  maxLength={50}
                  value={regUsername}
                  onChange={(e) => setRegUsername(e.target.value)}
                  className="mt-1 appearance-none block w-full px-3 py-2 border border-[var(--color-border)] rounded-md shadow-sm placeholder-[var(--color-text-faint)] text-[var(--color-text)] bg-[var(--color-bg-input)] focus:outline-none focus:ring-[var(--color-ring)] focus:border-[var(--color-ring)] sm:text-sm"
                  placeholder="johndoe"
                />
              </div>

              <div>
                <label htmlFor="reg-fullname" className="block text-sm font-medium text-[var(--color-text-secondary)]">
                  Full Name <span className="text-[var(--color-text-muted)]">(optional)</span>
                </label>
                <input
                  id="reg-fullname"
                  type="text"
                  value={regFullName}
                  onChange={(e) => setRegFullName(e.target.value)}
                  className="mt-1 appearance-none block w-full px-3 py-2 border border-[var(--color-border)] rounded-md shadow-sm placeholder-[var(--color-text-faint)] text-[var(--color-text)] bg-[var(--color-bg-input)] focus:outline-none focus:ring-[var(--color-ring)] focus:border-[var(--color-ring)] sm:text-sm"
                  placeholder="John Doe"
                />
              </div>

              <div>
                <label htmlFor="reg-password" className="block text-sm font-medium text-[var(--color-text-secondary)]">
                  Password <span className="text-[var(--status-failed)]">*</span>
                </label>
                <input
                  id="reg-password"
                  type="password"
                  required
                  minLength={8}
                  value={regPassword}
                  onChange={(e) => setRegPassword(e.target.value)}
                  className="mt-1 appearance-none block w-full px-3 py-2 border border-[var(--color-border)] rounded-md shadow-sm placeholder-[var(--color-text-faint)] text-[var(--color-text)] bg-[var(--color-bg-input)] focus:outline-none focus:ring-[var(--color-ring)] focus:border-[var(--color-ring)] sm:text-sm"
                  placeholder="At least 8 characters"
                />
              </div>

              <div>
                <label htmlFor="reg-confirm" className="block text-sm font-medium text-[var(--color-text-secondary)]">
                  Confirm Password <span className="text-[var(--status-failed)]">*</span>
                </label>
                <input
                  id="reg-confirm"
                  type="password"
                  required
                  minLength={8}
                  aria-invalid={passwordsMismatch}
                  aria-describedby={passwordsMismatch ? 'reg-confirm-error' : undefined}
                  value={regConfirmPassword}
                  onChange={(e) => setRegConfirmPassword(e.target.value)}
                  className={`mt-1 appearance-none block w-full px-3 py-2 border rounded-md shadow-sm placeholder-[var(--color-text-faint)] text-[var(--color-text)] bg-[var(--color-bg-input)] focus:outline-none focus:ring-[var(--color-ring)] focus:border-[var(--color-ring)] sm:text-sm ${
                    passwordsMismatch
                      ? 'border-[var(--status-failed-bd)]'
                      : 'border-[var(--color-border)]'
                  }`}
                  placeholder="Repeat your password"
                />
                {passwordsMismatch && (
                  <p
                    id="reg-confirm-error"
                    role="alert"
                    className="mt-1 text-xs text-[var(--status-failed)]"
                  >
                    Passwords do not match.
                  </p>
                )}
              </div>

              <button
                type="submit"
                disabled={isRegistering}
                className="w-full flex justify-center items-center gap-2 py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-[var(--color-btn-primary-text)] bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--color-ring)] focus:ring-offset-[var(--color-bg)] transition-colors disabled:opacity-50"
              >
                {isRegistering ? <LoadingSpinner size="sm" /> : (
                  <><UserPlus className="w-4 h-4" />Create Account</>
                )}
              </button>
            </form>
          )}

          {/* ── Mode toggle (hidden mid-MFA — there is nothing to switch to) ── */}
          <div
            className={`mt-5 pt-4 border-t border-[var(--color-border)] text-center text-sm text-[var(--color-text-muted)] ${
              mode === 'login' || mode === 'register' ? '' : 'hidden'
            }`}
          >
            {mode !== 'register' ? (
              <>
                Don't have an account?{' '}
                <button
                  type="button"
                  onClick={() => setMode('register')}
                  className="text-[var(--color-text)] hover:text-[var(--color-text-secondary)] font-medium transition-colors"
                >
                  Register
                </button>
              </>
            ) : (
              <>
                Already have an account?{' '}
                <button
                  type="button"
                  onClick={() => setMode('login')}
                  className="text-[var(--color-text)] hover:text-[var(--color-text-secondary)] font-medium transition-colors"
                >
                  Sign in
                </button>
              </>
            )}
          </div>

        </div>
      </div>
    </div>
  );
}
