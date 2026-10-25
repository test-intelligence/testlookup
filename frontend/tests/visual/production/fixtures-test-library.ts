/**
 * W13 "test-library" hermetic answers (`docs/deferred-scope-2026-10-13/work/W13-contract.md` §1.1, §3, §4.2, §6.2
 * G-E): the reads and writes the W13 specs (`tests/ci-e2e/suites-edit.spec.ts`, `test-management-filters.spec.ts`,
 * `test-management-health.spec.ts`, `test-management-import.spec.ts`, `test-management-timeline.spec.ts`,
 * `test-management-requirements.spec.ts`) and the W13 regions (`tests/visual/prod-test-library.visual.spec.ts`) meet.
 *
 *  - `caseFiltersWorld(options)`: `/test-management` Cases with the W13 list QUERY (repeated `status` / `priority` /
 *    `test_type`, `owner`, `automation`, `requirement`) answered and RECORDED, `automation_rows_excluded`, the export
 *    URL, and the saved case views (`/saved-views`, `page: test_management`, list / create / patch / delete);
 *  - `testLibraryHealthWorld(options)`: `GET /test-management/health` (a richer answer: flaky and ownerless cases),
 *    the evidence-gap pages (`kind=flaky_active|no_owner&page=&size=25`) and the target's `PUT /projects/{id}`;
 *  - `importWorld(options)`: `POST /test-management/cases/import?project_id=&dry_run=` (multipart);
 *  - `timelineWorld(options)`: `GET /test-management/cases/{id}/timeline` keyset pages;
 *  - `requirementsWorld(options)`: a case's requirement links (list / add / remove), the health's `requirements`,
 *    `GET /traceability/gaps` (the flag itself is the spec's `flags`);
 *  - `suitesEditWorld()`: `/suites`, `/suites/:id` with `PATCH /suites/{id}`, and the TM Suites tab's empty row.
 *
 * Every world is over `TEST_CASES` (through `testLibraryWorld()`), W13's answers PREPENDED (first match wins,
 * `production-pages.ts`). Each factory returns fresh state per call and records every write it answered
 * (`writes`: `METHOD path?query` and the parsed body), every W13 read too (`reads`), so a spec pins whole
 * bodies and whole queries. The sentences are COPIED from the contract (§1.2), never imported from the app.
 * Not a spec file.
 */
