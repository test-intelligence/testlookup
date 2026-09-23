/**
 * Visualization contracts C1–C6 — types plus hand-written runtime guards.
 *
 * The spec is `contracts/viz/README.md` at the repo root. The fixtures beside it
 * are checked by this module (`contracts.test.ts`) AND by the backend's Pydantic
 * models, so a rule that only one side enforces fails that side's suite.
 *
 * Conventions every guard follows:
 *
 *   - Signature `(input: unknown) => { ok: true; value: T } | { ok: false; errors }`.
 *   - Every error string STARTS with its rule id (`totals_subset: …`). Rule ids
 *     come from the README tables. Shape problems the README gives no id to use
 *     the local ids listed under `LOCAL_RULE_IDS`.
 *   - The contract is ADDITIVE: unknown extra fields are tolerated everywhere.
 *   - `value` is the INPUT ITSELF, never a rebuilt copy. That is what makes
 *     "unknown keys are preserved" and "untrusted text is never transformed"
 *     true by construction rather than by care: there is no copy step that
 *     could drop a key or normalise a string.
 *   - Strings naming tests / suites / releases / labels are untrusted plain
 *     text. They are never rejected for containing markup and never trimmed,
 *     case-folded or normalised — `unique_*` compares the exact strings.
 *   - Counts are integers: `true` is not a number (`invalid_type`), `1.5` is not
 *     a count (`integer_count`), and 2⁵³ is past what JavaScript reads exactly
 *     (`safe_integer`).
 *   - String lengths count Unicode code points, as Python's `len()` does.
 *   - Before any per-contract rule, ONE iterative walk over the whole payload
 *     (unknown keys included) enforces the payload-wide rules of change rule 8:
 *     `well_formed_string`, `nesting_depth`, `forbidden_key`. It stops at the
 *     first violation and reports only that one.
 *   - An OPTIONAL field may be absent but not `null`, unless the README writes
 *     `| null` for it. The one exception the README makes is a scope window,
 *     where a key present as `null` counts as absent.
 *   - A guard never throws, whatever it is handed.
 *   - Work is bounded on hostile input: once a list is over its cap, the cap is
 *     reported and the items past it are not inspected.
 *
 * No dependency is added for this — the rules are small, and a schema library
 * would sit in the eager bundle of every page that validates a response.
 */

// ── Result ────────────────────────────────────────────────────────────────

export type ValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; errors: string[] }

/** Rule ids used here that the README does not name (shape / local caps). */
export const LOCAL_RULE_IDS = [
  'required_field',
  'invalid_type',
  'invalid_value',
  'unknown_contract',
  'unexpected_error',
] as const

// ── Shared vocabularies ───────────────────────────────────────────────────

/** C5 `dimension_enum`; also the vocabulary of C4 `groupBy`. */
export const VIZ_DIMENSIONS = [
  'day',
  'week',
  'project',
  'release',
  'suite',
  'status',
  'failure_category',
  'branch',
  'environment',
  'ingestion_source',
  'test',
  'error_signature',
] as const
export type VizDimension = (typeof VIZ_DIMENSIONS)[number]

/** `status_vocab` — the TestStatus vocabulary. */
export const VIZ_STATUSES = ['passed', 'failed', 'broken', 'skipped', 'unknown'] as const
export type VizStatus = (typeof VIZ_STATUSES)[number]

export const WIDGET_CHART_TYPES = [
  'line',
  'bar',
  'area',
  'pie',
  'gauge',
  'metric',
  'table',
  'stacked_bar',
  'donut',
] as const
export type WidgetChartType = (typeof WIDGET_CHART_TYPES)[number]

export const WIDGET_TOP_N = [5, 10, 25, 50] as const
export type WidgetTopN = (typeof WIDGET_TOP_N)[number]

/** The sentinel that stands for "runs attributed to no release". */
export const UNATTRIBUTED_RELEASE = 'unattributed'

export const VIZ_LIMITS = {
  releases: 20,
  suites: 50,
  suiteNameLength: 500,
  windowDaysMax: 365,
  windowSpanDays: 366,
  series: 8,
  pointsPerSeries: 366,
  matrixCells: 5400,
  treeNodes: 500,
  graphNodes: 200,
  widgetInstances: 12,
  widgetTitleLength: 120,
  widgetGroupBy: 2,
  drillDepth: 4,
  drillValueLength: 2000,
  /** Change rule 8 `nesting_depth`: containers, counting the payload as 1. */
  nestingDepth: 32,
} as const

/** Change rule 8 `forbidden_key`. */
export const FORBIDDEN_KEYS = ['__proto__', 'constructor', 'prototype'] as const

/**
 * Upper bound on values + keys the payload walk visits. The largest legitimate
 * payload (a 5 400-cell matrix) is ~50 000; JSON input is a tree, so the walk
 * is linear in it. The cap exists for in-memory graphs that share sub-objects,
 * where the number of PATHS grows exponentially with depth.
 */
const PAYLOAD_WALK_BUDGET = 1_000_000

// ── C1 · Scope ────────────────────────────────────────────────────────────

export type ScopeWindow = { days: number } | { from: string; to: string }

export interface Scope {
  /** `null` = all accessible projects. */
  project_id: string | null
  /** UUIDs or the `unattributed` sentinel. */
  release_ids: string[]
  suite_names: string[]
  window: ScopeWindow
}

// ── C2 · Envelope meta ────────────────────────────────────────────────────

export interface EnvelopeMeta {
  schema_version: number
  scope: {
    projects: { id: string; name: string }[]
    releases: { id: string; name: string; status: string }[]
    suites: string[]
    window: { from: string; to: string; days: number; timezone: 'UTC' }
  }
  totals: {
    matched_runs: number
    total_runs: number
    matched_executions: number
    total_executions: number
  }
  pass_rate_basis: 'executions' | 'unique_tests' | null
  ignored_filters: { dimension: 'release' | 'suite' | 'window'; reason: string }[]
  truncated: boolean
  truncated_total: number | null
  measured: boolean
  reason: string | null
  includes_in_progress: number
  partial_day: string | null
  generated_at: string
  as_of: string
  /**
   * Optional (VIZ-203): what each axis lost, because `truncated_total` is one
   * number and a chart can lose buckets AND series at once — 400 suite
   * buckets keyed by 12 environments truncates both, and the single counter
   * could only ever name one of them. Present only when something was
   * truncated; the chart frame badges it per axis.
   */
  truncated_axes?: { x?: TruncatedAxis; series?: TruncatedAxis }
  /**
   * Optional (VIZ-203): rows whose bucket fell outside a generated axis — a
   * run with a future `created_at`. They cannot be drawn; they are counted,
   * so a clock-skewed CI agent does not lose its run in silence.
   */
  outside_window?: {
    buckets: number
    executions: number
    first: string
    last: string
  }
  /**
   * Optional (VIZ-404): whether the series of a comparison chart can be read
   * like-for-like. `/analytics/chart-data` sends it only when the series are
   * keyed by `release` or `branch` and there are at least two of them.
   * ABSENT MEANS "NOT ASSESSED", never "comparable".
   */
  comparability?: Comparability
}

/** C2 `comparability_reason_code`; the README says what each one means. */
export const COMPARABILITY_REASON_CODES = ['different_suites', 'partial_coverage'] as const
export type ComparabilityReasonCode = (typeof COMPARABILITY_REASON_CODES)[number]

