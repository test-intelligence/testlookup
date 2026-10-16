/**
 * W07 "runs-failures": the hermetic answers only this wave's specs use
 * (`docs/deferred-scope-2026-10-13/work/W07-contract.md` §6.2 fixture rules).
 * The answers every Runs / Failures / Run Compare page needs live in
 * `fixtures.ts` (`RUNS_PAGE`, `runListOf`, `FAILURES_ON`) and
 * `fixtures-ai-history.ts` (`RUN_COMPARE_PAGE`); here are the variants and the
 * worlds with writes:
 *
 *  - `RUNS_EQUAL_COUNTS`: builds 240 and 239 with the SAME pass / fail / total
 *    and different dominant errors (FAIL-03: two groups, not one);
 *  - `RUN_COMPARE_PICKER`: Run Compare over a 43-run history, so the picker
 *    pages (FAIL-06);
 *  - `FAILURES_TTF_NOT_MEASURED`: the Failures page below the time-to-fix
 *    floor (FAIL-02);
 *  - `withRoleActions`: a run page whose analysis has an action for each role
 *    (FAIL-07);
 *  - `ownershipWorld()`: `/ownership` with the Java source roots read / PUT /
 *    preview and a coverage badge that follows the saved roots (FAIL-08);
 *  - `hintsWorld()`: the Failures page with the classification-hint list,
 *    preview and create (FAIL-10);
 *  - `ACTIVITY_PAGE`, `AUDIT_PAGE`: the two pages WIN-01 added 14d to.
 *
 * Shapes are copied from the contract (§1.4, §1.5, §1.7), not imported from
 * `src/` (see `fixtures.ts`). Every factory returns fresh state per call.
 * Not a spec file.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import {
  failureSignaturesOf,
  FAILURES_ON,
  isoAgo,
  LAYOUT,
  PROJECT_ID,
  RUN_ID,
  RUN_RECOMMENDATIONS,
  RUN_SIGNATURES,
  RUNS_PAGE,
  runListOf,
  runs,
  SIGNATURE,
  TIME_TO_FIX_PATH,
  timeToFixBody,
  type SignatureSpec,
} from './fixtures'
import { RUN_COMPARE_PAGE, RUN_DETAIL_PATH } from './fixtures-ai-history'

const P = PROJECT_ID

const intParam = (request: ApiRequest, name: string, fallback: number) => {
  const raw = request.url.searchParams.get(name)
  const n = raw == null ? NaN : Number(raw)
  return Number.isFinite(n) ? n : fallback
}

/** The body of a write the page made, parsed. */
const bodyOf = (request: ApiRequest): unknown => request.route.request().postDataJSON()

/** One write a world answered: `METHOD path` and its JSON body. */
export interface RecordedWrite {
  line: string
  body: unknown
}

// ── FAIL-03: two builds with equal counts and different errors ────────────

/** `runs()` with build 239 given build 240's exact pass / fail / skip / total (and pass rate). */
export function equalCountRuns() {
  const all = runs()
  const [first] = all
  all[1] = {
    ...all[1],
    passed_tests: first.passed_tests,
    failed_tests: first.failed_tests,
    broken_tests: first.broken_tests,
    skipped_tests: first.skipped_tests,
    total_tests: first.total_tests,
    pass_rate: first.pass_rate,
  }
  return all
}

/** Build 239's NullPointerException over the 15 failing rows it has in this variant. */
const EQUAL_COUNT_SIGNATURES: Readonly<Record<number, SignatureSpec[]>> = {
  ...RUN_SIGNATURES,
  1: [{ ...SIGNATURE.npe, count: 15, tests: ['apply coupon', 'checkout total includes tax', 'guest checkout'] }],
}

const recommendationByIndex = (source: () => ReturnType<typeof runs>) => (run: { id: string }) =>
  RUN_RECOMMENDATIONS[source().findIndex((r) => r.id === run.id)] ?? null

/** `/runs` where builds 240 and 239 share every count but not their dominant error. */
export const RUNS_EQUAL_COUNTS: ApiHandlers = [
  ['/api/v1/runs', runListOf(equalCountRuns, recommendationByIndex(equalCountRuns))],
  ['/api/v1/runs/failure-signatures', failureSignaturesOf(equalCountRuns, (i) => EQUAL_COUNT_SIGNATURES[i])],
  ...RUNS_PAGE,
]

// ── FAIL-06: Run Compare over a longer history ───────────────────────────

