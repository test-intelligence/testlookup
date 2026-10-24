/**
 * W12 "search" hermetic answers (`docs/deferred-scope-2026-10-13/work/W12-contract.md` §1.1, §3, §4.2, §6.2 G-E):
 * the reads and writes the W12 specs (`tests/ci-e2e/search-*.spec.ts`, `test-case-similar.spec.ts`,
 * `settings-embedding.spec.ts`) and the W12 regions (`tests/visual/prod-search.visual.spec.ts`) meet.
 *
 *  - `searchWorld(options)`: `/search` as a W12 server answers it — the index status (with the SRCH-01 / -02 keys),
 *    the entity counts, `GET /search/global` (with `parsed`), `GET /search` (Hybrid / Semantic, the fallback
 *    reasons), the reader's recents (`GET` / `DELETE /search/recent`), the suggestions, the usage tiles (QA lead+,
 *    the route's 403 below), `POST /search/reindex`, and the saved searches (`/saved-views`, `page: "search"`,
 *    list / create / rename / share / delete, the list filtered like `routers/saved_views.list_saved_views`);
 *  - `similarWorld(options)`: one test result's page (`TEST_CASE`) with `GET /search/similar/{id}`;
 *  - `embeddingWorld(options)`: Settings › AI and Settings › Data & storage with `embedding_index`, the re-embed
 *    routes and the Collection prefix fields.
 *
 * W12's answers are listed BEFORE the worlds they compose (first match wins, `production-pages.ts`). Every factory
 * returns fresh state per call and records each write it answered (`writes`: `METHOD path?query` and the parsed
 * JSON body), so a spec pins the whole body. The sentences are COPIED from the contract (§1.2), never imported from
 * the app: a fixture that tracked a copy change would hide the change the spec exists to catch. No query text is
 * logged here either. Not a spec file.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { isoAgo, LAYOUT, NOW, PROJECT_ID, RUN_ID, USER } from './fixtures'
import { aiConfig, aiConfigHandlers, EMBEDDING_INDEX_IN_SYNC, MODEL_STATUS } from './fixtures-ai-config'
import { TEST_CASE, TEST_CASE_API, TEST_CASE_ERROR, TEST_CASE_ID } from './fixtures-pages'
import { ADMIN, QA_ENGINEER, VIEWER } from './fixtures-releases'

// ── Who ────────────────────────────────────────────────────────────────────

export type SearchRole = 'ADMIN' | 'QA_LEAD' | 'QA_ENGINEER' | 'VIEWER'
export { ADMIN, QA_ENGINEER, VIEWER }
export const QA_LEAD = USER
export const SEARCH_USERS: Record<SearchRole, typeof USER> = { ADMIN, QA_LEAD: USER, QA_ENGINEER, VIEWER }
/** `usePermissions().isQaLead`: QA lead and administrator. */
export const LEAD_ROLES: readonly SearchRole[] = ['QA_LEAD', 'ADMIN']

/** Another member of the project (a saved search they shared is run-only for the reader). */
export const OTHER_MEMBER_ID = '00000000-0000-4000-8000-0000000000b7'

// ── Paths ──────────────────────────────────────────────────────────────────

export const SEARCH_API = '/api/v1/search'
export const GLOBAL_API = '/api/v1/search/global'
export const INDEX_STATUS_API = '/api/v1/search/index-status'
export const ENTITY_COUNTS_API = '/api/v1/search/entity-counts'
export const RECENT_API = '/api/v1/search/recent'
export const SUGGESTED_API = '/api/v1/search/suggested'
export const USAGE_API = '/api/v1/search/usage'
export const REINDEX_API = '/api/v1/search/reindex'
export const SAVED_VIEWS_API = '/api/v1/saved-views'
const SAVED_VIEW_ID_PATH = /^\/api\/v1\/saved-views\/[^/]+$/
export const similarApi = (testId: string) => `/api/v1/search/similar/${testId}`

// ── The sentences (§1.2, §2; copied, not imported) ─────────────────────────

export const SEARCH_TEXT = {
  scopeSwitched: 'Semantic and hybrid search cover test results only, so the scope is now Tests.',
  modeSwitched: 'Keyword search covers every kind; semantic covers test results only.',
  fallback: {
    semantic_unavailable: 'Semantic index unavailable — showing keyword results.',
    no_semantic_match: 'No semantic matches — showing keyword results.',
    no_text: 'Semantic search needs words to compare — showing keyword results for the filters.',
  },
  semanticReason: {
    not_configured: 'Needs the semantic index: no ChromaDB server answers here.',
    unavailable: 'The semantic index answered with an error.',
    embedder_missing: 'The embedding model is not on this server, and offline mode forbids downloading it.',
    index_empty: 'The semantic index holds no test results here yet. Re-index it under Searchable data.',
    unknown: 'The semantic index status could not be read.',
  },
  lowerBound: 'Search sampled the most relevant matches. Counts shown are lower bounds.',
  similarEmpty: {
    ok: "No similar failures in this project's semantic index.",
    not_configured: 'Similar failures need the semantic index: no ChromaDB server answers here.',
    unavailable: 'The semantic index answered with an error. Try again later.',
    error: 'Could not load similar failures.',
  },
  reindexQueuedToast: 'Re-index queued.',
  reindexRefused: 'Triggering a reindex requires QA_LEAD or higher',
  clearRecents: "Clear all 2 recent searches? This can't be undone.",
  recentsCleared: 'Recent searches cleared',
  typeahead: {
    searching: 'Searching…',
    none: 'No matches. Press Enter to search everything.',
    error: 'Could not load matches.',
  },
  usageNotMeasured: 'Not measured yet: no search ran today.',
  usageRefused: 'Requires at least QA_LEAD role',
} as const

// ── The index status (`GET /search/index-status`, §1.1: every W12 key always present) ──

/** The legacy generation: today's collection names, the bundled MiniLM. */
const LEGACY_INDEX = {
  collection: 'test_case_search',
  generation: '',
  embedder: { provider: 'default', model: 'all-MiniLM-L6-v2' },
  embedder_available: true,
  reembed: null,
}

/** Healthy, 2,290 documents written an hour before `NOW`: Hybrid / Semantic offered. */
export const INDEX_READY = {
  status: 'healthy',
  document_count: 2290,
  last_indexed_at: isoAgo(0, 1),
  ...LEGACY_INDEX,
  semantic_available: true,
  semantic_reason: null,
  semantic_reason_text: null,
}

/** No ChromaDB server answers (`make dev`'s default): Hybrid / Semantic disabled with the reason. */
export const INDEX_NOT_CONFIGURED = {
  status: 'not_configured',
  document_count: 0,
  last_indexed_at: null,
  ...LEGACY_INDEX,
  semantic_available: false,
  semantic_reason: 'not_configured',
  semantic_reason_text: SEARCH_TEXT.semanticReason.not_configured,
}