export interface Comparability {
  comparable: boolean
  /** Human text naming counts only; `null` exactly when `comparable` is true. */
  reason: string | null
  /** `null` exactly when `comparable` is true. */
  reason_code: ComparabilityReasonCode | null
}

export interface TruncatedAxis {
  /** The dimension on that axis (`suite`, `environment`, `test`, …). */
  dimension: string
  kept: number
  /** How many distinct keys there really were. Always above `kept`. */
  total: number
}

// ── C3 · ChartSeries ──────────────────────────────────────────────────────

export interface SeriesPoint {
  x: string
  /** `null` = no data: drawn as a gap, never as zero. */
  y: number | null
  /** Sample size behind `y`. */
  n: number
  /** Optional (VIZ-203): `false` when `y` could not be computed here. */
  measured?: boolean
  /** Why `measured` is false. Required, non-blank, when it is. */
  reason?: string | null
}

export interface SeriesChart {
  kind: 'series'
  dimensions: string[]
  x_type: 'time' | 'category'
  series: { key: string; label: string; points: SeriesPoint[] }[]
  /** Optional (VIZ-203): display names for `x` values that are ids. */
  x_labels?: Record<string, string>
}

export interface MatrixChart {
  kind: 'matrix'
  value_type: 'rate' | 'count' | 'status'
  x_labels: string[]
  y_labels: string[]
  /** `value: null` = no data: an empty cell, never zero. */
  cells: { x: number; y: number; value: number | VizStatus | null; n: number }[]
}

export interface TreeChart {
  kind: 'tree'
  nodes: {
    id: string
    parent_id: string | null
    label: string
    value: number
    measure: number | null
  }[]
}

export interface GraphChart {
  kind: 'graph'
  nodes: { id: string; label: string; size: number }[]
  edges: { source: string; target: string; weight: number }[]
}

export type ChartSeries = SeriesChart | MatrixChart | TreeChart | GraphChart
export const CHART_SERIES_KINDS = ['series', 'matrix', 'tree', 'graph'] as const

// ── C4 · Widget config ────────────────────────────────────────────────────

export interface WidgetInstanceConfig {
  instanceId: string
  templateId: string
  title?: string
  chartType?: WidgetChartType
  metricVariant?: string
  filters?: Record<string, unknown>
  groupBy?: VizDimension[]
  topN?: WidgetTopN
  scale?: 'linear' | 'log'
  stack?: 'none' | 'absolute' | 'percent'
  bucket?: 'day' | 'week'
  /** Two writers share this object: keys this build does not know are kept. */
  [unknownKey: string]: unknown
}

export interface WidgetConfig {
  page: string
  version: number
  instances: WidgetInstanceConfig[]
  [unknownKey: string]: unknown
}

// ── C5 · Drill path ───────────────────────────────────────────────────────

export interface DrillLevel {
  dimension: VizDimension
  value: string
}

export interface DrillPath {
  /** Ordered from the top level down. */
  path: DrillLevel[]
}

// ── Primitives ────────────────────────────────────────────────────────────

type Fail = (rule: string, message: string) => void
type Dict = Record<string, unknown>

/** Enough to explain a payload; a hostile one cannot grow the list unbounded. */
const MAX_ERRORS = 50

const isDict = (v: unknown): v is Dict =>
  typeof v === 'object' && v !== null && !Array.isArray(v)

/** A real, finite number. `true`, `'1'`, `NaN` and `Infinity` are not. */
const isNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

const isInteger = (v: unknown): v is number => isNumber(v) && Number.isInteger(v)

/**
 * "Not supplied" for a scope WINDOW key only: the README lets a window carry
 * `from: null, to: null` beside `days`. Everywhere else an optional field is
 * absent or has a value — `null` is a wrong value, not an absence.
 */
const isNullOrAbsent = (v: unknown): v is null | undefined => v === null || v === undefined

const oneOf = <T extends string | number>(allowed: readonly T[], v: unknown): v is T =>
  (allowed as readonly unknown[]).includes(v)

/** A short, safe rendering of an untrusted value for an error message. */
function show(v: unknown): string {
  if (typeof v === 'string') return JSON.stringify(v.length > 40 ? `${v.slice(0, 40)}…` : v)
  if (v === null || typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (v === undefined) return 'undefined'
  if (Array.isArray(v)) return 'an array'
  return typeof v === 'object' ? 'an object' : `a ${typeof v}`
}

/**
 * Length in CHARACTERS (code points), to agree with Python's `len()` on the
 * backend — `.length` counts UTF-16 units and would call one emoji two.
 * Stops counting once past `stopAfter`, so a huge string costs no more than a
 * just-too-long one.
 */
function charLength(s: string, stopAfter: number): number {
  let n = 0
  for (const _char of s) {
    n += 1
    if (n > stopAfter) break
  }
  return n
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const isUuid = (v: unknown): v is string => typeof v === 'string' && UUID_RE.test(v)

const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})$/

/**
 * `YYYY-MM-DD` → a UTC day number, or `null` if it is not a real calendar day
 * in years 0001–9999.
 */
function dayNumber(v: unknown): number | null {
  if (typeof v !== 'string') return null
  const m = DAY_RE.exec(v)
  if (!m) return null
  const [year, month, day] = [Number(m[1]), Number(m[2]), Number(m[3])]
  if (year < 1) return null
  // NOT `Date.UTC(year, …)`: it maps years 0–99 to 1900–1999, so 0050-01-01
  // would come back as 1950. `setUTCFullYear` takes the year literally.
  const date = new Date(0)
  date.setUTCFullYear(year, month - 1, day)
  // Out-of-range parts roll over (2026-02-30 → March 2); a round trip catches that.
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return null
  }
  return Math.round(date.getTime() / 86_400_000)
}

/**
 * C2 `utc_instant`: the README's RFC 3339 profile. The regex fixes the shape;
 * the date and the clock are then checked by hand, because `Date.parse` is
 * lenient in engine-specific ways (it rolls some impossible dates over).
 */
const UTC_INSTANT_RE =
  /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d{1,6})?(Z|\+00:00)$/
function isUtcInstant(v: unknown): v is string {
  if (typeof v !== 'string') return false
  const m = UTC_INSTANT_RE.exec(v)
  if (!m || dayNumber(m[1]) === null) return false
  return Number(m[2]) <= 23 && Number(m[3]) <= 59 && Number(m[4]) <= 59
}

/**
 * Change rule 7: whitespace is exactly this set — ECMAScript WhiteSpace and
 * LineTerminator as of today — written out rather than borrowed from
 * `String.prototype.trim`, whose set follows the engine's Unicode version.
 */
/** Change rule 7's whitespace set, as inclusive code-point ranges. */
export const WHITESPACE_RANGES: readonly (readonly [number, number])[] = [
  [0x0009, 0x000d],
  [0x0020, 0x0020],
  [0x00a0, 0x00a0],
  [0x1680, 0x1680],
  [0x2000, 0x200a],
  [0x2028, 0x2029],
  [0x202f, 0x202f],
  [0x205f, 0x205f],
  [0x3000, 0x3000],
  [0xfeff, 0xfeff],
]

/** Only characters from the set (every one is in the BMP, so UTF-16 units suffice). */
function isBlank(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    const unit = s.charCodeAt(i)
    if (!WHITESPACE_RANGES.some(([lo, hi]) => unit >= lo && unit <= hi)) return false
  }
  return true
}

/**
 * Well-formed Unicode: no lone UTF-16 surrogate. Hand-written rather than
 * `String.prototype.isWellFormed`, which is ES2024 (this tsconfig's `lib` is
 * ES2020) and absent from older engines.
 */
