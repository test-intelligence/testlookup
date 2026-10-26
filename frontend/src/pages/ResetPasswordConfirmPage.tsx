/**
 * Where a password-reset link lands (W17b ADM-02): `/reset-password/confirm#token=…`.
 *
 * Public, and distinct from `/reset-password` (the forced first-login change,
 * behind sign-in). The token is read from the fragment once and cleared from
 * the address bar. Setting the password signs that account out everywhere (the
 * server revokes its tokens; a stale tab gets a 401), then this page sends the
 * user to sign in. It does not touch whoever is signed in in this browser —
 * that may be the administrator who issued the link.
 */
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'

import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { resetPasswordWithToken } from '@/services/authService'
import { extractErrorMessage } from '@/services/apiErrors'
import { LinkPageShell } from '@/components/auth/LinkPageShell'
import { useFragmentToken } from '@/hooks/useFragmentToken'
import { INVALID_RESET } from '@/utils/oneTimeLink'

const INPUT =
  'appearance-none block w-full px-3 py-2 border border-[var(--color-border)] rounded-md text-[var(--color-text)] bg-[var(--color-bg-input)] focus:outline-none focus:ring-[var(--color-ring)] focus:border-[var(--color-ring)] sm:text-sm'

export default function ResetPasswordConfirmPage() {
  const token = useFragmentToken()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [invalid, setInvalid] = useState(!token)
  const [formError, setFormError] = useState<string | null>(null)
  const mismatch = confirm.length > 0 && password !== confirm

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!token || mismatch || password.length < 8) return
    setSubmitting(true)
    setFormError(null)
    try {
      await resetPasswordWithToken(token, password)
      toast.success('Password changed')
      navigate('/login?reset=1', { replace: true })
    } catch (err: unknown) {
      const response = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response
      if (response?.status === 400) setInvalid(true)
      else setFormError(extractErrorMessage(response?.data?.detail, 'Could not change the password. Try again.'))
    } finally {
      setSubmitting(false)
    }
  }

  if (invalid) {
    return (
      <LinkPageShell>
        <div className="text-center space-y-3" data-testid="reset-invalid">
          <p role="alert" className="text-sm font-medium text-[var(--color-text)]">{INVALID_RESET}</p>
          <p className="text-sm text-[var(--color-text-muted)]">Ask for a new link from the sign-in page or your administrator.</p>
          <Link to="/forgot-password" className="btn-secondary inline-flex justify-center w-full">Get a new link</Link>
        </div>
      </LinkPageShell>
    )
  }

  return (
    <LinkPageShell>
      <h2 className="text-lg font-semibold text-center text-[var(--color-text)]">Set a new password</h2>
      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <label htmlFor="reset-password" className="block text-sm text-[var(--color-text-muted)] mb-1">New password</label>
          <input id="reset-password" type="password" required minLength={8} maxLength={128} value={password}
            onChange={(e) => setPassword(e.target.value)} className={INPUT} autoComplete="new-password" />
          <p className="mt-1 text-xs text-[var(--color-text-faint)]">At least 8 characters. Every session of this account is signed out.</p>
        </div>
        <div>
          <label htmlFor="reset-confirm" className="block text-sm text-[var(--color-text-muted)] mb-1">Confirm new password</label>
          <input id="reset-confirm" type="password" required value={confirm}
            onChange={(e) => setConfirm(e.target.value)} className={INPUT} autoComplete="new-password" />
          {mismatch && <p className="mt-1 text-xs text-[var(--ui-danger)]">Passwords do not match.</p>}
        </div>
        {formError && <p role="alert" className="text-sm text-[var(--ui-danger)]">{formError}</p>}
        <button type="submit" disabled={submitting || mismatch} className="btn-primary w-full flex justify-center">
          {submitting ? <LoadingSpinner size="sm" /> : 'Change password'}
        </button>
      </form>
    </LinkPageShell>
  )
}
