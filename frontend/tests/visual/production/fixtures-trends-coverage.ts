/**
 * W08 "trends-coverage" fixtures: the worlds the wave's specs drive and the
 * variants its new visual regions shoot. The DEFAULT answers of the wave's
 * new reads (the trend summary, the window's notes — empty —, the coverage
 * history, the untagged tests, value by team, the coverage map's paging)
 * live in `fixtures.ts`, inside the handler sets every `/trends`,
 * `/coverage` and `/value-metrics` spec already uses; this file only adds
 * what one spec needs. It imports from `fixtures.ts`, never the other way
 * round (no import cycle).
 *
 * Every write handler names its method (`'POST'`, `'PATCH'`, `'DELETE'`),
 * so a write the page should not make stays unmocked and fails the spec.
 */
import { RawResponse, respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import {
  CHART_DATA_PATH,
  COVERAGE_MAP_PATH,
  COVERAGE_MAP_SUITES,
  coverageMapOf,
  chartMeta,
  daysAgo,
  HOSTILE_NAME,
  isoAgo,
  LAYOUT,
  mapTests,
  onTheWire,
  PROJECT_ID,
  RELEASES,
  RUNS,
  suiteKey,
  TIMELINE_NOTES_PATH,
  timelineNotesList,
  USER,
  type MapSuite,
} from './fixtures'

const P = PROJECT_ID

const intOf = (request: ApiRequest, name: string, fallback: number) => {
  const raw = request.url.searchParams.get(name)
  const n = raw == null ? NaN : Number(raw)
  return Number.isFinite(n) ? n : fallback
}

const bodyOf = (request: ApiRequest): unknown => request.route.request().postDataJSON()

/** A write the page made, with its body, in order. */
export interface RecordedWrite {
  line: string
  body: unknown
}

// ── Users of the specs (USER is the QA lead) ───────────────────────────────

export const QA_ENGINEER = { ...USER, id: '00000000-0000-4000-8000-0000000008e1', email: 'eng@example.test', username: 'qa_engineer', full_name: 'Quinn Engineer', role: 'QA_ENGINEER' }
export const VIEWER = { ...USER, id: '00000000-0000-4000-8000-0000000008e2', email: 'viewer@example.test', username: 'viewer', full_name: 'Vic Viewer', role: 'VIEWER' }

// ── TRND-01: the Trends ⋯ menu's Email report and Export PDF ──────────────

const member = (userId: string, email: string, username: string, fullName: string | null, role: string, extra: Record<string, unknown> = {}) => ({
  id: `aaaa0000-0000-4000-8000-${userId.slice(-12)}`,
  user_id: userId,
  project_id: P,
  role,
  created_at: '2026-01-05T09:00:00Z',
  email,
  username,
  full_name: fullName,
  is_service_account: false,
  is_synthetic: false,
  is_active: true,
  ...extra,
})

/** Two people to send to, the reader, and three rows the dialog must leave out (service, synthetic, inactive). */
export const MEMBER = {
  dana: member('00000000-0000-4000-8000-0000000008a1', 'dana@example.test', 'dana', 'Dana Whitfield', 'QA_ENGINEER'),
  omar: member('00000000-0000-4000-8000-0000000008a2', 'omar@example.test', 'omar', 'Omar Haddad', 'TESTER'),
  lead: member(USER.id, USER.email, USER.username, USER.full_name, 'QA_LEAD'),
  service: member('00000000-0000-4000-8000-0000000008a3', 'ci-bot@example.test', 'ci-bot', 'CI bot', 'QA_ENGINEER', { is_service_account: true }),
  synthetic: member('00000000-0000-4000-8000-0000000008a4', 'qa-lead+checkout@example.test', 'qa_lead_checkout', 'Checkout QA lead', 'QA_LEAD', { is_synthetic: true }),
  inactive: member('00000000-0000-4000-8000-0000000008a5', 'left@example.test', 'left', 'Lee Former', 'VIEWER', { is_active: false }),
}
export const MEMBERS = Object.values(MEMBER)
/** The rows the dialog lists (people only). */
export const LISTED_MEMBERS = [MEMBER.dana, MEMBER.omar, MEMBER.lead]

export const EMAIL_AVAILABILITY_PATH = '/api/v1/reports/email-trends/availability'
export const EMAIL_TRENDS_PATH = '/api/v1/reports/email-trends'
export const TRENDS_PDF_PATH = '/api/v1/reports/trends/pdf'
export const NOT_CONFIGURED = 'Email is not configured — Settings › Notifications.'

/** A one-page PDF's bytes (what the browser saves; the spec reads its name and magic number). */
const PDF_BYTES = '%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n'

export interface ReportWorldOptions {
  /** `false`: the relay is not configured, so the dialog says why before any click. */
  available?: boolean
}

/** The members list, the availability read, the email POST (recorded) and the PDF GET (recorded). */
export function trendsReportWorld({ available = true }: ReportWorldOptions = {}) {
  const writes: RecordedWrite[] = []
  const pdfs: string[] = []
  const handlers: ApiHandlers = [
    [`/api/v1/projects/${P}/members`, () => MEMBERS],
    [
      EMAIL_AVAILABILITY_PATH,
      () => (available ? { available: true, code: null, reason: null } : { available: false, code: 'not_configured', reason: NOT_CONFIGURED }),
    ],
    [
      EMAIL_TRENDS_PATH,
      (request) => {
        const body = bodyOf(request) as { recipient_user_ids?: string[]; recipient_emails?: string[]; days?: number }
        writes.push({ line: `POST ${request.path}`, body })
        if (!available) return respond(503, { detail: NOT_CONFIGURED })
        const byId = new Map(MEMBERS.map((m) => [m.user_id, m.email]))
        const addresses = [...new Set([...(body.recipient_user_ids ?? []).map((id) => byId.get(id) ?? ''), ...(body.recipient_emails ?? [])])].filter(Boolean)
        return { status: 'sent', sent_to: addresses.length, recipient: addresses[0] ?? null, window_days: body.days ?? 14 }
      },
      'POST',
    ],
    [
      TRENDS_PDF_PATH,
      (request) => {
        pdfs.push(`GET ${request.path}${request.url.search}`)
        return new RawResponse(PDF_BYTES, 'application/pdf')
      },
    ],
  ]
  return { handlers, writes, pdfs }
}

// ── TRND-04: timeline notes ────────────────────────────────────────────────

export interface FixtureNote {
  id: string
  project_id: string
  starts_on: string
  ends_on: string
  text: string
  suite_name: string | null
  created_by_name: string | null
  can_edit: boolean
  created_at: string | null
  updated_at: string | null
}

export const note = (id: string, from: string, to: string, text: string, over: Partial<FixtureNote> = {}): FixtureNote => ({
  id,
  project_id: P,
  starts_on: from,
  ends_on: to,
  text,
  suite_name: null,
  created_by_name: 'QA Lead',
  can_edit: true,
  created_at: isoAgo(1),
  updated_at: null,
  ...over,
})

/**
 * The visual variant (`trends-on-pass-rate-notes`): one band over the 7-day
 * gap of the Trends fixture and one line on day 3 (the bad day), both inside
 * the default 14-day window.
 */
export const VISUAL_NOTES: FixtureNote[] = [
  note('99999999-9999-4999-8999-000000000001', daysAgo(11), daysAgo(5), 'CI runners paused for the datacentre move'),
  note('99999999-9999-4999-8999-000000000002', daysAgo(3), daysAgo(3), 'release freeze'),
]

const overlaps = (n: FixtureNote, from: string, to: string) => n.starts_on <= to && n.ends_on >= from

/**
 * `/projects/{p}/timeline-notes`: GET lists the window's notes (a suite note
 * only under that suite, any case; a project-wide note always), POST creates
 * (the body recorded and echoed back as the new note, authored by `author`),
 * PATCH / DELETE edit and remove (recorded).
 */
export function notesWorld(initial: readonly FixtureNote[] = [], { author = USER.full_name }: { author?: string } = {}) {
  const notes: FixtureNote[] = [...initial]
  const writes: RecordedWrite[] = []
  const list = (request: ApiRequest) => {
    const q = request.url.searchParams
    const from = q.get('from') ?? daysAgo(13)
    const to = q.get('to') ?? daysAgo(0)
    const suite = (q.get('suite') ?? '').trim().toLowerCase()
    const items = notes
      .filter((n) => overlaps(n, from, to))
      .filter((n) => n.suite_name === null || (suite !== '' && n.suite_name.trim().toLowerCase() === suite))
      .sort((a, b) => (a.starts_on < b.starts_on ? -1 : a.starts_on > b.starts_on ? 1 : 0))
    return timelineNotesList(request, items)
  }
  const one = /^\/api\/v1\/projects\/[^/]+\/timeline-notes\/[^/]+$/
  const handlers: ApiHandlers = [
    [TIMELINE_NOTES_PATH, list],
    [
      TIMELINE_NOTES_PATH,
      (request) => {
        const body = bodyOf(request) as { starts_on: string; ends_on?: string | null; text: string; suite_name?: string | null }
        writes.push({ line: `POST ${request.path}`, body })
        const created = note(`99999999-9999-4999-8999-${String(100 + notes.length).padStart(12, '0')}`, body.starts_on, body.ends_on ?? body.starts_on, body.text.trim(), {
          suite_name: body.suite_name ?? null,
          created_by_name: author,
          created_at: new Date('2026-09-18T12:00:00Z').toISOString(),
        })
        notes.push(created)
        return respond(201, created)
      },
      'POST',
    ],
    [
      one,
      (request) => {
        const body = bodyOf(request) as Partial<FixtureNote>
        writes.push({ line: `PATCH ${request.path}`, body })
        const target = notes.find((n) => request.path.endsWith(`/${n.id}`))
        if (!target) return respond(404, { detail: 'Timeline note not found in this project.' })
        Object.assign(target, body)
        return target
      },
      'PATCH',
    ],
    [
      one,
      (request) => {
        writes.push({ line: `DELETE ${request.path}`, body: null })
        const at = notes.findIndex((n) => request.path.endsWith(`/${n.id}`))
        if (at < 0) return respond(404, { detail: 'Timeline note not found in this project.' })
        notes.splice(at, 1)
        return respond(204)
      },
      'DELETE',
    ],
  ]
  return { handlers, writes, notes }
}

// ── TRND-07: Fix tagging (the catalogue's suites, the bulk link, set-default) ─

export const CHECKOUT_SUITE_ID = '88888888-8888-4888-8888-0000000000c1'
const suiteRow = (id: string, name: string, isDefault = false, count = 0) => ({
  id,
  project_id: P,
  name,
  description: null,
  is_default: isDefault,
  tags: null,
  test_case_count: count,
  created_at: '2026-02-01T09:00:00Z',
  updated_at: null,
})

/** `/api/v1/suites`: the catalogue suites the target select offers. */
export const CATALOGUE_SUITES = [
  suiteRow('88888888-8888-4888-8888-000000000001', 'Default Suite (checkout)', true, 41),
  suiteRow('88888888-8888-4888-8888-0000000000a1', 'Auth', false, 42),
  suiteRow(CHECKOUT_SUITE_ID, 'Checkout', false, 57),
  suiteRow('88888888-8888-4888-8888-0000000000b1', 'Payments', false, 33),
]

/** The suites list, the bulk-link POST and the set-default POST, each recorded; `missing` ids answer as gone from the catalogue. */
export function fixTaggingWorld({ missing = [] as string[] } = {}) {
  const writes: RecordedWrite[] = []
  const handlers: ApiHandlers = [
    ['/api/v1/suites', () => ({ items: CATALOGUE_SUITES, total: CATALOGUE_SUITES.length })],
    [
      '/api/v1/canonical-test-cases/bulk-link',
      (request) => {
        const body = bodyOf(request) as { target_test_suite_id: string; canonical_ids: string[] }
        writes.push({ line: `POST ${request.path}`, body })
        const gone = body.canonical_ids.filter((id) => missing.includes(id))
        return { moved: body.canonical_ids.length - gone.length, skipped_already_in_target: 0, missing_ids: gone }
      },
      'POST',
    ],
    [
      /^\/api\/v1\/suites\/[^/]+\/set-default$/,
      (request) => {
        writes.push({ line: `POST ${request.path}`, body: null })
        const id = request.path.split('/')[4]
        const row = CATALOGUE_SUITES.find((s) => s.id === id)
        return row ? { ...row, is_default: true } : respond(404, { detail: 'Suite not found' })
      },
      'POST',
    ],
  ]
  return { handlers, writes }
}

// ── TRND-08: the ownership rules a team link lands on ─────────────────────

const ownershipRule = (i: number, team: string, pattern: string) => ({
  id: `88888888-8888-4888-8888-00000000010${i}`,
  project_id: P,
  match_type: 'suite',
  match_pattern: pattern,
  service_name: null,
  team_name: team,
  team_contact: null,
  priority: i,
  is_active: true,
  created_by: null,
  created_at: isoAgo(20),
  updated_at: null,
})

/** Three rules of three teams: `?team=Payments` keeps one. */
export const OWNERSHIP_RULES = [ownershipRule(1, 'Payments', 'payments*'), ownershipRule(2, 'Checkout', 'checkout*'), ownershipRule(3, 'Identity', 'auth*')]

/** What `/ownership` asks besides the layout (its rules, the CODEOWNERS badge, the Java source roots). */
export const OWNERSHIP_PAGE: ApiHandlers = [
  [`/api/v1/projects/${P}/ownership/rules`, () => OWNERSHIP_RULES],
  [
    `/api/v1/projects/${P}/ownership/codeowners/coverage`,
    () => ({ path_rules: 0, codeowners_rules: 0, sampled: 0, located: 0, matched: 0, coverage_pct: null, lookback_days: 30, source_roots: 0 }),
  ],
  [`/api/v1/projects/${P}/ownership/source-roots`, () => ({ roots: [], max_roots: 20 })],
]

// ── TRND-12: the coverage map's "Other (n)" and needs-review variants ─────

/** 503 suites of one test each, `suite 001` the busiest: level 1 folds into 498 + "Other (5)". */
export const WIDE_SUITES: MapSuite[] = Array.from({ length: 503 }, (_, i) => {
  const n = String(i + 1).padStart(3, '0')
  // More tests for the first suites, so the ranking (most tests first) is the numbering.
  const size = i < 498 ? 1 + Math.floor((503 - i) / 100) : 1
  return { label: `suite ${n}`, classes: [{ key: `Spec${n}`, tests: mapTests(`wide-${n}`, Array.from({ length: size }, (_, t) => `case ${n}.${t + 1}`), 90, 1) }] }
})
export const WIDE_MAP: ApiHandlers = [[COVERAGE_MAP_PATH, coverageMapOf({ suites: WIDE_SUITES })]]

/**
 * The fingerprints the last suite sync marked `needs_review`: three of
 * Payments' tests (two in PaymentsSpec, one in RefundSpec) and one of Auth's.
 * The map's nodes then carry `needs_review_count` (3 on Payments, 1 on Auth,
 * 4 on the root, 0 elsewhere).
 */
export const NEEDS_REVIEW = new Set(['fp-payments-0-1', 'fp-payments-0-4', 'fp-payments-1-0', 'fp-auth-2'])
export const NEEDS_REVIEW_MAP: ApiHandlers = [[COVERAGE_MAP_PATH, coverageMapOf({ suites: COVERAGE_MAP_SUITES, needsReview: NEEDS_REVIEW })]]
/** Payments' key in the map (the suite the review intent opens in Test Management). */
export const NEEDS_REVIEW_SUITE = suiteKey('Payments')

// ── TRND-13: the Explorer's chart-data ─────────────────────────────────────

/** Each facet's keys, in the server's rank order (executions, largest first); `(none)` is the bucket a filter cannot name. */
export const EXPLORE_KEYS: Record<string, string[]> = {
  suite: ['checkout', 'auth', 'payments', '(none)', 'search', 'notifications'],
  release: ['unattributed'],
  branch: ['main', 'release/2.4', '(none)', 'feature/login', 'feature/cart', 'hotfix/tax', 'renovate/deps'],
  environment: ['prod', 'staging', 'qa', '(none)'],
  status: ['passed', 'failed', 'skipped', 'broken'],
  failure_category: ['unknown', 'product_bug', 'automation_bug', 'environment_issue'],
  ingestion_source: ['sdk', 'junit_upload', '(none)', 'api'],
  project: [P],
  test: ['fp-t1', 'fp-t2', 'fp-t3', 'fp-t4', 'fp-t5', 'fp-t6', 'fp-t7', 'fp-t8', 'fp-t9'],
}
/** The branches a "One per branch" grid draws (every key but `(none)`). */
export const EXPLORE_BRANCHES = EXPLORE_KEYS.branch.filter((key) => key !== '(none)')

const FACET_DIMENSIONS = ['branch', 'environment', 'status', 'failure_category', 'ingestion_source']
const ROW_LEVEL = ['status', 'failure_category']
const RATES = ['pass_rate', 'failure_rate']

/** A refusal in `analytics_errors.error_body`'s shape. */
const refused = (code: string, param: string, message: string) =>
  respond(422, { code, message, param, allowed: null, request_id: `req-fixture-${code}`, detail: message })

/** The Mondays (ISO weeks) whose week overlaps the `days` ending today. */
function weeksOf(days: number): string[] {
  const out: string[] = []
  for (let n = days - 1; n >= 0; n--) {
    const day = new Date(`${daysAgo(n)}T00:00:00Z`)
    const monday = new Date(day.getTime() - ((day.getUTCDay() + 6) % 7) * 86_400_000).toISOString().slice(0, 10)
    if (!out.includes(monday)) out.push(monday)
  }
  return out
}

/** A value in a plausible range, a pure function of where it is. */
const valueAt = (metric: string, facetIndex: number, seriesIndex: number, bucket: number) => {
  if (RATES.includes(metric)) {
    const base = metric === 'pass_rate' ? 92 - facetIndex * 4 : 6 + facetIndex * 3
    return Math.round(Math.max(0, Math.min(100, base + ((bucket * 7 + seriesIndex * 3) % 9) - 4 - seriesIndex)) * 10) / 10
  }
  return Math.max(0, 140 - facetIndex * 17 - seriesIndex * 23 + ((bucket * 13 + seriesIndex * 5) % 31))
}

/**
 * `chart-data` as the Explorer asks it (TRND-13, mirroring `parse_chart_spec`
 * + `parse_facet`): a DISCOVERY (a category first dimension: the facet's keys
 * ranked, one series per key of the second) or a PANEL (day / week, one series
 * per key of the series dimension, filtered to `facet_value` / `suite_name` /
 * `release_id`). `facet_pair`, `facet_dimension_enum` and `facet_metric_grain`
 * are refused as the server refuses them, so a request the Explorer must not
 * make fails loudly.
 */
export function exploreChartData(request: ApiRequest) {
  const q = request.url.searchParams
  const days = intOf(request, 'days', 30)
  const metric = q.get('metric') ?? 'executions'
  const groupBy = q.getAll('group_by')
  const facetDimension = q.get('facet_dimension')
  const facetValue = q.get('facet_value')
  const topN = q.get('top_n') === null ? null : Number(q.get('top_n'))
  if ((facetDimension === null) !== (facetValue === null)) return refused('facet_pair', 'facet_dimension', 'facet_dimension and facet_value are sent together')
  if (facetDimension !== null && !FACET_DIMENSIONS.includes(facetDimension)) {
    return refused('facet_dimension_enum', 'facet_dimension', 'facet_dimension is one of the facet dimensions')
  }
  if (facetDimension !== null && metric === 'run_count' && ROW_LEVEL.includes(facetDimension)) {
    return refused('facet_metric_grain', 'facet_dimension', 'one run holds tests of several statuses (or failure categories), so it would be counted in more than one panel')
  }
  const [first, second] = groupBy
  const seriesKeysOf = (dimension: string | undefined) => (dimension ? EXPLORE_KEYS[dimension] ?? ['value'] : ['value'])
  const capped = (keys: string[]) => (topN !== null && keys.length > topN ? [...keys.slice(0, topN), '__other__'] : keys)
  const definitions: Record<string, unknown> = { grain: facetDimension && ROW_LEVEL.includes(facetDimension) ? 'execution_row' : 'run_aggregate' }
  if (facetDimension !== null) definitions.facet = { dimension: facetDimension, value: facetValue }
  const meta = chartMeta(days, { definitions })
  if (first !== 'day' && first !== 'week') {
    // Discovery: executions by the facet (x), split by the series when paired.
    const keys = EXPLORE_KEYS[first] ?? []
    const series = capped(second ? seriesKeysOf(second) : ['value'])
    return onTheWire({
      meta,
      series: {
        kind: 'series',
        dimensions: [...groupBy],
        x_type: 'category',
        series: series.map((key, s) => ({
          key,
          label: key === '__other__' ? 'Other' : key,
          points: keys.map((x, i) => ({ x, y: 1_000 - i * 90 - s * 7, n: 1_000 - i * 90 - s * 7, measured: true, reason: null })),
        })),
      },
    })
  }
  const filterKey = facetValue ?? q.get('suite_name') ?? q.get('release_id')
  const facetKeys = facetDimension ? EXPLORE_KEYS[facetDimension] : q.get('suite_name') ? EXPLORE_KEYS.suite : EXPLORE_KEYS.release
  const facetIndex = Math.max(0, filterKey === null ? 0 : facetKeys.indexOf(filterKey))
  const buckets = first === 'week' ? weeksOf(days) : Array.from({ length: days }, (_, i) => daysAgo(days - 1 - i))
  const series = capped(seriesKeysOf(second))
  return onTheWire({
    meta,
    series: {
      kind: 'series',
      dimensions: [...groupBy],
      x_type: 'time',
      series: series.map((key, s) => ({
        key,
        label: key === '__other__' ? 'Other' : key,
        points: buckets.map((x, b) => {
          const y = valueAt(metric, facetIndex, s, b)
          return { x, y, n: 40 + ((b * 11 + s) % 25), measured: true, reason: null }
        }),
      })),
    },
  })
}

const RELEASES_ANSWER = { items: RELEASES, total: RELEASES.length, page: 1, size: 100, pages: 1 }

/** Everything `/explore` asks for: the layout, the existence probe, the release list, chart-data. */
export const EXPLORE: ApiHandlers = [['/api/v1/releases', () => RELEASES_ANSWER], [CHART_DATA_PATH, exploreChartData], ...LAYOUT, ...RUNS]

// ── TRND-10: Compare's metrics beyond the pass rate ────────────────────────

/** Compare's three busiest suites (`COMPARE_Q` in rollout-trends), as the fixture's suite series names them. */
const COMPARE_SUITES = ['Auth', 'Checkout', 'Payments']

/**
 * `chart-data?metric=failures|executions&group_by=day&group_by=suite&suite_name=…`
 * (Compare customised to a count): one line per named suite, the gap days of
 * the Trends fixture empty. Put BEFORE `TRENDS_ON` (first match wins); any
 * other request falls through to the shared handler.
 */
export function compareCountChartData(fallback: (request: ApiRequest) => unknown) {
  return (request: ApiRequest) => {
    const q = request.url.searchParams
    const metric = q.get('metric')
    if (!(metric === 'failures' || metric === 'executions') || q.getAll('group_by').join(',') !== 'day,suite' || q.getAll('suite_name').length === 0) {
      return fallback(request)
    }
    const days = intOf(request, 'days', 14)
    const named = q.getAll('suite_name').map((s) => s.toLowerCase())
    const gap = new Set([5, 6, 7, 8, 9, 10, 11])
    return onTheWire({
      meta: chartMeta(days),
      series: {
        kind: 'series',
        dimensions: ['day', 'suite'],
        x_type: 'time',
        series: COMPARE_SUITES.filter((s) => named.includes(s.toLowerCase())).map((suite, s) => ({
          key: suite,
          label: suite,
          points: Array.from({ length: days }, (_, i) => {
            const n = days - 1 - i
            if (gap.has(n)) return { x: daysAgo(n), y: null, n: 0, measured: false, reason: 'no executions in this bucket' }
            const y = metric === 'failures' ? 3 + ((n * 5 + s * 3) % 11) : 90 + ((n * 7 + s * 13) % 40)
            return { x: daysAgo(n), y, n: y, measured: true, reason: null }
          }),
        })),
      },
    })
  }
}

// ── Saved views (the report-views.spec harness, shared by Trends and the Explorer) ─

export interface SavedViewRow {
  id: string
  user_id: string
  project_id: string
  name: string
  description: null
  page: string
  filters: Record<string, unknown>
  is_shared: boolean
  is_default: boolean
  created_at: string
  updated_at: null
}

/** The saved-views API over an in-test list: GET by page, POST appends (and records the body). */
export function savedViewsStore(rows: SavedViewRow[] = []) {
  const posted: Record<string, unknown>[] = []
  const handlers: ApiHandlers = [
    ['/api/v1/saved-views', ({ url }) => rows.filter((row) => row.page === url.searchParams.get('page'))],
    [
      '/api/v1/saved-views',
      ({ route }) => {
        const body = route.request().postDataJSON() as Record<string, unknown>
        posted.push(body)
        const row: SavedViewRow = {
          id: `view-${rows.length + 1}`,
          user_id: USER.id,
          project_id: String(body.project_id),
          name: String(body.name),
          description: null,
          page: String(body.page),
          filters: body.filters as Record<string, unknown>,
          is_shared: body.is_shared === true,
          is_default: body.is_default === true,
          created_at: '2026-10-01T00:00:00Z',
          updated_at: null,
        }
        rows.push(row)
        return row
      },
      'POST',
    ],
  ]
  return { handlers, posted, rows }
}

/** A hostile note text (rendered as text: no element, no handler run). */
export const HOSTILE_NOTE = HOSTILE_NAME
