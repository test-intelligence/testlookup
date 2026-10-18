/**
 * W28 "test-case editing" hermetic answers (`docs/deferred-scope-2026-10-13/work/W28-contract.md` §4.4): the worlds
 * the five W28 specs (`tests/ci-e2e/test-case-{edit,status,bulk,history}.spec.ts`, `chat-suggestions.spec.ts`) and
 * `tests/visual/prod-test-case-editing.visual.spec.ts` open. Each factory COMPOSES an existing world
 * (`testLibraryWorld()`, `projectsInPageWorld()`, `chatInPageWorld()`; never edits one) and lists its own handlers
 * FIRST (first match wins, `production-pages.ts`).
 *
 * The test library world is a small server: one mutable catalogue (`TEST_CASES`' eight cases plus a deprecated
 * ninth, with steps, owners and authors), and every read answers it AS THE SIGNED-IN USER sees it: `allowed_actions`,
 * `reason_required_actions` and the Lifecycle tab's availability follow the lifecycle rules of contract §1.2 as the
 * AMENDMENT keeps them (Deprecate / Archive / Reinstate are QA_LEAD or above, a VIEWER changes nothing, review off
 * adds Draft / Needs update → Active and Active → Draft, review on adds Request review for an edited-after-approval
 * case and a lead's self-approval with a reason). Writes change the catalogue, so the re-reads after a save, a move,
 * a bulk edit or a restore show what the server would. Every write is recorded (`writes`: `METHOD path` and the
 * parsed JSON body), so a spec pins the whole body with `toEqual`.
 *
 * Not a spec file.
 */
