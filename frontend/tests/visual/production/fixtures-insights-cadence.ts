/**
 * W23a FAIL-11 / TRND-03 hermetic world: cross-run insights and expected run cadences
 * (`docs/deferred-scope-2026-10-13/work/W23a-contract.md` §6.2 G-A, §6.3 specs).
 *
 * `insightsCadenceWorld(options)` answers FIRST, with the REAL server shapes, for ONE project (`projectId`):
 *  - `GET …/insights` → `{enabled, range, items, total, dismissed_total, detectors (always four), generated_at}`:
 *    honours `range` (`24h` / `7d` by `last_seen_at` against `NOW`; another value → 422 `unknown_range`),
 *    `include_dismissed` and `limit` (1-100, default 50; else FastAPI's 422); open first, then severity, then
 *    `last_seen_at` desc; `total` counts the whole filter; `enabled: false` → every detector reads `flag_off`;
 *  - `POST …/insights/{id}/dismiss` (QA_ENGINEER+): records the write, flips the row (`dismissed_at` = NOW,
 *    `dismissed_by_name` = the signed-in user); already dismissed → 200 unchanged; unknown / another project's id →
 *    404 `insight_not_found`;
 *  - `GET` / `POST …/run-cadences`, `PATCH` / `DELETE …/run-cadences/{id}`, `POST …/{id}/pause` / `…/resume`
 *    (writes QA_LEAD+): validated in the server's order with the server's `CADENCE_ERRORS` sentences, recorded;
 *  - `GET …/run-cadences/status` → honours `days` (1-90, else FastAPI's 422); `options.status` (an answer, or a
 *    function of `(days, state)`) else derived from the cadences (`configured: false` without one; a cadence with
 *    no scripted counts is `measured: false` with the server's "No expected run fell in this window yet.").
 * Every refusal is `{"detail": {"code", "message"}}` (a role refusal is the guard's plain-string 403); a scripted
 * refusal (`refusals[call]`) answers BEFORE validation, after the role guard. Every write is recorded in `writes`
 * as `METHOD path` + its JSON body (a write with no body is recorded as `{}`).
 *
 * Then the page's own handlers: `page` picks ONE shared set exactly as the existing specs use it (`overview` →
 * `OVERVIEW_ON`, `trends` → `TRENDS_ON`, `coverage` → `COVERAGE_ON`, `inbox` → `inboxHandlers(writes)`, `settings` →
 * `RUNS` + `LAYOUT`); without `page` they are all composed (overview first). `base` handlers come between the
 * world's routes and the shared set. Every other request fails closed as today.
 *
 * Fresh state per call; `state` is live and mutable (a spec may edit rows, detectors, cadences or refusals between
 * steps). The clock is `NOW` (the pinned page clock). Sentinel names are non-hex words. Not a spec file.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import {
  INSIGHT_ERRORS,
  INSIGHT_KINDS,
  INSIGHT_LIST_DEFAULT,
  INSIGHT_LIST_MAX,
  NOT_MEASURED,
  type Insight,
  type InsightDetector,
  type InsightKind,
} from '../../../src/types/insights'
import {
  CADENCE_ERRORS,
  CADENCE_MODES,
  PAUSE_MAX_DAYS,
  PAUSE_REASON_MAX,
  WEEKDAYS,
  weekdaysLabel,
  type CadenceWindow,
  type RunCadence,
  type RunCadenceStatus,
  type RunCadenceStatusItem,
} from '../../../src/types/runCadence'
import { COVERAGE_ON, LAYOUT, NOW, OVERVIEW_ON, PROJECT_ID, RUNS, RUN_ID, TRENDS_ON, USER } from './fixtures'
import { inboxHandlers } from './fixtures-pages'
import { ROLE_USERS, WORLD_ROLES, type RecordedWrite, type WorldRole } from './fixtures-retention'

export type { RecordedWrite, WorldRole }

const HOUR_MS = 3_600_000
const DAY_MS = 86_400_000
const RANGE_HOURS: Readonly<Record<string, number>> = { '24h': 24, '7d': 168 }
const SEVERITY_RANK: Readonly<Record<string, number>> = { high: 0, medium: 1, low: 2 }
const NO_EXPECTED_RUN = 'No expected run fell in this window yet.'
const MAX_CADENCES = 20
const MAX_TIMES_PER_DAY = 24

const atLeast = (role: WorldRole, min: WorldRole) => WORLD_ROLES.indexOf(role) >= WORLD_ROLES.indexOf(min)
const roleRefusal = (min: WorldRole) => respond(403, { detail: `Requires at least ${min} role` })
const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const isoAt = (ms: number) => new Date(ms).toISOString()

/** A structured refusal with the server's own sentence (`INSIGHT_ERRORS` / `CADENCE_ERRORS`). */
function coded(status: number, code: string, message?: string) {
  const known = (INSIGHT_ERRORS as Record<string, string>)[code] ?? (CADENCE_ERRORS as Record<string, string>)[code]
  return respond(status, { detail: { code, message: message ?? known ?? code } })
}

