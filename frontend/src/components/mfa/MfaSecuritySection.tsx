import { useState } from 'react'
import {
  AlertTriangle,
  Building2,
  Loader2,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
  ShieldPlus,
} from 'lucide-react'
import toast from 'react-hot-toast'
import {
  disableMfa,
  regenerateMfaRecoveryCodes,
  startMfaEnrollment,
  useMfaStatus,
} from '@/hooks/useMfaStatus'
import type { MfaEnrollStartResponse } from '@/types/mfa'
import { describeMfaError, type MfaErrorInfo } from '@/utils/mfaErrors'
import MfaCodeField, { type MfaFactorMode } from './MfaCodeField'
import MfaEnrollPanel from './MfaEnrollPanel'
import RecoveryCodesPanel from './RecoveryCodesPanel'

type Panel = 'none' | 'enroll' | 'regenerate' | 'disable'

function formatEnrolledAt(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleString()
}

/**
 * Profile → Security. Owns every MFA state a signed-in user can be in.
 *
 * The two states that are easy to get wrong, and are handled explicitly here:
 *
 *  - `sso_managed` — the identity provider owns this user's second factor.
 *    Showing an Enable button would be a lie; we render an explanation and no
 *    controls at all.
 *  - `secret_unreadable` — MFA is ON but the stored seed will not decrypt. The
 *    user cannot log in with TOTP and cannot fix it themselves (disable and
 *    regenerate both 503). This is rendered as a loud broken state, never as
 *    "not enrolled", because "not enrolled" invites an enroll attempt that
 *    409s and leaves them stuck.
 */