import { respond, respondRaw, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { isoAgo, NOW, PROJECT_ID, USER } from './fixtures'
import { bodyOf, delegate } from './fixtures-in-page'
import { fakeGet, suitesInPageWorld, testLibraryWorld, tmCaseId, tmCaseTitle, tmMembers } from './fixtures-in-page-b'
import { libraryHealthOf, TEST_CASES, TEST_CASES_HEALTH } from './fixtures-pages'
import { ADMIN, QA_ENGINEER, VIEWER } from './fixtures-releases'
import { TESTER, type FixtureUser } from './fixtures-test-case-editing'

// ── Who ────────────────────────────────────────────────────────────────────

export type LibraryRole = 'ADMIN' | 'QA_LEAD' | 'QA_ENGINEER' | 'TESTER' | 'VIEWER'
export { ADMIN, QA_ENGINEER, TESTER, VIEWER, tmCaseId, tmCaseTitle }
export const QA_LEAD = USER
export const LIBRARY_USERS: Record<LibraryRole, FixtureUser> = { ADMIN, QA_LEAD: USER, QA_ENGINEER, TESTER, VIEWER }
const RANK: Record<string, number> = { VIEWER: 0, TESTER: 1, QA_ENGINEER: 2, QA_LEAD: 3, ADMIN: 4 }
const rankOf = (user: { role: string }) => RANK[user.role] ?? 0

/** Another member of the project (a saved view they shared is apply-only for the reader). */
export const OTHER_MEMBER_ID = '00000000-0000-4000-8000-0000000000b9'

// ── Paths ──────────────────────────────────────────────────────────────────

export const CASES_API = '/api/v1/test-management/cases'
export const EXPORT_API = '/api/v1/test-management/cases/export/excel'
export const HEALTH_API = '/api/v1/test-management/health'
export const EVIDENCE_GAPS_API = '/api/v1/test-management/cases/evidence-gaps'
export const IMPORT_API = '/api/v1/test-management/cases/import'
export const TRACEABILITY_GAPS_API = '/api/v1/test-management/traceability/gaps'
export const SAVED_VIEWS_API = '/api/v1/saved-views'
export const PROJECT_API = `/api/v1/projects/${PROJECT_ID}`
export const MEMBERS_API = `/api/v1/projects/${PROJECT_ID}/members`
export const TRACEABILITY_FLAG = 'test_case_requirement_traceability'
export const timelineApi = (caseId: string) => `${CASES_API}/${caseId}/timeline`
export const requirementsApi = (caseId: string) => `${CASES_API}/${caseId}/requirements`
const SAVED_VIEW_ID_PATH = /^\/api\/v1\/saved-views\/[^/]+$/
const TIMELINE_PATH = /^\/api\/v1\/test-management\/cases\/[^/]+\/timeline$/
const REQUIREMENTS_PATH = /^\/api\/v1\/test-management\/cases\/[^/]+\/requirements$/
const REQUIREMENT_LINK_PATH = /^\/api\/v1\/test-management\/cases\/[^/]+\/requirements\/[^/]+$/
const SUITE_ID_PATH = /^\/api\/v1\/suites\/[^/]+$/

// ── The sentences (§1.2, §2; copied, not imported) ─────────────────────────

export const LIBRARY_TEXT = {
  authoredOnly: 'Authored cases only: automation-ingested tests are left out under these filters.',
  healthNeedsProject: 'Choose a project: library health is per project.',
  flakyNoScores: 'No flaky scores have been computed for this project yet.',
  importLimits: 'CSV (UTF-8) or Excel, up to 5 MB and 2,000 rows. Every case is imported as a Draft.',
  importTooLarge: 'The file is larger than 5 MB.',
  importRowTitle: 'Title is required (3 to 500 characters).',
  importRowPriority: 'Priority must be critical, high, medium or low (or P0 to P3).',
  importRowAutomated: 'Automated must be Yes or No.',
  importWarnStatus: "Status 'approved' is ignored: imported cases start as Draft.",
  importDuplicateExisting: 'Same title, objective, steps and expected result as an existing case.',
  importRefused: 'Requires at least TESTER role',
  timelineEnd: 'Start of the timeline.',
  timelineEmpty: 'Nothing recorded for this case yet.',
  timelineNoAutomation: 'No automated test is linked to this case; plan executions are shown.',
  requirementAlreadyLinked: (key: string) => `This case already links ${key}.`,
  requirementLinkNotFound: 'Requirement link not found',
  traceabilityOff: 'Requirement traceability is off for this project.',
  requirementsUnmeasured: 'No requirement keys are known to this project yet: add one to a test case.',
  suiteNameTaken: 'Another suite in this project already uses that name',
  suiteEmptyLine: 'Waiting for its first run: no result reports this suite yet.',
  targetRefused: 'Requires at least QA_LEAD role',
} as const

// ── Recording ──────────────────────────────────────────────────────────────

/** One answered write (or a W13 read): `METHOD path?query` and its parsed body. */
export interface LibraryWrite {
  line: string
  body: unknown
}

const lineOf = (request: ApiRequest) => `${request.method} ${request.path}${request.url.search}`

function record(list: LibraryWrite[], request: ApiRequest, body: unknown = bodyOf(request)): unknown {
  list.push({ line: lineOf(request), body })
  return body
}

/** The ids in a path (`/…/cases/<id>/requirements/<link>` → the segment after `cases`, …). */
const segmentAfter = (path: string, name: string) => {
  const parts = path.split('/')
  const at = parts.indexOf(name)
  return at >= 0 ? decodeURIComponent(parts[at + 1] ?? '') : ''
}

/** `TEST_CASES`' eight cases (`fixtures-pages.ts`), fresh copies. */
export function libraryCases(): Record<string, unknown>[] {
  const list = delegate(TEST_CASES, fakeGet(`${CASES_API}?size=200`)) as { items: Record<string, unknown>[] }
  return list.items.map((c) => ({ ...c }))
}

// ── The case list (`GET /test-management/cases`, §3.2) ─────────────────────

/** The reader's own cases (assigned to the signed-in user): case 2 (a Draft) and case 3 (Review requested). */
export const MY_CASES = [1, 2] as const

/**
 * The list the server answers for a W13 query: repeated `status` / `priority` / `test_type` as IN, `owner`
 * (`me` = the reader; owner = assignee else author), `automation`, `search` (title), `requirement` (the cases
 * linking that normalised key), the default excluding Archived. `automation_rows_excluded` is true when
 * `include_automation` was asked and a filter sent the read down the authored-only path (§0.7).
 */
export function caseListAnswer(
  rows: readonly Record<string, unknown>[],
  params: URLSearchParams,
  readerId: string,
  linkedTo: (key: string) => readonly string[] = () => [],
) {
  const statuses = params.getAll('status')
  const priorities = params.getAll('priority')
  const types = params.getAll('test_type')
  const owner = params.get('owner')
  const automation = params.get('automation')
  const requirement = params.get('requirement')
  const search = (params.get('search') ?? '').trim().toLowerCase()
  const ownerId = owner === 'me' ? readerId : owner
  const matches = rows.filter((c) =>
    (statuses.length ? statuses.includes(String(c.status)) : c.status !== 'archived')
    && (!priorities.length || priorities.includes(String(c.priority)))
    && (!types.length || types.includes(String(c.test_type)))
    && (!ownerId || (c.assignee_id ?? c.author_id) === ownerId)
    && (!automation || (automation === 'automated' ? c.is_automated === true : c.is_automated !== true))
    && (!requirement || linkedTo(requirement).includes(String(c.id)))
    && (!search || String(c.title).toLowerCase().includes(search)))
  const size = Number(params.get('size') ?? 25)
  const page = Number(params.get('page') ?? 1)
  const items = matches.slice((page - 1) * size, page * size)
  const filtered = Boolean(owner || automation || requirement || statuses.length || priorities.length || types.length > 1
    || params.get('ai_generated') !== null || params.get('feature_area') !== null)
  return {
    items,
    total: matches.length,
    page,
    size,
    pages: Math.ceil(matches.length / size),
    automation_rows_excluded: params.get('include_automation') === 'true' && filtered,
  }
}

// ── Saved case views (`/saved-views`, `page: test_management`, `filters.kind: test_case_view`) ──

export interface CaseViewRow {
  id: string
  user_id: string
  project_id: string | null
  name: string
  description: null
  page: string
  filters: Record<string, unknown>
  is_shared: boolean
  is_default: boolean
  created_at: string
  updated_at: string | null
}

export const caseViewId = (n: number) => `5e000000-0000-4000-8000-0000000000${String(n).padStart(2, '0')}`

export function caseView(n: number, name: string, filters: Record<string, unknown>, over: Partial<CaseViewRow> = {}): CaseViewRow {
  return {
    id: caseViewId(n), user_id: USER.id, project_id: PROJECT_ID, name, description: null, page: 'test_management',
    filters: { kind: 'test_case_view', ...filters }, is_shared: false, is_default: false,
    created_at: isoAgo(3), updated_at: null, ...over,
  }
}

/** A reader's saved view and one another member shared (apply-only). `user_id` is filled per world. */
export const CASE_VIEWS = (readerId: string): CaseViewRow[] => [
  caseView(1, 'Flaky P1s', { priorities: ['high'], automation: 'automated' }, { user_id: readerId, is_shared: true }),
  caseView(2, 'Checkout regression', { types: ['regression'] }, { user_id: OTHER_MEMBER_ID, is_shared: true }),
]

/** The id the next saved view gets. */
export const NEW_CASE_VIEW_ID = caseViewId(90)

function savedViewHandlers(state: { views: CaseViewRow[] }, writes: LibraryWrite[], reader: FixtureUser, saveRefused?: string): ApiHandlers {
  const rowOf = (request: ApiRequest) => state.views.find((row) => row.id === request.path.split('/').pop())
  return [
    [SAVED_VIEWS_API, (request) => {
      const projectId = request.url.searchParams.get('project_id')
      const pageName = request.url.searchParams.get('page')
      return state.views
        .filter((row) => row.user_id === reader.id || (row.is_shared && row.project_id !== null))
        .filter((row) => (projectId ? row.project_id === projectId : true))
        .filter((row) => (pageName ? row.page === pageName : true))
    }],
    [SAVED_VIEWS_API, (request) => {
      const body = (record(writes, request) ?? {}) as Partial<CaseViewRow>
      if (saveRefused) return respond(422, { detail: [{ type: 'saved_case_view', loc: ['body'], msg: saveRefused }] })
      const row: CaseViewRow = {
        ...caseView(90, String(body.name ?? ''), {}),
        id: state.views.some((r) => r.id === NEW_CASE_VIEW_ID) ? caseViewId(91 + state.views.length) : NEW_CASE_VIEW_ID,
        user_id: reader.id,
        project_id: (body.project_id as string | null | undefined) ?? null,
        page: String(body.page ?? ''),
        filters: (body.filters as Record<string, unknown>) ?? {},
        is_shared: Boolean(body.is_shared),
        created_at: NOW.toISOString(),
      }
      state.views.push(row)
      return respond(201, row)
    }, 'POST'],
    [SAVED_VIEW_ID_PATH, (request) => {
      const body = (record(writes, request) ?? {}) as Partial<CaseViewRow>
      const row = rowOf(request)
      if (!row) return respond(404, { detail: 'Saved view not found' })
      if (row.user_id !== reader.id) return respond(403, { detail: 'Only the owner can change a saved view' })
      Object.assign(row, body, { updated_at: NOW.toISOString() })
      return row
    }, 'PATCH'],
    [SAVED_VIEW_ID_PATH, (request) => {
      record(writes, request)
      const row = rowOf(request)
      if (!row) return respond(404, { detail: 'Saved view not found' })
      if (row.user_id !== reader.id) return respond(403, { detail: 'Only the owner can delete a saved view' })
      state.views = state.views.filter((r) => r !== row)
      return respond(204)
    }, 'DELETE'],
  ]
}

// ── The shared base: TEST_CASES with the W13 reads ─────────────────────────

/** A `GET /test-management/health` body (`LibraryHealthResponse`; `libraryHealthOf`'s shape, loosely typed). */
export type LibraryHealthBody = Record<string, unknown>

interface BaseOptions {
  user?: FixtureUser
  views?: CaseViewRow[]
  saveRefused?: string
  health?: LibraryHealthBody | (() => LibraryHealthBody)
  /** The ids of the cases linking a normalised requirement key (`GET /cases?requirement=`). */
  linkedTo?: (key: string) => readonly string[]
  /** Cases a world adds (a flaky list's rows), answered by `GET /cases/{id}` and never listed. */
  extraCases?: Record<string, unknown>[]
}

const CASE_BY_ID = /^\/api\/v1\/test-management\/cases\/[^/]+$/

function libraryBase(options: BaseOptions = {}) {
  const user = options.user ?? USER
  const writes: LibraryWrite[] = []
  const reads: LibraryWrite[] = []
  const cases = libraryCases().map((c, i) => ((MY_CASES as readonly number[]).includes(i) ? { ...c, assignee_id: user.id } : c))
  const extra = options.extraCases ?? []
  const state = { views: options.views ?? [], health: options.health ?? TEST_CASES_HEALTH }
  const healthNow = () => (typeof state.health === 'function' ? state.health() : state.health)
  const handlers: ApiHandlers = [
    [CASES_API, (request) => {
      record(reads, request, null)
      return caseListAnswer(cases, request.url.searchParams, user.id, options.linkedTo)
    }],
    [EXPORT_API, (request) => {
      record(reads, request, null)
      return respondRaw('PK', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    }],
    [HEALTH_API, () => healthNow()],
    // Before the by-id read below (a one-segment path that would match it).
    [EVIDENCE_GAPS_API, () => ({ items: [], total: 0 })],
    [CASE_BY_ID, (request) => {
      const id = segmentAfter(request.path, 'cases')
      return [...cases, ...extra].find((c) => c.id === id) ?? respond(404, { detail: 'Test case not found' })
    }],
    [MEMBERS_API, () => tmMembers()],
    ...savedViewHandlers(state, writes, user, options.saveRefused),
    ...testLibraryWorld().handlers,
  ]
  return { handlers, writes, reads, cases, state, user }
}

/** The list queries the page sent (`GET /test-management/cases`), as URLSearchParams, in order. */
export function listQueries(reads: readonly LibraryWrite[]): URLSearchParams[] {
  return reads
    .filter((r) => r.line.startsWith(`GET ${CASES_API}?`) || r.line === `GET ${CASES_API}`)
    .map((r) => new URL(r.line.slice(4), 'http://x').searchParams)
}

// ── TCL-01: the filters and the saved case views ───────────────────────────

export interface CaseFiltersOptions {
  role?: LibraryRole
  /** The project's saved case views (default: the reader's `Flaky P1s` and another member's shared one). */
  views?: (readerId: string) => CaseViewRow[]
  /** Answer the save with a 422 carrying this sentence. */
  saveRefused?: string
}

export function caseFiltersWorld(options: CaseFiltersOptions = {}) {
  const user = LIBRARY_USERS[options.role ?? 'QA_ENGINEER']
  return libraryBase({ user, views: (options.views ?? CASE_VIEWS)(user.id), saveRefused: options.saveRefused })
}

// ── TCL-03: the library health with flaky and ownerless cases ──────────────

/** Approved / active cases whose automated test is flaky: 30 in all (five in the health answer, 25 per page). */
export const FLAKY_TOTAL = 30
/** Non-retired cases nobody active owns. */
export const NO_OWNER_TOTAL = 2
export const flakyCaseId = (n: number) => `0f000000-aaaa-4000-8000-0000000000${String(n).padStart(2, '0')}`
export const flakyCaseTitle = (n: number) => `Flaky checkout case ${n + 1}: the payment retries`
export const noOwnerCaseId = (n: number) => `0e000000-aaaa-4000-8000-0000000000${String(n).padStart(2, '0')}`

/** The flaky score of flaky case `n` (falling: the list is ordered by score). */
const flakyScore = (n: number) => Math.round((0.95 - n * 0.01) * 100) / 100

/** The cases the two lists name, as `GET /cases/{id}` answers them (an Active, automated case each). */
export function listedCases(): Record<string, unknown>[] {
  const base = libraryCases()[0]
  return [
    ...Array.from({ length: FLAKY_TOTAL }, (_, n) => ({ ...base, id: flakyCaseId(n), title: flakyCaseTitle(n), status: 'active', is_automated: true, automation_status: 'automated' })),
    ...Array.from({ length: NO_OWNER_TOTAL }, (_, n) => ({ ...base, id: noOwnerCaseId(n), title: `Unowned case ${n + 1}: the receipt prints`, status: 'draft' })),
  ]
}

/**
 * `TEST_CASES_HEALTH` (`libraryHealthOf` over the same eight cases, at `target`) plus a measured flaky list (30,
 * the top five in the answer) and two ownerless drafts, each with its strategy gap (`STRATEGY_GAP_KINDS` order:
 * … automation_below_target, stale_drafts, flaky_active, no_owner).
 */
export function libraryHealthW13(target = 60): LibraryHealthBody {
  const base = libraryHealthOf(libraryCases() as unknown as Parameters<typeof libraryHealthOf>[0], NOW, target)
  return {
    ...base,
    strategy_gaps: [
      ...base.strategy_gaps,
      { kind: 'flaky_active', severity: 'warn', title: 'Flaky and active', detail: 'approved or active cases whose automated test is flaky', count: FLAKY_TOTAL, pill: `${FLAKY_TOTAL} flaky` },
      { kind: 'no_owner', severity: 'warn', title: 'No owner', detail: 'nobody active owns them', count: NO_OWNER_TOTAL, pill: `${NO_OWNER_TOTAL} cases` },
    ],
    flaky_active: {
      total: FLAKY_TOTAL,
      measured: true,
      reason: null,
      items: Array.from({ length: 5 }, (_, n) => ({
        id: flakyCaseId(n), title: flakyCaseTitle(n), status: 'active', owner: 'Priya Shah', age_days: null,
        detail: `flaky score ${flakyScore(n).toFixed(2)} (high confidence)`,
      })),
    },
    no_owner: {
      total: NO_OWNER_TOTAL,
      measured: true,
      reason: null,
      items: Array.from({ length: NO_OWNER_TOTAL }, (_, n) => ({
        id: noOwnerCaseId(n), title: `Unowned case ${n + 1}: the receipt prints`, status: 'draft', owner: null, age_days: 12 + n,
        detail: n === 0 ? 'unassigned' : 'owner deactivated',
      })),
    },
  } as LibraryHealthBody
}

/** `GET /cases/evidence-gaps?kind=&page=&size=` for the two W13 kinds (the server's `EvidenceGapItem` rows). */
function evidenceGapPage(request: ApiRequest) {
  const params = request.url.searchParams
  const kind = params.get('kind')
  const page = Number(params.get('page') ?? 1)
  const size = Number(params.get('size') ?? 25)
  const rows = kind === 'flaky_active'
    ? Array.from({ length: FLAKY_TOTAL }, (_, n) => ({
      id: flakyCaseId(n), project_id: PROJECT_ID, title: flakyCaseTitle(n), status: 'active', flaky_score: flakyScore(n), flaky_confidence: 'high',
    }))
    : kind === 'no_owner'
      ? Array.from({ length: NO_OWNER_TOTAL }, (_, n) => ({
        id: noOwnerCaseId(n), project_id: PROJECT_ID, title: `Unowned case ${n + 1}: the receipt prints`, status: 'draft', owner_state: n === 0 ? 'unassigned' : 'deactivated',
      }))
      : []
  return { items: rows.slice((page - 1) * size, page * size), total: rows.length }
}

export interface LibraryHealthOptions {
  role?: LibraryRole
  /** The health answer (default `libraryHealthW13()`); a function is asked per read. */
  health?: LibraryHealthBody
  /** Answer the health read with a 500 (the cards say `Could not load library health.`). */
  healthFails?: boolean
}

/**
 * The health world: the richer answer, the evidence-gap pages, and the target's `PUT /projects/{id}` (QA_LEAD+,
 * as `routers/projects.py`; a lower role 403s) — after it the health answers the new target (the gap
 * `Automation below target` reads it).
 */
export function testLibraryHealthWorld(options: LibraryHealthOptions = {}) {
  const user = LIBRARY_USERS[options.role ?? 'QA_LEAD']
  const target = { pct: (options.health?.automation_target_pct as number | undefined) ?? 60 }
  const health = () => (options.health && target.pct === options.health.automation_target_pct ? options.health : libraryHealthW13(target.pct))
  const base = libraryBase({ user, health, extraCases: listedCases() })
  const handlers: ApiHandlers = [
    ...(options.healthFails ? [[HEALTH_API, () => respond(500, { detail: 'Internal Server Error' })] as const] : []),
    [EVIDENCE_GAPS_API, (request) => {
      record(base.reads, request, null)
      return evidenceGapPage(request)
    }],
    [PROJECT_API, (request) => {
      const body = record(base.writes, request) as { automation_target_pct?: number }
      if (rankOf(user) < RANK.QA_LEAD) return respond(403, { detail: LIBRARY_TEXT.targetRefused })
      const pct = body?.automation_target_pct
      if (typeof pct !== 'number' || pct < 0 || pct > 100) {
        return respond(422, { detail: [{ type: 'less_than_equal', loc: ['body', 'automation_target_pct'], msg: 'Input should be less than or equal to 100' }] })
      }
      target.pct = pct
      return { id: PROJECT_ID, name: 'Checkout', slug: 'checkout', description: '', is_active: true, created_at: '2026-01-05T09:00:00Z', automation_target_pct: pct }
    }, 'PUT'],
    ...base.handlers,
  ]
  return { ...base, handlers, target }
}

// ── TCL-04: the import (`POST /cases/import?project_id=&dry_run=`, multipart) ──

/** A title the server stores as typed (the preview must show those characters, never a link). */
export const FORMULA_TITLE = '=SUM(A1:A2) totals the basket'
export const IMPORT_FILE_NAME = 'checkout-cases.csv'
export const importedCaseId = (row: number) => `0d000000-aaaa-4000-8000-0000000000${String(row).padStart(2, '0')}`
export const importTitle = (row: number) => (row === 2 ? FORMULA_TITLE : `Imported checkout case ${row - 1}: the basket survives`)

/** Rows 2-21 of a 20-row file: 17 to create (row 2 the formula title, row 3 a Status the import ignores), 3 invalid. */
function importRows(applied: boolean) {
  return Array.from({ length: 20 }, (_, i) => {
    const row = i + 2
    if (row === 19) return { row, outcome: 'invalid', title: 'ab', reasons: [LIBRARY_TEXT.importRowTitle], warnings: [], duplicate_of: null, case_id: null }
    if (row === 20) return { row, outcome: 'invalid', title: importTitle(row), reasons: [LIBRARY_TEXT.importRowPriority], warnings: [], duplicate_of: null, case_id: null }
    if (row === 21) return { row, outcome: 'invalid', title: importTitle(row), reasons: [LIBRARY_TEXT.importRowAutomated], warnings: [], duplicate_of: null, case_id: null }
    return {
      row,
      outcome: applied ? 'created' : 'create',
      title: importTitle(row),
      reasons: [],
      warnings: row === 3 ? [LIBRARY_TEXT.importWarnStatus] : [],
      duplicate_of: null,
      case_id: applied ? importedCaseId(row) : null,
    }
  })
}

const importHeaders = { recognised: ['Title', 'Type', 'Priority', 'Automated', 'Tags'], ignored: ['Status', 'Version'], unknown: ['Owner'] }

/** The dry run's answer: `20 rows: 17 to create · 0 duplicates · 3 invalid`. */
export function importDryRun(fileName = IMPORT_FILE_NAME) {
  return {
    project_id: PROJECT_ID, dry_run: true, file_name: fileName, format: 'csv', headers: importHeaders,
    summary: { rows: 20, create: 17, duplicate: 0, invalid: 3, created: 0 }, rows: importRows(false),
  }
}

/** The apply's answer: `17 cases imported as Draft. 3 rows were not imported.` */
export function importApplied(fileName = IMPORT_FILE_NAME) {
  return {
    project_id: PROJECT_ID, dry_run: false, file_name: fileName, format: 'csv', headers: importHeaders,
    summary: { rows: 20, create: 17, duplicate: 0, invalid: 3, created: 17 }, rows: importRows(true),
  }
}

/** The CSV the import spec uploads (built in the spec's process; the fixture's answers do not parse it). */
export function importCsv(): string {
  const header = 'Title,Type,Priority,Automated,Tags,Status,Version,Owner'
  const lines = Array.from({ length: 20 }, (_, i) => {
    const row = i + 2
    const title = row === 19 ? 'ab' : importTitle(row)
    const priority = row === 20 ? 'urgent' : 'high'
    const automated = row === 21 ? 'maybe' : 'No'
    return [`"${title}"`, 'functional', priority, automated, 'checkout;smoke', row === 3 ? 'approved' : 'draft', '1', 'priya'].join(',')
  })
  return `${[header, ...lines].join('\r\n')}\r\n`
}

export interface ImportWorldOptions {
  role?: LibraryRole
  dryRun?: unknown
  apply?: unknown
  /** Answer the dry run with this status and sentence (413 / 422 / 400). */
  refuse?: { status: number; detail: string }
}

/**
 * The import world: `POST /cases/import` RECORDED with its whole query and what the multipart body carried (the
 * file name, the part name, the content type); `dry_run=true` answers `dryRun`, `false` answers `apply`; below
 * TESTER the route's `require_role` 403s.
 */
export function importWorld(options: ImportWorldOptions = {}) {
  const user = LIBRARY_USERS[options.role ?? 'TESTER']
  const base = libraryBase({ user })
  const handlers: ApiHandlers = [
    [IMPORT_API, (request) => {
      const req = request.route.request()
      const raw = req.postData() ?? ''
      record(base.writes, request, {
        contentType: (req.headers()['content-type'] ?? '').split(';')[0],
        part: /name="([^"]+)"; filename="/.exec(raw)?.[1] ?? null,
        fileName: /filename="([^"]*)"/.exec(raw)?.[1] ?? null,
        bytes: raw.length,
      })
      if (rankOf(user) < RANK.TESTER) return respond(403, { detail: LIBRARY_TEXT.importRefused })
      const dry = request.url.searchParams.get('dry_run') !== 'false'
      if (dry && options.refuse) return respond(options.refuse.status, { detail: options.refuse.detail })
      return dry ? (options.dryRun ?? importDryRun()) : (options.apply ?? importApplied())
    }, 'POST'],
    ...base.handlers,
  ]
  return { ...base, handlers }
}