/** The index status a W12 server answers after a re-index has written the project (one second after `NOW`). */
export const INDEX_REINDEXED = { ...INDEX_READY, document_count: 2296, last_indexed_at: new Date(NOW.getTime() + 1_000).toISOString() }

export const ENTITY_COUNTS = { test_case: 84, test_run: 32, suite: 12, defect: 5, flaky_test: 3, release: 4 }

// ── Recents (`GET /search/recent`, `RecentSearchRead`) ─────────────────────

export interface RecentFixture {
  q: string
  scope: string
  mode: string
  project_id: string | null
  result_count: number
  counts_exact: boolean
  ts: string
}

/** The reader's two recent searches, newest first (the server's shape; `fixtures-in-page-b.ts` re-exports them). */
export const RECENTS: RecentFixture[] = [
  { q: 'checkout timeout', scope: 'tests', mode: 'hybrid', project_id: PROJECT_ID, result_count: 12, counts_exact: false, ts: '2026-09-18T11:00:00Z' },
  { q: 'login flaky', scope: 'tests', mode: 'keyword', project_id: PROJECT_ID, result_count: 3, counts_exact: true, ts: '2026-09-18T10:00:00Z' },
]

export const recentsBody = (items: readonly RecentFixture[]) => ({ items: [...items], max: 20, retention_days: 30 })

// ── Suggestions (`GET /search/suggested`) ──────────────────────────────────

export interface SuggestionsFixture {
  window_days: number
  failing_tests: { kind: string; label: string; q: string; scope: string; detail: string | null }[]
  open_defects: { kind: string; label: string; q: string; scope: string; detail: string | null }[]
  active_release: { kind: string; label: string; q: string; scope: string; detail: string | null } | null
}

/** Three empty groups: the card is omitted (the hermetic `/search` worlds keep their layout). */
export const NO_SUGGESTIONS: SuggestionsFixture = { window_days: 7, failing_tests: [], open_defects: [], active_release: null }

/** One of each group: two failing tests, one open defect, the active release. */
export const SUGGESTIONS: SuggestionsFixture = {
  window_days: 7,
  failing_tests: [
    { kind: 'failing_test', label: 'checkout total includes tax', q: '"checkout total includes tax"', scope: 'tests', detail: '12 failures in 7 days' },
    { kind: 'failing_test', label: 'refund webhook retried', q: '"refund webhook retried"', scope: 'tests', detail: '5 failures in 7 days' },
  ],
  open_defects: [{ kind: 'open_defect', label: 'PAY-412', q: 'PAY-412', scope: 'defects', detail: 'Open · HIGH' }],
  active_release: { kind: 'active_release', label: '2026.11', q: 'release:2026.11 status:failed', scope: 'tests', detail: 'Active release' },
}

// ── Usage (`GET /search/usage`, `SearchUsageRead`) ─────────────────────────

const TODAY_UTC = `${NOW.toISOString().slice(0, 10)}T00:00:00Z`

export interface UsageFixture {
  measured: boolean
  searches: number
  zero_result_searches: number
  zero_result_rate: number | null
  p50_latency_ms: number | null
  p95_latency_ms: number | null
  fallback_searches: number
}

/** No search today: every rate null ("Not measured yet", never `0 %`). */
export const USAGE_NOT_MEASURED: UsageFixture = {
  measured: false,
  searches: 0,
  zero_result_searches: 0,
  zero_result_rate: null,
  p50_latency_ms: null,
  p95_latency_ms: null,
  fallback_searches: 0,
}

/** Three searches today, one found nothing: `3`, `33 %`, `120 ms` (`p50 40 ms`). */
export const USAGE_MEASURED: UsageFixture = {
  measured: true,
  searches: 3,
  zero_result_searches: 1,
  zero_result_rate: 1 / 3,
  p50_latency_ms: 40,
  p95_latency_ms: 120,
  fallback_searches: 0,
}

/** `GET /search/suggested?project_id=` (the project is REQUIRED: FastAPI's 422 without it). */
export function suggestedAnswer(suggested: SuggestionsFixture = NO_SUGGESTIONS) {
  return (request: ApiRequest) => {
    const projectId = request.url.searchParams.get('project_id')
    if (!projectId) return respond(422, { detail: [{ loc: ['query', 'project_id'], msg: 'Field required', type: 'missing' }] })
    return { project_id: projectId, ...suggested }
  }
}

/** `GET /search/usage?project_id=&days=` (`refused`: a role below QA lead, `require_role(UserRole.QA_LEAD)`'s 403). */
export function usageAnswer(usage: UsageFixture = USAGE_NOT_MEASURED, { refused = false }: { refused?: boolean } = {}) {
  return (request: ApiRequest) => {
    if (refused) return respond(403, { detail: SEARCH_TEXT.usageRefused })
    const params = request.url.searchParams
    return { project_id: params.get('project_id'), days: Number(params.get('days') ?? 1), since: TODAY_UTC, ...usage, retention_days: 30 }
  }
}

/**
 * The three reads W12 adds to a `/search` load with the box empty (§4.2), answered empty: no recents, three empty
 * suggestion groups (the card is omitted), usage not measured. For the worlds that are not about them
 * (`search-rail.spec.ts`).
 */
export const SEARCH_LOAD_READS: ApiHandlers = [
  [RECENT_API, () => recentsBody([])],
  [SUGGESTED_API, suggestedAnswer()],
  [USAGE_API, usageAnswer()],
]

// ── Saved searches (`saved_views` rows, `page: "search"`) ──────────────────

export interface SavedSearchRow {
  id: string
  user_id: string
  project_id: string | null
  name: string
  description: string | null
  page: string
  filters: { kind: 'search'; q: string; scope: string; mode: string }
  is_shared: boolean
  is_default: boolean
  created_at: string
  updated_at: string | null
}

export const savedId = (n: number) => `5ea0c000-0000-4000-8000-${String(n).padStart(12, '0')}`

const savedRow = (n: number, name: string, q: string, scope: string, mode: string, extra: Partial<SavedSearchRow> = {}): SavedSearchRow => ({
  id: savedId(n),
  user_id: USER.id,
  project_id: PROJECT_ID,
  name,
  description: null,
  page: 'search',
  filters: { kind: 'search', q, scope, mode },
  is_shared: false,
  is_default: false,
  created_at: isoAgo(3 + n),
  updated_at: null,
  ...extra,
})

/**
 * Four saved searches the QA lead lists in Checkout, in the server's order (`is_default desc, name`): an own
 * project-bound one, an own one shared with the project, an own ALL-PROJECTS one (no project: never shareable), and
 * one another member shared (run-only for the reader).
 */
