/**
 * W27b "in-page panels" hermetic answers (`docs/deferred-scope-2026-10-13/work/W27b-contract.md` §4.4): ONLY the
 * writes and reads the W27b characterization specs (`tests/ci-e2e/characterize-*.spec.ts`, phase 1) need and no
 * existing world answers. Each factory COMPOSES the exported arrays of the existing fixture modules (never edits
 * one; W27a's `fixtures-in-page.ts` is read-only here) and lists its own handlers FIRST (first match wins,
 * `production-pages.ts`), so a W27b write is answered before the page's reads.
 *
 * Every factory returns fresh state per call and records every write it answered (`writes`: `METHOD path` and
 * the parsed JSON body), so a spec pins the whole body with `toEqual`.
 * Not a spec file.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { isoAgo, LAYOUT, PROJECT_ID, SECOND_SUITE_ID, SUITE_DETAIL, SUITE_DETAIL_ON, SUITE_ID, USER } from './fixtures'
import { flagsHandlers } from './fixtures-agents-billing-flags'
import { bodyOf, delegate, type RecordedWrite } from './fixtures-in-page'
import { digestsWorld, notificationsWorld, preference } from './fixtures-notify-flaky'
import { inboxHandlers, projectMembers, TEST_CASES } from './fixtures-pages'
import { ownershipWorld } from './fixtures-runs-failures'

export type { RecordedWrite }

/** A GET of `path` (with its query), for delegating to an existing world from inside a handler. */
export function fakeGet(path: string): ApiRequest {
  const url = new URL(path, 'http://127.0.0.1')
  return { url, path: url.pathname, method: 'GET', route: undefined as never }
}

/** Record `request` in `writes` and return its parsed body. */
export function recordIn(writes: RecordedWrite[], request: ApiRequest): unknown {
  const body = bodyOf(request)
  writes.push({ line: `${request.method} ${request.path}`, body })
  return body
}

// ── /suites, /suites/:id (PAN-05a, L16-L19) ────────────────────────────────

export interface SuiteRowFixture {
  id: string
  project_id: string
  name: string
  description: string | null
  is_default: boolean
  tags: string[] | null
  test_case_count: number
  created_at: string
  updated_at: string | null
}

/** A third suite of the project, empty and not the default: the one L17 deletes and L18/L19 can move to. */
export const LEGACY_SUITE_ID = '66666666-6666-4666-8666-0000000000c3'
export const LEGACY_SUITE_NAME = 'Legacy smoke'
/** The id the next created suite gets. */
export const NEW_SUITE_ID = '66666666-6666-4666-8666-0000000000f1'

/**
 * The suite page / suites list world (`SUITE_DETAIL_ON`: `Auth` with five catalog cases, the default `All Tests`)
 * with a third, empty suite (`Legacy smoke`) and the writes PAN-05a's suite flows send: create
 * (`POST /api/v1/suites`, the list re-read shows it), delete (`DELETE /api/v1/suites/{id}`, gone from the next
 * read), move one (`POST /api/v1/canonical-test-cases/{id}/link`) and move many (`…/bulk-link`).
 */
export function suitesInPageWorld() {
  const writes: RecordedWrite[] = []
  const base = (delegate(SUITE_DETAIL, fakeGet('/api/v1/suites')) as { items: SuiteRowFixture[] }).items
  const auth = base.find((s) => s.id === SUITE_ID) as SuiteRowFixture
  const rows: SuiteRowFixture[] = [
    ...base,
    { ...auth, id: LEGACY_SUITE_ID, name: LEGACY_SUITE_NAME, description: 'Pre-2026 smoke checks', test_case_count: 0 },
  ]
  const list = () => ({ items: rows, total: rows.length, page: 1, size: 100, pages: 1 })
  const handlers: ApiHandlers = [
    ['/api/v1/suites', (request) => {
      const body = recordIn(writes, request) as { project_id: string; name: string; description: string | null }
      const row: SuiteRowFixture = {
        id: NEW_SUITE_ID,
        project_id: body.project_id,
        name: body.name,
        description: body.description,
        is_default: false,
        tags: null,
        test_case_count: 0,
        created_at: isoAgo(0),
        updated_at: null,
      }
      rows.push(row)
      return respond(201, row)
    }, 'POST'],
    [/^\/api\/v1\/suites\/[^/]+$/, (request) => {
      recordIn(writes, request)
      const id = request.path.split('/')[4]
      const at = rows.findIndex((s) => s.id === id)
      if (at < 0) return respond(404, { detail: 'Suite not found' })
      rows.splice(at, 1)
      return respond(204)
    }, 'DELETE'],
    ['/api/v1/canonical-test-cases/bulk-link', (request) => {
      const body = recordIn(writes, request) as { canonical_ids: string[] }
      return { moved: body.canonical_ids.length, skipped_already_in_target: 0, missing_ids: [] }
    }, 'POST'],
    [/^\/api\/v1\/canonical-test-cases\/[^/]+\/link$/, (request) => {
      const body = recordIn(writes, request) as { test_suite_id: string }
      return { id: request.path.split('/')[4], test_suite_id: body.test_suite_id }
    }, 'POST'],
    ['/api/v1/suites', list],
    [/^\/api\/v1\/suites\/[^/]+$/, (request) => {
      const id = request.path.split('/')[4]
      if (id === SUITE_ID || id === SECOND_SUITE_ID) return delegate(SUITE_DETAIL, request)
      return rows.find((s) => s.id === id) ?? respond(404, { detail: 'Suite not found' })
    }],
    ...SUITE_DETAIL_ON,
  ]
  return { handlers, writes, rows }
}

// ── /test-management (PAN-05a, L1-L11) ─────────────────────────────────────

/** `TEST_CASES`' eight managed cases (`fixtures-pages.ts`): case `i` (0-7) has this id and title. */
export const tmCaseId = (i: number) => `0000000${i}-aaaa-4000-8000-00000000000${i}`
export const tmCaseTitle = (i: number) => `Checkout case ${i + 1}: the cart survives a refresh`

/** The project's members (`projectMembers()`) plus a QA lead, the only role the Suites tab offers as an owner. */
export const SUITE_OWNER = { id: '00000000-0000-4000-8000-0000000001f1', username: 'lena.lead', name: 'Lena Lead' }
export function tmMembers() {
  const base = projectMembers()
  return [
    ...base,
    {
      ...base[0],
      id: '00000000-0000-4000-8000-0000000001f0',
      user_id: SUITE_OWNER.id,
      role: 'QA_LEAD',
      email: `${SUITE_OWNER.username}@example.test`,
      username: SUITE_OWNER.username,
      full_name: SUITE_OWNER.name,
    },
  ]
}

/** The plan the Plans tab lists; L6 links a suite into it. */
export const TM_PLAN_ID = '9a000000-0000-4000-8000-0000000000b1'
export const TM_PLAN_NAME = 'Sprint 42 regression'
/** The id the next created plan gets. */
export const NEW_PLAN_ID = '9a000000-0000-4000-8000-0000000000b2'
/** What `GET /test-management/suites/Checkout/cases` answers: two manual cases (linked, in order) and an automation row (skipped). */
export const CHECKOUT_SUITE_CASES = [
  { id: 'c1000000-0000-4000-8000-000000000001', test_name: 'cart keeps items after refresh', source: 'manual' },
  { id: 'c1000000-0000-4000-8000-000000000002', test_name: 'cart shows the saved coupon', source: 'automation' },
  { id: 'c1000000-0000-4000-8000-000000000003', test_name: 'cart total includes tax', source: 'manual' },
]

/** A lifecycle refusal (409) the case pane's Lifecycle tab shows inline. */
export const LIFECYCLE_REFUSAL = { current_state: 'deprecated', attempted_action: 'deprecate' }