/** FastAPI's own 422 for a query / body field (the shape `extractErrorMessage` reads first). */
function fastapi422(loc: string[], msg: string) {
  return respond(422, { detail: [{ loc, msg, type: 'value_error' }] })
}

// ── Fixed ids and sentinels (non-hex words: never a uuid collision, never a phone number) ──

export const WORLD_SUITE = 'Payments sentinel'
export const WORLD_TEST = 'checkout_sentinel_test'
export const WORLD_CADENCE_SUITE = 'nightly-regression'
export const WORLD_PAUSE_REASON = 'Release freeze sentinel'

let sequence = 0

/** A canonical (version 4 shaped) uuid, distinct per call. */
export function worldId(): string {
  sequence += 1
  return `00000000-0000-4000-8000-${String(0xf000 + sequence).padStart(12, '0')}`
}

// ── Insight builders (the server's `InsightItem` / `InsightDetectorStatus`, around `NOW`) ──

const LINKS: Readonly<Record<InsightKind, { path: string; label: string }>> = {
  perf_drift: { path: '/trends?tab=durations', label: 'Trends › Execution time' },
  flake_rising: { path: '/flaky', label: 'Flaky tests' },
  coverage_drop: { path: '/coverage?tab=history', label: 'Coverage › History' },
  cost_budget: { path: '/settings/billing', label: 'LLM cost budget' },
}

/** One OPEN `perf_drift` suite insight on "Payments sentinel" (high), first seen 3 h ago, confirmed 12 min ago. */
export function worldInsight(overrides: Partial<Insight> = {}): Insight {
  const kind = (overrides.kind ?? 'perf_drift') as InsightKind
  return {
    id: worldId(),
    kind,
    severity: 'high',
    status: 'open',
    resolved_reason: null,
    title: `Suite ${WORLD_SUITE} is 2.4× slower than its previous 20 runs`,
    subject_label: WORLD_SUITE,
    window_key: '7d',
    evidence: { suite_name: WORLD_SUITE, ratio: 2.4, baseline_runs: 20, newest_run_id: RUN_ID },
    first_seen_at: isoAt(NOW.getTime() - 3 * HOUR_MS),
    last_seen_at: isoAt(NOW.getTime() - 12 * 60_000),
    resolved_at: null,
    detection_count: 3,
    dismissed_at: null,
    dismissed_by_name: null,
    link: LINKS[kind],
    run_path: kind === 'perf_drift' ? `/runs/${RUN_ID}?tab=changes` : null,
    ...overrides,
  }
}

/**
 * The three rows the card / tab specs read: a perf suite slowdown (high), rising flakiness (medium) and a coverage
 * drop that RESOLVED (cleared 2 h ago) — in the server's order.
 */
export function worldInsightRows(): Insight[] {
  return [
    worldInsight(),
    worldInsight({
      kind: 'flake_rising',
      severity: 'medium',
      title: `Flaky tests rising in suite ${WORLD_SUITE} (1 → 4)`,
      subject_label: WORLD_SUITE,
      evidence: { suite_name: WORLD_SUITE, previous: 1, current: 4, examples: [WORLD_TEST] },
      link: LINKS.flake_rising,
      run_path: null,
      first_seen_at: isoAt(NOW.getTime() - 26 * HOUR_MS),
      last_seen_at: isoAt(NOW.getTime() - 20 * 60_000),
      detection_count: 25,
    }),
    worldInsight({
      kind: 'coverage_drop',
      severity: 'medium',
      status: 'resolved',
      resolved_reason: 'cleared',
      resolved_at: isoAt(NOW.getTime() - 2 * HOUR_MS),
      title: 'Tests exercised fell from 100 to 80 (−20 %) in a week',
      subject_label: 'project',
      evidence: { previous: 100, current: 80, pct: 20 },
      link: LINKS.coverage_drop,
      run_path: null,
      first_seen_at: isoAt(NOW.getTime() - 50 * HOUR_MS),
      last_seen_at: isoAt(NOW.getTime() - 3 * HOUR_MS),
      detection_count: 47,
    }),
  ]
}

/** One detector state: measured and quiet by default, evaluated 30 min before `NOW`. */
export function worldDetector(kind: InsightKind, overrides: Partial<InsightDetector> = {}): InsightDetector {
  return {
    kind,
    measured: true,
    reason: null,
    evaluated_at: isoAt(NOW.getTime() - 30 * 60_000),
    subjects_measured: 3,
    firing: 0,
    ...overrides,
  }
}