export const SAVED_SEARCHES: SavedSearchRow[] = [
  savedRow(1, 'Checkout timeouts', 'checkout timeout', 'tests', 'keyword'),
  savedRow(2, 'Failed payments', 'status:failed payment', 'tests', 'keyword', { is_shared: true }),
  savedRow(3, 'Login flakes', 'login', 'flaky', 'keyword', { project_id: null }),
  savedRow(4, 'Release blockers', 'release:2026.11', 'all', 'keyword', { user_id: OTHER_MEMBER_ID, is_shared: true }),
]

/** The id the next created saved search gets. */
export const NEW_SAVED_ID = savedId(90)

// ── Results ────────────────────────────────────────────────────────────────

/** A `GET /search` row (`SearchResult`). */
export interface TestResultRow {
  test_case_id: string
  test_run_id: string
  test_name: string
  suite_name: string | null
  status: string
  failure_count: number
  last_run_date: string
  relevance_score: number | null
  match_reasons: string[]
  source_mode_used: string
}

const resultId = (n: number) => `aaaa0000-0000-4000-8000-${String(n).padStart(12, '0')}`
const resultRow = (n: number, name: string, suite: string | null, status: string, relevance: number | null, mode: string): TestResultRow => ({
  test_case_id: n === 0 ? TEST_CASE_ID : resultId(n),
  test_run_id: RUN_ID,
  test_name: name,
  suite_name: suite,
  status,
  failure_count: 1 + (n % 4),
  last_run_date: isoAgo(n % 3, 2),
  relevance_score: relevance,
  match_reasons: [mode],
  source_mode_used: mode,
})

/** What a semantic / hybrid search answers: three test results by meaning (one with no suite). */
export const SEMANTIC_ROWS: TestResultRow[] = [
  resultRow(0, 'checkout total includes tax', 'Checkout', 'FAILED', 0.82, 'semantic'),
  resultRow(1, 'payment gateway times out after 30s', 'Payments', 'BROKEN', 0.74, 'semantic'),
  resultRow(2, 'refund webhook retried', null, 'FAILED', 0.61, 'semantic'),
]

/** What the keyword fallback of `GET /search` answers (`search_type: "keyword"`). */
export const KEYWORD_ROWS: TestResultRow[] = [
  resultRow(3, 'payment timeout shows the retry banner', 'Payments', 'FAILED', null, 'keyword'),
  resultRow(4, 'payment timeout is logged once', 'Payments', 'PASSED', null, 'keyword'),
]

/** A `parsed` block (`SearchParsed`) with nothing parsed out of `text`. */
export const plainParsed = (text: string, searched: readonly string[]) => ({
  text,
  filters: [],
  ignored_tokens: [],
  unsupported_by: {},
  searched_entity_types: [...searched],
  release_unresolved: null,
  notes: [],
})

export type SemanticOutcome = 'answered' | 'semantic_unavailable' | 'no_semantic_match' | 'no_text'

/**
 * `GET /search?q=&search_type=&page=&size=` as the W12 router answers it (§2.1): `answered` = the semantic / hybrid
 * rows, `result_status: "partial"` and lower-bound counts; a fallback = the keyword rows, `search_type: "keyword"`,
 * the reason and its sentence. `q`'s filters are the spec's to pin (`parsed`).
 */
export function semanticAnswer(outcome: SemanticOutcome = 'answered', parsed?: (q: string) => Record<string, unknown>) {
  return (request: ApiRequest) => {
    const params = request.url.searchParams
    const q = params.get('q') ?? ''
    const requested = params.get('search_type') ?? 'keyword'
    const size = Number(params.get('size') ?? 20)
    const pageNo = Number(params.get('page') ?? 1)
    const fell = requested !== 'keyword' && outcome !== 'answered'
    const rows = requested === 'keyword' || fell ? KEYWORD_ROWS : SEMANTIC_ROWS
    const reason = fell ? (outcome as Exclude<SemanticOutcome, 'answered'>) : null
    return {
      items: rows,
      total: rows.length,
      page: pageNo,
      size,
      pages: rows.length > 0 ? 1 : 0,
      query: q,
      search_type: fell ? 'keyword' : requested,
      requested_search_type: requested,
      fallback_reason: reason,
      fallback_message: reason ? SEARCH_TEXT.fallback[reason] : null,
      result_status: fell || requested === 'keyword' ? 'complete' : 'partial',
      counts_are_exact: fell || requested === 'keyword',
      failed_entity_types: [],
      scope: { release: 'not_applicable', note: 'Search spans every release.' },
      parsed: parsed ? parsed(q) : plainParsed(q, ['test_case']),
    }
  }
}

/** A `GET /search/global` row (`GlobalSearchResult`). */
export interface GlobalRow {
  entity_type: string
  entity_id: string
  title: string
  subtitle: string
  project_id: string | null
  project_name: string | null
  navigation_url: string
  relevance_score: number
  match_reasons: string[]
  metadata: Record<string, unknown>
}

const globalRow = (entity: string, id: string, title: string, subtitle: string, url: string, relevance: number): GlobalRow => ({
  entity_type: entity,
  entity_id: id,
  title,
  subtitle,
  project_id: PROJECT_ID,
  project_name: 'Checkout',
  navigation_url: url,
  relevance_score: relevance,
  match_reasons: ['name'],
  metadata: {},
})

/** Six kinds of match for "check…" (the top-bar list shows the first five; the API is asked for five). */
export const GLOBAL_ROWS: GlobalRow[] = [
  globalRow('test_case', TEST_CASE_ID, 'checkout total includes tax', 'Checkout · FAILED', `/runs/${RUN_ID}/tests/${TEST_CASE_ID}`, 0.95),
  globalRow('test_run', RUN_ID, 'Build #240', 'main · checkout-ci', `/runs/${RUN_ID}`, 0.9),
  globalRow('suite', 'suite:Checkout', 'Checkout', '57 tests', '/coverage/suite?name=Checkout', 0.85),
  globalRow('defect', 'dddd0000-0000-4000-8000-000000000412', 'PAY-412', 'Checkout total skips tax · Open', '/defects', 0.8),
  globalRow('flaky_test', 'fp-flaky-2', 'apply coupon', 'Checkout · 20% failure rate', '/flaky', 0.7),
  globalRow('release', 'eeee0000-0000-4000-8000-000000000011', 'Checkout 2026.11', 'Active', '/releases', 0.6),
]

// ── The top-bar typeahead's candidates (`GET /search/global?q=&size=15`) ──────────────────────────────────────
// The global search answers one row PER RUN for a test result (the same title, another run); the typeahead keeps
// the first row per (entity type, title) in the server's order and counts the rest (" · N runs", " · N matches",
// "N+" when more candidates exist than were fetched).

