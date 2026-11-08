import { getData } from './http'

/** What the sign-in page may offer. Public (`GET /api/v1/auth/options`): read before anyone signs in. */
export interface AuthOptions {
  registration_enabled: boolean
  /** W17b ADM-02: SMTP is configured, so "Forgot password?" offers the email form. Older servers omit it. */
  password_reset_email?: boolean
  /** The public read-only demo (server `DEMO_MODE`): one-click demo sign-in and the demo banner. Older servers omit it. */
  demo_mode?: boolean
}

/**
 * Quiet on failure: the page then offers sign-in only, which is the safe default.
 *
 * Its own module, not part of `authService`: the app shell reads it on every
 * page (the demo banner), and importing `authService` there would make the
 * whole sign-in service part of the eager bundle (scripts/check-bundle-budget.mjs).
 */
export function getAuthOptions(): Promise<AuthOptions> {
  return getData<AuthOptions>('/api/v1/auth/options', { suppressToast: true })
}
