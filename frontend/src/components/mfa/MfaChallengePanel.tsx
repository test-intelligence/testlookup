import { useState } from 'react'
import { Clock, Loader2, LogIn, ShieldCheck } from 'lucide-react'
import { useNow } from '@/hooks/useNow'
import { verifyMfaChallenge } from '@/hooks/useMfaStatus'
import type { MfaChallengeResponse, TokenResponse } from '@/types/mfa'
import { describeMfaError, formatRetryAfter, type MfaErrorInfo } from '@/utils/mfaErrors'
import MfaCodeField, { type MfaFactorMode } from './MfaCodeField'

interface Props {
  challenge: MfaChallengeResponse
  /** Shown so the user knows whose account they are completing sign-in for. */
  accountLabel?: string
  onVerified: (tokens: TokenResponse) => void
  /** Take the user back to the password step, with a reason to display. */
  onExpired: (reason: string) => void
  onCancel: () => void
}

function formatRemaining(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

const EXPIRED_REASON =
  'That sign-in request timed out before the code was entered. Enter your password again to get a fresh one.'

/**
 * The second step of password sign-in.
 *
 * Two behaviours are load-bearing:
 *
 *  1. A wrong code keeps the challenge. The user stays here with the error
 *     shown; they are NOT bounced back to the password field, which would
 *     throw away a challenge that is still perfectly valid.
 *  2. The challenge is short-lived (5 minutes today) and the remaining time is
 *     shown honestly. When it lapses — either because our countdown hit zero or
 *     because the backend rejected the token — the panel says so in words and
 *     offers the way back, instead of silently failing on every submit.
 */
export default function MfaChallengePanel({
  challenge,
  accountLabel,
  onVerified,
  onExpired,
  onCancel,
}: Props) {
  const [factor, setFactor] = useState<MfaFactorMode>('totp')
  const [value, setValue] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<MfaErrorInfo | null>(null)
  const [serverSaysExpired, setServerSaysExpired] = useState(false)

  // Captured once per challenge — the panel remounts when a new one arrives.
  const [expiresAt] = useState(() => Date.now() + challenge.expires_in * 1000)
  const now = useNow(1000)
  const remainingMs = expiresAt - now
  const expired = serverSaysExpired || remainingMs <= 0

  const canUseRecoveryCode = challenge.methods.includes('recovery_code')
  const ready = factor === 'totp' ? value.length === 6 : value.replace(/-/g, '').length >= 8

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!ready || submitting || expired) return
    setSubmitting(true)
    setError(null)
    try {
      // Send exactly one factor — the backend ignores `code` entirely when
      // `recovery_code` is present.
      const tokens = await verifyMfaChallenge(
        factor === 'totp'
          ? { challenge_token: challenge.challenge_token, code: value }
          : { challenge_token: challenge.challenge_token, recovery_code: value },
      )
      onVerified(tokens)
    } catch (err) {
      const info = describeMfaError(err)
      if (info.kind === 'challenge_expired') {
        setServerSaysExpired(true)
      }
      setError(info)
      setValue('')
    } finally {
      setSubmitting(false)
    }
  }

  if (expired) {
    return (
      <div className="space-y-4" data-testid="mfa-challenge-expired">
        <div
          role="alert"
          className="rounded border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg-soft)] px-3 py-2 text-sm text-[var(--color-text)]"
        >
          {error?.kind === 'challenge_expired' ? error.message : EXPIRED_REASON}
        </div>
        <button
          type="button"
          onClick={() => onExpired(EXPIRED_REASON)}
          className="btn-primary flex w-full items-center justify-center gap-2"
        >
          <LogIn className="h-4 w-4" />
          Enter your password again
        </button>
      </div>
    )
  }

  return (
    <form className="space-y-5" onSubmit={handleSubmit} data-testid="mfa-challenge-panel">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Two-factor authentication
        </h3>
        <p className="text-xs text-[var(--color-text-muted)]">
          {accountLabel
            ? `Finish signing in as ${accountLabel}.`
            : 'Finish signing in with your second factor.'}
        </p>
      </div>

      <MfaCodeField
        mode={factor}
        value={value}
        onValueChange={setValue}
        onModeChange={canUseRecoveryCode ? setFactor : undefined}
        disabled={submitting}
        autoFocus
        idPrefix="mfa-challenge"
      />

      <p className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
        <Clock className="h-3 w-3" />
        This request expires in{' '}
        <span className="font-mono text-[var(--color-text-secondary)]">
          {formatRemaining(remainingMs)}
        </span>
      </p>

      {error && !expired && (
        <p
          role="alert"
          className="rounded border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg-soft)] px-3 py-2 text-xs text-[var(--status-failed)]"
        >
          {error.message}
          {error.kind === 'locked_out' && error.retryAfterSeconds !== undefined && (
            <> Try again in about {formatRetryAfter(error.retryAfterSeconds)}.</>
          )}
        </p>
      )}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting || !ready}
          className="btn-primary flex flex-1 items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <ShieldCheck className="h-4 w-4" />
          )}
          Verify
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="rounded-md border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
        >
          Back
        </button>
      </div>
    </form>
  )
}