/** Thirty older builds (227 down to 198, one a day from day 24), behind the thirteen of `runs()`. */
export function longHistoryRuns() {
  const recent = runs()
  const template = recent[2]
  const older = Array.from({ length: 30 }, (_, k) => {
    const n = 24 + k
    const start = isoAgo(n, 3)
    return {
      ...template,
      id: `55555555-5555-4555-8555-${String(200000000000 + k)}`,
      build_number: 227 - k,
      status: k % 4 === 0 ? 'FAILED' : 'PASSED',
      failed_tests: k % 4 === 0 ? 4 : 0,
      pass_rate: k % 4 === 0 ? 97.9 : 100,
      start_time: start,
      end_time: start,
      created_at: start,
      exec_started_at: start,
      exec_finished_at: start,
      primary_suite_name: ['Auth', 'Checkout', 'Payments'][k % 3],
      suite_names: [['Auth', 'Checkout', 'Payments'][k % 3]],
      run_seq: 47 - k,
    }
  })
  return [...recent, ...older]
}

/** `/runs/compare` with the 43-run history: the pickers search and page it (20 at a time). */
export const RUN_COMPARE_PICKER: ApiHandlers = [
  ['/api/v1/runs', runListOf(longHistoryRuns, recommendationByIndex(longHistoryRuns), { bySuite: true })],
  [
    RUN_DETAIL_PATH,
    ({ path }) => {
      const run = longHistoryRuns().find((r) => r.id === path.split('/')[4])
      return run ? { ...run, ai_recommendation: null } : respond(404, { detail: 'Test run not found' })
    },
  ],
  ...RUN_COMPARE_PAGE,
]

// ── FAIL-02: below the time-to-fix floor ─────────────────────────────────

export const FAILURES_TTF_NOT_MEASURED: ApiHandlers = [
  [TIME_TO_FIX_PATH, (request) => timeToFixBody(intParam(request, 'days', 30), false)],
  ...FAILURES_ON,
]

// ── FAIL-07: a run whose analysis has an action for each role ────────────

export const ROLE_ACTIONS = {
  DEVELOPER: 'Fix the session TTL regression in AuthService.refresh.',
  QA: 'Re-run the Auth suite on build 241 before signing off.',
  RELEASE_MANAGER: 'Hold the release until the Auth suite is green.',
  SRE: '',
} as const

/** `handlers` with the run's `/intelligence` answer carrying `ROLE_ACTIONS` (everything else unchanged). */
export function withRoleActions(handlers: ApiHandlers): ApiHandlers {
  const path = `/api/v1/runs/${RUN_ID}/intelligence`
  const base = handlers.find(([matcher, , verb = 'GET']) =>
    verb === 'GET' && (typeof matcher === 'string' ? matcher === path : matcher.test(path)),
  )
  if (!base) throw new Error(`no handler answers ${path}`)
  return [
    [path, async (request) => ({ ...((await base[1](request)) as Record<string, unknown>), role_actions: { ...ROLE_ACTIONS } })],
    ...handlers,
  ]
}

// ── FAIL-08: /ownership with Java source roots ───────────────────────────

export interface FixtureSourceRoot {
  prefix: string
  root: string
}

const CODEOWNERS_RULE = {
  id: '88888888-8888-4888-8888-000000000001',
  project_id: P,
  match_type: 'path',
  match_pattern: 'src/test/java/com/acme/**',
  service_name: 'CODEOWNERS',
  team_name: '@acme/payments',
  team_contact: '@acme/payments',
  priority: 0,
  is_active: true,
  created_by: null,
  created_at: isoAgo(20),
  updated_at: null,
}

/** The badge with no roots: every sampled failure is Java, so nothing is located: `n/a`, never 0 %. */
export const COVERAGE_WITHOUT_ROOTS = {
  path_rules: 1, codeowners_rules: 1, sampled: 15, located: 0, matched: 0, coverage_pct: null, lookback_days: 30, source_roots: 0,
}

/** The badge once a root is saved: 16 of 25 located paths match a rule. */
export const coverageWithRoots = (count: number) => ({
  path_rules: 1, codeowners_rules: 1, sampled: 30, located: 25, matched: 16, coverage_pct: 64, lookback_days: 30, source_roots: count,
})

/** What the preview answers for any non-empty list: 12 of 15 recent Java failures map to a path; 9 match a rule. */
export const SOURCE_ROOTS_PREVIEW = {
  sampled: 20,
  java_frames: 15,
  located: 12,
  matched: 9,
  lookback_days: 30,
  examples: [
    { test_name: 'CheckoutTest.pays', path: 'src/test/java/com/acme/pay/CheckoutTest.java' },
    { test_name: 'RefundTest.refunds', path: 'src/test/java/com/acme/pay/RefundTest.java' },
  ],
}

const SOURCE_ROOTS_PATH = `/api/v1/projects/${P}/ownership/source-roots`

/**
 * `/ownership` for one project: one CODEOWNERS path rule, the stored roots
 * (`state.roots`, none at first), the badge computed from them, and the PUT /
 * preview writes recorded in order.
 */