/** The knowledge source the Knowledge tab lists (RAG on). */
export const RAG_SOURCE = {
  id: 'ks000000-0000-4000-8000-000000000001',
  project_id: PROJECT_ID,
  source_type: 'confluence',
  title: 'Checkout requirements',
  canonical_url: 'https://wiki.example.test/checkout',
  external_id: null,
  owner_id: null,
  sync_status: 'synced',
  last_synced_at: isoAgo(1),
  sync_error: null,
  content_hash: 'h1',
  classification: 'internal',
  is_archived: false,
  created_at: isoAgo(30),
  updated_at: isoAgo(1),
}
const citation = (caseIndex: number, n: number, title: string, heading: string) => ({
  case_index: caseIndex,
  vector_id: `vec-${caseIndex}-${n}`,
  source_id: RAG_SOURCE.id,
  source_title: title,
  section_heading: heading,
  chunk_text_preview: `${heading}: the evidence the case cites.`,
  relevance_score: 0.9 - n / 10,
  canonical_url: RAG_SOURCE.canonical_url,
})
/** The knowledge generation answer (L11): two cases, two citations for the first, one for the second. */
export const RAG_ANSWER = {
  batch_id: 'batch-w27b-1',
  generation_mode: 'grounded',
  test_cases: [
    {
      title: 'Coupon survives a page refresh',
      description: 'The applied coupon stays on the cart.',
      steps: [{ step_number: 1, action: 'Apply SAVE10', expected_result: 'Discount shown' }],
      test_type: 'functional',
      priority: 'high',
      severity: 'major',
    },
    { title: 'Tax recalculates after an address change', description: null, steps: [], test_type: 'functional', priority: 'medium', severity: 'major' },
  ],
  citations: [
    citation(0, 1, 'Checkout requirements', 'Coupons'),
    citation(0, 2, 'Cart persistence RFC', 'Refresh behaviour'),
    citation(1, 1, 'Tax rules', 'Address changes'),
  ],
  coverage_summary: '2 of 3 requirements',
  gaps_noted: [],
  created_ids: ['gen-1', 'gen-2'],
}

export interface TestLibraryOptions {
  /** `test_case_lifecycle_v2` on: the review cases offer the v2 decisions (Claim review, Approve, Request changes, Reject). */
  lifecycleV2?: boolean
  /** Answer the first lifecycle transition with a 409 (`LIFECYCLE_REFUSAL`), the rest normally. */
  refuseFirstTransition?: boolean
}

/**
 * `/test-management` (`TEST_CASES`) with the writes PAN-05a's test-library flows send: create a case, AI generate,
 * transition / delete a case (L3, L4, L9), the case's allowed transitions, the Plans tab (a plan, its items, create,
 * link a suite), the Suites tab's create, the strategy generation, the legacy review action, the project's members
 * (with a QA lead) and the knowledge generation (RAG on).
 */
export function testLibraryWorld(options: TestLibraryOptions = {}) {
  const writes: RecordedWrite[] = []
  const state = { transitions: 0 }
  const plans: Record<string, unknown>[] = [
    {
      id: TM_PLAN_ID,
      project_id: PROJECT_ID,
      name: TM_PLAN_NAME,
      description: 'Checkout before the cut',
      status: 'active',
      ai_generated: false,
      total_cases: 0,
      executed_cases: 0,
      passed_cases: 0,
      failed_cases: 0,
      blocked_cases: 0,
      created_at: isoAgo(5),
      updated_at: isoAgo(1),
    },
  ]
  const items: Record<string, Record<string, unknown>[]> = {}
  const caseOf = (id: string): Record<string, unknown> => {
    const list = delegate(TEST_CASES, fakeGet('/api/v1/test-management/cases?size=200')) as { items: Record<string, unknown>[] }
    return list.items.find((c) => c.id === id) ?? { id }
  }
  const V2_REVIEW = ['claim_review', 'approve', 'request_changes', 'reject']
  const NEXT_STATUS: Record<string, string> = { deprecate: 'deprecated', approve: 'approved', request_changes: 'draft', reject: 'rejected' }
  const handlers: ApiHandlers = [
    // L1
    ['/api/v1/test-management/cases', (request) => {
      const body = recordIn(writes, request) as Record<string, unknown>
      return respond(201, { ...body, id: 'c0000000-0000-4000-8000-0000000000f1', status: 'draft', version: 1, created_at: isoAgo(0), updated_at: isoAgo(0) })
    }, 'POST'],
    // L2
    ['/api/v1/test-management/cases/ai-generate/async', (request) => {
      recordIn(writes, request)
      return respond(202, { task_id: 'task-w27b-ai-1', status: 'queued' })
    }, 'POST'],
    // L11
    ['/api/v1/test-management/cases/rag-generate', (request) => {
      recordIn(writes, request)
      return RAG_ANSWER
    }, 'POST'],
    // L3 (v2), L4, L9 (v2)
    [/^\/api\/v1\/test-management\/cases\/[^/]+\/transition$/, (request) => {
      const body = recordIn(writes, request) as { action: string; expected_version: number }
      state.transitions += 1
      if (options.refuseFirstTransition && state.transitions === 1) return respond(409, { detail: LIFECYCLE_REFUSAL })
      const id = request.path.split('/')[5]
      return { ...caseOf(id), status: NEXT_STATUS[body.action] ?? 'active', version: body.expected_version + 1, allowed_actions: [] }
    }, 'POST'],
    // L9 (legacy)
    [/^\/api\/v1\/test-management\/cases\/[^/]+\/review-action$/, (request) => {
      const body = recordIn(writes, request) as { action: string }
      const id = request.path.split('/')[5]
      return { ...caseOf(id), status: NEXT_STATUS[body.action] ?? 'draft', version: 2 }
    }, 'POST'],
    // L3 (legacy): a DELETE with a body
    [/^\/api\/v1\/test-management\/cases\/[^/]+$/, (request) => {
      recordIn(writes, request)
      return respond(204)
    }, 'DELETE'],
    // L4: the case pane's Lifecycle tab
    [/^\/api\/v1\/test-management\/cases\/[^/]+\/allowed-transitions$/, () => [
      { action: 'request_review', allowed: true, blocked_reason: null },
      { action: 'deprecate', allowed: true, blocked_reason: null },
      { action: 'archive', allowed: false, blocked_reason: 'Deprecate the case first' },
    ]],
    // The review cases offer the v2 decisions when the flag is on.
    ['/api/v1/test-management/cases', (request) => {
      const answer = delegate(TEST_CASES, request) as { items: Record<string, unknown>[] }
      if (!options.lifecycleV2) return answer
      return {
        ...answer,
        items: answer.items.map((c) =>
          c.status === 'review_requested' || c.status === 'under_review' ? { ...c, allowed_actions: V2_REVIEW } : c,
        ),
      }
    }],
    // L5, L6: the Plans tab
    ['/api/v1/test-management/plans', (request) => {
      const body = recordIn(writes, request) as Record<string, unknown>
      const row = { ...body, id: NEW_PLAN_ID, created_at: isoAgo(0), updated_at: isoAgo(0) }
      plans.unshift(row)
      return respond(201, row)
    }, 'POST'],
    ['/api/v1/test-management/plans', () => ({ items: plans, total: plans.length, page: 1, size: 10, pages: 1 })],
    [/^\/api\/v1\/test-management\/plans\/[^/]+\/items$/, (request) => {
      const body = recordIn(writes, request) as { test_case_id: string }
      const planId = request.path.split('/')[5]
      const list = items[planId] ?? []
      const item = {
        id: `9b000000-0000-4000-8000-00000000000${list.length + 1}`,
        plan_id: planId,
        test_case_id: body.test_case_id,
        order_index: list.length,
        priority_override: null,
        execution_status: 'not_run',
        executed_by_id: null,
        executed_at: null,
        execution_notes: null,
        actual_duration_minutes: null,
        created_at: isoAgo(0),
      }
      items[planId] = [...list, item]
      return respond(201, item)
    }, 'POST'],
    [/^\/api\/v1\/test-management\/plans\/[^/]+\/items$/, (request) => items[request.path.split('/')[5]] ?? []],
    [/^\/api\/v1\/test-management\/suites\/[^/]+\/cases$/, (request) => {
      const name = decodeURIComponent(request.path.split('/')[5])
      const rows = name === 'Checkout' ? CHECKOUT_SUITE_CASES : []
      return {
        items: rows.map((r) => ({ ...r, suite_name: name, status: 'active', lifecycle_status: r.source === 'manual' ? 'active' : undefined, duration_ms: null, class_name: null, package_name: null, created_at: isoAgo(9) })),
        total: rows.length,
        page: 1,
        pages: 1,
        size: 500,
      }
    }],
    // L7: the Suites tab's create
    ['/api/v1/suites', (request) => {
      const body = recordIn(writes, request) as Record<string, unknown>
      return respond(201, { ...body, id: '5a000000-0000-4000-8000-0000000000f1', is_default: false, test_case_count: 0, created_at: isoAgo(0), updated_at: null })
    }, 'POST'],
    // L8
    ['/api/v1/test-management/strategies/ai-generate/async', (request) => {
      recordIn(writes, request)
      return respond(202, { task_id: 'task-w27b-strategy-1', status: 'queued' })
    }, 'POST'],
    // The members (L1's assignees, L7's owner candidates)
    [`/api/v1/projects/${PROJECT_ID}/members`, () => tmMembers()],
    // L11: knowledge generation is on
    ['/api/v1/test-management/rag/status', () => ({ enabled: true })],
    ['/api/v1/knowledge-sources', () => ({ items: [RAG_SOURCE], total: 1, page: 1, page_size: 20 })],
    ...TEST_CASES,
  ]
  return { handlers, writes, plans, items }
}