// ── TCL-05: the case timeline (`GET /cases/{id}/timeline`, keyset pages) ──

/** The case the timeline spec opens: case 1 (Active), an authored case with an automation identity. */
export const TIMELINE_CASE = 0
export const TIMELINE_RUN_ID = '33333333-3333-4333-8333-3333333333a1'
export const TIMELINE_RESULT_ID = '7a000000-0000-4000-8000-0000000000a1'
export const TIMELINE_DEFECT_ID = '7b000000-0000-4000-8000-0000000000b1'
export const TIMELINE_PLAN_ID = '9a000000-0000-4000-8000-0000000000b1'
export const TIMELINE_CURSOR = 'eyJ0IjoiMjAyNi0wOS0xMCJ9'
const HOSTILE_DETAIL = '<img src=x onerror="window.__xss=1"> retried twice'

const entry = (id: string, kind: string, daysAgo: number, hours: number, fields: Record<string, unknown>) => ({
  id, kind, occurred_at: isoAgo(daysAgo, hours), actor_id: null, actor_name: null, actor_source: null,
  detail: null, status: null, version: null, link: null, ...fields,
})

/** Page 1: six kinds, newest first, a cursor; page 2: the oldest three, no cursor (the start of the timeline). */
export function timelinePages(caseId = tmCaseId(TIMELINE_CASE)) {
  const first = [
    entry(TIMELINE_RESULT_ID, 'automated_execution', 0, 3, {
      actor_source: 'automation', title: 'Automated run · FAILED', status: 'FAILED', detail: 'Build 412 · main',
      link: { type: 'run_result', run_id: TIMELINE_RUN_ID, test_case_id: TIMELINE_RESULT_ID },
    }),
    entry(TIMELINE_DEFECT_ID, 'defect', 0, 5, {
      title: 'Defect PAY-123 opened', status: 'open', detail: HOSTILE_DETAIL,
      link: { type: 'defect', defect_id: TIMELINE_DEFECT_ID, jira_key: 'PAY-123', run_id: TIMELINE_RUN_ID, test_case_id: TIMELINE_RESULT_ID },
    }),
    entry('7c000000-0000-4000-8000-0000000000c1:decided', 'review_decided', 2, 0, {
      actor_id: '00000000-0000-4000-8000-000000000101', actor_name: 'Lena Lead', title: 'Approved', status: 'approved', detail: 'Steps read well; ship it.',
    }),
    entry('7d000000-0000-4000-8000-0000000000d1', 'comment', 3, 0, {
      actor_name: 'Priya Shah', title: 'Comment', detail: 'Step 2 needs a slower network to fail.',
    }),
    entry('7e000000-0000-4000-8000-0000000000e1', 'plan_execution', 4, 0, {
      actor_name: 'QA Engineer', title: 'Executed in plan Sprint 42 regression · PASSED (v2)', status: 'passed', version: 2,
      link: { type: 'plan', plan_id: TIMELINE_PLAN_ID },
    }),
    entry('7f000000-0000-4000-8000-0000000000f2', 'version', 5, 0, {
      actor_name: 'Priya Shah', actor_source: 'person', title: 'Saved as v2 (person)', status: 'active', version: 2, detail: 'Clearer steps',
    }),
  ]
  const second = [
    entry('7c000000-0000-4000-8000-0000000000c1:requested', 'review_requested', 9, 0, { actor_name: 'Priya Shah', title: 'Review requested' }),
    entry('7a100000-0000-4000-8000-0000000000a9', 'requirement', 10, 0, { actor_name: 'Priya Shah', title: 'Linked REQ-PAY-07 (verifies)' }),
    entry('7f000000-0000-4000-8000-0000000000f1', 'version', 12, 0, { actor_source: 'ai', title: 'Created as v1 (ai)', status: 'draft', version: 1 }),
  ]
  return [
    { case_id: caseId, kinds: [], items: first, next_cursor: TIMELINE_CURSOR, automation_linked: true },
    { case_id: caseId, kinds: [], items: second, next_cursor: null, automation_linked: true },
  ]
}

