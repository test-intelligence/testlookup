/**
 * "Forgot password?" from the sign-in page (W17b ADM-02): `/forgot-password`.
 *
 * With SMTP configured (`GET /auth/options` → `password_reset_email`) it takes
 * an address and always shows the same confirmation — the server answers the
 * same for every address, so the page cannot reveal whether an account exists.
 * Without SMTP (an offline deployment) it says to ask an administrator, who can
 * issue a reset link from User Management.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { getAuthOptions, requestPasswordReset } from '@/services/authService'
import { LinkPageShell } from '@/components/auth/LinkPageShell'
import { ASK_AN_ADMIN, RESET_ON_ITS_WAY } from '@/utils/oneTimeLink'

export default function ForgotPasswordPage() {
  const [emailAvailable, setEmailAvailable] = useState<boolean | null>(null)
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [sent, setSent] = useState(false)

  useEffect(() => {
    getAuthOptions()
      .then((o) => setEmailAvailable(o.password_reset_email === true))
      .catch(() => setEmailAvailable(false))
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    try {
      await requestPasswordReset(email.trim())
    } catch {
      // Same confirmation either way: a rate-limit or network error must not read differently.
    } finally {
      setSubmitting(false)
      setSent(true)
    }
  }

  return (
    <LinkPageShell>
      <h2 className="text-lg font-semibold text-center text-[var(--color-text)]">Reset your password</h2>
      {emailAvailable === null ? (
        <div className="flex justify-center" role="status"><LoadingSpinner size="sm" /></div>
      ) : !emailAvailable ? (
        <div className="space-y-3 text-center" data-testid="forgot-no-smtp">
          <p className="text-sm text-[var(--color-text)]">{ASK_AN_ADMIN}</p>
          <p className="text-xs text-[var(--color-text-muted)]">
            This deployment does not send email. An administrator can send you a single-use link from User Management.
          </p>
        </div>
      ) : sent ? (
        <p role="status" className="text-sm text-center text-[var(--color-text)]" data-testid="forgot-sent">
          {RESET_ON_ITS_WAY}
        </p>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-3">
          <p className="text-sm text-[var(--color-text-secondary)]">
            Enter the email address of your account and we will send you a link to set a new password.
          </p>
          <div>
            <label htmlFor="forgot-email" className="block text-sm text-[var(--color-text-muted)] mb-1">Email address</label>
            <input
              id="forgot-email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              className="appearance-none block w-full px-3 py-2 border border-[var(--color-border)] rounded-md text-[var(--color-text)] bg-[var(--color-bg-input)] focus:outline-none focus:ring-[var(--color-ring)] focus:border-[var(--color-ring)] sm:text-sm"
            />
          </div>
          <button type="submit" disabled={submitting} className="btn-primary w-full flex justify-center">
            {submitting ? <LoadingSpinner size="sm" /> : 'Send reset link'}
          </button>
        </form>
      )}
      <Link to="/login" className="block text-center text-sm text-[var(--color-accent)] hover:underline">
        Back to sign in
      </Link>
    </LinkPageShell>
  )
}
