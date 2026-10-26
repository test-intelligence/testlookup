import type { User } from '@/store/authStore'
import type { LoginResponse, MfaEnrollmentRequiredResponse, TokenResponse } from '@/types/mfa'
import { api } from './api'
import { getData, postData } from './http'

/**
 * Password sign-in.
 *
 * `POST /api/v1/auth/login` binds FastAPI's `OAuth2PasswordRequestForm`, so the
 * body is form-encoded, not JSON. Since MFA shipped it answers 200 with one of
 * THREE bodies (`LoginResponse` discriminates them): the ordinary token pair,
 * an MFA challenge, or a forced-enrollment handoff. Callers must narrow before
 * touching `access_token`.
 *
 * Error surface worth knowing: 401 bad credentials, 403 disabled/SSO-required,
 * 429 account lockout (carries `Retry-After`) or IP rate limit (does not),
 * 503 when the stored TOTP seed cannot be read.
 */
export function loginWithPassword(
  username: string,
  password: string,
): Promise<LoginResponse> {
  const params = new URLSearchParams()
  params.append('username', username)
  params.append('password', password)
  return api
    .post<LoginResponse>('/api/v1/auth/login', params, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
    .then(({ data }) => data)
}

/**
 * Fetch the signed-in user. `accessToken` is passed explicitly during login,
 * because the token is not in the auth store yet when this runs.
 */
export function fetchCurrentUser(accessToken?: string): Promise<User> {
  return getData<User>(
    '/api/v1/auth/me',
    accessToken ? { headers: { Authorization: `Bearer ${accessToken}` } } : undefined,
  )
}

/** What the sign-in page may offer. Public (`GET /api/v1/auth/options`): read before anyone signs in. */
export interface AuthOptions {
  registration_enabled: boolean
  /** W17b ADM-02: SMTP is configured, so "Forgot password?" offers the email form. Older servers omit it. */
  password_reset_email?: boolean
}

/** Quiet on failure: the page then offers sign-in only, which is the safe default. */
export function getAuthOptions(): Promise<AuthOptions> {
  return getData<AuthOptions>('/api/v1/auth/options', { suppressToast: true })
}

// ── W17b ADM-01 / ADM-02: one-time links (public; the token travels in a JSON body, never a URL) ──

/** What the accept page shows before the form. 400 = invalid, expired, used or revoked (one answer). */
export interface InvitationPreview {
  email: string
  role: User['role']
  invited_by_name: string | null
  expires_at: string
  project_names: string[]
  sso_required: boolean
}

export function previewInvitation(token: string): Promise<InvitationPreview> {
  return postData<InvitationPreview>('/api/v1/auth/invitations/preview', { token }, { suppressToast: true })
}

export interface AcceptInvitationBody {
  token: string
  username: string
  full_name?: string
  password: string
}

/** Signed in (tokens), or MFA set-up owed first (the login flow's enrollment shape). */
export function acceptInvitation(
  body: AcceptInvitationBody,
): Promise<TokenResponse | MfaEnrollmentRequiredResponse> {
  return postData<TokenResponse | MfaEnrollmentRequiredResponse>('/api/v1/auth/invitations/accept', body, {
    suppressToast: true,
  })
}

/** Always 202 with the same body, whether or not the address has an account. */
export function requestPasswordReset(email: string): Promise<{ detail: string }> {
  return postData<{ detail: string }>('/api/v1/auth/password/forgot', { email }, { suppressToast: true })
}

export function resetPasswordWithToken(token: string, new_password: string): Promise<void> {
  return postData<void>('/api/v1/auth/password/reset', { token, new_password }, { suppressToast: true })
}
