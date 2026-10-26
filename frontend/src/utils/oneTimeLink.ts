/**
 * Show a one-time link (invitation, password reset) the server returned (W17b ADM-01/02).
 *
 * The server answers an absolute `{PUBLIC_BASE_URL}/accept-invite#token=…` so the
 * same link works in an email. On screen we never render a server-supplied
 * origin: only the expected path and its `#token=` fragment are kept, re-based
 * on this page's origin. A compromised or buggy backend therefore cannot turn
 * the copy button into a phishing redirect (`//evil.com`, `https://evil…`,
 * `javascript:`), and a wrong `PUBLIC_BASE_URL` cannot hand out a dead link.
 */
export type OneTimeLinkPath = '/accept-invite' | '/reset-password/confirm'

export function buildOneTimeLink(raw: string, expectedPath: OneTimeLinkPath): string {
  if (typeof raw !== 'string' || raw.length === 0) return ''
  let parsed: URL
  try {
    parsed = new URL(raw, window.location.origin)
  } catch {
    return ''
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return ''
  if (parsed.pathname !== expectedPath) return ''
  const token = new URLSearchParams(parsed.hash.replace(/^#/, '')).get('token')
  if (!token || !/^[A-Za-z0-9_-]+$/.test(token)) return ''
  return `${window.location.origin}${expectedPath}#token=${token}`
}

export const INVALID_INVITE = 'This invitation link is invalid, expired or already used.'
export const INVALID_RESET = 'This reset link is invalid, expired or already used.'
export const RESET_ON_ITS_WAY = 'If an account exists for that address, a reset link is on its way.'
export const ASK_AN_ADMIN = 'Ask an administrator for a reset link.'

/** `QA_ENGINEER` -> `QA Engineer`. */
export function roleLabel(role: string): string {
  return String(role || '')
    .split('_')
    .map((w) => (w === 'QA' ? 'QA' : w.charAt(0) + w.slice(1).toLowerCase()))
    .join(' ')
}
