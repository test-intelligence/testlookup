/**
 * Turning MFA HTTP failures into honest user-facing copy.
 *
 * The rule this file exists to enforce: never let an infrastructure failure
 * read as a user mistake. A 503 (the TOTP seed will not decrypt, or the token
 * revocation store is down) is *not* "wrong password" and must never be shown
 * as one — that sends people to reset credentials that were fine.
 */

/** What kind of failure this was, for callers that need to branch on it. */
export type MfaErrorKind =
  | 'invalid_code' // wrong TOTP / recovery code
  | 'challenge_expired' // the short-lived login token lapsed or was already spent
  | 'locked_out' // 429 — account lockout, carries retryAfterSeconds
  | 'rate_limited' // 429 — per-IP throttle, no Retry-After
  | 'unavailable' // 503 — we cannot verify right now; NOT the user's fault
  | 'already_enrolled' // 409
  | 'not_enrolled' // 409 on a management call
  | 'forbidden' // 403 — API-key session, or policy forbids self-disable
  | 'bad_request' // 400 — wrong password / malformed
  | 'unknown'

export interface MfaErrorInfo {
  kind: MfaErrorKind
  status?: number
  /** Ready-to-render sentence. */
  message: string
  /** Seconds from the `Retry-After` header, when the backend sent one. */
  retryAfterSeconds?: number
}

interface HttpErrorLike {
  response?: {
    status?: number
    data?: { detail?: unknown }
    headers?: Record<string, unknown>
  }
}

function detailOf(err: unknown): string {
  const detail = (err as HttpErrorLike)?.response?.data?.detail
  return typeof detail === 'string' ? detail : ''
}

function retryAfterOf(err: unknown): number | undefined {
  const headers = (err as HttpErrorLike)?.response?.headers
  const raw = headers?.['retry-after'] ?? headers?.['Retry-After']
  if (raw == null) return undefined
  const seconds = Number(raw)
  return Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : undefined
}

/** "in about 3 minutes" / "in about 45 seconds" — for lockout copy. */
export function formatRetryAfter(seconds: number): string {
  if (seconds < 90) return `${seconds} second${seconds === 1 ? '' : 's'}`
  const minutes = Math.ceil(seconds / 60)
  return `${minutes} minute${minutes === 1 ? '' : 's'}`
}

/**
 * The backend answers BOTH "that code is wrong" and "that challenge token is
 * dead" with 401. The only signal separating them is the detail string, so we
 * sniff it — and callers that can also track the challenge TTL client-side
 * should prefer their own countdown, which does not depend on copy staying
 * stable.
 */
function looksLikeDeadToken(detail: string): boolean {
  const d = detail.toLowerCase()
  return d.includes('expired') || d.includes('start over')
}

export function describeMfaError(err: unknown): MfaErrorInfo {
  const status = (err as HttpErrorLike)?.response?.status
  const detail = detailOf(err)
  const retryAfterSeconds = retryAfterOf(err)

  if (status === 429) {
    if (retryAfterSeconds !== undefined) {
      return {
        kind: 'locked_out',
        status,
        retryAfterSeconds,
        message:
          detail ||
          `Too many failed attempts. This account is locked for about ${formatRetryAfter(retryAfterSeconds)}.`,
      }
    }
    return {
      kind: 'rate_limited',
      status,
      message: detail || 'Too many attempts from this device. Wait a minute and try again.',
    }
  }

  if (status === 503) {
    return {
      kind: 'unavailable',
      status,
      retryAfterSeconds,
      // Deliberately says nothing about the credentials being wrong.
      message:
        "We can't verify your second factor right now — this is a problem on our side, not with your code. Try again shortly, and contact an administrator if it persists.",
    }
  }

  if (status === 409) {
    const enrolled = detail.toLowerCase().includes('already')
    return {
      kind: enrolled ? 'already_enrolled' : 'not_enrolled',
      status,
      message:
        detail ||
        (enrolled
          ? 'MFA is already enabled on this account.'
          : 'MFA is not enabled on this account.'),
    }
  }

  if (status === 403) {
    return {
      kind: 'forbidden',
      status,
      message: detail || 'That action is not permitted for this session.',
    }
  }

  if (status === 401) {
    if (looksLikeDeadToken(detail)) {
      return {
        kind: 'challenge_expired',
        status,
        message:
          detail || 'That sign-in request expired. Enter your password again to start over.',
      }
    }
    return {
      kind: 'invalid_code',
      status,
      message: detail || 'That code did not match. Check your authenticator and try again.',
    }
  }

  if (status === 400) {
    return {
      kind: 'bad_request',
      status,
      message: detail || 'That request could not be completed. Check the details and retry.',
    }
  }

  return {
    kind: 'unknown',
    status,
    message: detail || 'Something went wrong. Please try again.',
  }
}