export interface TimelineWorldOptions {
  role?: LibraryRole
  /** The keyset pages (default `timelinePages()`): page n+1 answers the cursor page n carried. */
  pages?: ReturnType<typeof timelinePages>
  /** `automation_linked: false` on every answer (no automated test for the case). */
  noAutomation?: boolean
  /** Answer every timeline read with a 500. */
  fails?: boolean
}

/** The timeline world: each read RECORDED (its whole query); `kinds` narrows the entries to one page. */
export function timelineWorld(options: TimelineWorldOptions = {}) {
  const user = LIBRARY_USERS[options.role ?? 'QA_LEAD']
  const pages = options.pages ?? timelinePages()
  const base = libraryBase({ user })
  const handlers: ApiHandlers = [
    [TIMELINE_PATH, (request) => {
      record(base.reads, request, null)
      if (options.fails) return respond(500, { detail: 'Internal Server Error' })
      const params = request.url.searchParams
      const kinds = params.getAll('kinds')
      const cursor = params.get('cursor')
      const linked = !options.noAutomation
      const caseId = segmentAfter(request.path, 'cases')
      if (kinds.length) {
        const items = pages.flatMap((p) => p.items).filter((e) => kinds.includes(String(e.kind)))
        return { case_id: caseId, kinds, items, next_cursor: null, automation_linked: linked }
      }
      const at = cursor ? pages.findIndex((_, i) => i > 0 && pages[i - 1].next_cursor === cursor) : 0
      if (at < 0) return respond(422, { detail: 'This timeline cursor is not valid for this test case.' })
      return { ...pages[at], case_id: caseId, automation_linked: linked }
    }],
    ...base.handlers,
  ]
  return { ...base, handlers, pages }
}

