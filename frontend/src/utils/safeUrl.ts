/**
 * URL validation helpers — defense against open-redirect and javascript:
 * injection through hrefs or window.location assignment.
 *
 * Backend data is trusted for content but not for side effects. A compromised
 * upstream (or a malicious SAML IdP metadata payload) must not be able to push
 * `javascript:alert(1)` into an anchor or a redirect.
 */

const ALLOWED_EXTERNAL_SCHEMES = new Set(['https:', 'http:'])

/**
 * Return true if `url` is a syntactically valid external URL whose scheme is
 * safe to assign to an anchor `href` or `window.location.href`.
 *
 * In production, only `https:` is accepted. In dev, `http:` is also allowed
 * to support local IdPs and homelab Jira instances during development.
 */
export function isSafeExternalUrl(url: unknown): url is string {
  if (typeof url !== 'string' || url.length === 0) return false
  // Reject any control characters, whitespace, or CR/LF — URL() tolerates them
  // but they enable header-injection-style tricks in some browsers.
  // eslint-disable-next-line no-control-regex
  if (/[\s\u0000-\u001F\u007F]/.test(url)) return false
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  if (!ALLOWED_EXTERNAL_SCHEMES.has(parsed.protocol)) return false
  if (!import.meta.env.DEV && parsed.protocol !== 'https:') return false
  return true
}

/**
 * Return `url` if it passes {@link isSafeExternalUrl}, else the fallback.
 * Intended for inline use in JSX: `href={safeExternalUrl(x, '#')}`.
 */
export function safeExternalUrl(url: unknown, fallback = '#'): string {
  return isSafeExternalUrl(url) ? url : fallback
}