function isWellFormed(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    const unit = s.charCodeAt(i)
    if (unit < 0xd800 || unit > 0xdfff) continue
    if (unit >= 0xdc00) return false // a low surrogate with no high one before it
    const next = s.charCodeAt(i + 1) // NaN past the end
    if (!(next >= 0xdc00 && next <= 0xdfff)) return false // a high one with no low one after
    i += 1
  }
  return true
}

/**
 * Change rule 8, over the WHOLE payload — unknown keys and `filters` included,
 * object keys as well as values. Returns the first violation, or `null`.
 *
 * Iterative (an explicit stack), so a 100 000-deep input cannot overflow the
 * call stack; the depth cap stops the descent at 33 anyway. Work is bounded by
 * `PAYLOAD_WALK_BUDGET`, counted as children are queued, so one enormous array
 * is refused before it is copied onto the stack.
 */
function payloadViolation(input: unknown): string | null {
  const stack: [unknown, number][] = [[input, 1]]
  let work = 1
  const overBudget = () =>
    `payload_size: the payload has more than ${PAYLOAD_WALK_BUDGET} values and keys`
  while (stack.length > 0) {
    const [value, depth] = stack.pop() as [unknown, number]
    if (typeof value === 'string') {
      if (!isWellFormed(value)) {
        return 'well_formed_string: a string value contains a lone UTF-16 surrogate'
      }
      continue
    }
    if (typeof value !== 'object' || value === null) continue
    if (depth > VIZ_LIMITS.nestingDepth) {
      return `nesting_depth: containers nest more than ${VIZ_LIMITS.nestingDepth} deep`
    }
    if (Array.isArray(value)) {
      work += value.length
      if (work > PAYLOAD_WALK_BUDGET) return overBudget()
      for (let i = 0; i < value.length; i++) stack.push([value[i], depth + 1])
      continue
    }
    // OWN keys: `JSON.parse('{"__proto__":1}')` makes an own property (it does
    // not set the prototype), and a later merge would. Checked by name as well
    // as through `Object.keys`, so a non-enumerable one is caught too.
    for (const key of FORBIDDEN_KEYS) {
      if (Object.prototype.hasOwnProperty.call(value, key)) {
        return `forbidden_key: an object has the key "${key}"`
      }
    }
    const keys = Object.keys(value)
    work += keys.length * 2 // the key and its value
    if (work > PAYLOAD_WALK_BUDGET) return overBudget()
    for (const key of keys) {
      if (!isWellFormed(key)) {
        return 'well_formed_string: an object key contains a lone UTF-16 surrogate'
      }
      stack.push([(value as Dict)[key], depth + 1])
    }
  }
  return null
}

/** A required key: present, and not `undefined`. `null` counts as present. */
function has(obj: Dict, key: string, where: string, fail: Fail): boolean {
  if (obj[key] !== undefined) return true
  fail('required_field', `${where}${key} is required`)
  return false
}

function requireString(obj: Dict, key: string, where: string, fail: Fail): void {
  if (!has(obj, key, where, fail)) return
  if (typeof obj[key] !== 'string') {
    fail('invalid_type', `${where}${key} must be a string, got ${show(obj[key])}`)
  }
}

function requireBoolean(obj: Dict, key: string, where: string, fail: Fail): void {
  if (!has(obj, key, where, fail)) return
  if (typeof obj[key] !== 'boolean') {
    fail('invalid_type', `${where}${key} must be a boolean, got ${show(obj[key])}`)
  }
}

/**
 * A count (change rule 3): a JSON number with no fraction, ≥ 0, ≤ 2⁵³ − 1.
 * Returns it when valid, so callers can compare.
 */
function count(v: unknown, label: string, fail: Fail): number | null {
  if (!isNumber(v)) {
    fail('invalid_type', `${label} must be an integer, got ${show(v)}`)
    return null
  }
  if (!Number.isInteger(v)) {
    fail('integer_count', `${label} ${v} has a fraction`)
    return null
  }
  if (v < 0) {
    fail('non_negative', `${label} ${v} < 0`)
    return null
  }
  if (v > Number.MAX_SAFE_INTEGER) {
    fail('safe_integer', `${label} ${v} > 2^53 - 1`)
    return null
  }
  return v
}

function requireCount(obj: Dict, key: string, where: string, fail: Fail): number | null {
  if (!has(obj, key, where, fail)) return null
  return count(obj[key], `${where}${key}`, fail)
}

/**
 * A required array, cut to its cap. Over the cap, the cap is the finding and
 * the overflow is not inspected.
 */
function requireArray(
  obj: Dict,
  key: string,
  where: string,
  fail: Fail,
  cap?: { rule: string; max: number; noun: string },
): unknown[] | null {
  if (!has(obj, key, where, fail)) return null
  const v = obj[key]
  if (!Array.isArray(v)) {
    fail('invalid_type', `${where}${key} must be an array, got ${show(v)}`)
    return null
  }
  if (cap && v.length > cap.max) {
    fail(cap.rule, `${v.length} ${cap.noun} > ${cap.max}`)
    return v.slice(0, cap.max)
  }
  return v
}

function requireStringArray(obj: Dict, key: string, where: string, fail: Fail): void {
  const items = requireArray(obj, key, where, fail)
  items?.forEach((item, i) => {
    if (typeof item !== 'string') {
      fail('invalid_type', `${where}${key}[${i}] must be a string, got ${show(item)}`)
    }
  })
}

function optionalEnum<T extends string | number>(
  obj: Dict,
  key: string,
  allowed: readonly T[],
  rule: string,
  where: string,
  fail: Fail,
): void {
  const v = obj[key]
  if (v === undefined || oneOf(allowed, v)) return
  fail(rule, `${where}${key} ${show(v)} is not one of ${allowed.join(', ')}`)
}

/** A REQUIRED closed-set field: present, and `null` is not a member. */
function requireEnum<T extends string | number>(
  obj: Dict,
  key: string,
  allowed: readonly T[],
  rule: string,
  where: string,
  fail: Fail,
): void {
  if (!has(obj, key, where, fail) || oneOf(allowed, obj[key])) return
  fail(rule, `${where}${key} ${show(obj[key])} is not one of ${allowed.join(', ')}`)
}

/** First index at which each repeated id reappears. */
function duplicates(ids: unknown[]): { index: number; id: string }[] {
  const seen = new Set<string>()
  const out: { index: number; id: string }[] = []
  ids.forEach((id, index) => {
    if (typeof id !== 'string') return
    if (seen.has(id)) out.push({ index, id })
    seen.add(id)
  })
  return out
}

type Check = (input: unknown, fail: Fail) => void

/** The message of whatever was thrown — without trusting it to be readable. */
function thrownDetail(err: unknown): string {
  // `err instanceof Error` itself runs a Proxy's `getPrototypeOf` trap, and
  // `.message` may be a getter: either can throw again, inside the catch.
  try {
    if (err instanceof Error && typeof err.message === 'string') return err.message
  } catch {
    // fall through
  }
  return 'the input could not be read'
}

/**
 * Run one checker: the payload-wide walk first (change rule 8), then the
 * contract's own rules. Collects rule-prefixed errors, returns the input
 * untouched on success, and turns anything thrown (a hostile getter, a revoked
 * Proxy) into a finding instead of an exception.
 */