// ── TCL-06: requirement links, the coverage stat and the gaps ──────────────

/** The case the requirements spec opens: case 1 (Active). */
export const REQUIREMENTS_CASE = 0
export const requirementLinkId = (n: number) => `7a200000-0000-4000-8000-0000000000${String(n).padStart(2, '0')}`

/** = `core/test_library_vocab.normalize_requirement_key`: strip, ONE pair of [] or () dropped, spaces collapsed, upper-cased. */
export function normalizeRequirementKey(raw: string): string {
  let key = raw.trim()
  if ((key.startsWith('[') && key.endsWith(']')) || (key.startsWith('(') && key.endsWith(')'))) key = key.slice(1, -1).trim()
  return key.replace(/\s+/g, ' ').toUpperCase()
}

export interface RequirementLinkRow {
  id: string
  test_case_id: string
  requirement_key: string
  normalized_key: string
  requirement_title: string | null
  link_type: string
  created_by_name: string | null
  created_at: string
}

export function requirementLink(n: number, caseId: string, key: string, linkType: string, title: string | null, by: string | null = 'Priya Shah'): RequirementLinkRow {
  return {
    id: requirementLinkId(n), test_case_id: caseId, requirement_key: key, normalized_key: normalizeRequirementKey(key),
    requirement_title: title, link_type: linkType, created_by_name: by, created_at: isoAgo(10 - n),
  }
}