const otherRun = (row: GlobalRow, n: number, status: string): GlobalRow => ({
  ...row,
  entity_id: `aaaa1111-0000-4000-8000-${String(n).padStart(12, '0')}`,
  subtitle: `${row.subtitle.split(' · ')[0]} · ${status}`,
  navigation_url: `/runs/44444444-4444-4444-8444-${String(100000000000 + n)}/tests/aaaa1111-0000-4000-8000-${String(n).padStart(12, '0')}`,
  relevance_score: Math.round((row.relevance_score - n / 100) * 100) / 100,
})

/**
 * "check…": the test `checkout total includes tax` in THREE runs (its first, most relevant, row first), then the
 * five other kinds of `GLOBAL_ROWS` — 8 candidates, 6 distinct: the list shows the test once (` · 3 runs`) and the next
 * four kinds; the release (the 6th distinct) is not shown.
 */
export const TYPEAHEAD_MATCHES: GlobalRow[] = [
  GLOBAL_ROWS[0],
  otherRun(GLOBAL_ROWS[0], 1, 'PASSED'),
  otherRun(GLOBAL_ROWS[0], 2, 'FAILED'),
  ...GLOBAL_ROWS.slice(1),
]

/** The demo's case: `test_checkout_completes` in FIVE runs, a suite of the same name (another kind), one run. */
export const SAME_TEST_TITLE = 'test_checkout_completes'
export const TYPEAHEAD_SAME_TEST: GlobalRow[] = (() => {
  const first = globalRow('test_case', TEST_CASE_ID, SAME_TEST_TITLE, 'Checkout · FAILED', `/runs/${RUN_ID}/tests/${TEST_CASE_ID}`, 0.97)
  return [
    first,
    otherRun(first, 1, 'PASSED'),
    otherRun(first, 2, 'PASSED'),
    otherRun(first, 3, 'FAILED'),
    otherRun(first, 4, 'PASSED'),
    globalRow('suite', `suite:${SAME_TEST_TITLE}`, SAME_TEST_TITLE, '4 tests', `/coverage/suite?name=${SAME_TEST_TITLE}`, 0.8),
    globalRow('test_run', RUN_ID, 'Build #240', 'main · checkout-ci', `/runs/${RUN_ID}`, 0.7),
  ]
})()

/** "login…": more candidates exist than were fetched (`total` 40): a test in 3 runs (`3+ runs`), a defect twice (`2+ matches`). */
export const TYPEAHEAD_CAPPED_TOTAL = 40
export const TYPEAHEAD_CAPPED: GlobalRow[] = (() => {
  const test = globalRow('test_case', 'aaaa2222-0000-4000-8000-000000000001', 'login keeps session', 'Auth · FAILED', `/runs/${RUN_ID}/tests/aaaa2222-0000-4000-8000-000000000001`, 0.9)
  const defect = globalRow('defect', 'dddd0000-0000-4000-8000-000000000007', 'AUTH-7', 'Login loops after MFA · Open', '/defects', 0.85)
  return [
    test,
    otherRun(test, 11, 'FAILED'),
    otherRun(test, 12, 'PASSED'),
    defect,
    { ...defect, entity_id: 'dddd0000-0000-4000-8000-000000000008', subtitle: 'Login loops after MFA · In progress', relevance_score: 0.84 },
    globalRow('suite', 'suite:Auth', 'Auth', '42 tests', '/coverage/suite?name=Auth', 0.8),
  ]
})()

/**
 * `GET /search/global` as the typeahead meets it: `TYPEAHEAD_MATCHES` for "check…", `TYPEAHEAD_SAME_TEST` for
 * "test_check…", `TYPEAHEAD_CAPPED` (total 40) for "login…", nothing for anything else.
 */
export function typeaheadAnswer() {
  const answer = globalAnswer({
    rows: (q) => {
      const text = q.trim().toLowerCase()
      if (text.startsWith('test_check')) return TYPEAHEAD_SAME_TEST
      if (text.startsWith('check')) return TYPEAHEAD_MATCHES
      if (text.startsWith('login')) return TYPEAHEAD_CAPPED
      return []
    },
  })
  return (request: ApiRequest) => {
    const body = answer(request) as { total: number }
    const q = (request.url.searchParams.get('q') ?? '').trim().toLowerCase()
    return q.startsWith('login') ? { ...body, total: TYPEAHEAD_CAPPED_TOTAL } : body
  }
}

const ENTITY_OF_SCOPE: Record<string, string> = { tests: 'test_case', runs: 'test_run', suites: 'suite', defects: 'defect', flaky: 'flaky_test', releases: 'release' }

/**
 * `GET /search/global?q=&entity_types=&page=&size=` (§2.3): `rows(q)` matched (default: none for an empty or
 * unknown query, `GLOBAL_ROWS` for a query starting "check"), narrowed to `entity_types` and cut to `size`;
 * `parsed(q)` is the spec's (default: plain text, everything searched).
 */
export function globalAnswer(
  options: {
    rows?: (q: string) => readonly GlobalRow[]
    parsed?: (q: string, entityTypes: readonly string[]) => Record<string, unknown> | undefined
  } = {},
) {
  return (request: ApiRequest) => {
    const params = request.url.searchParams
    const q = params.get('q') ?? ''
    const types = (params.get('entity_types') ?? '').split(',').filter(Boolean)
    const size = Number(params.get('size') ?? 20)
    const pageNo = Number(params.get('page') ?? 1)
    const matched = (options.rows ?? ((text: string) => (text.trim().toLowerCase().startsWith('check') ? GLOBAL_ROWS : [])))(q)
    const narrowed = types.length > 0 ? matched.filter((row) => types.includes(row.entity_type)) : [...matched]
    const counts: Record<string, number> = {}
    for (const row of narrowed) counts[row.entity_type] = (counts[row.entity_type] ?? 0) + 1
    const searched = types.length > 0 ? types : Object.values(ENTITY_OF_SCOPE)
    return {
      items: narrowed.slice((pageNo - 1) * size, pageNo * size),
      total: narrowed.length,
      query: q,
      search_type: 'keyword',
      entity_counts: counts,
      page: pageNo,
      size,
      pages: narrowed.length > 0 ? Math.ceil(narrowed.length / size) : 0,
      counts_are_exact: true,
      result_status: 'complete',
      failed_entity_types: [],
      scope: { release: 'not_applicable', note: 'Search spans every release.' },
      parsed: options.parsed?.(q, types) ?? plainParsed(q.trim(), searched),
    }
  }
}

// ── The structured query syntax (SRCH-04) ──────────────────────────────────

