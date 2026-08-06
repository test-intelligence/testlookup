import useSWR from 'swr'
import { mfaService } from '@/services/mfaService'
import type {
  MfaDisableRequest,
  MfaEnrollConfirmResponse,
  MfaEnrollStartResponse,
  MfaRecoveryCodesRequest,
  MfaRecoveryCodesResponse,
  MfaStatus,
  MfaVerifyRequest,
  TokenResponse,
} from '@/types/mfa'

export const MFA_STATUS_KEY = '/api/v1/auth/mfa/status'

/**
 * The signed-in user's MFA state (Profile → Security).
 *
 * Reads go through SWR — the house rule — so the section refreshes itself
 * after an enroll/disable elsewhere. No polling: this only changes when the
 * user acts, and every mutation below is followed by an explicit `mutate()`.
 */
export function useMfaStatus(enabled = true) {
  return useSWR<MfaStatus>(enabled ? MFA_STATUS_KEY : null, () => mfaService.status(), {
    revalidateOnFocus: false,
    // A 401 here is not retryable in a useful way, and 503 means the seed is
    // unreadable — hammering it will not help and would mask the real state.
    shouldRetryOnError: false,
  })
}

// ── Imperative mutations ─────────────────────────────────────────────────────
// These are side-effecting POSTs, not SWR resources. Thin wrappers so pages
// never import the service directly and the whole surface is mockable in one
// place during tests.

/** Voluntary path when signed in; forced path when `enrollmentToken` is given. */
export function startMfaEnrollment(
  enrollmentToken?: string,
): Promise<MfaEnrollStartResponse> {
  return mfaService.enrollStart(enrollmentToken)
}

/**
 * Confirm with a live code. The response carries the one-and-only copy of the
 * recovery codes, plus `tokens` (non-null only on the forced path).
 */
export function confirmMfaEnrollment(
  code: string,
  enrollmentToken?: string,
): Promise<MfaEnrollConfirmResponse> {
  return mfaService.enrollConfirm(code, enrollmentToken)
}

/** Redeem a login challenge for a real token pair. */
export function verifyMfaChallenge(payload: MfaVerifyRequest): Promise<TokenResponse> {
  return mfaService.verify(payload)
}

/** 204 on success. 403 when workspace policy forbids self-disable. */
export function disableMfa(payload: MfaDisableRequest): Promise<void> {
  return mfaService.disable(payload)
}

/** Issues a fresh set of 10 and invalidates every previous code. */
export function regenerateMfaRecoveryCodes(
  payload: MfaRecoveryCodesRequest,
): Promise<MfaRecoveryCodesResponse> {
  return mfaService.regenerateRecoveryCodes(payload)
}
