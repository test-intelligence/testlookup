/**
 * MFA wire contract — transcribed verbatim from the live backend
 * (`backend/app/models/schemas.py`, `backend/app/routers/mfa.py`).
 *
 * Nothing here is inferred. Where the shape surprised us, the surprise is
 * documented inline so the next reader does not "fix" it back to a guess.
 */

/** Roles are UPPERCASE on the wire (`postgres.UserRole` is a str enum of its own names). */
export type MfaRole = 'VIEWER' | 'TESTER' | 'QA_ENGINEER' | 'QA_LEAD' | 'ADMIN'

export const MFA_ROLES: MfaRole[] = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']

/** The second factors `/auth/mfa/verify` accepts. Always `["totp", "recovery_code"]` today. */
export type MfaMethod = 'totp' | 'recovery_code'

// ── Login: one endpoint, three possible 200 bodies ───────────────────────────

/**
 * The ordinary success body. The two extra `?: undefined` members are the
 * discriminant negatives — without them TypeScript cannot narrow the union by
 * `'mfa_required' in res`, because the backend has no shared tag field.
 */
export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  must_change_password: boolean
  mfa_required?: undefined
  mfa_enrollment_required?: undefined
}

/** User has MFA on: prove a second factor within `expires_in` seconds. */
export interface MfaChallengeResponse {
  mfa_required: true
  challenge_token: string
  expires_in: number
  methods: MfaMethod[]
  mfa_enrollment_required?: undefined
}

/**
 * Workspace policy requires MFA for this user's role and they have not
 * enrolled. `required_for_role` is the *policy threshold* role (or null when
 * the policy applies to everyone), not necessarily the user's own role.
 */
export interface MfaEnrollmentRequiredResponse {
  mfa_enrollment_required: true
  enrollment_token: string
  expires_in: number
  required_for_role: string | null
  mfa_required?: undefined
}

export type LoginResponse =
  | TokenResponse
  | MfaChallengeResponse
  | MfaEnrollmentRequiredResponse

export function isMfaChallenge(res: LoginResponse): res is MfaChallengeResponse {
  return res.mfa_required === true
}

export function isMfaEnrollmentRequired(
  res: LoginResponse,
): res is MfaEnrollmentRequiredResponse {
  return res.mfa_enrollment_required === true
}

export function isTokenResponse(res: LoginResponse): res is TokenResponse {
  return !isMfaChallenge(res) && !isMfaEnrollmentRequired(res)
}

// ── Enrollment ───────────────────────────────────────────────────────────────

export interface MfaEnrollStartRequest {
  /** Omit when the caller already holds a session (Bearer). */
  enrollment_token?: string
}

export interface MfaEnrollStartResponse {
  /** Base32 TOTP seed. Shown to the user as the manual-entry fallback. */
  secret: string
  otpauth_uri: string
  issuer: string
  account_name: string
  /** 6 today. */
  digits: number
  /** 30 today. */
  period_seconds: number
}

export interface MfaEnrollConfirmRequest {
  code: string
  enrollment_token?: string
}

export interface MfaEnrollConfirmResponse {
  enabled: true
  /** Exactly 10 codes, shown once and never again. */
  recovery_codes: string[]
  /**
   * Non-null ONLY on the forced-enrollment path (the request carried an
   * `enrollment_token`). On the voluntary path the caller keeps its session
   * and this is null.
   */
  tokens: TokenResponse | null
}

// ── Challenge verification ───────────────────────────────────────────────────

export interface MfaVerifyRequest {
  challenge_token: string
  code?: string
  /**
   * Backend precedence: if `recovery_code` is present, `code` is ignored
   * entirely. Send exactly one.
   */
  recovery_code?: string
}

// ── Management (Bearer, interactive session only — API keys get a 403) ───────

export interface MfaSecondFactor {
  code?: string
  recovery_code?: string
}

export interface MfaDisableRequest extends MfaSecondFactor {
  password: string
}

export interface MfaRecoveryCodesRequest extends MfaSecondFactor {
  password: string
}

export interface MfaRecoveryCodesResponse {
  recovery_codes: string[]
}

export interface MfaStatus {
  enabled: boolean
  /** ISO-8601 with offset, or null when never enrolled. */
  enrolled_at: string | null
  recovery_codes_remaining: number
  /** Already net of SSO — true only when the policy binds *and* SSO does not manage this user. */
  required_by_policy: boolean
  sso_managed: boolean
  /**
   * Enrolled but the stored TOTP seed cannot be decrypted. This is BROKEN, not
   * "off": the user's next login 503s. Never render it as "not enrolled".
   */
  secret_unreadable: boolean
}

// ── Workspace policy (read QA_LEAD+, write ADMIN) ────────────────────────────

export interface MfaPolicy {
  require_mfa: boolean
  /** null = the requirement applies to every role. */
  required_for_role: MfaRole | null
  lockout_enabled: boolean
  lockout_threshold: number
  lockout_duration_minutes: number
}

export interface MfaPolicyUpdate {
  require_mfa?: boolean
  required_for_role?: MfaRole | null
  lockout_enabled?: boolean
  /** Backend bounds: 3..100. */
  lockout_threshold?: number
  /** Backend bounds: 1..1440. */
  lockout_duration_minutes?: number
  /**
   * The ONLY way to null out `required_for_role`. Sending
   * `required_for_role: null` on its own is a no-op — the backend merges with
   * `exclude_none=True`.
   */
  clear_required_for_role?: boolean
}