function runCheck<T>(check: Check, input: unknown): ValidationResult<T> {
  const errors: string[] = []
  const fail: Fail = (rule, message) => {
    if (errors.length < MAX_ERRORS) errors.push(`${rule}: ${message}`)
  }
  try {
    const violation = payloadViolation(input)
    // A payload that breaks a payload-wide rule is not read any further.
    if (violation !== null) return { ok: false, errors: [violation] }
    check(input, fail)
  } catch (err) {
    errors.push(`unexpected_error: ${thrownDetail(err)}`)
  }
  return errors.length === 0 ? { ok: true, value: input as T } : { ok: false, errors }
}

/** Wrap a checker as a public guard. */
const guard =
  <T>(check: Check) =>
  (input: unknown): ValidationResult<T> =>
    runCheck<T>(check, input)

// ── C1 · Scope ────────────────────────────────────────────────────────────

function checkScopeWindow(window: unknown, fail: Fail): void {
  if (!isDict(window)) {
    fail('invalid_type', `window must be an object, got ${show(window)}`)
    return
  }
  const hasDays = !isNullOrAbsent(window.days)
  const hasFrom = !isNullOrAbsent(window.from)
  const hasTo = !isNullOrAbsent(window.to)

  if (hasDays && (hasFrom || hasTo)) {
    fail('window_one_form', 'window has both days and from/to; send exactly one form')
    return
  }
  if (hasDays) {
    const { days } = window
    if (!isInteger(days) || days < 1 || days > VIZ_LIMITS.windowDaysMax) {
      fail(
        'window_days_range',
        `window.days ${show(days)} is not an integer 1–${VIZ_LIMITS.windowDaysMax}`,
      )
    }
    return
  }
  if (!hasFrom || !hasTo) {
    fail('window_one_form', 'window needs either days or both from and to')
    return
  }
  const from = dayNumber(window.from)
  const to = dayNumber(window.to)
  if (from === null || to === null) {
    fail(
      'window_order',
      `window.from ${show(window.from)} and window.to ${show(window.to)} must both be YYYY-MM-DD days`,
    )
    return
  }
  if (from > to) {
    fail('window_order', `window.from ${show(window.from)} is after window.to ${show(window.to)}`)
  } else if (to - from > VIZ_LIMITS.windowSpanDays) {
    fail('window_order', `window spans ${to - from} days > ${VIZ_LIMITS.windowSpanDays}`)
  }
}

export const validateScope = guard<Scope>((input, fail) => {
  if (!isDict(input)) {
    fail('invalid_type', `scope must be an object, got ${show(input)}`)
    return
  }

  if (has(input, 'project_id', '', fail)) {
    const id = input.project_id
    if (id !== null && !isUuid(id)) {
      fail('project_id_format', `project_id ${show(id)} is neither a UUID nor null`)
    }
  }

  const releases = requireArray(input, 'release_ids', '', fail, {
    rule: 'release_cap',
    max: VIZ_LIMITS.releases,
    noun: 'releases',
  })
  if (releases) {
    releases.forEach((id, i) => {
      if (id !== UNATTRIBUTED_RELEASE && !isUuid(id)) {
        fail(
          'release_id_format',
          `release_ids[${i}] ${show(id)} is neither a UUID nor "${UNATTRIBUTED_RELEASE}"`,
        )
      }
    })
    // A UUID is case-insensitive, so `…AB` and `…ab` are the same release.
    const folded = releases.map((id) => (typeof id === 'string' ? id.toLowerCase() : id))
    for (const dup of duplicates(folded)) {
      fail('unique_release', `release_ids[${dup.index}] ${show(dup.id)} is a duplicate`)
    }
    if (releases.length > 0 && input.project_id === null) {
      fail(
        'release_requires_project',
        'release_ids must be empty when project_id is null — a release belongs to one project',
      )
    }
  }

  const suites = requireArray(input, 'suite_names', '', fail, {
    rule: 'suite_cap',
    max: VIZ_LIMITS.suites,
    noun: 'suites',
  })
  if (suites) {
    suites.forEach((name, i) => {
      if (typeof name !== 'string') {
        fail('invalid_type', `suite_names[${i}] must be a string, got ${show(name)}`)
        return
      }
      const length = charLength(name, VIZ_LIMITS.suiteNameLength)
      if (length < 1 || length > VIZ_LIMITS.suiteNameLength) {
        fail(
          'suite_name_length',
          `suite_names[${i}] must be 1–${VIZ_LIMITS.suiteNameLength} characters`,
        )
      }
    })
    // Exact comparison: suite names are untrusted text and are never folded.
    for (const dup of duplicates(suites)) {
      fail('unique_suite', `suite_names[${dup.index}] ${show(dup.id)} is a duplicate`)
    }
  }

  if (has(input, 'window', '', fail)) checkScopeWindow(input.window, fail)
})

// ── C2 · Envelope meta ────────────────────────────────────────────────────

const IGNORABLE_DIMENSIONS = ['release', 'suite', 'window'] as const
const PASS_RATE_BASES = ['executions', 'unique_tests'] as const

function checkEnvelopeScope(scope: unknown, fail: Fail): void {
  if (!isDict(scope)) {
    fail('invalid_type', `scope must be an object, got ${show(scope)}`)
    return
  }
  requireArray(scope, 'projects', 'scope.', fail)?.forEach((project, i) => {
    const where = `scope.projects[${i}].`
    if (!isDict(project)) {
      fail('invalid_type', `scope.projects[${i}] must be an object, got ${show(project)}`)
      return
    }
    requireString(project, 'id', where, fail)
    requireString(project, 'name', where, fail)
  })
  requireArray(scope, 'releases', 'scope.', fail)?.forEach((release, i) => {
    const where = `scope.releases[${i}].`
    if (!isDict(release)) {
      fail('invalid_type', `scope.releases[${i}] must be an object, got ${show(release)}`)
      return
    }
    requireString(release, 'id', where, fail)
    requireString(release, 'name', where, fail)
    requireString(release, 'status', where, fail) // an open string, by contract
  })
  requireStringArray(scope, 'suites', 'scope.', fail)

  if (!has(scope, 'window', 'scope.', fail)) return
  const window = scope.window
  if (!isDict(window)) {
    fail('invalid_type', `scope.window must be an object, got ${show(window)}`)
    return
  }
  for (const key of ['from', 'to']) {
    if (has(window, key, 'scope.window.', fail) && dayNumber(window[key]) === null) {
      fail('invalid_value', `scope.window.${key} ${show(window[key])} is not a YYYY-MM-DD day`)
    }
  }
  requireCount(window, 'days', 'scope.window.', fail)
  if (has(window, 'timezone', 'scope.window.', fail) && window.timezone !== 'UTC') {
    fail('timezone_utc', `scope.window.timezone ${show(window.timezone)} is not "UTC"`)
  }
}

function checkTotals(totals: unknown, fail: Fail): void {
  if (!isDict(totals)) {
    fail('invalid_type', `totals must be an object, got ${show(totals)}`)
    return
  }
  for (const unit of ['runs', 'executions']) {
    const matched = requireCount(totals, `matched_${unit}`, 'totals.', fail)
    const total = requireCount(totals, `total_${unit}`, 'totals.', fail)
    if (matched !== null && total !== null && matched > total) {
      fail('totals_subset', `matched_${unit} ${matched} > total_${unit} ${total}`)
    }
  }
}

