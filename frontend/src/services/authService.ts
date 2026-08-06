import type { User } from '@/store/authStore'
import type { LoginResponse } from '@/types/mfa'
import { api } from './api'
import { getData } from './http'

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