import { respond, respondRaw, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { isoAgo, USER } from './fixtures'
import { bodyOf, CHAT_SESSION_ID, chatInPageWorld, delegate, type RecordedWrite } from './fixtures-in-page'
import { fakeGet, projectsInPageWorld, recordIn, testLibraryWorld, TM_PLAN_ID, TM_PLAN_NAME, tmCaseId, tmCaseTitle } from './fixtures-in-page-b'
import { RELEASE_PEOPLE, TEST_CASES } from './fixtures-pages'

export type { RecordedWrite }
export { ADMIN, QA_ENGINEER, VIEWER } from './fixtures-releases'

/** The role between a viewer and a QA engineer (contract §4.4): creates and edits cases, never retires one. */
export const TESTER = { ...USER, id: '00000000-0000-4000-8000-0000000000e3', username: 'tester', full_name: 'Tester', role: 'TESTER' }

/** A signed-in fixture user (`USER`, `TESTER`, `QA_ENGINEER`, `VIEWER`, `ADMIN`). */
export type FixtureUser = typeof USER

const RANK: Record<string, number> = { VIEWER: 0, TESTER: 1, QA_ENGINEER: 2, QA_LEAD: 3, ADMIN: 4 }
const rankOf = (user: { role: string }) => RANK[String(user.role)] ?? 0

// ── The people ─────────────────────────────────────────────────────────────

/** Who changed the case while the reader had it open (the 409 world), and who wrote v2 of the history case. */
export const OTHER_EDITOR = { id: '00000000-0000-4000-8000-0000000000f5', name: 'Priya Shah' }
/** The API key owner whose key wrote v3 of the history case (source `api`). */
export const API_EDITOR = { id: RELEASE_PEOPLE.dana.id, name: RELEASE_PEOPLE.dana.name }
/** The member the edit and bulk specs make the owner (`tmMembers()` lists her). */
export const NEW_OWNER = { id: RELEASE_PEOPLE.dana.id, name: RELEASE_PEOPLE.dana.name }

// ── The catalogue ──────────────────────────────────────────────────────────

/** The case the edit specs open: a Draft with two steps, a masked and a plain parameter, one tag (case 2). */
export const EDIT_CASE = 1
/** The Drafts and the Approved case the bulk spec selects (cases 2, 8 and 4), in list order. */
export const BULK_CASES = [1, 3, 7] as const
/** A Draft the signed-in QA lead (`USER`) wrote: their own case, for the self-approval (case 8). */
export const OWN_CASE = 7
/** The Active case that can be "edited after approval" (review on) and that a plan executed at v1 (case 1, now v2). */
export const ACTIVE_CASE = 0
/** The Active, AI-generated case with three versions: by the AI generator, `Priya Shah`, and the API (case 5, now v3). */
export const HISTORY_CASE = 4
/** A Deprecated case (case 9): Edit is disabled on it. */
export const DEPRECATED_CASE = 8

export interface StepFixture {
  step_number: number
  action: string
  expected_result?: string
}

/** Every case's steps unless it has its own. */
export const CASE_STEPS: StepFixture[] = [
  { step_number: 1, action: 'Add an item to the cart', expected_result: 'The cart badge shows 1' },
  { step_number: 2, action: 'Refresh the page', expected_result: 'The cart still holds the item' },
]

/** The steps the plan item ran (`ACTIVE_CASE` v1) and the ones the case has now (v2). */
export const RAN_STEPS: StepFixture[] = [
  { step_number: 1, action: 'Open the cart', expected_result: 'The cart is empty' },
  { step_number: 2, action: 'Add the blue mug', expected_result: 'The badge shows 1' },
]
export const NOW_STEPS: StepFixture[] = [
  { step_number: 1, action: 'Open the cart', expected_result: 'The cart is empty' },
  { step_number: 2, action: 'Add the blue mug and the red mug', expected_result: 'The badge shows 2' },
]

/** `HISTORY_CASE`'s versions: v1 (the AI generator), v2 (`Priya Shah`: steps and expected result), v3 (the API: tags, priority). */
export const HISTORY_V1 = {
  steps: CASE_STEPS,
  expected_result: 'The cart is unchanged after a refresh',
  tags: ['cart'],
  priority: 'critical',
}
export const HISTORY_V2 = {
  steps: [
    { step_number: 1, action: 'Add an item to the cart', expected_result: 'The cart badge shows 1' },
    { step_number: 2, action: 'Refresh the page twice', expected_result: 'The cart still holds the item' },
    { step_number: 3, action: 'Sign out and back in', expected_result: 'The cart still holds the item' },
  ],
  expected_result: 'The cart is unchanged after a refresh and a re-login',
}
export const HISTORY_V3 = { tags: ['cart', 'regression'], priority: 'high' }

/** The title `OTHER_EDITOR` saved while the reader had the case open (the 409 world's v3). */
export const THEIR_TITLE = 'Checkout case 2: the cart survives a refresh and a new tab'

/** The case fields a version snapshots (the backend's `RESTORABLE_FIELDS`, plus the status and the owner). */
const RESTORABLE_FIELDS = [
  'title', 'description', 'objective', 'preconditions', 'steps', 'parameters', 'expected_result', 'test_data',
  'test_type', 'priority', 'severity', 'feature_area', 'suite_name', 'tags', 'estimated_duration_minutes',
  'is_automated', 'automation_status',
] as const

type Row = Record<string, unknown>

/** The people's names, as `changed_by_name` and a case's `owner` read them (full name). */
const AUTHOR_NAMES: Record<string, string> = {
  [RELEASE_PEOPLE.priya.id]: RELEASE_PEOPLE.priya.name,
  [RELEASE_PEOPLE.dana.id]: RELEASE_PEOPLE.dana.name,
  [USER.id]: USER.full_name,
  [TESTER.id]: TESTER.full_name,
}

const json = (value: unknown) => JSON.stringify(value ?? null)
const caseIdOf = (request: ApiRequest) => request.path.split('/')[5]

/** The catalogue a world starts from: fresh per call. */
function seedCases(): Row[] {
  const base = (delegate(TEST_CASES, fakeGet('/api/v1/test-management/cases?size=200')) as { items: Row[] }).items
  const ninth: Row = {
    ...base[1],
    id: tmCaseId(DEPRECATED_CASE),
    title: tmCaseTitle(DEPRECATED_CASE),
    status: 'deprecated',
    priority: 'low',
    deprecation_reason: 'Replaced by case 2',
    created_at: isoAgo(50),
    updated_at: isoAgo(9),
  }
  return [...base, ninth].map((c, i): Row => {
    const row: Row = {
      ...c,
      description: 'The cart keeps its items when the shopper refreshes the page.',
      objective: 'Prove the cart survives a refresh',
      preconditions: 'A signed-in shopper with an empty cart',
      steps: CASE_STEPS.map((s) => ({ ...s })),
      parameters: [],
      expected_result: 'The cart is unchanged after a refresh',
      test_data: null,
      feature_area: 'Cart',
      tags: ['cart'],
      estimated_duration_minutes: 5,
      author_id: RELEASE_PEOPLE.priya.id,
      assignee_id: null,
      reviewer_id: null,
      edited_since_approval_version: null,
    }
    if (i === EDIT_CASE) {
      row.author_id = TESTER.id
      row.parameters = [
        { name: 'card_number', value: null, masked: true },
        { name: 'region', value: 'EU', masked: false },
      ]
    }
    if (i === OWN_CASE) row.author_id = USER.id
    if (i === ACTIVE_CASE) Object.assign(row, { steps: NOW_STEPS.map((s) => ({ ...s })), version: 2 })
    if (i === HISTORY_CASE) {
      Object.assign(row, { ...HISTORY_V2, ...HISTORY_V3, ai_generated: true, version: 3 })
    }
    // The under-review case is claimed by someone else (the Approvals tab's decisions stay theirs).
    if (row.status === 'under_review') row.reviewer_id = RELEASE_PEOPLE.dana.id
    return row
  })
}

// ── The lifecycle rules (contract §1.2 with the AMENDMENT) ─────────────────

const ACTIONS = [
  'request_review', 'claim_review', 'withdraw_review', 'unclaim', 'approve', 'reject', 'request_changes',
  'activate', 'flag_stale', 'revise', 'deprecate', 'reinstate', 'archive',
] as const
type Action = (typeof ACTIONS)[number]

const ALLOWED_TRANSITIONS: Record<Action, Record<string, string>> = {
  request_review: { draft: 'review_requested', rejected: 'review_requested' },
  claim_review: { review_requested: 'under_review' },
  withdraw_review: { review_requested: 'draft' },
  unclaim: { under_review: 'review_requested' },
  approve: { under_review: 'approved' },
  reject: { under_review: 'rejected' },
  request_changes: { under_review: 'draft' },
  activate: { approved: 'active' },
  flag_stale: { approved: 'needs_update', active: 'needs_update' },
  revise: { rejected: 'draft', needs_update: 'draft' },
  deprecate: { draft: 'deprecated', rejected: 'deprecated', approved: 'deprecated', active: 'deprecated', needs_update: 'deprecated' },
  reinstate: { deprecated: 'draft', archived: 'draft' },
  archive: { deprecated: 'archived' },
}
const DIRECT_TRANSITIONS: Partial<Record<Action, Record<string, string>>> = {
  activate: { draft: 'active', needs_update: 'active' },
  revise: { active: 'draft' },
}
const EDITED_AFTER_APPROVAL_TRANSITIONS: Partial<Record<Action, Record<string, string>>> = {
  request_review: { approved: 'review_requested', active: 'review_requested' },
}
const REASON_REQUIRED = new Set<Action>(['flag_stale', 'deprecate', 'reinstate', 'archive'])
const DECISIONS = new Set<Action>(['approve', 'reject', 'request_changes'])
/** The AMENDMENT: the retire moves stay QA_LEAD or above. */
const RETIRE = new Set<Action>(['deprecate', 'reinstate', 'archive'])

const isLead = (actor: FixtureUser) => rankOf(actor) >= RANK.QA_LEAD
const editedMarker = (c: Row, reviewRequired: boolean) => reviewRequired && c.edited_since_approval_version != null

function tableFor(c: Row, reviewRequired: boolean): Record<string, Record<string, string>> {
  const table: Record<string, Record<string, string>> = {}
  for (const action of ACTIONS) table[action] = { ...ALLOWED_TRANSITIONS[action] }
  const extra = !reviewRequired ? DIRECT_TRANSITIONS : editedMarker(c, reviewRequired) ? EDITED_AFTER_APPROVAL_TRANSITIONS : {}
  for (const [action, bySource] of Object.entries(extra)) Object.assign(table[action], bySource)
  return table
}

/** A QA lead or admin approving a case they wrote: from Review requested, or Under review when they hold the claim. */
function isSelfApproval(c: Row, actor: FixtureUser): boolean {
  if (c.author_id !== actor.id || !isLead(actor)) return false
  return c.status === 'review_requested' || (c.status === 'under_review' && c.reviewer_id === actor.id)
}

function targetOf(c: Row, actor: FixtureUser, action: Action, reviewRequired: boolean): string | null {
  const target = tableFor(c, reviewRequired)[action]?.[String(c.status)]
  if (!target && action === 'approve' && c.status === 'review_requested' && isSelfApproval(c, actor)) return 'approved'
  return target ?? null
}

/** `allowed_actions_for`: what this actor may attempt on this case now, in the table's order. */
export function allowedActionsFor(c: Row, actor: FixtureUser, reviewRequired: boolean): Action[] {
  if (rankOf(actor) < RANK.TESTER) return []
  const out: Action[] = []
  for (const action of ACTIONS) {
    if (!targetOf(c, actor, action, reviewRequired)) continue
    if ((action === 'claim_review' || DECISIONS.has(action)) && rankOf(actor) < RANK.QA_ENGINEER) continue
    if (RETIRE.has(action) && !isLead(actor)) continue
    if (action === 'approve' && isSelfApproval(c, actor)) {
      out.push(action)
      continue
    }
    if (DECISIONS.has(action) && c.reviewer_id !== actor.id) continue
    if (action === 'unclaim' && !(c.reviewer_id === actor.id || isLead(actor))) continue
    if (action === 'approve' && c.author_id === actor.id) continue
    if (action === 'withdraw_review' && !(c.author_id === actor.id || isLead(actor))) continue
    out.push(action)
  }
  return out
}

function reasonRequiredFor(c: Row, actor: FixtureUser, reviewRequired: boolean): Action[] {
  const self = isSelfApproval(c, actor)
  return allowedActionsFor(c, actor, reviewRequired).filter((a) => REASON_REQUIRED.has(a) || (a === 'approve' && self))
}

/** `GET …/allowed-transitions`: every action, with why a refused one is refused (the exact strings, §1.2). */
export function availabilityFor(c: Row, actor: FixtureUser, reviewRequired: boolean) {
  const allowed = new Set<Action>(allowedActionsFor(c, actor, reviewRequired))
  const viewer = rankOf(actor) < RANK.TESTER
  const self = isSelfApproval(c, actor)
  return ACTIONS.map((action) => {
    const ok = allowed.has(action)
    let blocked: string | null = null
    if (!ok) {
      if (viewer) blocked = 'Viewers cannot change test cases'
      else if (!targetOf(c, actor, action, reviewRequired)) {
        blocked = reviewRequired && action === 'activate' && (c.status === 'draft' || c.status === 'needs_update')
          ? 'Review is required in this project: request a review first'
          : `Not available from ${String(c.status)}`
      } else if (action === 'approve' && c.author_id === actor.id) blocked = 'Authors cannot approve their own test cases'
      else if (DECISIONS.has(action)) blocked = 'Only the current claimant may perform this action'
      else if (action === 'unclaim') blocked = 'Only the current claimant or a lead may unclaim'
      else if (RETIRE.has(action)) blocked = 'Requires QA_LEAD or ADMIN'
      else blocked = 'Insufficient permissions'
    }
    return { action, allowed: ok, blocked_reason: blocked, requires_reason: REASON_REQUIRED.has(action) || (action === 'approve' && self) }
  })
}

// ── The test library world ─────────────────────────────────────────────────

export interface TestCaseEditingOptions {
  /** The signed-in user the answers are computed for (default the QA lead, `USER`). */
  user?: FixtureUser
  /** The project's "Require review before Active" (default false, OR-04). */
  reviewRequired?: boolean
  /** `test_case_lifecycle_v2` is on (the spec turns the flag on too): the answers carry `allowed_actions`. */
  lifecycleV2?: boolean
  /** `ACTIVE_CASE` was edited after its approval (its marker is its current version). */
  editedAfterApproval?: boolean
  /** The first PATCH of `EDIT_CASE` answers 409: `OTHER_EDITOR` saved v2 and v3 (`THEIR_TITLE`) meanwhile. */
  conflictOnFirstPatch?: boolean
  /** Case ids the bulk endpoint answers `skipped` (changed since they were selected). */
  bulkSkips?: readonly string[]
  /** The plan holds three items: `ACTIVE_CASE` executed at v1 (now v2), one never run, one run before 0222. */
  planItems?: boolean
}

/** A version row as `GET …/history` answers it (W28: name, source, owner, executions). */
interface VersionRow extends Row {
  version: number
}

/**
 * `Checkout`'s suite cases with `lifecycle_status` (the W28 field Link Suite filters on): two Active manual cases
 * (active, approved), an automation row (never linked), a Draft (linked only when asked) and a Deprecated one (never).
 */
export const SUITE_CASES = [
  { id: 'c1000000-0000-4000-8000-000000000001', test_name: 'cart keeps items after refresh', source: 'manual', lifecycle_status: 'active' },
  { id: 'c1000000-0000-4000-8000-000000000002', test_name: 'cart shows the saved coupon', source: 'automation' },
  { id: 'c1000000-0000-4000-8000-000000000003', test_name: 'cart total includes tax', source: 'manual', lifecycle_status: 'approved' },
  { id: 'c1000000-0000-4000-8000-000000000004', test_name: 'cart merges a guest basket', source: 'manual', lifecycle_status: 'draft' },
  { id: 'c1000000-0000-4000-8000-000000000005', test_name: 'cart shows the old banner', source: 'manual', lifecycle_status: 'deprecated' },
]

/** The plan items of `planItems` (the version column's three states). */
export const PLAN_ITEM_IDS = {
  ran: '9b000000-0000-4000-8000-0000000000a1',
  notRun: '9b000000-0000-4000-8000-0000000000a2',
  unrecorded: '9b000000-0000-4000-8000-0000000000a3',
}

/**
 * `/test-management` as a W28 server answers it: `testLibraryWorld()` (plans, members, AI generation, the suites)
 * with the catalogue above and its writes: `PATCH …/cases/{id}` (the edit, the stale-version 409),
 * `POST …/cases/{id}/transition`, `POST …/cases/bulk`, `POST …/cases/{id}/restore`, and the reads they need
 * (`GET …/cases/{id}`, `…/history`, `…/allowed-transitions`, `…/comments`, `…/reviews`).
 */
export function testCaseEditingWorld(options: TestCaseEditingOptions = {}) {
  const actor = options.user ?? USER
  const reviewRequired = options.reviewRequired ?? false
  const lifecycleV2 = options.lifecycleV2 ?? false
  const base = testLibraryWorld({ lifecycleV2 })
  const writes = base.writes
  const cases = seedCases()
  const state = { patches: 0 }
  const find = (id: string) => cases.find((c) => c.id === id)
  if (options.editedAfterApproval) {
    const active = cases[ACTIVE_CASE]
    active.edited_since_approval_version = active.version
  }

  // The history, oldest first.
  const snapshot = (c: Row, over: Row = {}): Row => {
    const out: Row = {}
    for (const field of RESTORABLE_FIELDS) out[field] = c[field] ?? null
    return { ...out, status: c.status, assignee_id: c.assignee_id ?? null, ...over }
  }
  const versionRow = (c: Row, version: number, over: Row): VersionRow => ({
    // The case id with its last 12 characters replaced by the version: one stable id per version.
    id: `${String(c.id).slice(0, 24)}${String(version).padStart(12, '0')}`,
    test_case_id: c.id,
    version,
    ...snapshot(c),
    changed_fields: null,
    changed_by_id: c.author_id ?? null,
    changed_by_name: AUTHOR_NAMES[String(c.author_id)] ?? null,
    change_summary: null,
    change_type: version === 1 ? 'created' : 'updated',
    source: 'person',
    executions: [],
    created_at: isoAgo(30 - version),
    ...over,
  })
  const history: Record<string, VersionRow[]> = {}
  for (const c of cases) {
    if (c === cases[ACTIVE_CASE]) {
      history[String(c.id)] = [
        versionRow(c, 1, {
          steps: RAN_STEPS,
          change_summary: 'Created',
          executions: options.planItems
            ? [{ plan_id: TM_PLAN_ID, plan_name: TM_PLAN_NAME, item_id: PLAN_ITEM_IDS.ran, execution_status: 'passed', executed_at: isoAgo(3) }]
            : [],
        }),
        versionRow(c, 2, { steps: NOW_STEPS, changed_fields: ['steps'], change_summary: 'Two mugs' }),
      ]
    } else if (c === cases[HISTORY_CASE]) {
      history[String(c.id)] = [
        versionRow(c, 1, { ...HISTORY_V1, change_summary: 'Generated from the checkout requirements', source: 'ai', changed_by_id: USER.id, changed_by_name: USER.full_name, changed_fields: ['title', 'steps', 'expected_result'] }),
        versionRow(c, 2, { ...HISTORY_V2, tags: HISTORY_V1.tags, priority: HISTORY_V1.priority, change_summary: 'Covers the re-login path', changed_by_id: OTHER_EDITOR.id, changed_by_name: OTHER_EDITOR.name, changed_fields: ['steps', 'expected_result'] }),
        versionRow(c, 3, { change_summary: 'Tagged by the nightly sync', source: 'api', changed_by_id: API_EDITOR.id, changed_by_name: API_EDITOR.name, changed_fields: ['tags', 'priority'] }),
      ]
    } else {
      history[String(c.id)] = [versionRow(c, 1, { change_summary: 'Created' })]
    }
  }
  const pushVersion = (c: Row, over: Row) => {
    history[String(c.id)].push(versionRow(c, Number(c.version), { created_at: isoAgo(0), ...over }))
  }
  const byActor = { changed_by_id: actor.id, changed_by_name: actor.full_name, source: 'person' }

  /** A case as the signed-in user reads it (`owner`: the assignee's name, else the author's, as `case_response`). */
  const answer = (c: Row): Row => ({
    ...c,
    owner: AUTHOR_NAMES[String(c.assignee_id ?? c.author_id)] ?? null,
    allowed_actions: lifecycleV2 ? allowedActionsFor(c, actor, reviewRequired) : [],
    reason_required_actions: lifecycleV2 ? reasonRequiredFor(c, actor, reviewRequired) : [],
    review_required: reviewRequired,
    edited_after_approval: reviewRequired && (c.status === 'approved' || c.status === 'active') && c.edited_since_approval_version != null,
  })
  const stale = (c: Row, expected: number, message: string) =>
    respond(409, { detail: { current_version: c.version, expected_version: expected, message } })

  /** Apply one lifecycle move (the transition route and the bulk loop share it); null = refused. */
  const move = (c: Row, action: Action, reason: string | undefined): string | null => {
    const target = targetOf(c, actor, action, reviewRequired)
    if (!target || !allowedActionsFor(c, actor, reviewRequired).includes(action)) return null
    const self = action === 'approve' && isSelfApproval(c, actor)
    c.status = target
    c.version = Number(c.version) + 1
    if (action === 'approve' || (target !== 'approved' && target !== 'active')) c.edited_since_approval_version = null
    if (self) c.reviewer_id = actor.id
    if (action === 'deprecate') c.deprecation_reason = reason ?? null
    c.updated_at = isoAgo(0)
    pushVersion(c, { ...byActor, change_type: action, change_summary: reason ?? null, changed_fields: ['status'] })
    return target
  }

  /** The other editor's two saves while the reader had the case open (the 409 world). */
  const theyEdited = (c: Row) => {
    c.title = 'Checkout case 2: the cart survives two refreshes'
    c.version = 2
    pushVersion(c, { changed_by_id: OTHER_EDITOR.id, changed_by_name: OTHER_EDITOR.name, changed_fields: ['title'], change_summary: 'Wording' })
    c.title = THEIR_TITLE
    c.version = 3
    pushVersion(c, { changed_by_id: OTHER_EDITOR.id, changed_by_name: OTHER_EDITOR.name, changed_fields: ['title'], change_summary: 'Wording, again' })
  }

  if (options.planItems) {
    const item = (id: string, caseIndex: number, index: number, over: Row) => ({
      id,
      plan_id: TM_PLAN_ID,
      test_case_id: tmCaseId(caseIndex),
      order_index: index,
      priority_override: null,
      execution_status: 'not_run',
      executed_by_id: null,
      executed_at: null,
      execution_notes: null,
      actual_duration_minutes: null,
      executed_case_version: null,
      case_version: Number(cases[caseIndex].version),
      created_at: isoAgo(6),
      ...over,
    })
    base.items[TM_PLAN_ID] = [
      item(PLAN_ITEM_IDS.ran, ACTIVE_CASE, 0, { execution_status: 'passed', executed_by_id: RELEASE_PEOPLE.priya.id, executed_at: isoAgo(3), executed_case_version: 1 }),
      item(PLAN_ITEM_IDS.notRun, 2, 1, {}),
      item(PLAN_ITEM_IDS.unrecorded, 6, 2, { execution_status: 'failed', executed_by_id: RELEASE_PEOPLE.priya.id, executed_at: isoAgo(20) }),
    ]
    Object.assign(base.plans[0], { total_cases: 3, executed_cases: 2, passed_cases: 1, failed_cases: 1 })
  }

  const CASES = '/api/v1/test-management/cases'
  const ONE = /^\/api\/v1\/test-management\/cases\/[0-9a-f-]{36}$/
  const handlers: ApiHandlers = [
    // TCM-03 bulk (before the `{id}` routes, as the router includes it)
    [`${CASES}/bulk`, (request) => {
      const body = recordIn(writes, request) as {
        case_ids: string[]
        expected_versions?: Record<string, number>
        action: string
        transition?: string
        reason?: string
        assignee_id?: string | null
        priority?: string
        tags?: string[]
        suite_name?: string
      }
      const counts = { changed: 0, unchanged: 0, skipped: 0, not_found: 0 }
      const results = [...new Set(body.case_ids)].map((id) => {
        const c = find(id)
        if (!c) {
          counts.not_found += 1
          return { id, outcome: 'not_found', reason: null, version: null, status: null }
        }
        const expected = body.expected_versions?.[id]
        const skip = (reason: string) => {
          counts.skipped += 1
          return { id, outcome: 'skipped', reason, version: c.version, status: c.status }
        }
        if (options.bulkSkips?.includes(id)) return skip(`Changed since you selected it (v${expected ?? c.version} → v${Number(expected ?? c.version) + 1})`)
        if (expected !== undefined && expected !== c.version) return skip(`Changed since you selected it (v${expected} → v${String(c.version)})`)
        if (body.action === 'transition') {
          if (!move(c, body.transition as Action, body.reason)) return skip(`Not available from ${String(c.status)}`)
        } else {
          if (c.status === 'deprecated' || c.status === 'archived') return skip('Reinstate it to edit it')
          const field = { set_owner: 'assignee_id', set_priority: 'priority', add_tags: 'tags', remove_tags: 'tags', move_to_suite: 'suite_name' }[body.action]
          if (!field) return skip('This case could not be changed')
          const tags = (c.tags as string[] | null) ?? []
          const value = body.action === 'set_owner' ? (body.assignee_id ?? null)
            : body.action === 'set_priority' ? body.priority
            : body.action === 'add_tags' ? [...new Set([...tags, ...(body.tags ?? [])])]
            : body.action === 'remove_tags' ? tags.filter((t) => !(body.tags ?? []).includes(t))
            : body.suite_name
          if (json(value) === json(c[field])) {
            counts.unchanged += 1
            return { id, outcome: 'unchanged', reason: null, version: c.version, status: c.status }
          }
          c[field] = value
          c.version = Number(c.version) + 1
          pushVersion(c, { ...byActor, change_type: 'bulk_updated', changed_fields: [field], change_summary: 'Bulk edit' })
        }
        counts.changed += 1
        return { id, outcome: 'changed', reason: null, version: c.version, status: c.status }
      })
      return { action: body.action, requested: results.length, ...counts, results }
    }, 'POST'],
    // TCM-01 the edit
    [ONE, (request) => {
      const body = recordIn(writes, request) as Row & { expected_version: number; change_summary?: string }
      const c = find(caseIdOf(request))
      if (!c) return respond(404, { detail: 'Test case not found' })
      if (c.status === 'deprecated' || c.status === 'archived') return respond(409, { detail: `Cannot edit a ${String(c.status)} test case` })
      state.patches += 1
      if (options.conflictOnFirstPatch && state.patches === 1 && c === cases[EDIT_CASE]) theyEdited(c)
      if (body.expected_version !== c.version) return stale(c, body.expected_version, 'Test case changed; refresh before saving edits')
      const { expected_version: _expected, change_summary: summary, ...fields } = body
      void _expected
      Object.assign(c, fields)
      c.version = Number(c.version) + 1
      c.updated_at = isoAgo(0)
      pushVersion(c, { ...byActor, change_type: 'updated', changed_fields: Object.keys(fields), change_summary: summary ?? `Updated to v${String(c.version)}` })
      return answer(c)
    }, 'PATCH'],
    // TCM-02 the Status moves (and the Lifecycle tab's)
    [/^\/api\/v1\/test-management\/cases\/[^/]+\/transition$/, (request) => {
      const body = recordIn(writes, request) as { action: Action; reason?: string; notes?: string; expected_version?: number }
      const c = find(caseIdOf(request))
      if (!c) return respond(404, { detail: 'Test case not found' })
      if (rankOf(actor) < RANK.TESTER) return respond(403, { detail: 'Requires at least TESTER role' })
      if (body.expected_version !== undefined && body.expected_version !== c.version) {
        return stale(c, body.expected_version, 'Test case changed; refresh before applying this action')
      }
      if (!move(c, body.action, body.reason ?? body.notes)) {
        return respond(409, { detail: { current_state: c.status, attempted_action: body.action, allowed_actions: allowedActionsFor(c, actor, reviewRequired) } })
      }
      return answer(c)
    }, 'POST'],
    // TCM-04 restore
    [/^\/api\/v1\/test-management\/cases\/[^/]+\/restore$/, (request) => {
      const body = recordIn(writes, request) as { version: number; expected_version: number; change_summary?: string }
      const c = find(caseIdOf(request))
      if (!c) return respond(404, { detail: 'Test case not found' })
      const from = history[String(c.id)].find((v) => v.version === body.version)
      if (!from) return respond(404, { detail: 'Version not found' })
      if (body.version === c.version) return respond(422, { detail: `v${body.version} is the current version` })
      if (body.expected_version !== c.version) return stale(c, body.expected_version, 'Test case changed; refresh before saving edits')
      const differing = RESTORABLE_FIELDS.filter((f) => json(from[f]) !== json(c[f]))
      if (differing.length === 0) return answer(c)
      for (const f of differing) c[f] = from[f]
      c.version = Number(c.version) + 1
      c.updated_at = isoAgo(0)
      pushVersion(c, { ...byActor, change_type: 'restored', changed_fields: differing, change_summary: body.change_summary ?? `Restored from v${body.version}` })
      return answer(c)
    }, 'POST'],
    // The reads
    [/^\/api\/v1\/test-management\/cases\/[^/]+\/history$/, (request) => {
      const rows = history[caseIdOf(request)] ?? []
      return [...rows].reverse()
    }],
    [/^\/api\/v1\/test-management\/cases\/[^/]+\/allowed-transitions$/, (request) => {
      const c = find(caseIdOf(request))
      return c ? availabilityFor(c, actor, reviewRequired) : respond(404, { detail: 'Test case not found' })
    }],
    [/^\/api\/v1\/test-management\/cases\/[^/]+\/comments$/, () => [
      { id: 'cm000000-0000-4000-8000-000000000001', test_case_id: tmCaseId(EDIT_CASE), author_id: RELEASE_PEOPLE.priya.id, content: 'Step 2 needs a slower network to fail.', comment_type: 'general', step_number: 2, is_resolved: false, created_at: isoAgo(2) },
    ]],
    [/^\/api\/v1\/test-management\/cases\/[^/]+\/reviews$/, () => []],
    [ONE, (request) => {
      const c = find(caseIdOf(request))
      return c ? answer(c) : respond(404, { detail: 'Test case not found' })
    }],
    [CASES, ({ url }) => {
      const status = url.searchParams.get('status')
      const ai = url.searchParams.get('ai_generated')
      const size = Number(url.searchParams.get('size') ?? 25)
      const rows = cases.filter((c) => (status ? c.status === status : c.status !== 'archived') && (ai === 'true' ? c.ai_generated === true : true))
      return { items: rows.map(answer), total: rows.length, page: 1, size, pages: rows.length ? 1 : 0 }
    }],
    // TCM-02 Link Suite reads `lifecycle_status` (W28) on the suite's manual rows.
    [/^\/api\/v1\/test-management\/suites\/[^/]+\/cases$/, (request) => {
      const name = decodeURIComponent(request.path.split('/')[5])
      const rows = name === 'Checkout' ? SUITE_CASES : []
      return {
        items: rows.map((r) => ({ ...r, suite_name: name, status: 'active', duration_ms: null, class_name: null, package_name: null, created_at: isoAgo(9) })),
        total: rows.length,
        page: 1,
        pages: 1,
        size: 500,
      }
    }],
    ...base.handlers,
  ]
  return { handlers, writes, cases, history, plans: base.plans, items: base.items }
}

// ── /projects: Edit Project's review checkbox ─────────────────────────────

/** `projectsInPageWorld()` with each project's `require_case_review` (W28, false for every project: OR-04's default). */
export function projectsReviewWorld() {
  const world = projectsInPageWorld()
  for (const project of world.projects) project.require_case_review = false
  return world
}

// ── /chat: the suggested questions during a conversation ───────────────────

const sse = (events: Array<[string, unknown]>) =>
  respondRaw(events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''), 'text/event-stream')

