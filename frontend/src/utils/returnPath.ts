/**
 * Where to go after signing in, from the location `ProtectedRoute` saved.
 *
 * VIZ-306: a shared report link carries its filters in the QUERY STRING
 * (`?release=…&suites=…&window=…`). Returning to the pathname alone signed the
 * user in to an unfiltered page — the link "worked" and showed the wrong data.
 * So the full path is kept: pathname + search + hash.
 *
 * Only an in-app absolute path is honoured. The value comes from router state
 * (not the URL), but the check costs nothing and keeps this from ever becoming
 * an open redirect: anything not starting with a single `/` — including a
 * protocol-relative `//evil.example` — falls back to the default.
 */
export const DEFAULT_RETURN_PATH = '/overview'

interface SavedLocation {
  pathname?: unknown
  search?: unknown
  hash?: unknown
}

export function returnPathFrom(from: SavedLocation | null | undefined): string {
  const pathname = typeof from?.pathname === 'string' ? from.pathname : ''
  if (!pathname || pathname === '/reset-password') return DEFAULT_RETURN_PATH
  if (!pathname.startsWith('/') || pathname.startsWith('//') || pathname.includes('\\')) {
    return DEFAULT_RETURN_PATH
  }
  const search = typeof from?.search === 'string' && from.search.startsWith('?') ? from.search : ''
  const hash = typeof from?.hash === 'string' && from.hash.startsWith('#') ? from.hash : ''
  return `${pathname}${search}${hash}`
}

/**
 * The same rule for a path that arrives as a string: the SSO callback's `next`,
 * which round-tripped through the identity provider as the SAML RelayState.
 * The server already drops anything that is not app-relative; this is the
 * browser's own check before it navigates.
 */
export function returnPathFromString(raw: string | null | undefined): string {
  if (typeof raw !== 'string' || !raw.startsWith('/')) return DEFAULT_RETURN_PATH
  try {
    const base = 'https://app.invalid'
    const url = new URL(raw, base)
    if (url.origin !== base) return DEFAULT_RETURN_PATH
    return returnPathFrom({ pathname: url.pathname, search: url.search, hash: url.hash })
  } catch {
    return DEFAULT_RETURN_PATH
  }
}