/** Case 1's three links: one per link type (oldest first). */
export function requirementLinks(caseId = tmCaseId(REQUIREMENTS_CASE)): RequirementLinkRow[] {
  return [
    requirementLink(1, caseId, 'REQ-PAY-07', 'verifies', 'Refund within 24h'),
    requirementLink(2, caseId, '[req-pay-09]', 'partially_verifies', 'Partial refunds keep the tax line', null),
    requirementLink(3, caseId, 'REQ-PAY-11', 'related', '<b>Receipts</b> show the refund'),
  ]
}

const gap = (key: string, reason: string, sources: string[], batch = 0) => ({
  key, normalized_key: normalizeRequirementKey(key), reason, sources, linked_cases: reason === 'no_link' ? 0 : 1, batch_claimed_cases: batch,
})

/** `Requirements verified 12 of 15`, `2 partly verified`, three gaps (one per reason). */
export const REQUIREMENT_SUMMARY = {
  measured: true, known: 15, verified: 12, partly_verified: 2, gaps: 3, truncated: false, reason: null,
  top_gaps: [
    gap('REQ-PAY-12', 'no_link', ['generation_batch'], 3),
    gap('REQ-PAY-11', 'related_only', ['link', 'knowledge_source']),
    gap('[req-pay-09]', 'partly_verified', ['link']),
  ],
}

