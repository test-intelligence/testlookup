import { useState } from 'react'
import { Loader2, ShieldCheck } from 'lucide-react'
import { confirmMfaEnrollment } from '@/hooks/useMfaStatus'
import type { MfaEnrollConfirmResponse, MfaEnrollStartResponse } from '@/types/mfa'
import { describeMfaError, type MfaErrorInfo } from '@/utils/mfaErrors'
import MfaCodeField from './MfaCodeField'
import OtpAuthQr from './OtpAuthQr'
import RecoveryCodesPanel from './RecoveryCodesPanel'

interface Props {
  /** Result of `POST /auth/mfa/enroll/start`, fetched by the caller. */
  enrollment: MfaEnrollStartResponse
  /** Present on the forced-enrollment path; omitted when already signed in. */
  enrollmentToken?: string
  /** Fires after the user acknowledges the recovery codes. */
  onComplete: (result: MfaEnrollConfirmResponse) => void
  /** The enrollment token lapsed or was already spent — the caller must restart. */
  onTokenExpired?: (info: MfaErrorInfo) => void
  /** Already enrolled (409) — the caller should refresh status. */
  onAlreadyEnrolled?: (info: MfaErrorInfo) => void
  onCancel?: () => void
  cancelLabel?: string
}

/**
 * Scan → confirm → save recovery codes.
 *
 * Shared verbatim by the forced path on the login screen and the voluntary
 * path in Profile → Security, so the two cannot drift. The caller performs
 * `enroll/start` and hands the result in; only `enroll/confirm` happens here.
 */
export default function MfaEnrollPanel({
  enrollment,
  enrollmentToken,
  onComplete,
  onTokenExpired,
  onAlreadyEnrolled,
  onCancel,
  cancelLabel = 'Cancel',
}: Props) {
  const [code, setCode] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<MfaErrorInfo | null>(null)
  const [confirmed, setConfirmed] = useState<MfaEnrollConfirmResponse | null>(null)

  const handleConfirm = async (e: React.FormEvent) => {
    e.preventDefault()
    if (code.length < 6 || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await confirmMfaEnrollment(code, enrollmentToken)
      setConfirmed(result)
    } catch (err) {
      const info = describeMfaError(err)
      if (info.kind === 'challenge_expired') {
        onTokenExpired?.(info)
      } else if (info.kind === 'already_enrolled') {
        onAlreadyEnrolled?.(info)
      }
      setError(info)
      setCode('')
    } finally {
      setSubmitting(false)
    }
  }

  if (confirmed) {
    return (
      <RecoveryCodesPanel
        codes={confirmed.recovery_codes}
        variant="initial"
        acknowledgeLabel={confirmed.tokens ? 'Continue to TestLookup' : 'Finish'}
        onAcknowledge={() => onComplete(confirmed)}
      />
    )
  }

  return (
    <form className="space-y-5" onSubmit={handleConfirm}>
      <OtpAuthQr
        otpauthUri={enrollment.otpauth_uri}
        secret={enrollment.secret}
        issuer={enrollment.issuer}
        accountName={enrollment.account_name}
        digits={enrollment.digits}
        periodSeconds={enrollment.period_seconds}
      />

      <MfaCodeField
        mode="totp"
        value={code}
        onValueChange={setCode}
        disabled={submitting}
        autoFocus
        idPrefix="mfa-enroll"
        label="Enter the code your app shows now"
      />

      {error && (
        <p
          role="alert"
          className="rounded border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg-soft)] px-3 py-2 text-xs text-[var(--status-failed)]"
        >
          {error.message}
        </p>
      )}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting || code.length < 6}
          className="btn-primary flex flex-1 items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <ShieldCheck className="h-4 w-4" />
          )}
          Verify and enable
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded-md border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
          >
            {cancelLabel}
          </button>
        )}
      </div>
    </form>
  )
}