/** Three filters and a word on Tests (§7.3's demo query). */
export const SYNTAX_QUERY = 'status:failed suite:"API Regression" since:7d checkout'
/** The same with `since: 7d` removed by its chip's × (`q.slice(37, 45)` cut, whitespace collapsed). */
export const SYNTAX_QUERY_WITHOUT_SINCE = 'status:failed suite:"API Regression" checkout'
/** The `search-syntax-chips` region's query on All: chips, an ignored token and unsupported kinds at once. */
export const SYNTAX_REGION_QUERY = 'status:failed since:7d foo:bar checkout'

/**
 * The `parsed` blocks the W12 server answers for the syntax spec's queries — produced by G-B's real parser
 * (`search_query_parser.parse_query` + `parsed_block`, `NOW`-pinned) and pasted here, keyed by the stripped query.
 */
export const SYNTAX_PARSED: Record<string, Record<string, unknown>> = {
  [SYNTAX_QUERY]: {
    text: 'checkout',
    filters: [
      { key: 'status', value: 'failed', raw: 'status:failed', start: 0, end: 13 },
      { key: 'suite', value: 'API Regression', raw: 'suite:"API Regression"', start: 14, end: 36 },
      { key: 'since', value: '7d', raw: 'since:7d', start: 37, end: 45 },
    ],
    ignored_tokens: [],
    unsupported_by: {},
    searched_entity_types: ['test_case'],
    release_unresolved: null,
    notes: [],
  },
  [SYNTAX_QUERY_WITHOUT_SINCE]: {
    text: 'checkout',
    filters: [
      { key: 'status', value: 'failed', raw: 'status:failed', start: 0, end: 13 },
      { key: 'suite', value: 'API Regression', raw: 'suite:"API Regression"', start: 14, end: 36 },
    ],
    ignored_tokens: [],
    unsupported_by: {},
    searched_entity_types: ['test_case'],
    release_unresolved: null,
    notes: [],
  },
  'branch:main': {
    text: '',
    filters: [{ key: 'branch', value: 'main', raw: 'branch:main', start: 0, end: 11 }],
    ignored_tokens: [],
    unsupported_by: { defect: ['branch'] },
    searched_entity_types: [],
    release_unresolved: null,
    notes: ['branch: is not supported for Defects, so no defects were searched.'],
  },
  'foo:bar checkout': {
    text: 'foo:bar checkout',
    filters: [],
    ignored_tokens: [{ token: 'foo:bar', key: 'foo', reason: 'unknown_key', message: 'unknown filter foo: searched as text', start: 0, end: 7 }],
    unsupported_by: {},
    searched_entity_types: ['test_case', 'test_run', 'suite', 'defect', 'flaky_test', 'release'],
    release_unresolved: null,
    notes: ['unknown filter foo: searched as text'],
  },
  'release:"9.9"': {
    text: '',
    filters: [{ key: 'release', value: '9.9', raw: 'release:"9.9"', start: 0, end: 13 }],
    ignored_tokens: [],
    unsupported_by: {},
    searched_entity_types: ['test_case'],
    release_unresolved: '9.9',
    notes: ['No release named "9.9" in your projects.'],
  },
  'status:flaky checkout': {
    text: 'checkout',
    filters: [],
    ignored_tokens: [{ token: 'status:flaky', key: 'status', reason: 'invalid_value', message: 'flaky is not a status: use type:flaky', start: 0, end: 12 }],
    unsupported_by: {},
    searched_entity_types: ['test_case'],
    release_unresolved: null,
    notes: ['flaky is not a status: use type:flaky'],
  },
  // The region's query (All): two chips, one ignored token (muted) and the condensed unsupported sentence (warning ink).
  [SYNTAX_REGION_QUERY]: {
    text: 'foo:bar checkout',
    filters: [
      { key: 'status', value: 'failed', raw: 'status:failed', start: 0, end: 13 },
      { key: 'since', value: '7d', raw: 'since:7d', start: 14, end: 22 },
    ],
    ignored_tokens: [{ token: 'foo:bar', key: 'foo', reason: 'unknown_key', message: 'unknown filter foo: searched as text', start: 23, end: 30 }],
    unsupported_by: { suite: ['status'], defect: ['status'], flaky_test: ['status'], release: ['status'] },
    searched_entity_types: ['test_case', 'test_run'],
    release_unresolved: null,
    // G-B's condensed rule: ONE sentence per key that keeps two or more searched kinds out (`unsupported_by` stays
    // per kind).
    notes: [
      'unknown filter foo: searched as text',
      'status: applies only to Tests and Runs, so suites, defects, flaky tests and releases were not searched.',
    ],
  },
  // `status:failed` on All (G-B's exact block, "## NOTES CONDENSED"): one condensed sentence for four kinds.
  'status:failed': {
    text: '',
    filters: [{ key: 'status', value: 'failed', raw: 'status:failed', start: 0, end: 13 }],
    ignored_tokens: [],
    unsupported_by: { suite: ['status'], defect: ['status'], flaky_test: ['status'], release: ['status'] },
    searched_entity_types: ['test_case', 'test_run'],
    release_unresolved: null,
    notes: ['status: applies only to Tests and Runs, so suites, defects, flaky tests and releases were not searched.'],
  },
}

/** `GET /search/global` for the syntax queries: their `parsed` block; the test-result match for a filtered "checkout". */
export const syntaxGlobal = () =>
  globalAnswer({
    rows: (q) => {
      const parsed = SYNTAX_PARSED[q.trim()]
      if (!parsed) return q.trim().toLowerCase().startsWith('check') ? GLOBAL_ROWS : []
      // Only the kinds the server searched, and only when the text still matches.
      if (parsed.release_unresolved || !String(parsed.text).includes('checkout')) return []
      const searched = parsed.searched_entity_types as string[]
      return GLOBAL_ROWS.filter((row) => searched.includes(row.entity_type))
    },
    parsed: (q) => SYNTAX_PARSED[q.trim()],
  })

// ── The world ──────────────────────────────────────────────────────────────

export interface RecordedSearchWrite {
  /** `METHOD path?query` (the query kept: `POST /search/reindex?project_id=…&full=true`). */
  line: string
  body: unknown
}

const bodyOf = (request: ApiRequest): unknown => {
  try {
    return request.route.request().postDataJSON()
  } catch {
    return null
  }
}

export interface SearchWorldOptions {
  /** The reader (default the QA lead). */
  role?: SearchRole
  /** `GET /search/index-status` (default `INDEX_READY`). */
  indexStatus?: Record<string, unknown>
  /** After `POST /search/reindex`, the index status answers this (default `INDEX_REINDEXED`). */
  indexAfterReindex?: Record<string, unknown>
  /** `POST /search/reindex` refuses (403 with the router's sentence). */
  reindexRefused?: boolean
  /** The reader's recents (default none). */
  recents?: readonly RecentFixture[]
  /** The saved searches the server holds (default none). */
  saved?: readonly SavedSearchRow[]
  /** `GET /search/suggested` (default three empty groups). */
  suggested?: SuggestionsFixture
  /** `GET /search/usage` for a QA lead+ (default not measured). */
  usage?: UsageFixture
  /** `GET /search/global` (default `globalAnswer()`). */
  global?: (request: ApiRequest) => unknown
  /** `GET /search` (default `semanticAnswer('answered')`). */
  semantic?: (request: ApiRequest) => unknown
  /** `POST /saved-views` answers 422 with this sentence instead of creating. */
  saveRefused?: string
}

