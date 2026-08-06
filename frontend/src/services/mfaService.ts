import type {
  MfaDisableRequest,
  MfaEnrollConfirmResponse,
  MfaEnrollStartResponse,
  MfaPolicy,
  MfaPolicyUpdate,
  MfaRecoveryCodesRequest,
  MfaRecoveryCodesResponse,
  MfaStatus,
  MfaVerifyRequest,
  TokenResponse,
} from '@/types/mfa'
import { getData, postData, putData } from './http'

/**
 * TOTP MFA — enrollment, login challenge, recovery codes, workspace policy.
 *
 * Rides the shared Axios base (`services/api.ts`) via the `http` helpers.
 *
 * Two things about this contract that are easy to get wrong:
 *
 *  - `/auth/mfa/verify` and `/auth/mfa/enroll/*` answer a wrong code or a
 *    stale token with **401**, and they are called from the login screen with
 *    no session. `api.ts` excludes the `/api/v1/auth/mfa/` prefix from the
 *    401-refresh-and-retry path for exactly that reason — do not re-add it.
 *  - `enroll/confirm` returns `tokens` only on the forced-enrollment path.
 *    On the voluntary path it is null and the caller keeps its session.
 */
export const mfaService = {
  /** Begin enrollment. Pass `enrollmentToken` on the forced path, omit when signed in. */
  enrollStart: (enrollmentToken?: string) =>
    postData<MfaEnrollStartResponse, { enrollment_token?: string }>(
      '/api/v1/auth/mfa/enroll/start',
      enrollmentToken ? { enrollment_token: enrollmentToken } : {},
    ),

  /** Confirm enrollment with a live TOTP code. 400 when the code does not match. */
  enrollConfirm: (code: string, enrollmentToken?: string) =>
    postData<MfaEnrollConfirmResponse, { code: string; enrollment_token?: string }>(
      '/api/v1/auth/mfa/enroll/confirm',
      enrollmentToken ? { code, enrollment_token: enrollmentToken } : { code },
    ),

  /** Redeem a login challenge. 401 on a wrong code OR a spent/expired token. */
  verify: (payload: MfaVerifyRequest) =>
    postData<TokenResponse, MfaVerifyRequest>('/api/v1/auth/mfa/verify', payload),

  /** 204 No Content on success. Interactive session only — an API key gets 403. */
  disable: (payload: MfaDisableRequest) =>
    postData<void, MfaDisableRequest>('/api/v1/auth/mfa/disable', payload),

  /** Regenerate — invalidates every previously issued code. */
  regenerateRecoveryCodes: (payload: MfaRecoveryCodesRequest) =>
    postData<MfaRecoveryCodesResponse, MfaRecoveryCodesRequest>(
      '/api/v1/auth/mfa/recovery-codes',
      payload,
    ),

  status: () => getData<MfaStatus>('/api/v1/auth/mfa/status'),

  getPolicy: () => getData<MfaPolicy>('/api/v1/settings/mfa-policy'),

  updatePolicy: (payload: MfaPolicyUpdate) =>
    putData<MfaPolicy, MfaPolicyUpdate>('/api/v1/settings/mfa-policy', payload),
}
