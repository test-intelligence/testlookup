/**
 * The report scope in the address bar (VIZ-306, contract C1 "URL mapping").
 *
 * Keys, and ONLY these keys:
 *
 *   release  repeatable, release UUIDs or the `unattributed` sentinel. The key
 *            `ReleasePicker` has always used, so every link shared before
 *            multi-select still opens with its filter.
 *   suites   repeatable, suite names (1–500 code points).
 *   window   integer days, 1–365.
 *
 * `suite` and `days` are page-local elsewhere (Run compare, Run detail, Test
 * management and the Intelligence hub read `?suite`; Suite detail reads
 * `?days`), and `page, status, mode, tab, scope, q, name, left, right, event,
 * upload, report_version` belong to individual pages. This module never reads
 * or writes any of them; `writeScopeParams` touches only its own three keys
 * and leaves every other parameter — value, order and repetition — exactly as
 * it found it.
 *
 * Ids only. Nothing personal goes in the URL.
 *
 * Parsing NEVER throws: a URL is user input, and a malformed one must degrade
 * to "that value was dropped" (surfaced as a notice), not to a crashed page.
 */
import { RELEASE_CAP } from '@/store/releaseStore'
import { isValidSuiteName, SUITE_CAP } from '@/store/suiteStore'
import { DROP_REASONS, type DroppedNotice } from '@/store/scopeNoticeStore'

export const SCOPE_URL_KEYS = {
  release: 'release',
  suites: 'suites',
  window: 'window',
} as const

/** Query keys that belong to individual pages. The global sync must never
 *  read or write them — asserted by a collision test. */
export const RESERVED_URL_KEYS = [
  'suite',
  'days',
  'page',
  'status',
  'mode',
  'tab',
  'scope',
  'q',
  'name',
  'left',
  'right',
  'event',
  'upload',
  'report_version',
] as const

/** Must equal `UNATTRIBUTED` in `backend/app/core/release_filter.py` and
 *  `UNATTRIBUTED_RELEASE` in `ReleasePicker`. */
export const UNATTRIBUTED = 'unattributed'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export const WINDOW_MIN_DAYS = 1
export const WINDOW_MAX_DAYS = 365

/** A release id the contract accepts: a UUID or the sentinel. */
export function isValidReleaseId(value: unknown): value is string {
  return typeof value === 'string' && (value === UNATTRIBUTED || UUID_RE.test(value))
}

/** The scope a URL carries. `null` for a dimension means its key is ABSENT —
 *  distinct from present-but-empty-after-validation (`[]`). */
export interface UrlScope {
  releaseIds: string[] | null
  suiteNames: string[] | null
  windowDays: number | null
}

export interface ParsedUrlScope extends UrlScope {
  /** Values the URL named that were not applied, and why. */
  dropped: DroppedNotice[]
}

function toParams(search: URLSearchParams | string | null | undefined): URLSearchParams {
  try {
    if (search instanceof URLSearchParams) return search
    return new URLSearchParams(typeof search === 'string' ? search : '')
  } catch {
    return new URLSearchParams()
  }
}

function safeGetAll(params: URLSearchParams, key: string): string[] | null {
  try {
    if (!params.has(key)) return null
    return params.getAll(key)
  } catch {
    return null
  }
}

/** Shorten an untrusted value for display in a notice. */
function forNotice(value: string): string {
  const chars = [...value]
  return chars.length > 80 ? `${chars.slice(0, 79).join('')}…` : value
}

/**
 * Read the scope out of a query string. Values are validated against C1:
 * malformed ones and anything past the cap are dropped and reported in
 * `dropped`; the rest apply. Never throws.
 */