export function ownershipWorld(initial: FixtureSourceRoot[] = []) {
  const state = { roots: [...initial] }
  const writes: RecordedWrite[] = []
  const handlers: ApiHandlers = [
    ...LAYOUT,
    [`/api/v1/projects/${P}/ownership/rules`, () => [CODEOWNERS_RULE]],
    [
      `/api/v1/projects/${P}/ownership/codeowners/coverage`,
      () => (state.roots.length > 0 ? coverageWithRoots(state.roots.length) : COVERAGE_WITHOUT_ROOTS),
    ],
    [SOURCE_ROOTS_PATH, () => ({ roots: state.roots, max_roots: 20 })],
    [
      SOURCE_ROOTS_PATH,
      (request) => {
        const body = bodyOf(request) as { roots: FixtureSourceRoot[] }
        writes.push({ line: `PUT ${request.path}`, body })
        state.roots = body.roots.map((r) => ({ prefix: r.prefix.trim(), root: r.root.trim().replace(/\/+$/, '') }))
        return { roots: state.roots, max_roots: 20 }
      },
      'PUT',
    ],
    [
      `${SOURCE_ROOTS_PATH}/preview`,
      (request) => {
        const body = bodyOf(request) as { roots: FixtureSourceRoot[] }
        writes.push({ line: `POST ${request.path}`, body })
        return body.roots.length > 0
          ? SOURCE_ROOTS_PREVIEW
          : { ...SOURCE_ROOTS_PREVIEW, located: 0, matched: 0, examples: [] }
      },
      'POST',
    ],
  ]
  return { handlers, state, writes }
}

// ── FAIL-10: classification hints on /failures ───────────────────────────

const HINTS_PATH = `/api/v1/projects/${P}/classification-hints`

/** What the preview answers for `ECONNREFUSED` in error messages: 7 of the last 200 failures. */
export const HINT_PREVIEW = {
  matched: 7,
  sampled: 200,
  lookback_days: 30,
  examples: [
    { test_name: 'refund webhook retried', value: 'Error: connect ECONNREFUSED 10.0.0.12:5432' },
    { test_name: 'card declined shows reason', value: 'FetchError: request to http://payments:8080 failed, reason: connect ECONNREFUSED' },
  ],
}

/** The Failures page (`FAILURES_ON`) with a project's hints: list, preview, create; writes recorded. */
export function hintsWorld() {
  const hints: Record<string, unknown>[] = []
  const writes: RecordedWrite[] = []
  const handlers: ApiHandlers = [
    [HINTS_PATH, () => ({ items: hints, total: hints.length, max_hints: 50 })],
    [
      `${HINTS_PATH}/preview`,
      ({ url }) =>
        (url.searchParams.get('pattern') ?? '').trim().toUpperCase() === 'ECONNREFUSED'
          ? HINT_PREVIEW
          : { matched: 0, sampled: 200, lookback_days: 30, examples: [] },
    ],
    [
      HINTS_PATH,
      (request) => {
        const body = bodyOf(request) as Record<string, unknown>
        writes.push({ line: `POST ${request.path}`, body })
        const hint = {
          id: `99999999-9999-4999-8999-00000000000${hints.length + 1}`,
          project_id: P,
          match_field: body.match_field,
          pattern: body.pattern,
          category: body.category,
          note: body.note ?? null,
          enabled: body.enabled ?? true,
          created_by_name: 'QA Lead',
          created_at: isoAgo(0),
          updated_at: isoAgo(0),
        }
        hints.push(hint)
        return respond(201, hint)
      },
      'POST',
    ],
    ...FAILURES_ON,
  ]
  return { handlers, hints, writes }
}

// ── WIN-01: Activity and the Audit dashboard ─────────────────────────────

/** `/activity`: the registry and an empty feed for the window asked (`since` is echoed back). */
export const ACTIVITY_PAGE: ApiHandlers = [
  ...LAYOUT,
  [
    '/api/v1/activity/event-types',
    () => ({ categories: ['runs', 'release', 'configuration'], actor_types: ['user', 'api_key', 'system'], entity_types: ['project'], events: {} }),
  ],
  [
    `/api/v1/projects/${P}/activity`,
    ({ url }) => ({
      items: [],
      next_cursor: null,
      ledger_started_at: isoAgo(60),
      window: { since: url.searchParams.get('since'), until: null },
    }),
  ],
]

/** `/settings/audit`: the categories and an empty event list. */
export const AUDIT_PAGE: ApiHandlers = [
  ...LAYOUT,
  ['/api/v1/audit-dashboard/categories', () => [{ key: 'security', description: 'Sign-ins and keys' }]],
  ['/api/v1/audit-dashboard/events', () => ({ total: 0, items: [] })],
]