/** The four detector states, `INSIGHT_KINDS` order; `overrides[kind]` changes one. */
export function worldDetectors(overrides: Partial<Record<InsightKind, Partial<InsightDetector>>> = {}): InsightDetector[] {
  return INSIGHT_KINDS.map((kind) => worldDetector(kind, overrides[kind] ?? {}))
}

/** A not-measured detector with its fixed sentence (`NOT_MEASURED[code]`). */
export function notMeasured(kind: InsightKind, code: keyof typeof NOT_MEASURED): InsightDetector {
  return worldDetector(kind, { measured: false, reason: NOT_MEASURED[code], subjects_measured: 0, firing: 0 })
}

// ── Cadence words (G-C's `run_cadence_schedule`: the cron subset, the summary, the local day label) ──

// One cron field over [lo, hi] — a star, a star with a step, a, a-b, a-b with a step, comma lists, day names for the
// day of week — or null when it cannot be read.
function cronField(text: string, lo: number, hi: number, names: readonly string[] = []): number[] | null {
  const out = new Set<number>()
  for (const part of text.split(',')) {
    const [range, stepText] = part.split('/')
    const step = stepText === undefined ? 1 : Number(stepText)
    if (!Number.isInteger(step) || step < 1) return null
    const value = (token: string) => {
      const named = names.indexOf(token.toLowerCase())
      return named >= 0 ? named : /^\d+$/.test(token) ? Number(token) : NaN
    }
    let from: number
    let to: number
    if (range === '*') [from, to] = [lo, hi]
    else if (range.includes('-')) [from, to] = range.split('-').map(value) as [number, number]
    else [from, to] = [value(range), stepText === undefined ? value(range) : hi]
    if (!Number.isInteger(from) || !Number.isInteger(to) || from < lo || to > hi || from > to) return null
    for (let n = from; n <= to; n += step) out.add(n)
  }
  return [...out].sort((a, b) => a - b)
}

/** `parse_cron`'s refusals in its order, else the Python weekdays and the day's sorted "HH:MM" times. */
export function parseWorldCron(text: string): { code: string } | { weekdays: string[]; times: string[] } {
  const fields = text.split(/\s+/).filter(Boolean)
  if (fields.length !== 5) return { code: 'bad_cron' }
  const minutes = cronField(fields[0], 0, 59)
  const hours = cronField(fields[1], 0, 23)
  const dows = cronField(fields[4], 0, 7, ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'])
  if (!minutes || !hours || !dows || !cronField(fields[2], 1, 31) || !cronField(fields[3], 1, 12)) return { code: 'bad_cron' }
  if (fields[2] !== '*' || fields[3] !== '*') return { code: 'cron_day_fields' }
  if (minutes.length * hours.length > MAX_TIMES_PER_DAY) return { code: 'cron_too_frequent' }
  const pad = (n: number) => String(n).padStart(2, '0')
  const times = hours.flatMap((h) => minutes.map((m) => `${pad(h)}:${pad(m)}`)).sort()
  const weekdays = [...new Set(dows.map((d) => WEEKDAYS[(d + 6) % 7]))]
  return { weekdays, times }
}

/** `RunCadenceRead.summary` as `describe` writes it: "Mon–Fri by 06:00 (Europe/Berlin) · grace 60 min". */
export function describeCadence(c: Pick<RunCadence, 'mode' | 'weekdays' | 'expected_by' | 'cron' | 'timezone' | 'grace_minutes'>): string {
  let days = weekdaysLabel(c.weekdays ?? [])
  let times = [c.expected_by ?? '']
  if (c.mode === 'cron') {
    const parsed = parseWorldCron(c.cron ?? '')
    if ('weekdays' in parsed) [days, times] = [weekdaysLabel(parsed.weekdays), parsed.times]
  }
  const shown = times.slice(0, 4).join(', ') + (times.length > 4 ? ` and ${times.length - 4} more` : '')
  return `${days} by ${shown} (${c.timezone}) · grace ${c.grace_minutes ?? 60} min`
}

/** "Wed 16 Sep": an instant's day in a zone (`local_label`). */
export function localLabel(iso: string, timeZone = 'Europe/Berlin'): string {
  const parts = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone })
    .formatToParts(new Date(iso))
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  return `${get('weekday')} ${get('day')} ${get('month')}`
}

// ── Cadence builders (the server's `RunCadenceRead` / status shapes, around `NOW` = Fri 18 Sep 2026 12:00 UTC) ──