/**
 * Per-axis truncation (VIZ-203), optional and additive.
 *
 * Absent means the producer has nothing to say; `{}` does not, because
 * "nothing was truncated" is already `truncated: false` and an empty object
 * here would be a second, silent way to say it. Every entry must lose
 * something (`kept < total`), `truncated` must agree, and `truncated_total`
 * must be one of these totals -- otherwise the scalar and the detail describe
 * two different charts.
 */
function checkTruncatedAxes(input: Dict, fail: Fail): void {
  if (!('truncated_axes' in input)) return
  const axes = input.truncated_axes
  if (!isDict(axes)) {
    fail('invalid_type', `truncated_axes must be an object, got ${show(axes)}`)
    return
  }
  const names = Object.keys(axes)
  if (names.length === 0) {
    fail('truncated_axes', 'truncated_axes is empty; omit it instead')
    return
  }
  if (input.truncated !== true) {
    fail('truncated_axes', 'an axis was truncated but truncated is false')
  }
  const totals: number[] = []
  for (const name of names) {
    const where = `truncated_axes.${name}.`
    if (name !== 'x' && name !== 'series') {
      fail('truncated_axes', `truncated_axes.${name} is neither x nor series`)
      continue
    }
    const axis = axes[name]
    if (!isDict(axis)) {
      fail('invalid_type', `truncated_axes.${name} must be an object, got ${show(axis)}`)
      continue
    }
    requireString(axis, 'dimension', where, fail)
    const kept = requireCount(axis, 'kept', where, fail)
    const total = requireCount(axis, 'total', where, fail)
    if (total !== null) totals.push(total)
    if (kept !== null && total !== null && kept >= total) {
      fail('truncated_axis', `${where}kept ${kept} is not below total ${total}`)
    }
  }
  if (totals.length > 0 && typeof input.truncated_total === 'number'
      && !totals.includes(input.truncated_total)) {
    fail('truncated_axes', 'truncated_total names no axis’s full count')
  }
}

/** Buckets outside a generated axis (VIZ-203), optional and additive. */
function checkOutsideWindow(input: Dict, fail: Fail): void {
  if (!('outside_window' in input)) return
  const outside = input.outside_window
  if (!isDict(outside)) {
    fail('invalid_type', `outside_window must be an object, got ${show(outside)}`)
    return
  }
  const buckets = requireCount(outside, 'buckets', 'outside_window.', fail)
  if (buckets === 0) {
    fail('outside_window', 'outside_window with no buckets is nothing to report; omit it')
  }
  requireCount(outside, 'executions', 'outside_window.', fail)
  for (const key of ['first', 'last']) {
    if (has(outside, key, 'outside_window.', fail) && dayNumber(outside[key]) === null) {
      fail('invalid_value', `outside_window.${key} ${show(outside[key])} is not a YYYY-MM-DD day`)
    }
  }
}

/**
 * Series comparability (VIZ-404), optional and additive. Absent is "not
 * assessed"; `null` would be a second, silent spelling of that and is
 * refused. Inside the object every key is required, a nullable one included.
 */
function checkComparability(input: Dict, fail: Fail): void {
  if (!('comparability' in input)) return
  const judged = input.comparability
  if (!isDict(judged)) {
    fail('invalid_type', `comparability must be an object, got ${show(judged)}`)
    return
  }
  requireBoolean(judged, 'comparable', 'comparability.', fail)
  const hasReason = has(judged, 'reason', 'comparability.', fail)
  const hasCode = has(judged, 'reason_code', 'comparability.', fail)
  if (!hasReason || !hasCode) return
  const { comparable, reason, reason_code: code } = judged
  if (reason !== null && typeof reason !== 'string') {
    fail('invalid_type', `comparability.reason must be a string or null, got ${show(reason)}`)
    return
  }
  if (code !== null && !oneOf(COMPARABILITY_REASON_CODES, code)) {
    fail(
      'comparability_reason_code',
      `comparability.reason_code ${show(code)} is not one of ${COMPARABILITY_REASON_CODES.join(', ')}`,
    )
    return
  }
  if (comparable === true && (reason !== null || code !== null)) {
    fail('comparability_reason', 'comparable series carry no reason and no reason_code')
  } else if (comparable === false && (reason === null || isBlank(reason) || code === null)) {
    fail('comparability_reason', 'series that are not comparable need a reason and a reason_code')
  }
}

export const validateEnvelopeMeta = guard<EnvelopeMeta>((input, fail) => {
  if (!isDict(input)) {
    fail('invalid_type', `meta must be an object, got ${show(input)}`)
    return
  }

  const schemaVersion = requireCount(input, 'schema_version', '', fail)
  if (schemaVersion !== null && schemaVersion < 1) {
    fail('invalid_value', `schema_version ${schemaVersion} < 1`)
  }
  if (has(input, 'scope', '', fail)) checkEnvelopeScope(input.scope, fail)
  if (has(input, 'totals', '', fail)) checkTotals(input.totals, fail)

  if (has(input, 'pass_rate_basis', '', fail)) {
    const basis = input.pass_rate_basis
    if (basis !== null && !oneOf(PASS_RATE_BASES, basis)) {
      fail(
        'invalid_value',
        `pass_rate_basis ${show(basis)} is not one of ${PASS_RATE_BASES.join(', ')}, null`,
      )
    }
  }

  requireArray(input, 'ignored_filters', '', fail)?.forEach((entry, i) => {
    const where = `ignored_filters[${i}].`
    if (!isDict(entry)) {
      fail('invalid_type', `ignored_filters[${i}] must be an object, got ${show(entry)}`)
      return
    }
    if (has(entry, 'dimension', where, fail) && !oneOf(IGNORABLE_DIMENSIONS, entry.dimension)) {
      fail(
        'ignored_dimension',
        `${where}dimension ${show(entry.dimension)} is not one of ${IGNORABLE_DIMENSIONS.join(', ')}`,
      )
    }
    requireString(entry, 'reason', where, fail)
  })

  requireBoolean(input, 'truncated', '', fail)
  if (has(input, 'truncated_total', '', fail)) {
    if (input.truncated_total !== null) count(input.truncated_total, 'truncated_total', fail)
    else if (input.truncated === true) {
      fail('truncated_total', 'truncated is true, so truncated_total must carry the full count')
    }
  }
  checkTruncatedAxes(input, fail)
  checkOutsideWindow(input, fail)
  checkComparability(input, fail)

  requireBoolean(input, 'measured', '', fail)
  if (has(input, 'reason', '', fail)) {
    const reason = input.reason
    if (reason !== null && typeof reason !== 'string') {
      fail('invalid_type', `reason must be a string or null, got ${show(reason)}`)
    } else if (input.measured === false && (reason === null || isBlank(reason))) {
      fail('measured_reason', 'measured is false, so reason must say why')
    }
  }

  requireCount(input, 'includes_in_progress', '', fail)

  if (has(input, 'partial_day', '', fail)) {
    const day = input.partial_day
    if (day !== null && dayNumber(day) === null) {
      fail('invalid_value', `partial_day ${show(day)} is neither a YYYY-MM-DD day nor null`)
    }
  }
  for (const key of ['generated_at', 'as_of']) {
    if (has(input, key, '', fail) && !isUtcInstant(input[key])) {
      fail(
        'utc_instant',
        `${key} ${show(input[key])} is not YYYY-MM-DDTHH:MM:SS[.ffffff] with Z or +00:00, on a real date and clock`,
      )
    }
  }
})

// ── C3 · ChartSeries ──────────────────────────────────────────────────────