export interface RequirementsWorldOptions {
  role?: LibraryRole
  /** The links (default: case 1's three). */
  links?: RequirementLinkRow[]
  /** The health's `requirements` (default `REQUIREMENT_SUMMARY`). */
  summary?: Record<string, unknown>
  /** The flag OFF for the project: the four routes 404 and the health carries no `requirements`. */
  flagOff?: boolean
}

/**
 * The traceability world (the spec turns `test_case_requirement_traceability` ON through `flags`): a case's
 * links (list / add / remove, each write RECORDED; a second link of one normalised key 409s; below TESTER 403),
 * `GET /cases?requirement=` answering the cases that link the key, the health with `requirements`, and
 * `GET /traceability/gaps` (25 per page over 40 gaps, four cases linking none).
 */
export function requirementsWorld(options: RequirementsWorldOptions = {}) {
  const user = LIBRARY_USERS[options.role ?? 'TESTER']
  const links: RequirementLinkRow[] = (options.links ?? requirementLinks()).map((l) => ({ ...l }))
  const linkedTo = (key: string) => links.filter((l) => l.normalized_key === normalizeRequirementKey(key)).map((l) => l.test_case_id)
  const health = () => ({ ...TEST_CASES_HEALTH, requirements: options.flagOff ? null : (options.summary ?? REQUIREMENT_SUMMARY) }) as unknown as LibraryHealthBody
  const base = libraryBase({ user, health, linkedTo })
  const off = () => respond(404, { detail: LIBRARY_TEXT.traceabilityOff })
  const allGaps = Array.from({ length: 40 }, (_, n) => gap(`REQ-GAP-${String(n + 1).padStart(2, '0')}`, n < 30 ? 'no_link' : 'related_only', ['knowledge_source']))
  const handlers: ApiHandlers = [
    [REQUIREMENTS_PATH, (request) => {
      if (options.flagOff) return off()
      const caseId = segmentAfter(request.path, 'cases')
      const items = links.filter((l) => l.test_case_id === caseId)
      return { items, total: items.length }
    }],
    [REQUIREMENTS_PATH, (request) => {
      const body = (record(base.writes, request) ?? {}) as { requirement_key?: string; requirement_title?: string | null; link_type?: string }
      if (options.flagOff) return off()
      if (rankOf(user) < RANK.TESTER) return respond(403, { detail: LIBRARY_TEXT.importRefused })
      const caseId = segmentAfter(request.path, 'cases')
      const norm = normalizeRequirementKey(String(body.requirement_key ?? ''))
      if (links.some((l) => l.test_case_id === caseId && l.normalized_key === norm)) {
        return respond(409, { detail: LIBRARY_TEXT.requirementAlreadyLinked(norm) })
      }
      const row = { ...requirementLink(40 + links.length, caseId, String(body.requirement_key ?? ''), String(body.link_type ?? 'verifies'), body.requirement_title ?? null, user.full_name), created_at: NOW.toISOString() }
      links.push(row)
      return respond(201, row)
    }, 'POST'],
    [REQUIREMENT_LINK_PATH, (request) => {
      record(base.writes, request)
      if (options.flagOff) return off()
      const caseId = segmentAfter(request.path, 'cases')
      const linkId = segmentAfter(request.path, 'requirements')
      const at = links.findIndex((l) => l.id === linkId && l.test_case_id === caseId)
      if (at < 0) return respond(404, { detail: LIBRARY_TEXT.requirementLinkNotFound })
      links.splice(at, 1)
      return respond(204)
    }, 'DELETE'],
    [TRACEABILITY_GAPS_API, (request) => {
      record(base.reads, request, null)
      if (options.flagOff) return off()
      const page = Number(request.url.searchParams.get('page') ?? 1)
      const size = Number(request.url.searchParams.get('size') ?? 25)
      const without = libraryCases().filter((c) => c.status === 'active' || c.status === 'approved').slice(0, 4)
      return {
        project_id: PROJECT_ID, summary: { ...(options.summary ?? REQUIREMENT_SUMMARY), top_gaps: [] },
        gaps: allGaps.slice((page - 1) * size, page * size), gaps_total: allGaps.length, page, size,
        cases_without_requirements_total: without.length,
        cases_without_requirements: without.map((c) => ({ id: c.id, title: c.title, status: c.status, owner: null, age_days: null, detail: null })),
      }
    }],
    ...base.handlers,
  ]
  return { ...base, handlers, links }
}

