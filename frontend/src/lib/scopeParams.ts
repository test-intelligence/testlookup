/**
 * Wire and cache-key helpers for the multi-value scope axes (contract C1).
 *
 * A scope value reaching a service or an SWR key has one of three shapes:
 *
 *   - `null` / `undefined` / `''` / `[]`  — no filter
 *   - `'R1'`                              — one value (every legacy caller)
 *   - `['R1', 'R2']`                      — several (VIZ-303, flag on)
 *
 * The WIRE rule (C1, "omit-when-empty, scalar-for-one"):
 *   none → the parameter is ABSENT (not `release_id=`, not `null`);
 *   one  → a single scalar `release_id=R1`, byte-identical to the request the
 *          endpoint received before multi-select existed, so every test that
 *          asserts a scalar keeps passing unmodified;
 *   many → a repeated parameter `release_id=R1&release_id=R2`. That relies on
 *          the shared axios instance's `paramsSerializer: { indexes: null }`
 *          (services/api.ts): the axios default is `release_id[]=`, which
 *          FastAPI silently drops.
 *
 * The KEY rule: an SWR key must never carry an array built during render. A
 * fresh array is a fresh identity every render, and anything keyed or memoised
 * on it refetches in a loop. `scopeKey` reduces a value to a SORTED, JOINED
 * string — and to the value itself for a scalar, so a flag-off key is
 * byte-identical to today's.
 */

/** A scope value as hooks and services pass it around. */
export type ScopeValue = string | readonly string[] | null | undefined

/** Separator for joined keys. A control character, so no id or suite name
 *  (untrusted plain text, but never containing U+001F in practice) can forge a
 *  boundary between two values. */
export const SCOPE_KEY_SEP = '\u001f'
const KEY_SEP = SCOPE_KEY_SEP

/** Distinct, non-empty values, sorted. Order never matters for a filter
 *  (OR within a dimension), so `[R2, R1]` and `[R1, R2]` are one scope. */
export function normalizeScope(value: ScopeValue): string[] {
  if (value === null || value === undefined) return []
  // Defensive: a store field restored from storage or set by an old writer
  // may be any JSON value. Anything that is neither a string nor an array is
  // no filter — never a throw in the middle of a render.
  const list: readonly unknown[] = typeof value === 'string' ? [value] : Array.isArray(value) ? value : []
  return Array.from(
    new Set(list.filter((v): v is string => typeof v === 'string' && v !== '')),
  ).sort()
}

/**
 * The value a service should receive: `null` for none, the scalar for exactly
 * one, a sorted array for several. The one-value case collapsing to a scalar is
 * what keeps a single-release request identical to the legacy one.
 */
export function scopeArg(value: ScopeValue): string | string[] | null {
  const list = normalizeScope(value)
  if (list.length === 0) return null
  if (list.length === 1) return list[0]
  return list
}

/**
 * For an endpoint whose parameter is SCALAR-only on the backend: the value
 * when exactly one is selected, else `null`. (No E3 read needs it any more —
 * `/runs`, `/me/assigned-failures` and `/stream/active` take a repeatable
 * `release_id` / `suite_name` since E3; use `scopeArg` for those. It remains
 * for any endpoint still declared `Optional[str]`.)
 *
 * Several values must NOT be sent there. FastAPI binds a repeated key to a
 * scalar parameter by keeping the LAST one, so `release_id=R1&release_id=R2`
 * would silently answer for R2 alone under a heading that says R1 and R2 —
 * confidently wrong. Omitting the filter answers for all releases instead,
 * which is at least what an unfiltered panel says it is; the panel should
 * carry a "Not filtered by release" badge (VIZ-307) in that state.
 */
export function singleScopeArg(value: ScopeValue): string | null {
  const list = normalizeScope(value)
  return list.length === 1 ? list[0] : null
}

/**
 * The suite a bulk WRITE may be scoped to. A write must never be broader than
 * what is on screen, and the write endpoints take ONE `suite_name`: none
 * selected → unscoped, one → that suite, several → refuse (sending none would
 * write to every suite). Callers pass the names the page SHOWS, so the write
 * and its confirmation text agree.
 */
export type BulkWriteSuite = { kind: 'none' } | { kind: 'one'; name: string } | { kind: 'several' }

export function bulkWriteSuite(names: ScopeValue): BulkWriteSuite {
  const list = normalizeScope(names)
  if (list.length === 0) return { kind: 'none' }
  if (list.length === 1) return { kind: 'one', name: list[0] }
  return { kind: 'several' }
}

/**
 * `{}` or `{ [name]: value }` for a query-params spread — the C1 wire rule in
 * one place. Several values go out as an array, which the shared serializer
 * emits as a repeated key.
 */
export function scopeParam(name: string, value: ScopeValue): Record<string, string | string[]> {
  const arg = scopeArg(value)
  return arg === null ? {} : { [name]: arg }
}

/**
 * A stable SWR-key fragment: `null` for none, the scalar itself for one (so a
 * flag-off key is unchanged), a sorted joined string for several. Never an
 * array.
 */
export function scopeKey(value: ScopeValue): string | null {
  const list = normalizeScope(value)
  if (list.length === 0) return null
  return list.join(KEY_SEP)
}

/**
 * An SWR-key part that leaves a scalar (or null/undefined) EXACTLY as it was —
 * so every flag-off key stays byte-identical to the pre-multi-select key — and
 * reduces an array to its sorted, joined `scopeKey`.
 */
export function keyPart(value: ScopeValue): string | null | undefined {
  if (value === null || value === undefined || typeof value === 'string') return value
  return scopeKey(value)
}