/** A Mon–Fri 06:00 Europe/Berlin cadence on suite "nightly-regression", created by the QA lead 10 days ago. */
export function worldCadence(overrides: Partial<RunCadence> = {}): RunCadence {
  const base: RunCadence = {
    id: worldId(),
    project_id: PROJECT_ID,
    suite_name: WORLD_CADENCE_SUITE,
    mode: 'weekly',
    weekdays: ['mon', 'tue', 'wed', 'thu', 'fri'],
    expected_by: '06:00',
    cron: null,
    timezone: 'Europe/Berlin',
    grace_minutes: 60,
    summary: '',
    effective_from: isoAt(NOW.getTime() - 10 * DAY_MS),
    paused: false,
    paused_at: null,
    paused_until: null,
    paused_reason: null,
    paused_by_name: null,
    next_deadline: '2026-09-21T04:00:00.000Z',
    created_by_name: USER.full_name,
    created_at: isoAt(NOW.getTime() - 10 * DAY_MS),
    updated_at: isoAt(NOW.getTime() - 10 * DAY_MS),
    ...overrides,
  }
  return { ...base, summary: overrides.summary ?? describeCadence(base) }
}

/** A paused copy of `cadence`: until Fri 25 Sep, the sentinel reason, set by the QA lead. */
export function pausedCadence(cadence: RunCadence = worldCadence(), overrides: Partial<RunCadence> = {}): RunCadence {
  return {
    ...cadence,
    paused: true,
    paused_at: isoAt(NOW.getTime() - 2 * HOUR_MS),
    paused_until: '2026-09-25T21:59:00.000Z',
    paused_reason: WORLD_PAUSE_REASON,
    paused_by_name: USER.full_name,
    ...overrides,
  }
}

/** One missed window whose 06:00 Europe/Berlin deadline was `daysAgo` days before `NOW` (labelled in Berlin). */
export function worldMissedWindow(daysAgo: number, overrides: Partial<CadenceWindow> = {}): CadenceWindow {
  const day = new Date(NOW.getTime() - daysAgo * DAY_MS)
  const deadline = new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), 4, 0)).toISOString()
  return {
    deadline,
    window_start: isoAt(Date.parse(deadline) - DAY_MS + HOUR_MS),
    window_end: isoAt(Date.parse(deadline) + HOUR_MS),
    outcome: 'missed',
    recorded: true,
    run_count: 0,
    local_label: localLabel(deadline),
    ...overrides,
  }
}

/** One status row: 5 expected, 5 met by default (pass `missed_windows` with `missed` to script misses). */
export function worldStatusItem(cadence: RunCadence, overrides: Partial<RunCadenceStatusItem> = {}): RunCadenceStatusItem {
  return {
    cadence,
    measured: true,
    reason: null,
    expected: 5,
    met: 5,
    missed: 0,
    paused: 0,
    pending: 0,
    missed_windows: [],
    last_run_at: isoAt(NOW.getTime() - 6 * HOUR_MS),
    ...overrides,
  }
}

/** The status answer over `items` (sums derived); no item → `configured: false`. */
export function worldCadenceStatus(items: RunCadenceStatusItem[], days = 14): RunCadenceStatus {
  const sum = (key: 'expected' | 'met' | 'missed' | 'paused') => items.reduce((acc, it) => acc + (it[key] ?? 0), 0)
  return {
    configured: items.length > 0,
    days,
    window_start: isoAt(NOW.getTime() - days * DAY_MS),
    window_end: NOW.toISOString(),
    items,
    total: items.length,
    expected: sum('expected'),
    met: sum('met'),
    missed: sum('missed'),
    paused: sum('paused'),
  }
}

// ── The world ────────────────────────────────────────────────────────────────

/** Every call a spec may script a refusal for (reads too: a failed read shows the page's "Could not load …"). */
export type WorldCall = 'insights' | 'dismiss' | 'cadences' | 'status' | 'create' | 'update' | 'delete' | 'pause' | 'resume'

/** A scripted answer: `{"detail": {"code", "message"}}` (`message` defaults to the server's sentence for `code`). */
export interface WorldRefusal {
  status: number
  code: string
  message?: string
}

/** The shared set the page's own requests are answered from (see the module docstring). */
export type WorldPage = 'overview' | 'trends' | 'coverage' | 'inbox' | 'settings'

export interface InsightsCadenceWorldOptions {
  /** The signed-in role (default QA_LEAD, the fixture user). */
  role?: WorldRole
  /** The ONE project the world answers for (default `PROJECT_ID`); another project's paths fail closed. */
  projectId?: string
  /** The stored insights (any status, dismissed or not); default none. */
  insights?: Insight[]
  /** The detector states (default the four, measured and quiet); a kind left out reads "Not evaluated yet…". */
  detectors?: InsightDetector[]
  /** The project's `cross_run_insights` (default true). */
  enabled?: boolean
  /** The stored cadences; default none. */
  cadences?: RunCadence[]
  /** The status answer, or a function of the asked days and the live state; default derived from `cadences`. */
  status?: RunCadenceStatus | ((days: number, state: InsightsCadenceWorldState) => RunCadenceStatus)
  /** Scripted refusals by call (live in `state.refusals`: delete one to let the next call through). */
  refusals?: Partial<Record<WorldCall, WorldRefusal>>
  /** Where the writes are recorded (default a fresh array). */
  writes?: RecordedWrite[]
  /** One shared set (exactly as the existing specs use it); default all of them, overview first. */
  page?: WorldPage
  /** Extra handlers, answered after the world's routes and before the shared set. */
  base?: ApiHandlers
}