/** A data value: a finite number, or `null` for "no data". */
function nullableNumber(v: unknown, label: string, fail: Fail): void {
  if (v !== null && !isNumber(v)) {
    fail('invalid_type', `${label} must be a number or null, got ${show(v)}`)
  }
}

/**
 * A point's optional `measured` / `reason` (VIZ-203).
 *
 * Both are additive, so a payload that carries neither is valid and unchanged.
 * `measured` is a boolean when present, never null; a `false` needs a reason
 * with a non-whitespace character, for the same rule the envelope states: a
 * gap the reader cannot explain is indistinguishable from a bug.
 */
function checkPointMeasured(point: Dict, at: string, fail: Fail): void {
  const measured = point.measured
  if (measured !== undefined && typeof measured !== 'boolean') {
    fail('invalid_type', `${at}measured must be a boolean, got ${show(measured)}`)
  }
  const reason = point.reason
  if (reason !== undefined && reason !== null && typeof reason !== 'string') {
    fail('invalid_type', `${at}reason must be a string or null, got ${show(reason)}`)
  }
  if (measured === false && (typeof reason !== 'string' || isBlank(reason))) {
    fail('measured_reason', `${at}measured is false, so reason must say why`)
  }
}

function checkSeriesChart(input: Dict, fail: Fail): void {
  requireStringArray(input, 'dimensions', '', fail)
  requireEnum(input, 'x_type', ['time', 'category'], 'invalid_value', '', fail)
  const series = requireArray(input, 'series', '', fail, {
    rule: 'series_cap',
    max: VIZ_LIMITS.series,
    noun: 'series',
  })
  if (!series) return

  series.forEach((entry, i) => {
    const where = `series[${i}].`
    if (!isDict(entry)) {
      fail('invalid_type', `series[${i}] must be an object, got ${show(entry)}`)
      return
    }
    requireString(entry, 'key', where, fail)
    requireString(entry, 'label', where, fail)
    const points = requireArray(entry, 'points', where, fail, {
      rule: 'point_cap',
      max: VIZ_LIMITS.pointsPerSeries,
      noun: `points in series[${i}]`,
    })
    points?.forEach((point, j) => {
      const at = `${where}points[${j}].`
      if (!isDict(point)) {
        fail('invalid_type', `${where}points[${j}] must be an object, got ${show(point)}`)
        return
      }
      requireString(point, 'x', at, fail)
      if (has(point, 'y', at, fail)) nullableNumber(point.y, `${at}y`, fail)
      requireCount(point, 'n', at, fail)
      checkPointMeasured(point, at, fail)
    })
  })
  const keys = series.map((entry) => (isDict(entry) ? entry.key : undefined))
  for (const dup of duplicates(keys)) {
    fail('unique_series_key', `series[${dup.index}].key ${show(dup.id)} is a duplicate`)
  }
}

function checkMatrixChart(input: Dict, fail: Fail): void {
  const valueTypes = ['rate', 'count', 'status'] as const
  requireEnum(input, 'value_type', valueTypes, 'invalid_value', '', fail)
  requireStringArray(input, 'x_labels', '', fail)
  requireStringArray(input, 'y_labels', '', fail)
  const width = Array.isArray(input.x_labels) ? input.x_labels.length : 0
  const height = Array.isArray(input.y_labels) ? input.y_labels.length : 0

  const cells = requireArray(input, 'cells', '', fail, {
    rule: 'cell_cap',
    max: VIZ_LIMITS.matrixCells,
    noun: 'cells',
  })
  cells?.forEach((cell, i) => {
    const where = `cells[${i}].`
    if (!isDict(cell)) {
      fail('invalid_type', `cells[${i}] must be an object, got ${show(cell)}`)
      return
    }
    for (const [axis, size] of [['x', width], ['y', height]] as const) {
      // A count (change rule 3), so a fraction, a negative or 2⁵³ is reported
      // as that; only a well-formed index is then checked against the labels.
      const index = requireCount(cell, axis, where, fail)
      if (index !== null && index >= size) {
        fail('cell_index_range', `${where}${axis} ${index} does not address one of ${size} labels`)
      }
    }
    if (has(cell, 'value', where, fail) && cell.value !== null) {
      if (input.value_type === 'status') {
        if (!oneOf(VIZ_STATUSES, cell.value)) {
          fail(
            'status_vocab',
            `${where}value ${show(cell.value)} is not one of ${VIZ_STATUSES.join(', ')}`,
          )
        }
      } else if (input.value_type === 'count') {
        count(cell.value, `${where}value`, fail)
      } else {
        nullableNumber(cell.value, `${where}value`, fail)
      }
    }
    requireCount(cell, 'n', where, fail)
  })
}

function checkTreeChart(input: Dict, fail: Fail): void {
  const nodes = requireArray(input, 'nodes', '', fail, {
    rule: 'node_cap',
    max: VIZ_LIMITS.treeNodes,
    noun: 'tree nodes',
  })
  if (!nodes) return

  /** id → parent id, for the well-formed nodes only. */
  const parentOf = new Map<string, string | null>()
  nodes.forEach((node, i) => {
    const where = `nodes[${i}].`
    if (!isDict(node)) {
      fail('invalid_type', `nodes[${i}] must be an object, got ${show(node)}`)
      return
    }
    requireString(node, 'id', where, fail)
    requireString(node, 'label', where, fail)
    if (has(node, 'parent_id', where, fail)) {
      const parent = node.parent_id
      if (parent !== null && typeof parent !== 'string') {
        fail('invalid_type', `${where}parent_id must be a string or null, got ${show(parent)}`)
      }
    }
    if (has(node, 'value', where, fail)) {
      if (!isNumber(node.value)) {
        fail('invalid_type', `${where}value must be a number, got ${show(node.value)}`)
      } else if (node.value < 0) {
        fail('non_negative', `${where}value ${node.value} < 0`)
      }
    }
    if (has(node, 'measure', where, fail)) nullableNumber(node.measure, `${where}measure`, fail)

    if (typeof node.id !== 'string') return
    if (parentOf.has(node.id)) {
      fail('unique_node_id', `${where}id ${show(node.id)} is a duplicate`)
      return
    }
    const parent = node.parent_id
    parentOf.set(node.id, typeof parent === 'string' ? parent : null)
  })

  for (const [id, parent] of parentOf) {
    if (parent !== null && !parentOf.has(parent)) {
      fail('tree_parent_exists', `node ${show(id)} names a parent ${show(parent)} that does not exist`)
    }
  }

  // An empty tree is "no data", not a malformed one; a non-empty tree needs a root.
  if (parentOf.size === 0) return
  if (![...parentOf.values()].includes(null)) {
    fail('tree_acyclic', 'no node has parent_id null, so the tree has no root')
  }
  // Walk each node up to a root. `settled` nodes are known to reach one (or to
  // fall off a missing parent, already reported); meeting a node from the
  // CURRENT walk again is a cycle. Iterative, so depth cannot blow the stack.
  const settled = new Set<string>()
  for (const start of parentOf.keys()) {
    const walk = new Set<string>()
    let at: string | null | undefined = start
    while (typeof at === 'string' && !settled.has(at)) {
      if (walk.has(at)) {
        fail('tree_acyclic', `node ${show(at)} is its own ancestor`)
        break
      }
      walk.add(at)
      at = parentOf.get(at)
    }
    for (const id of walk) settled.add(id)
  }
}

