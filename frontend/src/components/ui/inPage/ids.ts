/**
 * Id shapes a detail key in the URL is checked against BEFORE it is used
 * (W27 PAN-01, the in-page rule).
 *
 * A deep link is input from anyone: `?release=../../admin/x` interpolated into
 * `/api/v1/releases/${id}` becomes `GET /api/v1/admin/x`, because the browser
 * resolves `..` in a request URL. So a page reads its detail id through
 * `useDetailParam`, whose `accept` is one of these, and a value that fails is
 * never put in a request path: the page shows its not-found card instead.
 */

/** A server id (every row id the API hands out is a UUID). Case-insensitive. */
export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function isUuid(value: string | null | undefined): value is string {
  return typeof value === 'string' && UUID_RE.test(value)
}

/**
 * A client-side key (a /flaky fingerprint, a row id in a fixture): letters,
 * digits, `_`, `-`, `:`; 1-128 characters. No `.` and no `/`, so it can never
 * walk a request path, and no `?`, `#`, `%` or `&`, so it can never add a
 * query or a fragment to one.
 */
export const DETAIL_TOKEN_RE = /^[A-Za-z0-9_:-]{1,128}$/

export function isDetailToken(value: string | null | undefined): value is string {
  return typeof value === 'string' && DETAIL_TOKEN_RE.test(value)
}