/** The localStorage key the chat remembers "Hide suggestions" under (contract §1.6). */
export const HIDE_SUGGESTIONS_KEY = 'tl.chat.hideSuggestions'

/**
 * `chatInPageWorld()` (the session created on the first question) answering ANY number of questions: each turn
 * gets its own ids, so a chip's question is a second, distinct turn. `posted` holds every stream body, in order.
 * With `holdTurn: n` the n-th answer waits until the spec calls `release()` (an answer "being written").
 */
export function chatSuggestionsWorld(options: { holdTurn?: number } = {}) {
  const base = chatInPageWorld()
  const posted: unknown[] = []
  const messages: Row[] = []
  const gate: { release: () => void } = { release: () => undefined }
  const held = new Promise<void>((resolve) => {
    gate.release = resolve
  })
  let turn = 0
  const handlers: ApiHandlers = [
    [`/api/v1/chat/sessions/${CHAT_SESSION_ID}/messages`, () => messages],
    [`/api/v1/chat/sessions/${CHAT_SESSION_ID}/messages/stream`, async (request) => {
      const body = bodyOf(request) as { message?: string }
      posted.push(body)
      turn += 1
      if (options.holdTurn === turn) await held
      const question = { id: `q-w28-${turn}`, session_id: CHAT_SESSION_ID, role: 'user', content: body?.message ?? '', sources: null, created_at: isoAgo(0) }
      messages.push(question)
      const meta = { type: 'meta', status: 'complete', model: 'mistralai/mistral-nemo', first_token_ms: 800, total_ms: 1900, tool_calls: 1 }
      const answer = {
        id: `a-w28-${turn}`,
        session_id: CHAT_SESSION_ID,
        role: 'assistant',
        content: `Answer ${turn}: two tests failed in the Auth suite.`,
        created_at: isoAgo(0),
        sources: [meta],
      }
      messages.push(answer)
      return sse([
        ['start', { user_message_id: question.id, retry: false }],
        ['delta', { text: answer.content }],
        ['done', { message: answer, sources: [], tool_trace: [], suggested_actions: [], meta }],
      ])
    }, 'POST'],
    ...base.handlers,
  ]
  return { handlers, posted, writes: base.writes, release: () => gate.release() }
}

/** The five starter prompts for `Checkout` with no run summary (`starterPrompts('Checkout', null)`), label → prompt. */
export const CHAT_PROMPTS = [
  { label: 'What failed', prompt: 'What failed in the latest run, and why?' },
  { label: 'What changed', prompt: 'What changed since the previous build? Any new failures?' },
  { label: 'Flaky tests', prompt: 'Which tests are flaky right now, and are any of them quarantined?' },
  { label: 'Release', prompt: 'Is Checkout ready to release? What blocks it?' },
  { label: 'Standup summary', prompt: "Summarize this project's recent test health for a standup, in five bullets." },
]