// ── The lifecycle hosts (PAN-05a, L12-L15) ─────────────────────────────────

/** Two deleted automations waiting for a human reason (Cases tab › Unconfirmed automation retirements). */
export const ORPHANS = [
  { id: '88888888-8888-4888-8888-0000000000d1', test_name: 'checkout applies the legacy coupon' },
  { id: '88888888-8888-4888-8888-0000000000d2', test_name: 'checkout shows the old banner' },
]
/** The server's refusal of a retirement confirmation (409), shown inline AND as a toast. */
export const RETIREMENT_REFUSAL = 'This automation reappeared in a newer run; refresh before confirming.'

/** The canonical case `/canonical-test-cases/:id` shows, linked to a managed case (L13). */
export const CANONICAL_ID = '88888888-8888-4888-8888-0000000000a1'
export const CANONICAL_NAME = 'login keeps the session after refresh'
export const LINKED_MANAGED_ID = '0000000a-aaaa-4000-8000-0000000000a1'

const canonicalRow = (overrides: Record<string, unknown> = {}) => ({
  id: CANONICAL_ID,
  project_id: PROJECT_ID,
  test_suite_id: SUITE_ID,
  test_suite_name: 'Auth',
  test_fingerprint: 'fp-auth-session-refresh',
  test_name: CANONICAL_NAME,
  class_name: 'AuthSpec',
  status: 'active',
  source: 'linked',
  first_seen_run_id: null,
  last_seen_run_id: null,
  last_seen_test_case_id: null,
  deleted_at_run_id: null,
  managed_test_case_id: LINKED_MANAGED_ID,
  review_tag: null,
  tags: null,
  run_count: 0,
  created_at: isoAgo(40),
  updated_at: isoAgo(2),
  ...overrides,
})

export interface LifecycleHostsOptions {
  /** Answer the first retirement confirmation with a 409 (`RETIREMENT_REFUSAL`). */
  refuseFirstRetirement?: boolean
  /** `test_case_lifecycle_v2` on: the inbox's review cases offer Claim review / Approve / Request changes / Reject. */
  lifecycleV2?: boolean
}

/**
 * The `TransitionReasonDialog` hosts PAN-05a moves to their control, with the writes they send: the Cases tab's
 * orphaned retirements (`TEST_CASES` with two orphans; the card's open is tracked by `POST /onboarding/track`,
 * answered and recorded in `tracked`), the canonical case page (`SUITE_DETAIL` for its suite) and the inbox's
 * test-case approvals (`inboxHandlers()`).
 */
export function lifecycleHostsWorld(options: LifecycleHostsOptions = {}) {
  const writes: RecordedWrite[] = []
  const tracked: unknown[] = []
  const state = { retirements: 0, confirmed: new Set<string>() }
  const orphanRows = () =>
    ORPHANS.filter((o) => !state.confirmed.has(o.id)).map((o) => ({
      ...canonicalRow({ id: o.id, test_name: o.test_name, managed_test_case_id: null, source: 'execution', status: 'deleted' }),
      deleted_observed_at: isoAgo(3),
    }))
  const V2_REVIEW = ['claim_review', 'approve', 'request_changes', 'reject']
  const inbox = inboxHandlers()
  const handlers: ApiHandlers = [
    // L12
    [/^\/api\/v1\/canonical-test-cases\/[^/]+\/confirm-retirement$/, (request) => {
      recordIn(writes, request)
      state.retirements += 1
      if (options.refuseFirstRetirement && state.retirements === 1) return respond(409, { detail: RETIREMENT_REFUSAL })
      const id = request.path.split('/')[4]
      state.confirmed.add(id)
      return { ...orphanRows()[0], id, retirement_confirmed_at: isoAgo(0) }
    }, 'POST'],
    ['/api/v1/canonical-test-cases/orphaned', () => {
      const items = orphanRows()
      return { items, total: items.length, page: 1, size: 25 }
    }],
    ['/api/v1/onboarding/track', (request) => {
      tracked.push(bodyOf(request))
      return respond(204)
    }, 'POST'],
    // L13
    [`/api/v1/canonical-test-cases/${CANONICAL_ID}/managed-link`, (request) => {
      recordIn(writes, request)
      return canonicalRow({ managed_test_case_id: null, source: 'execution' })
    }, 'DELETE'],
    [`/api/v1/canonical-test-cases/${CANONICAL_ID}`, () => canonicalRow()],
    [`/api/v1/canonical-test-cases/${CANONICAL_ID}/runs`, () => ({ items: [], total: 0, ai_truncated: false })],
    // L14
    [/^\/api\/v1\/test-management\/cases\/[^/]+\/(transition|review-action)$/, (request) => {
      const body = recordIn(writes, request) as { action: string }
      const id = request.path.split('/')[5]
      const status = body.action === 'claim_review' ? 'under_review' : body.action === 'unclaim' ? 'review_requested' : 'approved'
      return { id, project_id: PROJECT_ID, title: `Checkout case ${id}`, test_type: 'functional', priority: 'high', status, version: 2 }
    }, 'POST'],
    ['/api/v1/test-management/cases', (request) => {
      if (request.url.searchParams.has('status')) {
        const answer = delegate(inbox, request) as { items: Record<string, unknown>[] }
        return options.lifecycleV2 ? { ...answer, items: answer.items.map((c) => ({ ...c, allowed_actions: V2_REVIEW })) } : answer
      }
      return delegate(TEST_CASES, request)
    }],
    ...inbox,
    ...TEST_CASES,
    ...SUITE_DETAIL,
  ]
  return { handlers, writes, tracked }
}

// ── /trends notes, /activity, /value-metrics (PAN-05b, N3, N4, N6, N7) ─────

/** A note the server refuses to change (PATCH 403): its refusal is shown inline, as text. */
export const NOTE_REFUSAL = 'Only the author or a QA lead can edit this note.'

/**
 * `notesWorld(initial)` (`fixtures-trends-coverage.ts`) with the notes of `refused` answered 403
 * (`NOTE_REFUSAL`) on PATCH, recorded in the same `writes`.
 */