/**
 * `/search` as a W12 server answers it, stateful: a cleared history reads empty, a created saved search is listed,
 * a rename / share / delete is applied, and a re-index's next index-status read is `indexAfterReindex`.
 */
export function searchWorld(options: SearchWorldOptions = {}) {
  const role = options.role ?? 'QA_LEAD'
  const user = SEARCH_USERS[role]
  const writes: RecordedSearchWrite[] = []
  const state = {
    recents: [...(options.recents ?? [])],
    saved: (options.saved ?? []).map((row) => ({ ...row, filters: { ...row.filters } })),
    indexStatus: options.indexStatus ?? INDEX_READY,
  }
  const record = (request: ApiRequest) => {
    const body = bodyOf(request)
    writes.push({ line: `${request.method} ${request.path}${request.url.search}`, body })
    return body
  }
  const lead = LEAD_ROLES.includes(role)
  const savedRowOf = (request: ApiRequest) => state.saved.find((row) => row.id === request.path.split('/').pop())

  const handlers: ApiHandlers = [
    [INDEX_STATUS_API, () => state.indexStatus],
    [ENTITY_COUNTS_API, () => ENTITY_COUNTS],
    [GLOBAL_API, options.global ?? globalAnswer()],
    [SEARCH_API, options.semantic ?? semanticAnswer('answered')],
    [RECENT_API, () => recentsBody(state.recents)],
    [
      RECENT_API,
      (request) => {
        record(request)
        state.recents = []
        return respond(204)
      },
      'DELETE',
    ],
    [SUGGESTED_API, suggestedAnswer(options.suggested)],
    // `require_role(UserRole.QA_LEAD)`: a lower role is refused (a page that asked anyway would show it).
    [USAGE_API, usageAnswer(options.usage, { refused: !lead })],
    [
      REINDEX_API,
      (request) => {
        record(request)
        if (options.reindexRefused || !lead) return respond(403, { detail: SEARCH_TEXT.reindexRefused })
        state.indexStatus = options.indexAfterReindex ?? INDEX_REINDEXED
        const full = request.url.searchParams.get('full') === 'true'
        return { task_id: 'c0ffee00-0000-4000-8000-0000000000a2', status: 'queued', mode: full ? 'full' : 'incremental' }
      },
      'POST',
    ],
    [
      SAVED_VIEWS_API,
      (request) => {
        const params = request.url.searchParams
        const projectId = params.get('project_id')
        const pageName = params.get('page')
        const owned = (row: SavedSearchRow) => row.user_id === user.id
        return state.saved
          .filter((row) => owned(row) || (row.is_shared && row.project_id !== null))
          .filter((row) => (projectId ? row.project_id === projectId || (owned(row) && row.project_id === null) : true))
          .filter((row) => (pageName ? row.page === pageName : true))
          .sort((a, b) => Number(b.is_default) - Number(a.is_default) || a.name.localeCompare(b.name))
      },
    ],
    [
      SAVED_VIEWS_API,
      (request) => {
        const body = (record(request) ?? {}) as Partial<SavedSearchRow>
        if (options.saveRefused) return respond(422, { detail: [{ type: 'saved_search', loc: ['body'], msg: options.saveRefused }] })
        const row: SavedSearchRow = {
          id: state.saved.some((r) => r.id === NEW_SAVED_ID) ? savedId(91 + state.saved.length) : NEW_SAVED_ID,
          user_id: user.id,
          project_id: (body.project_id as string | null | undefined) ?? null,
          name: String(body.name ?? ''),
          description: null,
          page: String(body.page ?? ''),
          filters: body.filters as SavedSearchRow['filters'],
          is_shared: Boolean(body.is_shared),
          is_default: Boolean(body.is_default),
          created_at: NOW.toISOString(),
          updated_at: null,
        }
        state.saved.push(row)
        return respond(201, row)
      },
      'POST',
    ],
    [
      SAVED_VIEW_ID_PATH,
      (request) => {
        const body = (record(request) ?? {}) as Partial<SavedSearchRow>
        const row = savedRowOf(request)
        if (!row) return respond(404, { detail: 'Saved view not found' })
        if (row.user_id !== user.id) return respond(403, { detail: 'Only the owner can change a saved view' })
        Object.assign(row, body, { updated_at: NOW.toISOString() })
        return row
      },
      'PATCH',
    ],
    [
      SAVED_VIEW_ID_PATH,
      (request) => {
        record(request)
        const row = savedRowOf(request)
        if (!row) return respond(404, { detail: 'Saved view not found' })
        if (row.user_id !== user.id) return respond(403, { detail: 'Only the owner can delete a saved view' })
        state.saved = state.saved.filter((r) => r !== row)
        return respond(204)
      },
      'DELETE',
    ],
    [SAVED_VIEW_ID_PATH, (request) => savedRowOf(request) ?? respond(404, { detail: 'Saved view not found' })],
    ...LAYOUT,
  ]
  return { handlers, writes, state, user }
}

// ── Similar failures (`/runs/:runId/tests/:testId?tab=similar`) ────────────

/** Five failed / broken results of the same project, most similar first (`SearchResult`). */
export const SIMILAR_ITEMS: TestResultRow[] = [
  resultRow(11, 'checkout total skips the tax line', 'Checkout', 'FAILED', 0.82, 'semantic'),
  resultRow(12, 'cart subtotal rounds the discount down', 'Checkout', 'FAILED', 0.77, 'semantic'),
  resultRow(13, 'invoice total includes shipping', 'Billing', 'BROKEN', 0.7, 'semantic'),
  resultRow(14, 'refund amount matches the order total', 'Payments', 'FAILED', 0.63, 'semantic'),
  resultRow(15, 'tax is applied to gift cards', null, 'FAILED', 0.55, 'semantic'),
]

export type SimilarOutcome = 'ok' | 'empty' | 'not_configured' | 'unavailable' | 'error'

export interface SimilarWorldOptions {
  /** What `GET /search/similar/{id}` answers (default five links). */
  outcome?: SimilarOutcome
  /** The result's status on its page (default the fixture's FAILED). */
  status?: 'FAILED' | 'BROKEN' | 'PASSED' | 'SKIPPED'
}