export function parseScopeParams(search: URLSearchParams | string | null | undefined): ParsedUrlScope {
  const params = toParams(search)
  const dropped: DroppedNotice[] = []

  // ── release ─────────────────────────────────────────────────────────────
  let releaseIds: string[] | null = null
  const rawReleases = safeGetAll(params, SCOPE_URL_KEYS.release)
  if (rawReleases !== null) {
    const malformed: string[] = []
    const seen = new Set<string>()
    const valid: string[] = []
    for (const raw of rawReleases) {
      const v = raw.trim()
      if (v === '') continue
      // UUIDs compare case-insensitively (C1 `unique_release`); store lower.
      const normalized = v.toLowerCase() === UNATTRIBUTED ? UNATTRIBUTED : v.toLowerCase()
      if (!isValidReleaseId(normalized)) {
        malformed.push(forNotice(v))
        continue
      }
      if (seen.has(normalized)) continue
      seen.add(normalized)
      valid.push(normalized)
    }
    const kept = valid.slice(0, RELEASE_CAP)
    const overCap = valid.slice(RELEASE_CAP)
    if (malformed.length) dropped.push({ dimension: 'release', values: malformed, reason: DROP_REASONS.malformed })
    if (overCap.length) dropped.push({ dimension: 'release', values: overCap, reason: DROP_REASONS.overCap(RELEASE_CAP) })
    releaseIds = kept
  }

  // ── suites ──────────────────────────────────────────────────────────────
  let suiteNames: string[] | null = null
  const rawSuites = safeGetAll(params, SCOPE_URL_KEYS.suites)
  if (rawSuites !== null) {
    const malformed: string[] = []
    const seen = new Set<string>()
    const valid: string[] = []
    for (const raw of rawSuites) {
      if (raw === '') continue
      if (!isValidSuiteName(raw)) {
        malformed.push(forNotice(raw))
        continue
      }
      if (seen.has(raw)) continue
      seen.add(raw)
      valid.push(raw)
    }
    const kept = valid.slice(0, SUITE_CAP)
    const overCap = valid.slice(SUITE_CAP).map(forNotice)
    if (malformed.length) dropped.push({ dimension: 'suite', values: malformed, reason: DROP_REASONS.malformed })
    if (overCap.length) dropped.push({ dimension: 'suite', values: overCap, reason: DROP_REASONS.overCap(SUITE_CAP) })
    suiteNames = kept
  }

  // ── window ──────────────────────────────────────────────────────────────
  let windowDays: number | null = null
  const rawWindow = safeGetAll(params, SCOPE_URL_KEYS.window)
  if (rawWindow !== null && rawWindow.length > 0) {
    // First value only; digits only (no "7.5", "1e2", "0x10", " 7").
    const w = rawWindow[0]
    if (/^\d{1,3}$/.test(w)) {
      const n = Number(w)
      if (n >= WINDOW_MIN_DAYS && n <= WINDOW_MAX_DAYS) windowDays = n
    }
  }

  return { releaseIds, suiteNames, windowDays, dropped }
}

/** A canonical string for one dimension's values, for "did this change?"
 *  comparisons. Sorted, because order is not meaning (OR within). */
export function canonicalValues(values: readonly string[] | null | undefined): string | null {
  if (values === null || values === undefined) return null
  return JSON.stringify([...values].sort())
}

/**
 * Return a COPY of `base` with this module's keys rewritten from `scope`.
 *
 *  - a dimension that is `undefined` in `scope` is left exactly as it is;
 *  - `null` or `[]` removes the key;
 *  - values are written sorted, so one selection has one URL.
 *
 * Every other key is untouched.
 */
export function writeScopeParams(
  base: URLSearchParams | string,
  scope: Partial<UrlScope>,
): URLSearchParams {
  const next = new URLSearchParams(typeof base === 'string' ? base : base.toString())
  const writeList = (key: string, values: readonly string[] | null | undefined) => {
    if (values === undefined) return
    next.delete(key)
    for (const v of [...(values ?? [])].sort()) next.append(key, v)
  }
  writeList(SCOPE_URL_KEYS.release, scope.releaseIds)
  writeList(SCOPE_URL_KEYS.suites, scope.suiteNames)
  if (scope.windowDays !== undefined) {
    next.delete(SCOPE_URL_KEYS.window)
    // Only a window the contract accepts (an integer 1-365). "All time" is
    // 0 days on /runs and /live: it is written as NO key — `?window=0` is a
    // link every reader rejects (and drops with a notice).
    const days = scope.windowDays
    if (days !== null && Number.isInteger(days) && days >= WINDOW_MIN_DAYS && days <= WINDOW_MAX_DAYS) {
      next.set(SCOPE_URL_KEYS.window, String(days))
    }
  }
  return next
}

/**
 * Routes whose URL carries the scope (VIZ-301's report routes, plus the other
 * pages that are filtered by it). The sync WRITES only on these, and only
 * these treat an absent key on Back as "the earlier state had no filter". Any
 * route still ADOPTS a scope a link carries, as `?release=` always has.
 */
export const SCOPE_ROUTE_PATTERNS: readonly RegExp[] = [
  /^\/overview\/?$/,
  /^\/trends\/?$/,
  /^\/coverage\/?$/,
  /^\/coverage\/suite\/?$/,
  /^\/failures\/?$/,
  /^\/defects\/?$/,
  /^\/reports\/summary\/?$/,
  /^\/value-metrics\/?$/,
  /^\/intelligence\/?$/,
  /^\/runs\/[^/]+\/intelligence\/?$/,
  /^\/release-gate(\/[^/]+)?\/?$/,
  /^\/runs\/compare\/?$/,
  /^\/flaky-coach\/?$/,
  /^\/runs\/?$/,
  /^\/live\/?$/,
  /^\/my-failures\/?$/,
  /^\/deep-investigate(\/[^/]+)?\/?$/,
]

export function isScopeRoute(pathname: string): boolean {
  return SCOPE_ROUTE_PATTERNS.some((re) => re.test(pathname))
}