export function refusingNotesWorld(world: { handlers: ApiHandlers; writes: RecordedWrite[] }, refused: readonly string[]): ApiHandlers {
  return [
    [/^\/api\/v1\/projects\/[^/]+\/timeline-notes\/[^/]+$/, (request) => {
      if (!refused.some((id) => request.path.endsWith(`/${id}`))) return delegate(world.handlers, request)
      world.writes.push({ line: `PATCH ${request.path}`, body: bodyOf(request) })
      return respond(403, { detail: NOTE_REFUSAL })
    }, 'PATCH'],
    ...world.handlers,
  ]
}

const activityEvent = (id: string, summary: string, eventType: string, minutesAgo: number, extra: Record<string, unknown> = {}) => ({
  id,
  project_id: PROJECT_ID,
  release_id: null,
  occurred_at: new Date(Date.parse(isoAgo(0)) - minutesAgo * 60_000).toISOString(),
  category: 'configuration',
  event_type: eventType,
  actor: { type: 'user', id: '00000000-0000-4000-8000-0000000000b1', name: 'Priya Nair', ref: null },
  entity: { type: 'project', id: PROJECT_ID, label: 'Checkout', href: null },
  target: null,
  summary,
  context: null,
  has_diff: false,
  source: null,
  group_key: null,
  ...extra,
})

/** The feed `/activity` lists (newest first); `ev-gone` was purged (its detail answers 404). */
export const ACTIVITY_EVENTS = [
  activityEvent('ev-flag-1', 'Feature flag defect_commander turned on', 'feature_flag.updated', 5, {
    has_diff: true,
    context: { flag: 'defect_commander' },
    source: { table: 'audit_logs', id: 'al-1' },
  }),
  activityEvent('ev-retention-2', 'Retention policy changed to 90 days', 'retention.updated', 60),
  activityEvent('ev-gone', 'API key ci-uploader revoked', 'api_key.revoked', 180),
]

/** `/activity` with a three-event feed and each event's detail (`GET /projects/{p}/activity/{id}`; `ev-gone` → 404). */
export function activityInPageWorld() {
  const handlers: ApiHandlers = [
    [`/api/v1/projects/${PROJECT_ID}/activity`, ({ url }) => ({
      items: ACTIVITY_EVENTS,
      next_cursor: null,
      ledger_started_at: isoAgo(60),
      window: { since: url.searchParams.get('since'), until: null },
    })],
    [/^\/api\/v1\/projects\/[^/]+\/activity\/[^/]+$/, (request) => {
      const id = request.path.split('/').pop()
      const event = ACTIVITY_EVENTS.find((e) => e.id === id)
      if (!event || id === 'ev-gone') return respond(404, { detail: 'Activity event not found' })
      return {
        ...event,
        diff: event.has_diff ? { before: { enabled_global: false }, after: { enabled_global: true }, changed_fields: ['enabled_global'] } : null,
      }
    }],
    [
      '/api/v1/activity/event-types',
      () => ({ categories: ['runs', 'release', 'configuration'], actor_types: ['user', 'api_key', 'system'], entity_types: ['project'], events: {} }),
    ],
    ...LAYOUT,
  ]
  return { handlers }
}

/** `GET /api/v1/value-metrics/methodology` (the methodology the hero's "How is this calculated?" reads once). */
export const METHODOLOGY = {
  version: 3,
  legs: [
    {
      key: 'triage',
      title: 'Triage time saved',
      formula: 'auto-classified failures × triage minutes per failure',
      inputs: ['auto-classified failures'],
      caveats: ['Counts only failures the AI classified with confidence ≥ 0.7'],
    },
    { key: 'blocked', title: 'Blocked runs unblocked', formula: 'unblocked runs × blocked-run wait', inputs: [], caveats: [] },
  ],
  defaults: { triage_minutes_per_failure: 6, blocked_run_wait_minutes: 30, defect_filing_minutes: 10 },
  research_notes: ['Minutes per failure from the 2025 triage study'],
}
export const METHODOLOGY_HANDLERS: ApiHandlers = [['/api/v1/value-metrics/methodology', () => METHODOLOGY]]

// ── Saved views on a report page (PAN-07, N12) ─────────────────────────────

const reportView = (id: string, name: string, userId: string, over: Record<string, unknown> = {}) => ({
  id,
  user_id: userId,
  project_id: PROJECT_ID,
  name,
  description: null,
  page: 'trends',
  filters: { kind: 'report_view', page: 'trends', window: 14 },
  is_shared: false,
  is_default: false,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: null,
  ...over,
})
/** Trends' Views: my view (Delete offered) and a teammate's shared one (no Delete). */
export const MY_VIEW = reportView('view-mine-1', 'Payments release watch', USER.id)
export const SHARED_VIEW = reportView('view-shared-1', 'Team weekly', '00000000-0000-4000-8000-0000000000b2', { is_shared: true })

/** `GET /api/v1/saved-views?page=` over `MY_VIEW` and `SHARED_VIEW`; `DELETE /api/v1/saved-views/{id}` (recorded) removes one. */
export function savedViewsDeleteWorld() {
  const writes: RecordedWrite[] = []
  const rows = [MY_VIEW, SHARED_VIEW]
  const handlers: ApiHandlers = [
    ['/api/v1/saved-views', ({ url }) => rows.filter((row) => row.page === url.searchParams.get('page'))],
    [/^\/api\/v1\/saved-views\/[^/]+$/, (request) => {
      recordIn(writes, request)
      const at = rows.findIndex((row) => request.path.endsWith(`/${row.id}`))
      if (at < 0) return respond(404, { detail: 'Saved view not found' })
      rows.splice(at, 1)
      return respond(204)
    }, 'DELETE'],
  ]
  return { handlers, writes, rows }
}

// ── /projects, /ownership, /users, the key pages, flags, billing, project data (PAN-05c, PAN-06a) ──

/** A second project of the instance (the Checkout fixture project stays active): the one P2 edits and P3 archives. */
export const LEGACY_PROJECT = {
  id: '11111111-1111-4111-8111-1111111111a2',
  name: 'Legacy portal',
  slug: 'legacy-portal',
  description: '',
  is_active: true,
  created_at: '2026-01-07T09:00:00Z',
}
/** The id the next created project gets. */
export const NEW_PROJECT_ID = '11111111-1111-4111-8111-1111111111f1'

/** `/projects`: `GET /api/v1/projects` over the fixture project and `LEGACY_PROJECT`, with create / update / archive. */
export function projectsInPageWorld() {
  const writes: RecordedWrite[] = []
  const projects: Record<string, unknown>[] = [
    ...(delegate(LAYOUT, fakeGet('/api/v1/projects')) as Record<string, unknown>[]),
    { ...LEGACY_PROJECT },
  ]
  const idOf = (request: ApiRequest) => request.path.split('/')[4]
  const handlers: ApiHandlers = [
    ['/api/v1/projects', (request) => {
      const body = recordIn(writes, request) as Record<string, unknown>
      const row = { id: NEW_PROJECT_ID, is_active: true, created_at: isoAgo(0), description: '', ...body }
      projects.push(row)
      return respond(201, row)
    }, 'POST'],
    [/^\/api\/v1\/projects\/[^/]+$/, (request) => {
      const body = recordIn(writes, request) as Record<string, unknown>
      const row = projects.find((p) => p.id === idOf(request))
      if (!row) return respond(404, { detail: 'Project not found' })
      Object.assign(row, body)
      return row
    }, 'PUT'],
    [/^\/api\/v1\/projects\/[^/]+$/, (request) => {
      recordIn(writes, request)
      const at = projects.findIndex((p) => p.id === idOf(request))
      if (at < 0) return respond(404, { detail: 'Project not found' })
      projects.splice(at, 1)
      return respond(204)
    }, 'DELETE'],
    ['/api/v1/projects', () => projects],
    ...LAYOUT,
  ]
  return { handlers, writes, projects }
}