export default function MfaSecuritySection() {
  const { data: status, error, isLoading, mutate } = useMfaStatus()

  const [panel, setPanel] = useState<Panel>('none')
  const [busy, setBusy] = useState(false)
  const [enrollment, setEnrollment] = useState<MfaEnrollStartResponse | null>(null)
  const [newCodes, setNewCodes] = useState<string[] | null>(null)
  const [formError, setFormError] = useState<MfaErrorInfo | null>(null)

  // Second-factor form (shared by regenerate + disable)
  const [password, setPassword] = useState('')
  const [factor, setFactor] = useState<MfaFactorMode>('totp')
  const [factorValue, setFactorValue] = useState('')

  const resetForms = () => {
    setPanel('none')
    setBusy(false)
    setEnrollment(null)
    setNewCodes(null)
    setFormError(null)
    setPassword('')
    setFactor('totp')
    setFactorValue('')
  }

  const secondFactorPayload = () =>
    factor === 'totp' ? { code: factorValue } : { recovery_code: factorValue }

  const handleBeginEnroll = async () => {
    setBusy(true)
    setFormError(null)
    try {
      const started = await startMfaEnrollment()
      setEnrollment(started)
      setPanel('enroll')
    } catch (err) {
      const info = describeMfaError(err)
      setFormError(info)
      if (info.kind === 'already_enrolled') {
        toast.error(info.message)
        void mutate()
      } else {
        toast.error(info.message)
      }
    } finally {
      setBusy(false)
    }
  }

  const handleRegenerate = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setFormError(null)
    try {
      const res = await regenerateMfaRecoveryCodes({ password, ...secondFactorPayload() })
      setNewCodes(res.recovery_codes)
      setPassword('')
      setFactorValue('')
      void mutate()
    } catch (err) {
      const info = describeMfaError(err)
      setFormError(info)
      if (info.kind === 'not_enrolled') void mutate()
    } finally {
      setBusy(false)
    }
  }

  const handleDisable = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setFormError(null)
    try {
      await disableMfa({ password, ...secondFactorPayload() })
      toast.success('Two-factor authentication disabled')
      resetForms()
      void mutate()
    } catch (err) {
      const info = describeMfaError(err)
      setFormError(info)
      if (info.kind === 'not_enrolled') void mutate()
    } finally {
      setBusy(false)
    }
  }

  // ── Shell ─────────────────────────────────────────────────────────────────
  const heading = (
    <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]">
      <ShieldCheck className="h-4 w-4 text-[var(--color-text-muted)]" />
      Two-Factor Authentication
    </h2>
  )

  if (isLoading) {
    return (
      <section className="card space-y-4">
        {heading}
        <p className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin" />
          Checking your security settings…
        </p>
      </section>
    )
  }

  if (error || !status) {
    const info = describeMfaError(error)
    return (
      <section className="card space-y-4">
        {heading}
        <p role="alert" className="text-sm text-[var(--status-failed)]">
          {info.kind === 'unavailable'
            ? info.message
            : "We couldn't load your two-factor settings. Reload the page to try again."}
        </p>
      </section>
    )
  }

  // ── SSO-managed: the IdP owns the second factor ───────────────────────────
  if (status.sso_managed) {
    return (
      <section className="card space-y-3" data-testid="mfa-sso-managed">
        {heading}
        <div className="flex items-start gap-2">
          <Building2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--color-text-muted)]" />
          <p className="text-sm text-[var(--color-text-secondary)]">
            Your account signs in through your organisation&apos;s identity provider, and
            your second factor is managed there. There is nothing to configure in
            TestLookup — change your MFA settings with your identity provider instead.
          </p>
        </div>
      </section>
    )
  }

  // ── Broken: enrolled but the seed will not decrypt ────────────────────────
  if (status.secret_unreadable) {
    return (
      <section
        className="card space-y-3 border-[var(--status-failed-bd)]"
        data-testid="mfa-secret-unreadable"
      >
        {heading}
        <div className="flex items-start gap-2 rounded-md border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg-soft)] p-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--status-failed)]" />
          <div className="space-y-1.5">
            <p className="text-sm font-semibold text-[var(--status-failed)]">
              Two-factor authentication is enabled but broken
            </p>
            <p className="text-sm text-[var(--color-text-secondary)]">
              Your authenticator secret is stored but cannot be read, so codes from your
              app will not work and you will not be able to sign in with them. This is a
              server-side problem — you cannot fix it from here, and turning MFA off or
              regenerating recovery codes will fail for the same reason.
            </p>
            <p className="text-sm text-[var(--color-text-secondary)]">
              <strong>Contact an administrator</strong> to have your MFA device reset. If
              you still have unused recovery codes, they remain your way in.
            </p>
          </div>
        </div>
        <p className="text-xs text-[var(--color-text-muted)]">
          Recovery codes remaining: {status.recovery_codes_remaining}
        </p>
      </section>
    )
  }

  // ── Not enrolled ──────────────────────────────────────────────────────────
  if (!status.enabled) {
    return (
      <section className="card space-y-4" data-testid="mfa-not-enrolled">
        {heading}

        {panel === 'enroll' && enrollment ? (
          <MfaEnrollPanel
            enrollment={enrollment}
            onCancel={resetForms}
            onAlreadyEnrolled={() => {
              resetForms()
              void mutate()
            }}
            onComplete={() => {
              toast.success('Two-factor authentication enabled')
              resetForms()
              void mutate()
            }}
          />
        ) : (
          <>
            <p className="text-sm text-[var(--color-text-secondary)]">
              Add a time-based code from an authenticator app on top of your password.
              You&apos;ll also get ten single-use recovery codes for when you don&apos;t
              have your phone.
            </p>

            {status.required_by_policy && (
              <p
                role="alert"
                className="rounded-md border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg-soft)] px-3 py-2 text-xs text-[var(--color-text)]"
              >
                This workspace requires two-factor authentication for your role. You will
                be asked to set it up the next time you sign in — doing it now avoids that
                interruption.
              </p>
            )}

            {formError && (
              <p role="alert" className="text-xs text-[var(--status-failed)]">
                {formError.message}
              </p>
            )}

            <button
              type="button"
              onClick={handleBeginEnroll}
              disabled={busy}
              className="btn-primary flex items-center gap-2 disabled:opacity-50"
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <ShieldPlus className="h-4 w-4" />
              )}
              Enable two-factor authentication
            </button>
          </>
        )}
      </section>
    )
  }

  // ── Enrolled and healthy ──────────────────────────────────────────────────
  const enrolledAt = formatEnrolledAt(status.enrolled_at)
  const codesLow = status.recovery_codes_remaining <= 2

  return (
    <section className="card space-y-4" data-testid="mfa-enrolled">
      {heading}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="inline-flex items-center gap-1.5 rounded border border-[var(--status-passed-bd)] bg-[var(--status-passed-bg-soft)] px-2 py-0.5 text-xs font-medium text-[var(--status-passed)]">
          <ShieldCheck className="h-3 w-3" />
          Enabled
        </span>
        {enrolledAt && (
          <span className="text-xs text-[var(--color-text-muted)]">since {enrolledAt}</span>
        )}
      </div>

      <p
        className={`text-sm ${codesLow ? 'text-[var(--status-broken)]' : 'text-[var(--color-text-secondary)]'}`}
      >
        <strong>{status.recovery_codes_remaining}</strong> of 10 recovery codes remaining.
        {codesLow && ' Regenerate a fresh set before you run out.'}
      </p>

      {status.required_by_policy && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Workspace policy requires MFA for your role, so it cannot be turned off from
          here.
        </p>
      )}

      {newCodes && (
        <RecoveryCodesPanel
          codes={newCodes}
          variant="regenerated"
          acknowledgeLabel="Done"
          onAcknowledge={resetForms}
        />
      )}

      {!newCodes && panel === 'none' && (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              setPanel('regenerate')
              setFormError(null)
            }}
            className="flex items-center gap-1.5 rounded border border-[var(--color-border)] px-3 py-1.5 text-sm text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)]"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Regenerate recovery codes
          </button>
          <button
            type="button"
            onClick={() => {
              setPanel('disable')
              setFormError(null)
            }}
            className="flex items-center gap-1.5 rounded border border-[var(--status-failed-bd)] px-3 py-1.5 text-sm text-[var(--status-failed)] transition-colors hover:bg-[var(--status-failed-bg-soft)]"
          >
            <ShieldOff className="h-3.5 w-3.5" />
            Turn off two-factor authentication
          </button>
        </div>
      )}

      {!newCodes && panel !== 'none' && (
        <form
          className="space-y-4 rounded-md border border-[var(--color-border)] p-4"
          onSubmit={panel === 'disable' ? handleDisable : handleRegenerate}
          data-testid={panel === 'disable' ? 'mfa-disable-form' : 'mfa-regenerate-form'}
        >
          <p className="text-sm text-[var(--color-text-secondary)]">
            {panel === 'disable'
              ? 'Turning MFA off leaves your password as the only thing protecting this account. Confirm with your password and a current second factor.'
              : 'Regenerating issues ten new codes and immediately invalidates every code you have now. Confirm with your password and a current second factor.'}
          </p>

          <div>
            <label
              htmlFor="mfa-confirm-password"
              className="mb-1 block text-sm font-medium text-[var(--color-text-secondary)]"
            >
              Password
            </label>
            <input
              id="mfa-confirm-password"
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input w-full"
              placeholder="Your account password"
            />
          </div>

          <MfaCodeField
            mode={factor}
            value={factorValue}
            onValueChange={setFactorValue}
            onModeChange={setFactor}
            disabled={busy}
            idPrefix={`mfa-${panel}`}
          />

          {formError && (
            <p role="alert" className="text-xs text-[var(--status-failed)]">
              {formError.message}
            </p>
          )}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={busy || !password || !factorValue}
              className="btn-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {panel === 'disable' ? 'Turn off MFA' : 'Generate new codes'}
            </button>
            <button
              type="button"
              onClick={resetForms}
              disabled={busy}
              className="rounded-md border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  )
}