export interface InsightsCadenceWorldState {
  insights: Insight[]
  detectors: InsightDetector[]
  enabled: boolean
  cadences: RunCadence[]
  status: InsightsCadenceWorldOptions['status']
  refusals: Partial<Record<WorldCall, WorldRefusal>>
}

export interface InsightsCadenceWorld {
  handlers: ApiHandlers
  writes: RecordedWrite[]
  state: InsightsCadenceWorldState
  /** The `user` to sign in as (`ROLE_USERS[role]`). */
  user: typeof USER
}

/** The parsed JSON body of a write; no body → `{}`. */
function bodyOf(request: ApiRequest): Record<string, unknown> {
  try {
    const parsed = request.route.request().postDataJSON() as unknown
    return isObject(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

/** The second-to-last / last path segment (`…/{id}/pause` → id; `…/{id}` → id). */
const idBefore = (path: string, suffix: string | null) => {
  const parts = path.split('/')
  return decodeURIComponent(suffix === null ? parts[parts.length - 1] : parts[parts.length - 2])
}

// ── Insights: the list (R-4) and the dismiss (R-3) ──

/** `list_insights`: confirmed in the range, dismissed only when asked, open → severity → newest → id. */
function insightList(state: InsightsCadenceWorldState, url: URL) {
  const range = url.searchParams.get('range') ?? '7d'
  if (!(range in RANGE_HOURS)) return coded(422, 'unknown_range')
  const limitText = url.searchParams.get('limit')
  const limit = limitText === null ? INSIGHT_LIST_DEFAULT : Number(limitText)
  if (!Number.isInteger(limit) || limit < 1 || limit > INSIGHT_LIST_MAX) {
    return fastapi422(['query', 'limit'], `Input should be between 1 and ${INSIGHT_LIST_MAX}`)
  }
  const includeDismissed = url.searchParams.get('include_dismissed') === 'true'
  const since = NOW.getTime() - RANGE_HOURS[range] * HOUR_MS
  const inRange = state.insights.filter((row) => Date.parse(row.last_seen_at ?? '') >= since)
  const matching = inRange
    .filter((row) => includeDismissed || !row.dismissed_at)
    .sort((a, b) =>
      Number(a.status === 'resolved') - Number(b.status === 'resolved')
      || (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9)
      || (b.last_seen_at ?? '').localeCompare(a.last_seen_at ?? '')
      || a.id.localeCompare(b.id))
  const byKind = new Map(state.detectors.map((d) => [d.kind, d]))
  const detectors = INSIGHT_KINDS.map((kind) => {
    if (!state.enabled) {
      return { kind, measured: false, reason: NOT_MEASURED.flag_off, evaluated_at: byKind.get(kind)?.evaluated_at ?? null, subjects_measured: 0, firing: 0 }
    }
    return byKind.get(kind) ?? { kind, measured: false, reason: NOT_MEASURED.not_evaluated, evaluated_at: null, subjects_measured: 0, firing: 0 }
  })
  return {
    enabled: state.enabled,
    range,
    items: matching.slice(0, limit),
    total: matching.length,
    dismissed_total: inRange.filter((row) => row.dismissed_at).length,
    detectors,
    generated_at: NOW.toISOString(),
  }
}

/** `dismiss_insight`: WHERE id AND project (another project's id is the unknown-id 404); idempotent. */
function dismiss(state: InsightsCadenceWorldState, insightId: string, user: typeof USER) {
  const row = state.insights.find((candidate) => candidate.id === insightId)
  if (!row) return coded(404, 'insight_not_found')
  if (!row.dismissed_at) {
    row.dismissed_at = NOW.toISOString()
    row.dismissed_by_name = user.full_name || user.username
  }
  return row
}

// ── Cadences: validation in the server's order (schema, then `_definition` / `spec_from`, then count / scope) ──

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/
const ZONES: ReadonlySet<string> = new Set([...Intl.supportedValuesOf('timeZone'), 'UTC'])

type Definition = Pick<RunCadence, 'mode' | 'weekdays' | 'expected_by' | 'cron' | 'timezone' | 'grace_minutes'>

/** The schema's own 422s (pattern / bounds / lengths), FastAPI-shaped; null when the body passes them. */
function schemaRefusal(body: Record<string, unknown>) {
  if (body.expected_by != null && !(typeof body.expected_by === 'string' && HHMM.test(body.expected_by))) {
    return fastapi422(['body', 'expected_by'], "String should match pattern '^([01]\\d|2[0-3]):[0-5]\\d$'")
  }
  const grace = body.grace_minutes
  if (grace != null && !(Number.isInteger(grace) && (grace as number) >= 0 && (grace as number) <= 720)) {
    return fastapi422(['body', 'grace_minutes'], 'Input should be between 0 and 720')
  }
  if (typeof body.suite_name === 'string' && body.suite_name.length > 500) {
    return fastapi422(['body', 'suite_name'], 'String should have at most 500 characters')
  }
  return null
}

/** `_definition`: mode → the mode's fields → the cron text → the zone. A refusal code, or the stored definition. */
function definitionOf(d: Definition): { code: string } | Definition {
  if (!(CADENCE_MODES as readonly string[]).includes(String(d.mode))) return { code: 'unknown_mode' }
  let cron: string | null = null
  if (d.mode === 'weekly') {
    const names = d.weekdays ?? []
    if (names.some((n) => !(WEEKDAYS as readonly string[]).includes(n))) return { code: 'weekly_fields' }
    if (names.length === 0 || !d.expected_by || (d.cron && d.cron.trim())) return { code: 'weekly_fields' }
  } else {
    if ((d.weekdays ?? []).length > 0) return { code: 'cron_fields' }
    if (!d.cron || !d.cron.trim() || d.expected_by != null) return { code: 'cron_fields' }
    cron = d.cron.split(/\s+/).filter(Boolean).join(' ')
    const parsed = parseWorldCron(cron)
    if ('code' in parsed) return parsed
  }
  if (!ZONES.has(String(d.timezone))) return { code: 'unknown_timezone' }
  const weekdays = d.mode === 'weekly' ? WEEKDAYS.filter((n) => (d.weekdays ?? []).includes(n)) : null
  return {
    mode: d.mode,
    weekdays,
    expected_by: d.mode === 'weekly' ? d.expected_by : null,
    cron,
    timezone: d.timezone,
    grace_minutes: d.grace_minutes ?? 60,
  }
}

const suiteKey = (name: string | null | undefined) => (name ? name.trim().toLowerCase() : null)
const cleanSuite = (name: unknown) => (typeof name === 'string' && name.trim() ? name.trim() : null)

/** `_assert_scope_free`: one "any run" cadence and one per suite (lower + trim), the any-run sentence its own. */
function scopeRefusal(state: InsightsCadenceWorldState, suiteName: string | null, exceptId: string | null) {
  const key = suiteKey(suiteName)
  const taken = state.cadences.some((c) => c.id !== exceptId && suiteKey(c.suite_name) === key)
  if (!taken) return null
  return key === null
    ? coded(409, 'duplicate_scope', 'This project already has an expected cadence for any run.')
    : coded(409, 'duplicate_scope')
}

/** The list order: the any-run cadence first, then by suite name. */
const listOrder = (a: RunCadence, b: RunCadence) =>
  Number(a.suite_name != null) - Number(b.suite_name != null) || (a.suite_name ?? '').localeCompare(b.suite_name ?? '')

/** `paused` is derived: `paused_until > NOW`. */
const asRead = (c: RunCadence): RunCadence => ({ ...c, paused: Boolean(c.paused_until && Date.parse(c.paused_until) > NOW.getTime()) })

function createCadence(state: InsightsCadenceWorldState, body: Record<string, unknown>, projectId: string, user: typeof USER) {
  const schema = schemaRefusal(body)
  if (schema) return schema
  if (typeof body.timezone !== 'string' || !body.timezone) return fastapi422(['body', 'timezone'], 'Field required')
  const suiteName = cleanSuite(body.suite_name)
  const definition = definitionOf({
    mode: (body.mode as string | undefined) ?? 'weekly',
    weekdays: (body.weekdays as string[] | null | undefined) ?? null,
    expected_by: (body.expected_by as string | null | undefined) ?? null,
    cron: (body.cron as string | null | undefined) ?? null,
    timezone: body.timezone,
    grace_minutes: (body.grace_minutes as number | undefined) ?? 60,
  })
  if ('code' in definition) return coded(422, definition.code)
  if (state.cadences.length >= MAX_CADENCES) return coded(409, 'too_many')
  const scope = scopeRefusal(state, suiteName, null)
  if (scope) return scope
  const row = worldCadence({
    ...definition,
    project_id: projectId,
    suite_name: suiteName,
    effective_from: NOW.toISOString(),
    created_at: NOW.toISOString(),
    updated_at: NOW.toISOString(),
    created_by_name: user.full_name || user.username,
    summary: describeCadence(definition),
  })
  state.cadences.push(row)
  return respond(201, asRead(row))
}

/** `update_cadence`: only the SENT fields; switching the mode drops the other mode's fields; a change resets `effective_from`. */
function updateCadence(state: InsightsCadenceWorldState, cadenceId: string, body: Record<string, unknown>) {
  const schema = schemaRefusal(body)
  if (schema) return schema
  const row = state.cadences.find((c) => c.id === cadenceId)
  if (!row) return coded(404, 'cadence_not_found')
  if (body.clear_suite === true && cleanSuite(body.suite_name) !== null) return coded(422, 'suite_conflict')
  const mode = (body.mode as string | undefined) ?? row.mode ?? 'weekly'
  const sameMode = mode === row.mode
  const sent = (key: string) => body[key] !== undefined && body[key] !== null
  const definition = definitionOf({
    mode,
    weekdays: sent('weekdays') ? (body.weekdays as string[]) : mode === 'weekly' && sameMode ? row.weekdays ?? null : null,
    expected_by: sent('expected_by') ? (body.expected_by as string) : mode === 'weekly' && sameMode ? row.expected_by ?? null : null,
    cron: sent('cron') ? (body.cron as string) : mode === 'cron' && sameMode ? row.cron ?? null : null,
    timezone: sent('timezone') ? (body.timezone as string) : row.timezone,
    grace_minutes: sent('grace_minutes') ? (body.grace_minutes as number) : row.grace_minutes,
  })
  if ('code' in definition) return coded(422, definition.code)
  const suiteName = body.clear_suite === true ? null : sent('suite_name') ? cleanSuite(body.suite_name) : row.suite_name ?? null
  const scope = scopeRefusal(state, suiteName, row.id)
  if (scope) return scope
  const next = { ...definition, suite_name: suiteName }
  const changed = (Object.keys(next) as (keyof typeof next)[])
    .some((key) => JSON.stringify(next[key] ?? null) !== JSON.stringify(row[key] ?? null))
  if (changed) {
    Object.assign(row, next, { summary: describeCadence(definition), effective_from: NOW.toISOString(), updated_at: NOW.toISOString() })
  }
  return asRead(row)
}

/** `pause_cadence`: until in (NOW, NOW + 90 d], a stripped non-empty reason; re-pausing replaces the pause. */
function pauseCadence(state: InsightsCadenceWorldState, cadenceId: string, body: Record<string, unknown>, user: typeof USER) {
  const until = typeof body.until === 'string' ? body.until : ''
  if (!until || !/(Z|[+-]\d{2}:?\d{2})$/.test(until) || Number.isNaN(Date.parse(until))) {
    return fastapi422(['body', 'until'], 'Input should have timezone info')
  }
  if (typeof body.reason !== 'string') return fastapi422(['body', 'reason'], 'Field required')
  if (body.reason.length > PAUSE_REASON_MAX) {
    return fastapi422(['body', 'reason'], `String should have at most ${PAUSE_REASON_MAX} characters`)
  }
  const row = state.cadences.find((c) => c.id === cadenceId)
  if (!row) return coded(404, 'cadence_not_found')
  const untilMs = Date.parse(until)
  if (untilMs <= NOW.getTime()) return coded(422, 'pause_until_past')
  if (untilMs > NOW.getTime() + PAUSE_MAX_DAYS * DAY_MS) return coded(422, 'pause_too_long')
  const reason = body.reason.trim()
  if (!reason) return coded(422, 'pause_reason_required')
  Object.assign(row, {
    paused_at: NOW.toISOString(),
    paused_until: new Date(untilMs).toISOString(),
    paused_reason: reason,
    paused_by_name: user.full_name || user.username,
    updated_at: NOW.toISOString(),
  })
  return asRead(row)
}

/** `resume_cadence`: 409 unless a pause is in force; clears the four pause fields. */
function resumeCadence(state: InsightsCadenceWorldState, cadenceId: string) {
  const row = state.cadences.find((c) => c.id === cadenceId)
  if (!row) return coded(404, 'cadence_not_found')
  if (!row.paused_until || Date.parse(row.paused_until) <= NOW.getTime()) return coded(409, 'not_paused')
  Object.assign(row, { paused_at: null, paused_until: null, paused_reason: null, paused_by_name: null, updated_at: NOW.toISOString() })
  return asRead(row)
}

/** `cadence_status`: the scripted answer, else one honest row per cadence ("No expected run fell…"), never "all met". */
function statusAnswer(state: InsightsCadenceWorldState, url: URL) {
  const daysText = url.searchParams.get('days')
  const days = daysText === null ? 14 : Number(daysText)
  if (!Number.isInteger(days) || days < 1 || days > 90) {
    return fastapi422(['query', 'days'], days < 1 ? 'Input should be greater than or equal to 1' : 'Input should be less than or equal to 90')
  }
  const scripted = state.status
  if (typeof scripted === 'function') return scripted(days, state)
  if (scripted) return scripted
  const items = [...state.cadences].sort(listOrder).map((c) =>
    worldStatusItem(asRead(c), { measured: false, reason: NO_EXPECTED_RUN, expected: 0, met: 0, last_run_at: null }))
  return worldCadenceStatus(items, days)
}

/** The page's own handlers (see `WorldPage`); without `page`, every set, overview first. */
function sharedSet(page: WorldPage | undefined, writes: RecordedWrite[]): ApiHandlers {
  switch (page) {
    case 'overview':
      return OVERVIEW_ON
    case 'trends':
      return TRENDS_ON
    case 'coverage':
      return COVERAGE_ON
    case 'inbox':
      return inboxHandlers(writes)
    case 'settings':
      return [...RUNS, ...LAYOUT]
    default:
      return [...OVERVIEW_ON, ...TRENDS_ON, ...COVERAGE_ON, ...inboxHandlers(writes), ...RUNS, ...LAYOUT]
  }
}

/**
 * The W23a world (see the module docstring). Handlers in order: the insight routes, the cadence routes (`/status`
 * before `/{id}`), `base`, then the page's shared set.
 */
export function insightsCadenceWorld(options: InsightsCadenceWorldOptions = {}): InsightsCadenceWorld {
  const role: WorldRole = options.role ?? 'QA_LEAD'
  const user = ROLE_USERS[role]
  const projectId = options.projectId ?? PROJECT_ID
  const writes = options.writes ?? []
  const state: InsightsCadenceWorldState = {
    insights: (options.insights ?? []).map((row) => ({ ...row })),
    detectors: (options.detectors ?? worldDetectors()).map((d) => ({ ...d })),
    enabled: options.enabled ?? true,
    cadences: (options.cadences ?? []).map((c) => ({ ...c, project_id: projectId })),
    status: options.status,
    refusals: { ...(options.refusals ?? {}) },
  }
  const refusal = (call: WorldCall) => {
    const scripted = state.refusals[call]
    return scripted ? coded(scripted.status, scripted.code, scripted.message) : null
  }
  /** Record the write, then the role guard, then a scripted refusal; null = go on. */
  const gate = (request: ApiRequest, call: WorldCall, min: WorldRole) => {
    writes.push({ line: `${request.method} ${request.path}`, body: bodyOf(request) })
    if (!atLeast(role, min)) return roleRefusal(min)
    return refusal(call)
  }
  const insightsPath = `/api/v1/projects/${projectId}/insights`
  const cadencesPath = `/api/v1/projects/${projectId}/run-cadences`
  const cadenceItem = new RegExp(`^${cadencesPath}/[^/]+$`)

  const routes: ApiHandlers = [
    [insightsPath, ({ url }) => refusal('insights') ?? insightList(state, url)],
    [new RegExp(`^${insightsPath}/[^/]+/dismiss$`), (request) =>
      gate(request, 'dismiss', 'QA_ENGINEER') ?? dismiss(state, idBefore(request.path, 'dismiss'), user), 'POST'],
    [`${cadencesPath}/status`, ({ url }) => refusal('status') ?? statusAnswer(state, url)],
    [cadencesPath, () => refusal('cadences') ?? {
      items: [...state.cadences].sort(listOrder).map(asRead),
      total: state.cadences.length,
    }],
    [cadencesPath, (request) =>
      gate(request, 'create', 'QA_LEAD') ?? createCadence(state, bodyOf(request), projectId, user), 'POST'],
    [cadenceItem, (request) =>
      gate(request, 'update', 'QA_LEAD') ?? updateCadence(state, idBefore(request.path, null), bodyOf(request)), 'PATCH'],
    [cadenceItem, (request) => {
      const refused = gate(request, 'delete', 'QA_LEAD')
      if (refused) return refused
      const id = idBefore(request.path, null)
      const index = state.cadences.findIndex((c) => c.id === id)
      if (index < 0) return coded(404, 'cadence_not_found')
      state.cadences.splice(index, 1)
      return respond(204)
    }, 'DELETE'],
    [new RegExp(`^${cadencesPath}/[^/]+/pause$`), (request) =>
      gate(request, 'pause', 'QA_LEAD') ?? pauseCadence(state, idBefore(request.path, 'pause'), bodyOf(request), user), 'POST'],
    [new RegExp(`^${cadencesPath}/[^/]+/resume$`), (request) =>
      gate(request, 'resume', 'QA_LEAD') ?? resumeCadence(state, idBefore(request.path, 'resume')), 'POST'],
  ]
  const handlers: ApiHandlers = [...routes, ...(options.base ?? []), ...sharedSet(options.page, writes)]
  return { handlers, writes, state, user }
}