function checkGraphChart(input: Dict, fail: Fail): void {
  const nodes = requireArray(input, 'nodes', '', fail, {
    rule: 'node_cap',
    max: VIZ_LIMITS.graphNodes,
    noun: 'graph nodes',
  })
  const ids = new Set<string>()
  nodes?.forEach((node, i) => {
    const where = `nodes[${i}].`
    if (!isDict(node)) {
      fail('invalid_type', `nodes[${i}] must be an object, got ${show(node)}`)
      return
    }
    requireString(node, 'id', where, fail)
    requireString(node, 'label', where, fail)
    if (has(node, 'size', where, fail)) {
      if (!isNumber(node.size)) {
        fail('invalid_type', `${where}size must be a number, got ${show(node.size)}`)
      } else if (node.size < 0) {
        fail('non_negative', `${where}size ${node.size} < 0`)
      }
    }
    if (typeof node.id !== 'string') return
    if (ids.has(node.id)) fail('unique_node_id', `${where}id ${show(node.id)} is a duplicate`)
    ids.add(node.id)
  })

  requireArray(input, 'edges', '', fail)?.forEach((edge, i) => {
    const where = `edges[${i}].`
    if (!isDict(edge)) {
      fail('invalid_type', `edges[${i}] must be an object, got ${show(edge)}`)
      return
    }
    for (const end of ['source', 'target']) {
      if (!has(edge, end, where, fail)) continue
      const id = edge[end]
      if (typeof id !== 'string' || !ids.has(id)) {
        fail('edge_endpoints', `${where}${end} ${show(id)} is not a node id`)
      }
    }
    if (has(edge, 'weight', where, fail)) {
      const weight = edge.weight
      if (!isNumber(weight)) {
        fail('invalid_type', `${where}weight must be a number, got ${show(weight)}`)
      } else if (weight < 0 || weight > 1) {
        fail('weight_range', `${where}weight ${weight} is outside 0–1`)
      }
    }
  })
}

export const validateChartSeries = guard<ChartSeries>((input, fail) => {
  if (!isDict(input)) {
    fail('invalid_type', `chart series must be an object, got ${show(input)}`)
    return
  }
  switch (input.kind) {
    case 'series':
      return checkSeriesChart(input, fail)
    case 'matrix':
      return checkMatrixChart(input, fail)
    case 'tree':
      return checkTreeChart(input, fail)
    case 'graph':
      return checkGraphChart(input, fail)
    default:
      fail('kind_enum', `kind ${show(input.kind)} is not one of ${CHART_SERIES_KINDS.join(', ')}`)
  }
})

// ── C4 · Widget config ────────────────────────────────────────────────────

/** A non-empty string under a required key (`instanceId`, `templateId`). */
function requireNonEmptyString(obj: Dict, key: string, where: string, fail: Fail): void {
  const v = obj[key]
  if (typeof v !== 'string' || v.length === 0) {
    fail('required_field', `${where}${key} must be a non-empty string, got ${show(v)}`)
  }
}

function checkWidgetInstance(instance: unknown, i: number, fail: Fail): void {
  const where = `instances[${i}].`
  if (!isDict(instance)) {
    fail('invalid_type', `instances[${i}] must be an object, got ${show(instance)}`)
    return
  }
  requireNonEmptyString(instance, 'instanceId', where, fail)
  requireNonEmptyString(instance, 'templateId', where, fail)

  // Optional keys: absent is fine, `null` is not (README change rule 6).
  const { title, metricVariant, filters, groupBy } = instance
  if (title !== undefined) {
    if (typeof title !== 'string') {
      fail('invalid_type', `${where}title must be a string, got ${show(title)}`)
    } else if (charLength(title, VIZ_LIMITS.widgetTitleLength) > VIZ_LIMITS.widgetTitleLength) {
      fail('title_length', `${where}title is longer than ${VIZ_LIMITS.widgetTitleLength} characters`)
    }
  }
  optionalEnum(instance, 'chartType', WIDGET_CHART_TYPES, 'chart_type_enum', where, fail)
  if (metricVariant !== undefined && typeof metricVariant !== 'string') {
    fail('invalid_type', `${where}metricVariant must be a string, got ${show(metricVariant)}`)
  }
  if (filters !== undefined && !isDict(filters)) {
    fail('invalid_type', `${where}filters must be an object, got ${show(filters)}`)
  }
  if (groupBy !== undefined) {
    if (!Array.isArray(groupBy)) {
      fail('invalid_type', `${where}groupBy must be an array, got ${show(groupBy)}`)
    } else if (groupBy.length > VIZ_LIMITS.widgetGroupBy) {
      fail('group_by_cap', `${where}groupBy has ${groupBy.length} items > ${VIZ_LIMITS.widgetGroupBy}`)
    } else {
      groupBy.forEach((dimension, j) => {
        if (!oneOf(VIZ_DIMENSIONS, dimension)) {
          fail('dimension_enum', `${where}groupBy[${j}] ${show(dimension)} is not a dimension`)
        }
      })
    }
  }
  optionalEnum(instance, 'topN', WIDGET_TOP_N, 'top_n_enum', where, fail)
  optionalEnum(instance, 'scale', ['linear', 'log'], 'invalid_value', where, fail)
  optionalEnum(instance, 'stack', ['none', 'absolute', 'percent'], 'invalid_value', where, fail)
  optionalEnum(instance, 'bucket', ['day', 'week'], 'invalid_value', where, fail)
}

/**
 * On success `value` is the input object itself, so every key this build does
 * not know — top-level or per-instance — is still there for the next write.
 */
export const validateWidgetConfig = guard<WidgetConfig>((input, fail) => {
  if (!isDict(input)) {
    fail('invalid_type', `widget config must be an object, got ${show(input)}`)
    return
  }
  requireString(input, 'page', '', fail)
  const version = requireCount(input, 'version', '', fail)
  if (version !== null && version < 1) fail('invalid_value', `version ${version} < 1`)
  const instances = requireArray(input, 'instances', '', fail, {
    rule: 'instance_cap',
    max: VIZ_LIMITS.widgetInstances,
    noun: 'instances',
  })
  if (!instances) return
  instances.forEach((instance, i) => checkWidgetInstance(instance, i, fail))
  const ids = instances.map((instance) => (isDict(instance) ? instance.instanceId : undefined))
  for (const dup of duplicates(ids)) {
    fail('unique_instance_id', `instances[${dup.index}].instanceId ${show(dup.id)} is a duplicate`)
  }
})

// ── C5 · Drill path ───────────────────────────────────────────────────────

export const validateDrillPath = guard<DrillPath>((input, fail) => {
  if (!isDict(input)) {
    fail('invalid_type', `drill path must be an object, got ${show(input)}`)
    return
  }
  const path = requireArray(input, 'path', '', fail, {
    rule: 'depth_cap',
    max: VIZ_LIMITS.drillDepth,
    noun: 'levels',
  })
  if (!path) return

  path.forEach((level, i) => {
    const where = `path[${i}].`
    if (!isDict(level)) {
      fail('invalid_type', `path[${i}] must be an object, got ${show(level)}`)
      return
    }
    if (has(level, 'dimension', where, fail) && !oneOf(VIZ_DIMENSIONS, level.dimension)) {
      fail('dimension_enum', `${where}dimension ${show(level.dimension)} is not a dimension`)
    }
    if (!has(level, 'value', where, fail)) return
    const { value } = level
    if (typeof value !== 'string') {
      fail('invalid_type', `${where}value must be a string, got ${show(value)}`)
      return
    }
    const length = charLength(value, VIZ_LIMITS.drillValueLength)
    if (length < 1 || length > VIZ_LIMITS.drillValueLength) {
      fail('value_length', `${where}value must be 1–${VIZ_LIMITS.drillValueLength} characters`)
    } else if (level.dimension === 'status' && !oneOf(VIZ_STATUSES, value)) {
      fail('status_vocab', `${where}value ${show(value)} is not one of ${VIZ_STATUSES.join(', ')}`)
    }
  })
  const dimensions = path.map((level) => (isDict(level) ? level.dimension : undefined))
  for (const dup of duplicates(dimensions)) {
    fail('unique_dimension', `path[${dup.index}].dimension ${show(dup.id)} appears more than once`)
  }
})