// ── TCL-07: edit a suite; a suite before its first run ─────────────────────

/** A first-class suite no result reports yet: `/suites` lists it, the TM Suites tab shows it as `Empty`. */
export const EMPTY_SUITE_ID = '66666666-6666-4666-8666-0000000000e7'
export const EMPTY_SUITE_NAME = 'Checkout API'

/**
 * `/suites` and `/suites/:id` (`suitesInPageWorld()`: `Auth`, the default `All Tests`, `Legacy smoke`) plus
 * `Checkout API` (no run yet), and `PATCH /suites/{id}` (QA_ENGINEER+ as `require_role`; a name another suite of
 * the project uses 409s; each RECORDED, the row updated so the next read shows it). `GET /test-management/suites`
 * answers `TEST_CASES`' aggregated rows and the ADDED `Checkout API` row (`empty: true`, `suite_id`), so the TM
 * Suites tab (over `TEST_CASES`) shows the empty card.
 */
export function suitesEditWorld(options: { role?: LibraryRole } = {}) {
  const user = LIBRARY_USERS[options.role ?? 'QA_ENGINEER']
  const inPage = suitesInPageWorld()
  const writes: LibraryWrite[] = []
  inPage.rows.push({
    id: EMPTY_SUITE_ID, project_id: PROJECT_ID, name: EMPTY_SUITE_NAME, description: 'The public checkout endpoints',
    is_default: false, tags: ['api'], test_case_count: 0, created_at: isoAgo(1), updated_at: null,
  })
  // `TEST_CASES`' aggregates (no `last_run_id`: no AI-verdict read per card), then the ADDED empty row.
  const aggregated = delegate(TEST_CASES, fakeGet('/api/v1/test-management/suites')) as Record<string, unknown>[]
  const emptyRow = {
    suite_name: EMPTY_SUITE_NAME, test_count: 0, passed_count: 0, failed_count: 0, last_run_at: null, last_run_id: null,
    pass_rate: null, run_count: 0, total_executions: 0, total_passed: 0, total_failed: 0, total_skipped: 0, total_broken: 0,
    owner_user_id: USER.id, owner_email: USER.email, owner_full_name: USER.full_name, owner_is_fallback: false,
    empty: true, suite_id: EMPTY_SUITE_ID,
  }
  const handlers: ApiHandlers = [
    [SUITE_ID_PATH, (request) => {
      const body = (record(writes, request) ?? {}) as { name?: string; description?: string; tags?: string[] }
      if (rankOf(user) < RANK.QA_ENGINEER) return respond(403, { detail: 'Requires at least QA_ENGINEER role' })
      const id = segmentAfter(request.path, 'suites')
      const row = inPage.rows.find((s) => s.id === id)
      if (!row) return respond(404, { detail: 'Suite not found' })
      const wanted = body.name?.trim().toLowerCase()
      if (wanted && inPage.rows.some((s) => s.id !== id && s.project_id === row.project_id && s.name.trim().toLowerCase() === wanted)) {
        return respond(409, { detail: LIBRARY_TEXT.suiteNameTaken })
      }
      if (body.name !== undefined) row.name = body.name.trim()
      if (body.description !== undefined) row.description = body.description
      if (body.tags !== undefined) row.tags = body.tags
      row.updated_at = NOW.toISOString()
      return row
    }, 'PATCH'],
    ['/api/v1/test-management/suites', (request) => (request.url.searchParams.get('release_id') ? aggregated : [...aggregated, emptyRow])],
    ...inPage.handlers,
    ...testLibraryWorld().handlers,
  ]
  return { handlers, writes, rows: inPage.rows, user }
}