const ownershipRuleRow = (id: string, type: string, pattern: string, team: string, priority: number) => ({
  id,
  project_id: PROJECT_ID,
  match_type: type,
  match_pattern: pattern,
  service_name: null,
  team_name: team,
  team_contact: null,
  priority,
  is_active: true,
  created_by: null,
  created_at: isoAgo(20),
  updated_at: null,
})
/** `/ownership`'s two hand-authored rules (O1 deletes the second). */
export const OWNERSHIP_RULE_ROWS = [
  ownershipRuleRow('88888888-8888-4888-8888-0000000002a1', 'suite', 'payments*', 'Payments', 1),
  ownershipRuleRow('88888888-8888-4888-8888-0000000002a2', 'suite', 'search*', 'Search', 2),
]
/** What the import answers: two rules created, one CODEOWNERS rule replaced. */
export const IMPORT_RESULT = { rules_created: 2, rules_replaced: 1 }

/** `ownershipWorld()` (`fixtures-runs-failures.ts`: coverage, source roots, the layout) with a stateful rule list, its delete and the CODEOWNERS import. */
export function ownershipInPageWorld() {
  const writes: RecordedWrite[] = []
  const rules = OWNERSHIP_RULE_ROWS.map((r) => ({ ...r }))
  const RULES = `/api/v1/projects/${PROJECT_ID}/ownership/rules`
  const handlers: ApiHandlers = [
    [new RegExp(`^${RULES}/[^/]+$`), (request) => {
      recordIn(writes, request)
      const at = rules.findIndex((r) => request.path.endsWith(`/${r.id}`))
      if (at < 0) return respond(404, { detail: 'Rule not found' })
      rules.splice(at, 1)
      return respond(204)
    }, 'DELETE'],
    [RULES, () => rules],
    [`/api/v1/projects/${PROJECT_ID}/ownership/codeowners/import`, (request) => {
      recordIn(writes, request)
      return { ...IMPORT_RESULT, warnings: [] }
    }, 'POST'],
    ...ownershipWorld().handlers,
  ]
  return { handlers, writes, rules }
}

const userRow = (id: string, username: string, fullName: string | null, role: string, isActive = true) => ({
  id,
  email: `${username}@example.test`,
  username,
  full_name: fullName,
  role,
  is_active: isActive,
  created_at: '2026-02-01T00:00:00Z',
})
/** The instance's users (`/users`): the signed-in admin and three others; U1 edits Priya. */
export const UM_USERS = {
  admin: userRow('00000000-0000-4000-8000-0000000000ad', 'admin', 'Admin', 'ADMIN'),
  priya: userRow('00000000-0000-4000-8000-0000000000b1', 'priya', 'Priya Nair', 'QA_ENGINEER'),
  dana: userRow('00000000-0000-4000-8000-0000000000b2', 'dana', 'Dana Whitfield', 'QA_LEAD'),
  omar: userRow('00000000-0000-4000-8000-0000000000b3', 'omar', 'Omar Haddad', 'TESTER'),
}
/** The one-time values the fixtures answer (fixture strings, not real secrets). */
export const ONE_TIME = {
  tempPassword: 'Tmp-w27b-fixture-Zq81x',
  invitePath: '/accept-invite?token=w27b-fixture-invite-token',
  streamingKey: 'qai_w27b_fixture_streaming_key_0001',
  personalKey: 'qai_w27b_fixture_personal_key_0002',
  qaLeadPassword: 'QaLead-w27b-fixture-Pw77',
}
export const DEFAULT_QA_LEAD_EMAIL = 'qa-lead-checkout@example.test'
/** The id the next created user gets. */
export const NEW_USER_ID = '00000000-0000-4000-8000-0000000000c9'

const memberRow = (user: ReturnType<typeof userRow>, role: string, i: number) => ({
  id: `00000000-0000-4000-8000-0000000002${String(i).padStart(2, '0')}`,
  user_id: user.id,
  project_id: PROJECT_ID,
  role,
  created_at: '2026-03-01T00:00:00Z',
  email: user.email,
  username: user.username,
  full_name: user.full_name,
})

/**
 * `/users` (both tabs): the users, Priya's memberships, edit / create / invite, the Checkout project's members (Dana a
 * QA lead, Priya a QA engineer) with add / remove, and the default QA lead's password reset. `GET /api/v1/projects`
 * answers the fixture project with Dana as its default QA lead.
 */
export function usersInPageWorld() {
  const writes: RecordedWrite[] = []
  const users = Object.values(UM_USERS).map((u) => ({ ...u }))
  const members = [memberRow(UM_USERS.dana, 'QA_LEAD', 1), memberRow(UM_USERS.priya, 'QA_ENGINEER', 2)]
  const projects = (delegate(LAYOUT, fakeGet('/api/v1/projects')) as Record<string, unknown>[]).map((p) => ({
    ...p,
    default_qa_lead_user_id: UM_USERS.dana.id,
  }))
  const MEMBERS = `/api/v1/projects/${PROJECT_ID}/members`
  const handlers: ApiHandlers = [
    // U1
    [/^\/api\/v1\/users\/[^/]+$/, (request) => {
      const body = recordIn(writes, request) as Record<string, unknown>
      const row = users.find((u) => request.path.endsWith(`/${u.id}`))
      if (!row) return respond(404, { detail: 'User not found' })
      Object.assign(row, body)
      return row
    }, 'PATCH'],
    [/^\/api\/v1\/users\/[^/]+\/memberships$/, (request) =>
      request.path.includes(UM_USERS.priya.id) ? [{ project_id: PROJECT_ID, project_name: 'Checkout', role: 'QA_ENGINEER', created_at: '2026-03-01T00:00:00Z' }] : []],
    // U2
    ['/api/v1/users', (request) => {
      const body = recordIn(writes, request) as { email: string; username: string; role: string; full_name?: string }
      const row = userRow(NEW_USER_ID, body.username, body.full_name ?? null, body.role)
      users.push({ ...row, email: body.email })
      return respond(201, { ...row, email: body.email, temp_password: ONE_TIME.tempPassword })
    }, 'POST'],
    // U3
    ['/api/v1/users/invite', (request) => {
      recordIn(writes, request)
      return respond(201, { invitation_link: ONE_TIME.invitePath, expires_at: isoAgo(-7) })
    }, 'POST'],
    ['/api/v1/users', () => users],
    // M1, M2 (and U1's project add / remove, any project)
    [/^\/api\/v1\/projects\/[^/]+\/members$/, (request) => {
      const body = recordIn(writes, request) as { user_id: string; role: string }
      const user = users.find((u) => u.id === body.user_id) ?? userRow(body.user_id, 'someone', null, body.role)
      const row = { ...memberRow(user, body.role, members.length + 10), project_id: request.path.split('/')[4] }
      if (row.project_id === PROJECT_ID) members.push(row)
      return respond(201, row)
    }, 'POST'],
    [/^\/api\/v1\/projects\/[^/]+\/members\/[^/]+$/, (request) => {
      recordIn(writes, request)
      const at = members.findIndex((m) => request.path.endsWith(`/members/${m.user_id}`))
      if (at >= 0 && request.path.includes(PROJECT_ID)) members.splice(at, 1)
      return respond(204)
    }, 'DELETE'],
    [MEMBERS, () => members],
    // M3
    [`/api/v1/projects/${PROJECT_ID}/default-qa-lead/reset-password`, (request) => {
      recordIn(writes, request)
      return { user_id: '00000000-0000-4000-8000-0000000000c1', email: DEFAULT_QA_LEAD_EMAIL, password: ONE_TIME.qaLeadPassword }
    }, 'POST'],
    ['/api/v1/projects', () => projects],
    ...LAYOUT,
  ]
  return { handlers, writes, users, members }
}