// ── C6 · Report metrics ───────────────────────────────────────────────────

/** A period's metrics, in the strip's order. `null` = not measured. */
export const REPORT_METRIC_KEYS = [
  'runs',
  'total_tests',
  'passed',
  'failed',
  'broken',
  'skipped',
  'unknown',
  'pass_rate',
  'total_duration_ms',
  'avg_duration_ms',
  'duration_runs',
] as const
export type ReportMetricKey = (typeof REPORT_METRIC_KEYS)[number]

export const COMPARABLE_REASON_CODES = [
  'no_data',
  'partial_window',
  'different_basis',
  'not_measured',
] as const
export type ComparableReasonCode = (typeof COMPARABLE_REASON_CODES)[number]

export type MetricsPeriod = { [K in ReportMetricKey]: number | null } & {
  reasons: Partial<Record<ReportMetricKey, string>>
  window: { from: string; to: string; days: number }
}

export interface ReportMetrics {
  schema_version: number
  pass_rate_basis: 'executions' | 'unique_tests'
  current: MetricsPeriod
  previous: MetricsPeriod & {
    comparable: boolean
    reason: string | null
    reason_code: ComparableReasonCode | null
  }
}

/** One period; returns its `runs` when that is a valid count. */
function checkMetricsPeriod(period: unknown, where: string, fail: Fail): number | null {
  if (!isDict(period)) {
    fail('invalid_type', `${where} must be an object, got ${show(period)}`)
    return null
  }
  const prefix = `${where}.`
  let runs: number | null = null
  for (const key of REPORT_METRIC_KEYS) {
    if (!has(period, key, prefix, fail) || period[key] === null) continue
    if (key === 'pass_rate') {
      const rate = period[key]
      if (!isNumber(rate)) fail('invalid_type', `${prefix}pass_rate must be a number, got ${show(rate)}`)
      else if (rate < 0 || rate > 100) fail('rate_range', `${prefix}pass_rate ${rate} is not within 0–100`)
      continue
    }
    const value = count(period[key], `${prefix}${key}`, fail)
    if (key === 'runs') runs = value
  }
  if (has(period, 'reasons', prefix, fail)) {
    const reasons = period.reasons
    if (!isDict(reasons)) {
      fail('invalid_type', `${prefix}reasons must be an object, got ${show(reasons)}`)
    } else {
      for (const [key, reason] of Object.entries(reasons)) {
        if (typeof reason !== 'string') {
          fail('invalid_type', `${prefix}reasons.${key} must be a string, got ${show(reason)}`)
        }
      }
      for (const key of REPORT_METRIC_KEYS) {
        const reason = Object.prototype.hasOwnProperty.call(reasons, key) ? reasons[key] : undefined
        if (period[key] === null && (typeof reason !== 'string' || isBlank(reason))) {
          fail('metric_reason', `${prefix}${key} is null, so ${prefix}reasons.${key} must say why`)
        }
      }
    }
  }
  if (has(period, 'window', prefix, fail)) {
    const window = period.window
    if (!isDict(window)) {
      fail('invalid_type', `${prefix}window must be an object, got ${show(window)}`)
    } else {
      for (const key of ['from', 'to']) {
        if (has(window, key, `${prefix}window.`, fail) && dayNumber(window[key]) === null) {
          fail('invalid_value', `${prefix}window.${key} ${show(window[key])} is not a YYYY-MM-DD day`)
        }
      }
      requireCount(window, 'days', `${prefix}window.`, fail)
    }
  }
  return runs
}

export const validateReportMetrics = guard<ReportMetrics>((input, fail) => {
  if (!isDict(input)) {
    fail('invalid_type', `report_metrics must be an object, got ${show(input)}`)
    return
  }
  const schemaVersion = requireCount(input, 'schema_version', '', fail)
  if (schemaVersion !== null && schemaVersion < 1) {
    fail('invalid_value', `schema_version ${schemaVersion} < 1`)
  }
  requireEnum(input, 'pass_rate_basis', PASS_RATE_BASES, 'invalid_value', '', fail)
  const currentRuns = has(input, 'current', '', fail)
    ? checkMetricsPeriod(input.current, 'current', fail)
    : null
  if (!has(input, 'previous', '', fail)) return
  const previous = input.previous
  const previousRuns = checkMetricsPeriod(previous, 'previous', fail)
  if (!isDict(previous)) return
  requireBoolean(previous, 'comparable', 'previous.', fail)
  const hasReason = has(previous, 'reason', 'previous.', fail)
  const hasCode = has(previous, 'reason_code', 'previous.', fail)
  if (!hasReason || !hasCode) return
  const { comparable, reason, reason_code: code } = previous
  if (reason !== null && typeof reason !== 'string') {
    fail('invalid_type', `previous.reason must be a string or null, got ${show(reason)}`)
    return
  }
  if (code !== null && !oneOf(COMPARABLE_REASON_CODES, code)) {
    fail(
      'comparable_reason_code',
      `previous.reason_code ${show(code)} is not one of ${COMPARABLE_REASON_CODES.join(', ')}`,
    )
    return
  }
  if (comparable === true) {
    if (reason !== null || code !== null) {
      fail('comparable_reason', 'a comparable period carries no reason and no reason_code')
    } else if (!((currentRuns ?? 0) >= 1 && (previousRuns ?? 0) >= 1)) {
      fail('comparable_measured', 'comparable is true only when both periods have runs')
    }
  } else if (comparable === false && (reason === null || isBlank(reason) || code === null)) {
    fail('comparable_reason', 'a period that is not comparable needs a reason and a reason_code')
  }
})

// ── Dispatch by fixture folder ────────────────────────────────────────────

const VALIDATORS = {
  scope: validateScope,
  envelope: validateEnvelopeMeta,
  chart_series: validateChartSeries,
  widget_config: validateWidgetConfig,
  drill_path: validateDrillPath,
  report_metrics: validateReportMetrics,
} as const

/** Contract names — the folder names under `contracts/viz/fixtures/`. */
export type ContractKind = keyof typeof VALIDATORS
export const CONTRACT_KINDS = Object.keys(VALIDATORS) as ContractKind[]

export interface ContractTypes {
  scope: Scope
  envelope: EnvelopeMeta
  chart_series: ChartSeries
  widget_config: WidgetConfig
  drill_path: DrillPath
  report_metrics: ReportMetrics
}

export function validateContract<K extends ContractKind>(
  kind: K,
  input: unknown,
): ValidationResult<ContractTypes[K]> {
  // Own-property lookup: `'toString'` must not resolve to Object.prototype.
  if (!Object.prototype.hasOwnProperty.call(VALIDATORS, kind)) {
    return { ok: false, errors: [`unknown_contract: ${show(kind)} is not a viz contract`] }
  }
  return VALIDATORS[kind](input) as ValidationResult<ContractTypes[K]>
}