/** The first handler of `handlers` answering `path` (GET), to wrap it. */
function readOf(handlers: ApiHandlers, path: string) {
  const hit = handlers.find(([matcher, , verb = 'GET']) => verb === 'GET' && matcher === path)
  if (!hit) throw new Error(`no handler for GET ${path}`)
  return hit[1]
}

/** One test result's page (`TEST_CASE`, `fixtures-pages.ts`) with the Similar failures read. */
export function similarWorld(options: SimilarWorldOptions = {}) {
  const outcome = options.outcome ?? 'ok'
  const detail = readOf(TEST_CASE, `${TEST_CASE_API}/rich-detail`)
  const handlers: ApiHandlers = [
    [
      similarApi(TEST_CASE_ID),
      () => {
        if (outcome === 'error') return respond(500, { detail: 'Internal Server Error' })
        const items = outcome === 'ok' ? SIMILAR_ITEMS : []
        const semanticStatus = outcome === 'ok' || outcome === 'empty' ? 'ok' : outcome
        return { items, total: items.length, query: TEST_CASE_ERROR.slice(0, 100), semantic_status: semanticStatus }
      },
    ],
    ...(options.status && options.status !== 'FAILED'
      ? ([
          [
            `${TEST_CASE_API}/rich-detail`,
            (request: ApiRequest) => {
              const body = detail(request) as Record<string, unknown> & { execution: Record<string, unknown> }
              return {
                ...body,
                status: options.status,
                error_message: null,
                execution: { ...body.execution, status: options.status, error_message: null, stack_trace: null, failure_category: null },
              }
            },
          ],
        ] as ApiHandlers)
      : []),
    ...TEST_CASE,
  ]
  return { handlers }
}

// ── The embedding index (Settings › AI, Settings › Data & storage) ─────────

export interface GenerationFixture {
  provider: string
  model: string
  prefix: string
  key: string
  tag: string
}

const generation = (provider: string, model: string, prefix: string, tag: string): GenerationFixture => ({
  provider,
  model,
  prefix,
  key: provider === 'default' ? 'default' : `${provider}:${model}`,
  tag,
})

/** The legacy generation: the bundled MiniLM, no prefix (tag ""). */
export const GEN_MINILM = generation('default', 'all-MiniLM-L6-v2', '', '')
/** Ollama `nomic-embed-text:v1.5`, no prefix (the configured default of `config.py`). */
export const GEN_NOMIC = generation('ollama', 'nomic-embed-text:v1.5', '', 'e7d5a41f5')
/** The bundled MiniLM under the prefix `demo`. */
export const GEN_DEMO = generation('default', 'all-MiniLM-L6-v2', 'demo', 'e37a8eec1')

export interface EmbeddingIndexFixture {
  active: GenerationFixture
  configured: GenerationFixture
  in_sync: boolean
  status: string
  target: GenerationFixture | null
  families: { family: string; state: string; processed: number; total: number | null }[]
  processed: number
  total: number | null
  requested_at: string | null
  switched_at: string | null
  last_error: string | null
  configured_note: string | null
}

const indexOf = (active: GenerationFixture, configured: GenerationFixture, extra: Partial<EmbeddingIndexFixture> = {}): EmbeddingIndexFixture => ({
  active,
  configured,
  in_sync: active.key === configured.key && active.prefix === configured.prefix,
  status: 'idle',
  target: null,
  families: [],
  processed: 0,
  total: null,
  requested_at: null,
  switched_at: null,
  last_error: null,
  configured_note: null,
  ...extra,
})

/**
 * `embedding_index` of the worlds that are not about it (`fixtures-ai-config.ts`'s `aiConfig()`,
 * `settings-two-column-forms`' `AI_CONFIG`): idle and in sync with their `ollama` · `nomic-embed-text` form.
 */
export { EMBEDDING_INDEX_IN_SYNC }

/** A fresh install: the bundled MiniLM in use, nothing configured otherwise. */
export const INDEX_MINILM_IN_SYNC = indexOf(GEN_MINILM, GEN_MINILM)
/** The configured nomic default is not applied yet (§0.18): `… is not in use yet.` + Re-embed now. */
export const INDEX_NOMIC_NOT_APPLIED = indexOf(GEN_MINILM, GEN_NOMIC)
/** A re-embed toward nomic, test results 1,200 / 2,290, knowledge chunks done, agent memory pending. */
export const INDEX_BUILDING = indexOf(GEN_MINILM, GEN_NOMIC, {
  status: 'building',
  target: GEN_NOMIC,
  families: [
    { family: 'test_case_search', state: 'running', processed: 1200, total: 2290 },
    { family: 'knowledge_chunks', state: 'done', processed: 340, total: 340 },
    { family: 'agent_memory_vectors', state: 'pending', processed: 0, total: null },
  ],
  processed: 1200,
  total: 2290,
  requested_at: NOW.toISOString(),
})
/** The last re-embed toward nomic failed; MiniLM stays in use. */
export const INDEX_FAILED = indexOf(GEN_MINILM, GEN_NOMIC, {
  status: 'failed',
  target: GEN_NOMIC,
  requested_at: isoAgo(0, 1),
  last_error: 'ConnectError while embedding test results',
})

export const EMBEDDING_INDEX_API = '/api/v1/settings/ai/embedding-index'
export const EMBEDDING_REBUILD_API = '/api/v1/settings/ai/embedding-index/rebuild'
export const AI_SETTINGS_API = '/api/v1/settings/ai'
export const STORAGE_API = '/api/v1/settings/storage'

export const EMBEDDING_TEXT = {
  unreadable: 'Could not read the embedding index state.',
  nothingToDo: 'Nothing to re-embed: the configured embedder and prefix are already in use.',
  openaiOffline: 'OpenAI embeddings are refused while offline mode is on: choose Bundled MiniLM or Ollama.',
  prefixInvalid: "A collection prefix is up to 8 lower-case letters, digits or '-', starting with a letter or digit.",
  saveWarning: 'Saving re-embeds every index into new collections; semantic search keeps using the current model until it finishes.',
  prefixHelp:
    "Up to 8 lower-case letters, digits or '-'. Every vector collection's name starts with it, so two deployments can share one ChromaDB. Changing it re-embeds every index into new collections; search keeps the current ones until that finishes.",
} as const

/** `GET /settings/storage` (`StorageConfigRead` + the three W12 fields). */
export function storageRead(stored: string, active: string | null) {
  const valid = /^[a-z0-9][a-z0-9-]{0,7}$/.test(stored)
  const legacy = stored === '' || stored === 'testlookup_embeddings'
  return {
    storage_backend: 'minio',
    postgres_connected: true,
    mongo_connected: true,
    redis_connected: true,
    minio_endpoint: 'minio:9000',
    minio_bucket_name: 'testlookup-artifacts',
    minio_use_ssl: false,
    chroma_host: 'chromadb',
    chroma_port: 8001,
    chroma_collection: stored,
    collection_prefix: valid ? stored : '',
    collection_prefix_note:
      valid || legacy ? null : `The stored value '${stored}' is not a valid prefix and is ignored: collections carry no prefix.`,
    active_collection_prefix: active,
  }
}