const keyRow = (id: string, name: string, projectId: string | null, scopes: string[]) => ({
  id,
  name,
  key_hint: `qai_${id.slice(0, 4)}...`,
  scopes,
  project_id: projectId,
  is_active: true,
  expires_at: null,
  last_used_at: null,
  created_at: '2026-09-01T10:00:00Z',
})
export const STREAMING_KEY = keyRow('k1000000-0000-4000-8000-000000000001', 'ci-runner-prod', PROJECT_ID, ['stream:write'])
export const PERSONAL_KEY = keyRow('k2000000-0000-4000-8000-000000000002', 'my-laptop-script', null, [])

/**
 * `GET /api/v1/keys` (the project's streaming keys when asked with `project_id`, the caller's own without),
 * `POST /api/v1/keys` (a streaming key when the body names a project, a personal key otherwise: each answers its
 * `ONE_TIME` raw key once) and `DELETE /api/v1/keys/{id}`.
 */
export function keysInPageWorld() {
  const writes: RecordedWrite[] = []
  const keys = [{ ...STREAMING_KEY }, { ...PERSONAL_KEY }]
  const handlers: ApiHandlers = [
    ['/api/v1/keys', (request) => {
      const body = recordIn(writes, request) as { name: string; scopes: string[]; project_id?: string | null }
      const projectId = body.project_id ?? null
      const row = keyRow(projectId ? 'k3000000-0000-4000-8000-000000000003' : 'k4000000-0000-4000-8000-000000000004', body.name, projectId, body.scopes)
      keys.push(row)
      return respond(201, { ...row, raw_key: projectId ? ONE_TIME.streamingKey : ONE_TIME.personalKey })
    }, 'POST'],
    [/^\/api\/v1\/keys\/[^/]+$/, (request) => {
      recordIn(writes, request)
      const at = keys.findIndex((k) => request.path.endsWith(`/${k.id}`))
      if (at >= 0) keys.splice(at, 1)
      return respond(204)
    }, 'DELETE'],
    ['/api/v1/keys', ({ url }) => {
      const projectId = url.searchParams.get('project_id')
      return keys.filter((k) => (projectId ? k.project_id === projectId : k.project_id === null))
    }],
    ...LAYOUT,
  ]
  return { handlers, writes, keys }
}

/** `flagsHandlers()` with `DELETE /api/v1/feature-flags/{key}` (recorded in the same writes; the next read drops it). */
export function flagsDeleteWorld() {
  const base = flagsHandlers()
  const handlers: ApiHandlers = [
    [/^\/api\/v1\/feature-flags\/[a-z0-9_]+$/, (request) => {
      base.state.writes.push({ line: `${request.method} ${request.path}`, body: bodyOf(request) })
      const key = request.path.split('/')[4]
      const at = base.state.flags.findIndex((f) => f.key === key)
      if (at < 0) return respond(404, { detail: 'Feature flag not found' })
      base.state.flags.splice(at, 1)
      return respond(204)
    }, 'DELETE'],
    ...base.handlers,
  ]
  return { handlers, state: base.state }
}

/** What a runs reset deletes (D1). */
export const RESET_DELETED = { test_runs: 12, test_cases: 340, ai_analysis: 18 }

/** `/settings/project-data`: `POST /api/v1/projects/{id}/reset` answers the deleted counts. */
export function projectDataWorld() {
  const writes: RecordedWrite[] = []
  const handlers: ApiHandlers = [
    [/^\/api\/v1\/projects\/[^/]+\/reset$/, (request) => {
      const body = recordIn(writes, request) as { mode: string }
      return { project_id: request.path.split('/')[4], mode: body.mode, deleted: RESET_DELETED }
    }, 'POST'],
    ...LAYOUT,
  ]
  return { handlers, writes }
}

// ── Policies, search, chat, the settings pages (PAN-05d, PAN-06b) ──────────

const POLICY_DOCUMENT = {
  schema_version: 1,
  thresholds: { go_threshold: 20, no_go_threshold: 55, pass_rate_minimum: 90, pass_rate_hard_floor_factor: 0.7 },
  dimension_weights: {
    user_impact: 0.25, env_sensitivity: 0.1, reproducibility: 0.15, regression_likely: 0.2,
    hist_recurrence: 0.1, blast_radius: 0.15, diagnosis_conf: 0.05,
  },
  rules: [],
  pass_rate_bands: { orange_min: 90, yellow_min: 95, green_min: 99 },
  hard_caps: { max_p0_defects: 0, max_flaky_count: 10, max_new_failures_24h: 20 },
  kind_rules: { enabled: false },
}
const policyRow = (id: string, name: string, version: number, state: { is_active: boolean; is_draft: boolean }) => ({
  id,
  project_id: PROJECT_ID,
  version,
  name,
  description: null,
  rules: POLICY_DOCUMENT,
  ...state,
  created_by: 'admin',
  activated_by: state.is_active ? 'admin' : null,
  activated_at: state.is_active ? isoAgo(2) : null,
  created_at: isoAgo(3),
  updated_at: isoAgo(1),
})
/** A draft (Y1 publishes it) and the active policy (Y2 deactivates it). */
export const DRAFT_POLICY = policyRow('bbbbbbbb-0000-4000-8000-0000000000e1', 'Checkout gate (strict)', 5, { is_active: false, is_draft: true })
export const ACTIVE_POLICY = policyRow('bbbbbbbb-0000-4000-8000-0000000000e2', 'Checkout gate', 4, { is_active: true, is_draft: false })

/** `/policies` and `/policies/:id`: the list, one policy, publish (the draft becomes active) and deactivate. */
export function policiesInPageWorld() {
  const writes: RecordedWrite[] = []
  const rows = [{ ...DRAFT_POLICY }, { ...ACTIVE_POLICY }]
  const LIST = '/api/v1/release-gate-policies'
  const handlers: ApiHandlers = [
    [new RegExp(`^${LIST}/[^/]+/publish$`), (request) => {
      recordIn(writes, request)
      const row = rows.find((r) => r.id === request.path.split('/')[4])
      if (!row) return respond(404, { detail: 'Policy not found' })
      for (const other of rows) other.is_active = false
      Object.assign(row, { is_active: true, is_draft: false })
      return row
    }, 'POST'],
    [new RegExp(`^${LIST}/[^/]+/deactivate$`), (request) => {
      recordIn(writes, request)
      const row = rows.find((r) => r.id === request.path.split('/')[4])
      if (!row) return respond(404, { detail: 'Policy not found' })
      row.is_active = false
      return row
    }, 'POST'],
    [new RegExp(`^${LIST}/[^/]+$`), (request) => rows.find((r) => r.id === request.path.split('/')[4]) ?? respond(404, { detail: 'Policy not found' })],
    [LIST, () => rows],
    ...LAYOUT,
  ]
  return { handlers, writes, rows }
}

/** `/search` with nothing typed (the rail's three reads, `search-rail.spec.ts`' shapes). */
export const SEARCH_PAGE: ApiHandlers = [
  ['/api/v1/search/index-status', () => ({ status: 'not_configured', document_count: 0, last_indexed_at: null })],
  ['/api/v1/search/entity-counts', () => ({ test_case: 84, test_run: 32, suite: 12, defect: 5, flaky_test: 3, release: 4 })],
  ['/api/v1/search/global', ({ url }) => ({
    items: [], total: 0, query: url.searchParams.get('q') ?? '', search_type: 'keyword',
    entity_counts: { test_case: 84, test_run: 32, suite: 12, defect: 5, flaky_test: 3, release: 4 }, page: 1, size: 25, pages: 0,
  })],
  ...LAYOUT,
]
/** Two recent searches as `SearchPage` stores them (`localStorage['tl.search.recent']`). */
export const RECENT_SEARCHES = [
  { id: 'r1', query: 'checkout timeout', mode: 'hybrid', scope: 'all', resultCount: 12, ts: Date.parse('2026-09-18T11:00:00Z') },
  { id: 'r2', query: 'login flaky', mode: 'keyword', scope: 'tests', resultCount: 3, ts: Date.parse('2026-09-18T10:00:00Z') },
]

