/**
 * Where an invitation link lands (W17b ADM-01): `/accept-invite#token=…`.
 *
 * Public — the invitee has no account yet. The token rides in the fragment, so
 * it never reaches a server log, a proxy or a Referer header; this page reads it
 * once, clears it from the address bar, previews the invitation ("invited by
 * Admin as QA Engineer") and redeems it with the chosen username and password.
 * Success signs the new user in and lands on Overview; when the workspace
 * requires MFA for the role, the account is created and sign-in sets it up.
 */
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'

import { LinkPageShell } from '@/components/auth/LinkPageShell'
import { useFragmentToken } from '@/hooks/useFragmentToken'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import {
  acceptInvitation,
  fetchCurrentUser,
  previewInvitation,
  type InvitationPreview,
} from '@/services/authService'
import { extractErrorMessage } from '@/services/apiErrors'
import { useAuthStore } from '@/store/authStore'
import { INVALID_INVITE, roleLabel } from '@/utils/oneTimeLink'

const INPUT =
  'appearance-none block w-full px-3 py-2 border border-[var(--color-border)] rounded-md text-[var(--color-text)] bg-[var(--color-bg-input)] focus:outline-none focus:ring-[var(--color-ring)] focus:border-[var(--color-ring)] sm:text-sm'

/** The HTTP status of a failed request, if it got one. */
function statusOf(err: unknown): number | undefined {
  return (err as { response?: { status?: number } })?.response?.status
}

const INVALID_STATE = { kind: 'invalid' } as const

type State =
  | { kind: 'loading' }
  | { kind: 'invalid' }
  | { kind: 'ready'; preview: InvitationPreview }

export default function AcceptInvitePage() {
  const token = useFragmentToken()
  const navigate = useNavigate()
  const setAuth = useAuthStore((s) => s.setAuth)
  const [state, setState] = useState<State>(token ? { kind: 'loading' } : { kind: 'invalid' })
  const [username, setUsername] = useState('')
  const [fullName, setFullName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  // A 400 is a dead link; anything else (server down, network) is not the invitee's fault.
  const [loadError, setLoadError] = useState(false)
  const started = useRef(false)

  useEffect(() => {
    if (!token || started.current) return
    started.current = true
    previewInvitation(token)
      .then((preview) => setState({ kind: 'ready', preview }))
      .catch((err) => { if (statusOf(err) !== 400) setLoadError(true); else setState(INVALID_STATE) })
  }, [token])

  const mismatch = confirm.length > 0 && password !== confirm

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!token || mismatch || password.length < 8) return
    setSubmitting(true)
    setFormError(null)
    try {
      const res = await acceptInvitation({
        token,
        username: username.trim(),
        full_name: fullName.trim() || undefined,
        password,
      })
      if ('access_token' in res && res.access_token) {
        const user = await fetchCurrentUser(res.access_token)
        setAuth(res.access_token, res.refresh_token, user)
        toast.success('Welcome to TestLookup')
        navigate('/overview', { replace: true })
      } else {
        navigate('/login?invited=1', { replace: true })
      }
    } catch (err: unknown) {
      const response = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response
      if (response?.status === 400) setState({ kind: 'invalid' })
      else setFormError(extractErrorMessage(response?.data?.detail, 'Could not create the account. Try again.'))
    } finally {
      setSubmitting(false)
    }
  }

  if (loadError) {
    return (
      <LinkPageShell>
        <div className="text-center space-y-3" data-testid="invite-load-error">
          <p role="alert" className="text-sm text-[var(--color-text)]">
            We could not check this invitation right now. Try the link again in a moment.
          </p>
        </div>
      </LinkPageShell>
    )
  }

  if (state.kind === 'loading') {
    return (
      <LinkPageShell>
        <div className="flex items-center justify-center gap-2 text-sm text-[var(--color-text-muted)]" role="status">
          <LoadingSpinner size="sm" /> Checking your invitation…
        </div>
      </LinkPageShell>
    )
  }

  if (state.kind === 'invalid') {
    return (
      <LinkPageShell>
        <div className="text-center space-y-3" data-testid="invite-invalid">
          <p role="alert" className="text-sm font-medium text-[var(--color-text)]">{INVALID_INVITE}</p>
          <p className="text-sm text-[var(--color-text-muted)]">Ask your administrator for a new invitation.</p>
          <Link to="/login" className="btn-secondary inline-flex justify-center w-full">Go to sign in</Link>
        </div>
      </LinkPageShell>
    )
  }

  const { preview } = state
  return (
    <LinkPageShell>
      <div className="space-y-1 text-center">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">Join TestLookup</h2>
        <p className="text-sm text-[var(--color-text-secondary)]" data-testid="invite-summary">
          You were invited by <strong>{preview.invited_by_name || 'an administrator'}</strong> as{' '}
          <strong>{roleLabel(preview.role)}</strong>.
        </p>
        {preview.project_names.length > 0 && (
          <p className="text-xs text-[var(--color-text-muted)]">Projects: {preview.project_names.join(', ')}</p>
        )}
      </div>
      {preview.sso_required ? (
        <div className="space-y-3 text-center">
          <p role="alert" className="text-sm text-[var(--color-text)]">
            This workspace signs in with single sign-on. Use the SSO button on the sign-in page with{' '}
            <strong>{preview.email}</strong>.
          </p>
          <Link to="/login" className="btn-primary inline-flex justify-center w-full">Go to sign in</Link>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label htmlFor="invite-email" className="block text-sm text-[var(--color-text-muted)] mb-1">Email</label>
            <input id="invite-email" value={preview.email} readOnly disabled className={`${INPUT} opacity-70`} />
          </div>
          <div>
            <label htmlFor="invite-username" className="block text-sm text-[var(--color-text-muted)] mb-1">Username</label>
            <input id="invite-username" required minLength={3} maxLength={50} value={username}
              onChange={(e) => setUsername(e.target.value)} className={INPUT} autoComplete="username" />
          </div>
          <div>
            <label htmlFor="invite-fullname" className="block text-sm text-[var(--color-text-muted)] mb-1">Full name</label>
            <input id="invite-fullname" maxLength={255} value={fullName}
              onChange={(e) => setFullName(e.target.value)} className={INPUT} autoComplete="name" />
          </div>
          <div>
            <label htmlFor="invite-password" className="block text-sm text-[var(--color-text-muted)] mb-1">Password</label>
            <input id="invite-password" type="password" required minLength={8} maxLength={128} value={password}
              onChange={(e) => setPassword(e.target.value)} className={INPUT} autoComplete="new-password" />
            <p className="mt-1 text-xs text-[var(--color-text-faint)]">At least 8 characters.</p>
          </div>
          <div>
            <label htmlFor="invite-confirm" className="block text-sm text-[var(--color-text-muted)] mb-1">Confirm password</label>
            <input id="invite-confirm" type="password" required value={confirm}
              onChange={(e) => setConfirm(e.target.value)} className={INPUT} autoComplete="new-password" />
            {mismatch && <p className="mt-1 text-xs text-[var(--ui-danger)]">Passwords do not match.</p>}
          </div>
          {formError && <p role="alert" className="text-sm text-[var(--ui-danger)]">{formError}</p>}
          <button type="submit" disabled={submitting || mismatch} className="btn-primary w-full flex justify-center">
            {submitting ? <LoadingSpinner size="sm" /> : 'Create account and sign in'}
          </button>
        </form>
      )}
    </LinkPageShell>
  )
}