export interface EmbeddingWorldOptions {
  /** `embedding_index` on load (default `INDEX_NOMIC_NOT_APPLIED`; `null` = the state could not be read). */
  index?: EmbeddingIndexFixture | null
  /** The stored `ai_config.embedding_*` (default the configured generation's). */
  embeddingProvider?: string
  embeddingModel?: string
  /** The stored `chroma_collection` (default `""`). */
  storedPrefix?: string
  /** `GET /settings/ai/embedding-index` polls before the build reports done (default 1: the second poll is idle). */
  pollsUntilDone?: number
}

/**
 * Settings › AI + Settings › Data & storage as a W12 server answers them (§2.4, §3): GET `/settings/ai` carries
 * `embedding_index`; a PUT that CHANGES the embedder (or `POST …/rebuild`) starts a build toward the configured
 * generation (`building`), whose `GET …/embedding-index` polls walk to the switch (`idle`, in sync); a PUT of
 * `/settings/storage` that changes the prefix to a valid one starts a build with the ACTIVE embedder, an invalid one
 * is the 422 sentence. Every write is recorded. Composes `aiConfigHandlers` (model status, ML status) after.
 */
export function embeddingWorld(options: EmbeddingWorldOptions = {}) {
  const initial = options.index === undefined ? INDEX_NOMIC_NOT_APPLIED : options.index
  const configured = initial?.configured ?? GEN_NOMIC
  const writes: RecordedSearchWrite[] = []
  const state = {
    index: initial ? { ...initial } : null,
    provider: options.embeddingProvider ?? configured.provider,
    model: options.embeddingModel ?? configured.model,
    storedPrefix: options.storedPrefix ?? '',
    indexPolls: 0,
  }
  const record = (request: ApiRequest) => {
    const body = bodyOf(request)
    writes.push({ line: `${request.method} ${request.path}${request.url.search}`, body })
    return body
  }
  const keyOf = (provider: string, model: string) => (provider === 'default' ? 'default' : `${provider}:${model}`)
  const startBuild = (target: GenerationFixture) => {
    const active = state.index?.active ?? GEN_MINILM
    state.index = { ...INDEX_BUILDING, active, configured: target, in_sync: false, target, requested_at: NOW.toISOString() }
    state.indexPolls = 0
  }
  const config = () => ({ ...aiConfig({ embedding_provider: state.provider, embedding_model: state.model }), embedding_index: state.index })
  const base = aiConfigHandlers({ config: { embedding_provider: state.provider, embedding_model: state.model } })

  const handlers: ApiHandlers = [
    [AI_SETTINGS_API, config],
    [
      AI_SETTINGS_API,
      (request) => {
        const body = (record(request) ?? {}) as Record<string, unknown>
        const provider = typeof body.embedding_provider === 'string' ? body.embedding_provider : state.provider
        const model = typeof body.embedding_model === 'string' ? body.embedding_model : state.model
        const changed = keyOf(provider, model) !== keyOf(state.provider, state.model)
        if (changed && provider === 'openai' && body.ai_offline_mode !== false) {
          return respond(422, { detail: EMBEDDING_TEXT.openaiOffline })
        }
        state.provider = provider
        state.model = model
        if (changed) startBuild(generation(provider, provider === 'default' ? 'all-MiniLM-L6-v2' : model, state.index?.active.prefix ?? '', 'e00000001'))
        return config()
      },
      'PUT',
    ],
    [
      EMBEDDING_INDEX_API,
      () => {
        if (!state.index) return respond(503, { detail: EMBEDDING_TEXT.unreadable })
        if (state.index.status === 'building') {
          state.indexPolls += 1
          if (state.indexPolls >= (options.pollsUntilDone ?? 1) + 1 && state.index.target) {
            const switched = state.index.target
            state.index = indexOf(switched, switched, { switched_at: NOW.toISOString() })
          }
        }
        return state.index
      },
    ],
    [
      EMBEDDING_REBUILD_API,
      (request) => {
        record(request)
        if (!state.index) return respond(503, { detail: EMBEDDING_TEXT.unreadable })
        if (state.index.in_sync && state.index.status !== 'failed') return respond(422, { detail: EMBEDDING_TEXT.nothingToDo })
        startBuild(state.index.configured)
        return state.index
      },
      'POST',
    ],
    [STORAGE_API, () => storageRead(state.storedPrefix, state.index ? state.index.active.prefix : null)],
    [
      STORAGE_API,
      (request) => {
        const body = (record(request) ?? {}) as Record<string, unknown>
        const next = (typeof body.chroma_collection === 'string' ? body.chroma_collection : state.storedPrefix).trim()
        const prefixOf = (value: string) => (/^[a-z0-9][a-z0-9-]{0,7}$/.test(value) ? value : '')
        // As `routers/app_settings.update_storage_config`: a CHANGED raw value must be "", the legacy default or a
        // prefix (else 422); an unchanged one passes through; a build starts only when the prefix it means changes.
        if (next !== state.storedPrefix.trim()) {
          if (next !== '' && next !== 'testlookup_embeddings' && prefixOf(next) === '') {
            return respond(422, { detail: EMBEDDING_TEXT.prefixInvalid })
          }
          if (prefixOf(next) !== prefixOf(state.storedPrefix)) {
            const active = state.index?.active ?? GEN_MINILM
            startBuild(generation(active.provider, active.model, prefixOf(next), 'e00000002'))
          }
        }
        state.storedPrefix = next
        return storageRead(state.storedPrefix, state.index ? state.index.active.prefix : null)
      },
      'PUT',
    ],
    [
      '/api/v1/settings/ai/model-status',
      // §2.4: an Ollama embedding model is REQUIRED while it is the active or the target embedder (`in_use` for the
      // active one); a configured-but-unapplied one is not.
      () => {
        const active = state.index?.active
        const target = state.index?.target
        const embedder = [active, target].find((gen) => gen?.provider === 'ollama')
        const required: Record<string, unknown>[] = MODEL_STATUS.required
          .filter((model) => model.purpose !== 'embedding')
          .map((model) => ({ ...model, in_use: null }))
        if (embedder) required.push({ name: embedder.model, purpose: 'embedding', present: true, remedy: null, in_use: embedder === active })
        return { ...MODEL_STATUS, installed_models: [...MODEL_STATUS.installed_models, 'nomic-embed-text:v1.5'], required }
      },
    ],
    ...base.handlers,
  ]
  return { handlers, writes, state }
}