/** Two conversations of the project (Y4 deletes the open one). */
export const CHAT_SESSIONS = [
  { id: '99999999-9999-4999-8999-0000000000c1', project_id: PROJECT_ID, title: 'Why did build 105 fail?', created_at: isoAgo(1), updated_at: isoAgo(1) },
  { id: '99999999-9999-4999-8999-0000000000c2', project_id: PROJECT_ID, title: 'Flaky tests this week', created_at: isoAgo(2), updated_at: isoAgo(2) },
]

/** `/chat` (`ask_ai_chat` on): the sessions, a session's messages, `DELETE /api/v1/chat/sessions/{id}`. */
export function chatDeleteWorld() {
  const writes: RecordedWrite[] = []
  const sessions = CHAT_SESSIONS.map((s) => ({ ...s }))
  const handlers: ApiHandlers = [
    ['/api/v1/settings/ai/mode', () => ({ analysis_mode: 'llm' })],
    ['/api/v1/chat/run-summaries', () => []],
    ['/api/v1/chat/sessions', () => sessions],
    [/^\/api\/v1\/chat\/sessions\/[^/]+\/messages$/, (request) => {
      const id = request.path.split('/')[5]
      return [
        { id: `${id}-q`, session_id: id, role: 'user', content: 'Why did build 105 fail?', sources: null, created_at: isoAgo(1) },
        { id: `${id}-a`, session_id: id, role: 'assistant', content: 'Two tests failed in the Auth suite.', sources: null, created_at: isoAgo(1) },
      ]
    }],
    [/^\/api\/v1\/chat\/sessions\/[^/]+$/, (request) => {
      recordIn(writes, request)
      const at = sessions.findIndex((s) => request.path.endsWith(`/${s.id}`))
      if (at >= 0) sessions.splice(at, 1)
      return respond(204)
    }, 'DELETE'],
    ...LAYOUT,
  ]
  return { handlers, writes, sessions }
}

/** The retention page's reads (`settings-two-column-forms.spec.ts`' shapes) plus the purge and the criteria deletion. */
export const CRITERIA_PREVIEW = {
  job_id: 'del-job-w27b-1',
  project_id: PROJECT_ID,
  run_count: 3,
  run_ids: ['33333333-3333-4333-8333-0000000000a1', '33333333-3333-4333-8333-0000000000a2', '33333333-3333-4333-8333-0000000000a3'],
  candidate_hash: 'h-w27b',
  truncated: false,
  refused_prefixes: [],
  blocked: [],
}
export function retentionInPageWorld() {
  const writes: RecordedWrite[] = []
  const policy = { enabled: true, raw_events_days: 90, runs_days: 365, artifacts_days: 180, audit_days: 2555, source: 'default', last_purge: null }
  const handlers: ApiHandlers = [
    [`/api/v1/projects/${PROJECT_ID}/retention-policy/purge`, (request) => {
      recordIn(writes, request)
      return respond(202, { queued: true })
    }, 'POST'],
    [`/api/v1/projects/${PROJECT_ID}/deletion/preview`, (request) => {
      recordIn(writes, request)
      return CRITERIA_PREVIEW
    }, 'POST'],
    [`/api/v1/projects/${PROJECT_ID}/deletion/execute`, (request) => {
      recordIn(writes, request)
      return respond(202, { job_id: CRITERIA_PREVIEW.job_id, status: 'queued', run_count: CRITERIA_PREVIEW.run_count })
    }, 'POST'],
    [`/api/v1/projects/${PROJECT_ID}/retention-policy`, () => policy],
    [`/api/v1/projects/${PROJECT_ID}/storage`, () => ({
      project_id: PROJECT_ID,
      computed_at: '2026-09-18T11:00:00Z',
      stores: [{ store: 'postgres', measured: true, exact: true, complete: true, bytes: 1_048_576, items: 120, estimate_basis: null, unreachable_reason: null }],
      total_bytes: 1_048_576,
      total_is_estimate: false,
      fully_measured: true,
    })],
    ['/api/v1/admin/storage/deleted-projects', () => ({
      computed_at: '2026-09-18T11:00:00Z',
      projects_total: 0,
      projects_measured: 0,
      truncated: false,
      projects: [],
      total_bytes: 0,
      total_is_estimate: false,
      unreachable_by_retention: 0,
    })],
    ['/api/v1/auth/me/dismissals', () => ({ dismissed: [] })],
    ...LAYOUT,
  ]
  return { handlers, writes }
}

/** `digestsWorld()` (`fixtures-notify-flaky.ts`) with the two deletes: a subscription and a saved view (recorded in its writes). */
export function digestsDeleteWorld() {
  const base = digestsWorld()
  const handlers: ApiHandlers = [
    [/^\/api\/v1\/digests\/subscriptions\/[^/]+$/, (request) => {
      base.state.writes.push({ line: `${request.method} ${request.path}`, body: bodyOf(request) })
      base.state.subscriptions = base.state.subscriptions.filter((s) => !request.path.endsWith(`/${s.id}`))
      return respond(204)
    }, 'DELETE'],
    [/^\/api\/v1\/saved-views\/[^/]+$/, (request) => {
      base.state.writes.push({ line: `${request.method} ${request.path}`, body: bodyOf(request) })
      base.state.views = base.state.views.filter((v) => !request.path.endsWith(`/${v.id}`))
      return respond(204)
    }, 'DELETE'],
    ...base.handlers,
  ]
  return { handlers, state: base.state }
}

const ssoConfig = (id: string, name: string, mode: 'OPTIONAL' | 'SSO_REQUIRED', active: boolean) => ({
  id,
  display_name: name,
  provider_type: 'SAML',
  idp_entity_id: `https://idp.example.test/${id.slice(-2)}`,
  idp_sso_url: 'https://idp.example.test/sso',
  idp_slo_url: null,
  idp_certificate_fingerprint: 'ab12cd34ef56ab12cd34ef56ab12cd34',
  sp_entity_id: 'https://testlookup.example.test/sp',
  sp_acs_url: 'https://testlookup.example.test/acs',
  audience: null,
  role_mapping: null,
  default_role: 'VIEWER',
  group_attribute: null,
  enforcement_mode: mode,
  is_active: active,
  last_test_at: null,
  last_test_success: null,
  last_test_error: null,
  created_at: isoAgo(10),
  updated_at: null,
})
/** An inactive enforced config (S1 asks before activating it), an inactive optional one (activates at once), and a SCIM token. */
export const SSO_ENFORCED = ssoConfig('5500000a-0000-4000-8000-0000000000e1', 'Okta (enforced)', 'SSO_REQUIRED', false)
export const SSO_OPTIONAL = ssoConfig('5500000a-0000-4000-8000-0000000000e2', 'Azure AD (optional)', 'OPTIONAL', false)
export const SCIM_TOKEN = {
  id: '5500000b-0000-4000-8000-0000000000f1',
  name: 'okta-provisioning',
  token_hint: 'scim_ab12...',
  sso_config_id: null,
  is_active: true,
  last_used_at: null,
  expires_at: null,
  created_at: isoAgo(5),
}

/** `/settings/sso`: the configs (PATCH, DELETE), the SCIM tokens (DELETE), and the other tabs' reads. */
export function ssoInPageWorld() {
  const writes: RecordedWrite[] = []
  const configs = [{ ...SSO_ENFORCED }, { ...SSO_OPTIONAL }]
  const tokens = [{ ...SCIM_TOKEN }]
  const handlers: ApiHandlers = [
    [/^\/api\/v1\/sso\/configs\/[^/]+$/, (request) => {
      const body = recordIn(writes, request) as Record<string, unknown>
      const row = configs.find((c) => request.path.endsWith(`/${c.id}`))
      if (!row) return respond(404, { detail: 'SSO config not found' })
      Object.assign(row, body)
      return row
    }, 'PATCH'],
    [/^\/api\/v1\/sso\/configs\/[^/]+$/, (request) => {
      recordIn(writes, request)
      const at = configs.findIndex((c) => request.path.endsWith(`/${c.id}`))
      if (at >= 0) configs.splice(at, 1)
      return respond(204)
    }, 'DELETE'],
    [/^\/api\/v1\/scim-tokens\/[^/]+$/, (request) => {
      recordIn(writes, request)
      const row = tokens.find((t) => request.path.endsWith(`/${t.id}`))
      if (row) row.is_active = false
      return respond(204)
    }, 'DELETE'],
    ['/api/v1/sso/configs', () => configs],
    ['/api/v1/scim-tokens', () => tokens],
    ['/api/v1/identity-events', () => []],
    ['/api/v1/sso/sync-status', () => []],
    ...LAYOUT,
  ]
  return { handlers, writes, configs, tokens }
}

/** `/settings/github`: a stored integration and its DELETE. */
export function githubInPageWorld() {
  const writes: RecordedWrite[] = []
  const PATH = `/api/v1/projects/${PROJECT_ID}/github-integration`
  const state = {
    row: {
      id: 'gh000000-0000-4000-8000-000000000001',
      project_id: PROJECT_ID,
      enabled: true,
      repo_owner: 'acme',
      repo_name: 'web-shop',
      api_base_url: 'https://api.github.com',
      has_pat: true,
      pr_comment_mode: 'always',
      last_posted_at: null,
      last_error: null,
      last_error_at: null,
      created_at: isoAgo(10),
      updated_at: isoAgo(2),
    } as Record<string, unknown> | null,
  }
  const handlers: ApiHandlers = [
    [PATH, (request) => {
      recordIn(writes, request)
      state.row = null
      return respond(204)
    }, 'DELETE'],
    [PATH, () => state.row ?? respond(404, { detail: 'GitHub integration not configured' })],
    ...LAYOUT,
  ]
  return { handlers, writes }
}

/** `/settings/gitlab`: a stored configuration with a token, and its PUT (the token's `''` clears it). */
export const GITLAB_CONFIG = {
  enabled: true,
  base_url: 'https://gitlab.example.test',
  project_path: 'acme/web-shop',
  mr_comment_mode: 'failures_only',
  commit_status_enabled: true,
  has_token: true,
  last_error: null,
  last_error_at: null,
}
export function gitlabInPageWorld() {
  const writes: RecordedWrite[] = []
  const PATH = `/api/v1/projects/${PROJECT_ID}/integrations/gitlab`
  const state = { config: { ...GITLAB_CONFIG } }
  const handlers: ApiHandlers = [
    [PATH, (request) => {
      const body = recordIn(writes, request) as Record<string, unknown> & { token?: string }
      const { token, ...rest } = body
      state.config = { ...state.config, ...rest, has_token: token === '' ? false : state.config.has_token || !!token }
      return state.config
    }, 'PUT'],
    [PATH, () => state.config],
    ...LAYOUT,
  ]
  return { handlers, writes }
}

/** `/settings/my-notifications` with a Slack preference and its DELETE (in `notificationsWorld`'s writes). */
export const SLACK_PREF_ID = '00000000-0000-4000-8000-000000000a51'
export function notificationsDeleteWorld() {
  const base = notificationsWorld({
    preferences: [preference(SLACK_PREF_ID, 'slack', ['run_failed'], { slack_webhook_url: 'https://hooks.slack.example.test/T1/B1/x' })],
  })
  const handlers: ApiHandlers = [
    [/^\/api\/v1\/notifications\/preferences\/[^/]+$/, (request) => {
      base.state.writes.push({ line: `${request.method} ${request.path}`, body: bodyOf(request) })
      base.state.preferences = base.state.preferences.filter((p) => !request.path.endsWith(`/${p.id}`))
      return respond(204)
    }, 'DELETE'],
    ...base.handlers,
  ]
  return { handlers, state: base.state }
}

/** `/settings/webhooks`: the event catalog, one subscription and its DELETE. */
export const WEBHOOK = {
  id: 'wh000000-0000-4000-8000-000000000001',
  project_id: PROJECT_ID,
  name: 'Release bot',
  target_url: 'https://hooks.example.test/release',
  events: ['run.completed'],
  enabled: true,
  has_secret: true,
  max_retries: 3,
  last_delivered_at: null,
  last_failure_at: null,
  last_error: null,
  failure_count: 0,
  total_delivered: 4,
  created_at: isoAgo(9),
  updated_at: isoAgo(1),
}
export function webhooksInPageWorld() {
  const writes: RecordedWrite[] = []
  const rows = [{ ...WEBHOOK }]
  const handlers: ApiHandlers = [
    [/^\/api\/v1\/webhooks\/[^/]+$/, (request) => {
      recordIn(writes, request)
      const at = rows.findIndex((r) => request.path.endsWith(`/${r.id}`))
      if (at >= 0) rows.splice(at, 1)
      return respond(204)
    }, 'DELETE'],
    ['/api/v1/webhooks/events', () => ({ events: [{ type: 'run.completed', description: 'A run finished' }] })],
    ['/api/v1/webhooks', () => rows],
    ...LAYOUT,
  ]
  return { handlers, writes, rows }
}

// ── Phase 2 (the in-page shape specs, the crawl, the visual regions) ───────
// Added after phase 1 was committed: nothing above changes (the characterization specs read it).

/** An event older than the loaded feed (a deep link from the Releases rail): its detail is read, it is in no row. */
export const ACTIVITY_OLD_EVENT = {
  ...ACTIVITY_EVENTS[1],
  id: 'ev-old-9',
  summary: 'Release 2026.08 was archived',
  event_type: 'release.archived',
  category: 'release',
  occurred_at: isoAgo(40),
}

/** `/settings/team-channels`: two teams (Identity routed to Slack, Payments not), and the channel's DELETE. */
export function teamChannelsWorld() {
  const writes: RecordedWrite[] = []
  const channel = (team: string) => ({
    id: `ch-${team}`,
    project_id: PROJECT_ID,
    team_name: team,
    channel_type: 'slack',
    target: `https://hooks.slack.example.test/${team.toLowerCase()}`,
    is_active: true,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: null,
  })
  const channels = new Map([['Identity', channel('Identity')]])
  const handlers: ApiHandlers = [
    [new RegExp(`^/api/v1/projects/${PROJECT_ID}/ownership/team-channels/[^/]+$`), (request) => {
      recordIn(writes, request)
      channels.delete(decodeURIComponent(request.path.split('/').pop() ?? ''))
      return respond(204)
    }, 'DELETE'],
    [`/api/v1/projects/${PROJECT_ID}/ownership/team-channels`, () => [...channels.values()]],
    [`/api/v1/projects/${PROJECT_ID}/ownership/rules`, () => [
      ownershipRuleRow('88888888-8888-4888-8888-0000000002b1', 'suite', 'payments*', 'Payments', 1),
      ownershipRuleRow('88888888-8888-4888-8888-0000000002b2', 'suite', 'auth*', 'Identity', 2),
    ]],
    ...LAYOUT,
  ]
  return { handlers, writes }
}

/** `activityInPageWorld()` plus the detail of `ACTIVITY_OLD_EVENT` (not in the feed). */
export function activityDeepLinkWorld() {
  const base = activityInPageWorld()
  const handlers: ApiHandlers = [
    [`/api/v1/projects/${PROJECT_ID}/activity/${ACTIVITY_OLD_EVENT.id}`, () => ({ ...ACTIVITY_OLD_EVENT, diff: null })],
    ...base.handlers,
  ]
  return { handlers }
}
