/**
 * W22a ACT-05 / ACT-04 hermetic world: retention holds and the keep-last floor
 * (`docs/deferred-scope-2026-10-13/work/W22a-contract.md` §6.2 G-A, §6.3 specs).
 *
 * `retentionHoldsWorld(options)` answers, with the REAL server shapes, every request the W22a specs make:
 *  - `GET /api/v1/projects/{id}/retention-holds` → `{items, total, counts}` (never a bare array), filtered by
 *    `state` (default `live`), `entity_type`, `entity_id`, `limit`; `counts` ignores the state filter;
 *  - `POST …/retention-holds` (QA_LEAD+) and `POST …/retention-holds/{id}/release` (ADMIN): validated like the
 *    server, recorded in `writes`; a scripted failure (`placeError` / `releaseError`) answers
 *    `{"detail": {"code", "message"}}` with the server's own sentence;
 *  - `GET` / `PUT …/retention-policy` (with `keep_last_runs`), `POST …/retention-policy/preview` (with `holds`,
 *    `floor`, `references_broken`, `references_measured`), `GET …/storage`, `GET /api/v1/admin/storage/deleted-projects`,
 *    `GET /api/v1/auth/me/dismissals`, `POST …/deletion/preview` (a held run in `blocked[]` with `Held: …` first,
 *    `references_broken`, `floor_applies: false`, `floor_note`), and (W22b) the jobs card's `GET
 *    …/deletion/jobs` → `{jobs: [], total: 0}` (ADMIN) and `candidates.export_archives_expired: 0`;
 *  - when `release` / `packs` / `base` is given: `GET /api/v1/releases/{id}` (+ `holds`), `GET
 *    /api/v1/releases/{id}/compliance-packs` (+ per-pack `holds`), `POST /api/v1/compliance-packs/{id}/retire`
 *    and `DELETE /api/v1/compliance-packs/{id}` — both 409 `{"detail": {"blockers": ["Held: …", …]}}` while a
 *    live hold covers the pack (the server raises `HTTPException(409, detail={"blockers": …})`, so the wire body
 *    nests the list under `detail`); delete also lists the open retention window, as the server does;
 *  - when `regressionDiff` is given: `GET /api/v1/runs/{id}/regression-diff` (with `baseline_purged`).
 * Every other request fails closed as today: `base` handlers (an existing world) come AFTER these, then `LAYOUT`.
 *
 * Hold state is DERIVED on read from `released_at` / `expires_at` against `NOW` (the pinned page clock), exactly
 * like the server; `state.holds` is the live, mutable list (a spec may push, edit or empty it — "the hold scripted
 * off"). Fresh state per call. Not a spec file.
 */
import { ApiResponse, respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import {
  FLOOR_NOTE_EXPLICIT,
  HELD_SENTENCE,
  HOLD_ENTITY_TYPES,
  HOLD_ERROR_SENTENCES,
  HOLD_LIST_DEFAULT_LIMIT,
  HOLD_LIST_MAX_LIMIT,
  HOLD_MAX_EXPIRY_DAYS,
  HOLD_REASON_MAX,
  HOLD_REASON_MIN,
  HOLD_STATES,
  PROJECT_HOLD_LABEL,
  type HoldErrorCode,
  type HoldRef,
  type HoldState,
  type RetentionHold,
} from '../../../src/types/retentionHolds'
import { isoAgo, LAYOUT, NOW, PROJECT_ID, USER } from './fixtures'
import { bodyOf, delegate, type RecordedWrite } from './fixtures-in-page'
import { RELEASE_PACK, RELEASE_SHIPPED_ID } from './fixtures-pages'
import { ADMIN, QA_ENGINEER, VIEWER } from './fixtures-releases'

export type { RecordedWrite }

const DAY_MS = 86_400_000

/** The roles the world knows, lowest first (the server's `UserRole` order). */
export const WORLD_ROLES = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN'] as const
export type WorldRole = (typeof WORLD_ROLES)[number]

/** The signed-in user of each role (pass it as `user` to `openRollout` / `mockApi`). */
export const ROLE_USERS: Record<WorldRole, typeof USER> = {
  VIEWER,
  TESTER: { ...QA_ENGINEER, id: '00000000-0000-4000-8000-0000000000e3', username: 'tester', full_name: 'Tester', role: 'TESTER' },
  QA_ENGINEER,
  QA_LEAD: USER,
  ADMIN,
}

const atLeast = (role: WorldRole, min: WorldRole) => WORLD_ROLES.indexOf(role) >= WORLD_ROLES.indexOf(min)

// ── Fixed ids and the sentinel reasons (non-hex words: never a uuid collision, never a phone number) ──

/** The shipped release `2026.10` of `RELEASES_PAGE` (it has the world's one compliance pack). */
export const HOLD_RELEASE_ID = RELEASE_SHIPPED_ID
export const HOLD_PACK_ID = RELEASE_PACK.id
export const HOLD_RUN_ID = '33333333-3333-4333-8333-0000000022a1'
export const HOLD_BASELINE_RUN_ID = '33333333-3333-4333-8333-0000000022b2'
export const AUDIT_REASON = 'Audit 2026-Q4'
export const LITIGATION_REASON = 'Litigation hold'

/** One hold row as the world stores it (the server's `RetentionHoldRead` minus the derived `state`). */
export type WorldHold = Omit<RetentionHold, 'state'> & { state?: string }

let holdSequence = 0
/** A distinct hold id per call (canonical v4 shape). */
export function worldHoldId(): string {
  holdSequence += 1
  return `dddddddd-2200-4000-8000-${String(holdSequence).padStart(12, '0')}`
}

/** A hold row (a live release hold on `HOLD_RELEASE_ID`, reason `Audit 2026-Q4`, by default). */
export function worldHold(overrides: Partial<WorldHold> = {}): WorldHold {
  return {
    id: worldHoldId(),
    project_id: PROJECT_ID,
    entity_type: 'release',
    entity_id: HOLD_RELEASE_ID,
    entity_label: 'release 2026.10',
    reason: AUDIT_REASON,
    expires_at: null,
    placed_by_name: 'QA Lead',
    placed_at: isoAgo(2),
    released_at: null,
    released_by_name: null,
    release_reason: null,
    ...overrides,
  }
}

/** Live / expired / released, derived like the server (`hold_state`): released wins, then expiry vs `now`. */
export function holdStateAt(hold: WorldHold, now: Date = NOW): HoldState {
  if (hold.released_at) return 'released'
  if (hold.expires_at && Date.parse(hold.expires_at) <= now.getTime()) return 'expired'
  return 'live'
}

const asRead = (hold: WorldHold): RetentionHold => ({ ...hold, state: holdStateAt(hold) })

const asRef = (hold: WorldHold): HoldRef => ({
  hold_id: hold.id,
  entity_type: hold.entity_type,
  entity_id: hold.entity_id,
  entity_label: hold.entity_label,
  reason: hold.reason,
  expires_at: hold.expires_at,
})

const sentence = (hold: WorldHold) => HELD_SENTENCE.replace('{reason}', () => hold.reason)

/** The route's own 4xx: `{"detail": {"code", "message"}}` with the server's sentence. */
export function holdError(status: number, code: HoldErrorCode) {
  return respond(status, { detail: { code, message: HOLD_ERROR_SENTENCES[code] } })
}

/** `require_role`'s refusal (a string `detail`, as FastAPI sends it). */
const roleRefusal = (min: WorldRole) => respond(403, { detail: `Requires at least ${min} role` })

/** Python's `datetime.isoformat()` of a UTC instant (the server's window sentence prints it). */
const pythonIso = (iso: string) => new Date(iso).toISOString().replace(/\.\d{3}Z$/, '+00:00')

// ── Default answers (the REAL shapes; every W22a field present) ──────────────

/** `RetentionPolicyRead`: enabled, the code-default windows, the floor at its default 10. */
export function worldPolicy(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    enabled: true,
    raw_events_days: 90,
    runs_days: 365,
    artifacts_days: 180,
    audit_days: 2555,
    ai_versions_per_run: 20,
    ai_versions_per_test: 10,
    keep_last_runs: 10,
    source: 'custom',
    last_purge: null,
    ...overrides,
  }
}

/** The reference warning every default answer carries: `3 runs lose their baseline link`. */
export const BASELINE_LINKS_BROKEN = {
  table: 'run_baselines',
  column: 'baseline_run_id',
  count: 3,
  label: '3 runs lose their baseline link',
}

/** `RetentionPreviewCandidates`: every class the server declares (17 keys + `mongo_docs`). */
export function worldCandidates(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    runs: 12,
    test_cases: 840,
    mongo_docs: { execution_logs: 12, run_summaries: 12 },
    minio_objects: 36,
    event_archive_rows: 4,
    audit_rows: 0,
    revoked_share_links: 0,
    provenance_rows: 0,
    compliance_packs_expired: 0,
    evidence_artifact_rows: 0,
    memory_entries_expired: 0,
    analysis_cache_entries: 0,
    search_index_documents: 0,
    ai_history_versions: 0,
    search_usage_rows: 0,
    shadow_pairs_unlabelled: 0,
    // W22b E-12: retained deletion archives that leave with their job rows.
    export_archives_expired: 0,
    ...overrides,
  }
}

/** Runs / packs a hold keeps in the preview, by hold id (absent = 0 / 0). */
export type KeptByHold = Record<string, { runs_kept?: number; packs_kept?: number }>

function previewHolds(live: WorldHold[], kept: KeptByHold) {
  const active = [...live]
    .sort((a, b) => a.placed_at.localeCompare(b.placed_at) || a.id.localeCompare(b.id))
    .map((hold) => ({
      ...asRef(hold),
      runs_kept: kept[hold.id]?.runs_kept ?? 0,
      packs_kept: kept[hold.id]?.packs_kept ?? 0,
    }))
  return {
    project_held: live.some((hold) => hold.entity_type === 'project'),
    runs_kept: active.reduce((sum, hold) => sum + hold.runs_kept, 0),
    packs_kept: active.reduce((sum, hold) => sum + hold.packs_kept, 0),
    active,
  }
}

/**
 * `RetentionPreviewResponse` for the world's live holds and policy. A live PROJECT hold zeroes every held class
 * (the five not-held classes keep their counts), like the server.
 */
function buildPreview(live: WorldHold[], policy: Record<string, unknown>, kept: KeptByHold) {
  const holds = previewHolds(live, kept)
  const zero = holds.project_held
    ? {
        runs: 0, test_cases: 0, mongo_docs: {}, minio_objects: 0, event_archive_rows: 0, audit_rows: 0,
        revoked_share_links: 0, provenance_rows: 0, compliance_packs_expired: 0, evidence_artifact_rows: 0,
        ai_history_versions: 0,
      }
    : {}
  return {
    cutoffs: {
      raw_events: isoAgo(90),
      runs: isoAgo(Number(policy.runs_days ?? 365)),
      artifacts: isoAgo(180),
      audit: isoAgo(2555),
    },
    candidates: worldCandidates(zero),
    unmeasured: [],
    holds,
    floor: holds.project_held
      ? null
      : { keep_last_runs: Number(policy.keep_last_runs ?? 10), runs_kept: 10, latest_passed_kept: true, suites_kept: 2 },
    references_broken: holds.project_held ? [] : [BASELINE_LINKS_BROKEN],
    references_measured: true,
  }
}

/** `GET …/storage` and the deleted-projects read (the shapes `retentionInPageWorld` answers). */
const STORAGE = {
  project_id: PROJECT_ID,
  computed_at: '2026-09-18T11:00:00Z',
  stores: [{ store: 'postgres', measured: true, exact: true, complete: true, bytes: 1_048_576, items: 120, estimate_basis: null, unreachable_reason: null }],
  total_bytes: 1_048_576,
  total_is_estimate: false,
  fully_measured: true,
}
const DELETED_PROJECTS = {
  computed_at: '2026-09-18T11:00:00Z',
  projects_total: 0,
  projects_measured: 0,
  truncated: false,
  projects: [],
  total_bytes: 0,
  total_is_estimate: false,
  unreachable_by_retention: 0,
}

/** The three runs a criteria preview resolves when the request names none (the first is `HOLD_RUN_ID`). */
export const CRITERIA_RUN_IDS = [HOLD_RUN_ID, '33333333-3333-4333-8333-0000000022a2', '33333333-3333-4333-8333-0000000022a3']

/** The purged-baseline fact both regression-diff answers carry (build 4471, purged three days before `NOW`). */
export const BASELINE_PURGED = { build_number: '4471', purged_at: isoAgo(3) }

/**
 * `GET /runs/{id}/regression-diff`, baseline FOUND (`compute_regression_diff`'s second answer): the run's stored
 * baseline (build 4471) was purged, so a surviving older PASSED run (4470) was chosen live.
 */
export function worldRegressionDiff(runId: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: runId,
    baseline_run_id: HOLD_BASELINE_RUN_ID,
    baseline_available: true,
    build_number: '4472',
    baseline_build_number: '4470',
    pass_rate: 96.5,
    baseline_pass_rate: 97.2,
    pass_rate_delta: -0.7,
    new_failing_tests: [],
    new_failing_count: 0,
    resolved_count: 1,
    commit_range: [],
    commit_count: 0,
    commit_range_status: {
      available: false,
      source: 'unavailable',
      base_source: 'unavailable',
      base_anchor_is_strong: false,
      base_run_id: null,
      base_build_number: null,
      reason: 'no_integration',
      commit_count: 0,
      resolved_at: null,
    },
    baseline_purged: { ...BASELINE_PURGED },
    ...overrides,
  }
}

/** The NO-baseline answer (`compute_regression_diff`'s first): no surviving PASSED run, the purge still named. */
export function worldRegressionDiffNoBaseline(runId: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: runId,
    baseline_run_id: null,
    baseline_available: false,
    message: 'No recent passing baseline run found for this project',
    baseline_purged: { ...BASELINE_PURGED },
    ...overrides,
  }
}

// ── The world ────────────────────────────────────────────────────────────────

/** A compliance pack as the per-release list answers it (`CompliancePackRead`, before the world adds `holds`). */
export type WorldPack = Record<string, unknown> & { id: string; release_id: string | null; retention_expires_at: string }

export interface RetentionHoldsWorldOptions {
  /** The signed-in role (the world enforces the routes' role guards); default `ADMIN`. */
  role?: WorldRole
  /** default `PROJECT_ID`. */
  projectId?: string
  /** The project's holds, any state (copied; `state` is ignored and derived). Default none. */
  holds?: WorldHold[]
  /** The preview, merged OVER the world's computed one (holds from the live rows, the floor, the warnings). */
  preview?: Record<string, unknown>
  /** Runs / packs each hold keeps in the computed preview. */
  keptByHold?: KeptByHold
  /** The policy, merged over `worldPolicy()`. */
  policy?: Record<string, unknown>
  /** The criteria preview, merged OVER the computed one (held runs blocked with `Held: …` first). */
  criteriaPreview?: Record<string, unknown>
  /** Run ids a release links (a release hold pins them); default: the `release` detail's `linked_runs`. */
  linkedRuns?: Record<string, string[]>
  /** A release DETAIL answered for its id (+ `holds`); otherwise `base`'s answer is decorated. */
  release?: Record<string, unknown> & { id: string }
  /** The per-release pack lists, from these rows (+ `holds`); otherwise `base`'s answer is decorated. */
  packs?: WorldPack[]
  /** `GET /runs/{id}/regression-diff`: an answer, or one per run id. Omitted: the route is not answered here. */
  regressionDiff?: Record<string, unknown> | ((runId: string) => Record<string, unknown>)
  /** An existing world (e.g. `RELEASES_PAGE`, `RUN_PAGE`): its handlers come after these, and its release detail /
   * pack list answers are decorated with `holds`. */
  base?: ApiHandlers
  /** Scripted refusals of the two hold writes (`{"detail": {"code", "message"}}`). */
  placeError?: { status: number; code: HoldErrorCode }
  releaseError?: { status: number; code: HoldErrorCode }
  /** Where answered writes are recorded; default a fresh array. */
  writes?: RecordedWrite[]
}

/** The world's live state (mutable: a spec may edit `holds` between steps). */
export interface RetentionHoldsWorldState {
  holds: WorldHold[]
  policy: Record<string, unknown>
  /** Packs deleted / retired through the world (a re-read honours both). */
  deletedPacks: Set<string>
  retiredPacks: Map<string, string>
  /** pack id -> release id / retention_expires_at, learnt from every pack list answered (and `packs`). */
  packRelease: Map<string, string | null>
  packExpiry: Map<string, string>
}

export interface RetentionHoldsWorld {
  handlers: ApiHandlers
  writes: RecordedWrite[]
  state: RetentionHoldsWorldState
  /** The `user` to sign in as (`ROLE_USERS[role]`). */
  user: typeof USER
}

type Target = { kind: 'project' } | { kind: 'release'; id: string } | { kind: 'compliance_pack'; id: string; releaseId: string | null } | { kind: 'run'; id: string }

/** Live holds covering `target`, placed order — the server's `holds_covering_*` (project ∪ own ∪ the pack's release). */
function covering(state: RetentionHoldsWorldState, target: Target, linkedRuns: Record<string, string[]>): WorldHold[] {
  return state.holds
    .filter((hold) => holdStateAt(hold) === 'live')
    .filter((hold) => {
      if (hold.entity_type === 'project') return true
      switch (target.kind) {
        case 'project':
          return false
        case 'release':
          return hold.entity_type === 'release' && hold.entity_id === target.id
        case 'compliance_pack':
          return (hold.entity_type === 'compliance_pack' && hold.entity_id === target.id)
            || (hold.entity_type === 'release' && target.releaseId !== null && hold.entity_id === target.releaseId)
        case 'run':
          return (hold.entity_type === 'run' && hold.entity_id === target.id)
            || (hold.entity_type === 'release' && (linkedRuns[hold.entity_id ?? ''] ?? []).includes(target.id))
      }
    })
    .sort((a, b) => a.placed_at.localeCompare(b.placed_at) || a.id.localeCompare(b.id))
}

/** A plain JSON object answer (not an explicit `respond(...)`, not a list). */
const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value) && !(value instanceof ApiResponse)

/** The hold list: the server's filters, `placed_at DESC, id DESC`, `total` of the filter, `counts` per state. */
function listAnswer(state: RetentionHoldsWorldState, url: URL) {
  const stateFilter = url.searchParams.get('state') ?? 'live'
  if (stateFilter !== 'all' && !(HOLD_STATES as readonly string[]).includes(stateFilter)) return holdError(422, 'unknown_state')
  const entityType = url.searchParams.get('entity_type')
  if (entityType !== null && !(HOLD_ENTITY_TYPES as readonly string[]).includes(entityType)) {
    return holdError(422, 'unknown_entity_type')
  }
  const entityId = url.searchParams.get('entity_id')
  const limit = Number(url.searchParams.get('limit') ?? HOLD_LIST_DEFAULT_LIMIT)
  if (!Number.isInteger(limit) || limit < 1 || limit > HOLD_LIST_MAX_LIMIT) {
    return respond(422, { detail: [{ loc: ['query', 'limit'], msg: `Input should be between 1 and ${HOLD_LIST_MAX_LIMIT}`, type: 'value_error' }] })
  }
  const scoped = state.holds.filter(
    (hold) => (entityType === null || hold.entity_type === entityType) && (entityId === null || hold.entity_id === entityId),
  )
  const counts: Record<HoldState, number> = { live: 0, expired: 0, released: 0 }
  for (const hold of scoped) counts[holdStateAt(hold)] += 1
  const matching = scoped
    .filter((hold) => stateFilter === 'all' || holdStateAt(hold) === stateFilter)
    .sort((a, b) => b.placed_at.localeCompare(a.placed_at) || b.id.localeCompare(a.id))
  return { items: matching.slice(0, limit).map(asRead), total: matching.length, counts }
}

/** `place_hold`'s validation, in the server's order; `null` when the body is valid. */
function placeRefusal(body: Record<string, unknown>) {
  const entityType = typeof body.entity_type === 'string' ? body.entity_type : ''
  if (!(HOLD_ENTITY_TYPES as readonly string[]).includes(entityType)) return holdError(422, 'unknown_entity_type')
  const entityId = body.entity_id ?? null
  if (entityType === 'project' && entityId !== null) return holdError(422, 'entity_id_forbidden')
  if (entityType !== 'project' && entityId === null) return holdError(422, 'entity_id_required')
  const reason = typeof body.reason === 'string' ? body.reason.trim() : ''
  if (reason.length < HOLD_REASON_MIN || reason.length > HOLD_REASON_MAX) return holdError(422, 'reason_length')
  if (typeof body.expires_at === 'string' && body.expires_at) {
    const at = Date.parse(body.expires_at)
    if (Number.isNaN(at) || at <= NOW.getTime()) return holdError(422, 'expiry_not_future')
    if (at > NOW.getTime() + HOLD_MAX_EXPIRY_DAYS * DAY_MS) return holdError(422, 'expiry_too_far')
  }
  return null
}

/**
 * The W22a world (see the module docstring). Handlers in order: the hold routes, the retention page's reads and
 * writes, the release / pack / regression-diff routes that were asked for, then `base`, then `LAYOUT`.
 */
export function retentionHoldsWorld(options: RetentionHoldsWorldOptions = {}): RetentionHoldsWorld {
  const role: WorldRole = options.role ?? 'ADMIN'
  const user = ROLE_USERS[role]
  const projectId = options.projectId ?? PROJECT_ID
  const writes = options.writes ?? []
  const base = options.base ?? []
  const state: RetentionHoldsWorldState = {
    holds: (options.holds ?? []).map((hold) => ({ ...hold })),
    policy: worldPolicy(options.policy),
    deletedPacks: new Set(),
    retiredPacks: new Map(),
    packRelease: new Map((options.packs ?? []).map((pack) => [pack.id, pack.release_id])),
    packExpiry: new Map((options.packs ?? []).map((pack) => [pack.id, pack.retention_expires_at])),
  }
  const linkedRuns: Record<string, string[]> = { ...(options.linkedRuns ?? {}) }
  const releaseNames = new Map<string, string>()
  const packDates = new Map<string, string>()
  if (options.release) {
    const detail = options.release
    if (typeof detail.name === 'string') releaseNames.set(detail.id, detail.name)
    if (!linkedRuns[detail.id] && Array.isArray(detail.linked_runs)) {
      linkedRuns[detail.id] = (detail.linked_runs as Array<{ id?: unknown }>).map((run) => String(run.id))
    }
  }
  for (const pack of options.packs ?? []) {
    if (typeof pack.generated_at === 'string') packDates.set(pack.id, pack.generated_at)
  }
  const record = (request: ApiRequest): Record<string, unknown> => {
    const body = bodyOf(request)
    writes.push({ line: `${request.method} ${request.path}`, body })
    return isObject(body) ? body : {}
  }
  const holdsPath = `/api/v1/projects/${projectId}/retention-holds`

  /** `entity_label` as `place_hold` snapshots it (`release <name>`, `Build <n>`, `pack of <date>`). */
  const labelFor = (entityType: string, entityId: string | null): string => {
    if (entityType === 'project') return PROJECT_HOLD_LABEL
    if (entityType === 'release') return `release ${releaseNames.get(entityId ?? '') ?? entityId?.slice(0, 8)}`
    if (entityType === 'compliance_pack') {
      const generated = packDates.get(entityId ?? '')
      return generated ? `pack of ${generated.slice(0, 10)}` : `pack ${entityId?.slice(0, 8)}`
    }
    return `Build ${entityId?.slice(0, 8)}`
  }

  const holdRoutes: ApiHandlers = [
    [holdsPath, ({ url }) => listAnswer(state, url)],
    [holdsPath, (request) => {
      const body = record(request)
      if (!atLeast(role, 'QA_LEAD')) return roleRefusal('QA_LEAD')
      if (options.placeError) return holdError(options.placeError.status, options.placeError.code)
      const refusal = placeRefusal(body)
      if (refusal) return refusal
      const entityType = String(body.entity_type)
      const entityId = (body.entity_id as string | null | undefined) ?? null
      const hold = worldHold({
        id: worldHoldId(),
        project_id: projectId,
        entity_type: entityType,
        entity_id: entityId,
        entity_label: labelFor(entityType, entityId),
        reason: String(body.reason).trim(),
        expires_at: typeof body.expires_at === 'string' && body.expires_at ? new Date(body.expires_at).toISOString() : null,
        placed_by_name: user.full_name,
        placed_at: NOW.toISOString(),
      })
      state.holds.push(hold)
      return respond(201, asRead(hold))
    }, 'POST'],
    [new RegExp(`^${holdsPath}/[^/]+/release$`), (request) => {
      const body = record(request)
      if (!atLeast(role, 'ADMIN')) return roleRefusal('ADMIN')
      if (options.releaseError) return holdError(options.releaseError.status, options.releaseError.code)
      const holdId = request.path.split('/').slice(-2)[0]
      const hold = state.holds.find((row) => row.id === holdId && row.project_id === projectId)
      if (!hold) return holdError(404, 'hold_not_found')
      if (holdStateAt(hold) !== 'live') return holdError(409, 'hold_not_live')
      const reason = typeof body.reason === 'string' ? body.reason.trim() : ''
      if (reason.length < HOLD_REASON_MIN || reason.length > HOLD_REASON_MAX) return holdError(422, 'reason_length')
      hold.released_at = NOW.toISOString()
      hold.released_by_name = user.full_name
      hold.release_reason = reason
      return asRead(hold)
    }, 'POST'],
  ]
  return assemble({ options, state, writes, user, role, projectId, base, linkedRuns, record, holdRoutes, releaseNames, packDates })
}

interface WorldContext {
  options: RetentionHoldsWorldOptions
  state: RetentionHoldsWorldState
  writes: RecordedWrite[]
  user: typeof USER
  role: WorldRole
  projectId: string
  base: ApiHandlers
  linkedRuns: Record<string, string[]>
  record: (request: ApiRequest) => Record<string, unknown>
  holdRoutes: ApiHandlers
  releaseNames: Map<string, string>
  packDates: Map<string, string>
}

/** The retention page's reads and writes: the policy, the preview, storage, the criteria preview. */
function retentionPageRoutes(ctx: WorldContext): ApiHandlers {
  const { options, state, projectId, record, role, linkedRuns } = ctx
  const live = () => state.holds.filter((hold) => holdStateAt(hold) === 'live')
  return [
    [`/api/v1/projects/${projectId}/retention-policy/preview`, (request) => {
      record(request)
      if (!atLeast(role, 'ADMIN')) return roleRefusal('ADMIN')
      return { ...buildPreview(live(), state.policy, options.keptByHold ?? {}), ...(options.preview ?? {}) }
    }, 'POST'],
    [`/api/v1/projects/${projectId}/retention-policy`, () => state.policy],
    [`/api/v1/projects/${projectId}/retention-policy`, (request) => {
      const body = record(request)
      if (!atLeast(role, 'ADMIN')) return roleRefusal('ADMIN')
      const keep = body.keep_last_runs
      if (keep !== undefined && (typeof keep !== 'number' || !Number.isInteger(keep) || keep < 0 || keep > 1000)) {
        return respond(422, { detail: `keep_last_runs must be between 0 and 1000 (got ${String(keep)})` })
      }
      state.policy = { ...state.policy, ...body, source: 'custom' }
      return state.policy
    }, 'PUT'],
    [`/api/v1/projects/${projectId}/storage`, () => STORAGE],
    ['/api/v1/admin/storage/deleted-projects', () => DELETED_PROJECTS],
    ['/api/v1/auth/me/dismissals', () => ({ dismissed: [] })],
    // W22b: the deletion-jobs card's one read (ADMIN, like the server) — an empty list WITH its total.
    [`/api/v1/projects/${projectId}/deletion/jobs`, () => (atLeast(role, 'ADMIN') ? { jobs: [], total: 0 } : roleRefusal('ADMIN'))],
    [`/api/v1/projects/${projectId}/deletion/preview`, (request) => {
      const body = record(request)
      if (!atLeast(role, 'ADMIN')) return roleRefusal('ADMIN')
      const asked = Array.isArray(body.run_ids) && body.run_ids.length ? body.run_ids.map(String) : CRITERIA_RUN_IDS
      const blocked: Array<{ run_id: string; reasons: string[] }> = []
      const deletable: string[] = []
      for (const runId of asked) {
        const held = covering(state, { kind: 'run', id: runId }, linkedRuns)
        const releases = Object.entries(linkedRuns).filter(([, runs]) => runs.includes(runId)).length
        const reasons = [
          ...held.map(sentence),
          ...(releases ? [`linked to ${releases} release(s) — unlink the run first`] : []),
        ]
        if (reasons.length) blocked.push({ run_id: runId, reasons })
        else deletable.push(runId)
      }
      return {
        job_id: 'del-job-w22a-1',
        project_id: projectId,
        run_count: deletable.length,
        run_ids: deletable,
        candidate_hash: 'h-w22a',
        truncated: false,
        refused_prefixes: [],
        blocked,
        references_broken: deletable.length ? [BASELINE_LINKS_BROKEN] : [],
        references_measured: true,
        floor_applies: false,
        floor_note: FLOOR_NOTE_EXPLICIT,
        ...(options.criteriaPreview ?? {}),
      }
    }, 'POST'],
  ]
}

const RELEASE_DETAIL = /^\/api\/v1\/releases\/[^/]+$/
const RELEASE_PACKS = /^\/api\/v1\/releases\/[^/]+\/compliance-packs$/
const PACK_RETIRE = /^\/api\/v1\/compliance-packs\/[^/]+\/retire$/
const PACK_ITEM = /^\/api\/v1\/compliance-packs\/[^/]+$/
const REGRESSION_DIFF = /^\/api\/v1\/runs\/[^/]+\/regression-diff$/
const segment = (path: string, fromEnd: number) => path.split('/').slice(-1 - fromEnd)[0]

/** The release page's two reads (decorated with `holds`) and the pack Retire / Delete writes. */
function releaseRoutes(ctx: WorldContext): ApiHandlers {
  const { options, state, base, record, role, linkedRuns, releaseNames, packDates } = ctx
  const hasBase = (method: string, path: string) =>
    base.some(([m, , verb = 'GET']) => verb === method && (typeof m === 'string' ? m === path : m.test(path)))
  const blockerOf = (hold: HoldRef) => HELD_SENTENCE.replace('{reason}', () => hold.reason)
  const packHolds = (pack: Record<string, unknown> & { id: string }) => {
    const releaseId = (pack.release_id as string | null | undefined) ?? state.packRelease.get(pack.id) ?? null
    return covering(state, { kind: 'compliance_pack', id: pack.id, releaseId }, linkedRuns).map(asRef)
  }
  /** A pack row as the list answers it now: retired / deleted through the world, with its live holds. */
  const shown = (pack: Record<string, unknown> & { id: string }) => {
    state.packRelease.set(pack.id, (pack.release_id as string | null | undefined) ?? null)
    if (typeof pack.generated_at === 'string') packDates.set(pack.id, pack.generated_at)
    if (typeof pack.retention_expires_at === 'string') state.packExpiry.set(pack.id, pack.retention_expires_at)
    const retiredAt = state.retiredPacks.get(pack.id)
    return { ...pack, ...(retiredAt ? { retention_expires_at: retiredAt } : {}), holds: packHolds(pack) }
  }
  const packOf = (id: string) => (options.packs ?? []).find((pack) => pack.id === id)

  const routes: Array<ApiHandlers[number]> = []
  if (options.release || base.length) {
    routes.push([RELEASE_DETAIL, (request) => {
      const id = segment(request.path, 0)
      let answer: unknown
      if (options.release && options.release.id === id) answer = options.release
      else if (hasBase('GET', request.path)) answer = delegate(base, request)
      else return respond(404, { detail: 'Release not found' })
      if (!isObject(answer) || answer.id !== id) return answer
      if (typeof answer.name === 'string') releaseNames.set(id, answer.name)
      if (!linkedRuns[id] && Array.isArray(answer.linked_runs)) {
        linkedRuns[id] = (answer.linked_runs as Array<{ id?: unknown }>).map((run) => String(run.id))
      }
      return { ...answer, holds: covering(state, { kind: 'release', id }, linkedRuns).map(asRef) }
    }])
  }
  if (options.packs || base.length) {
    routes.push([RELEASE_PACKS, (request) => {
      const releaseId = segment(request.path, 1)
      let rows: unknown
      if (options.packs) rows = options.packs.filter((pack) => pack.release_id === releaseId)
      else if (hasBase('GET', request.path)) rows = delegate(base, request)
      else return []
      if (!Array.isArray(rows)) return rows
      return (rows as Array<Record<string, unknown> & { id: string }>)
        .filter((pack) => !state.deletedPacks.has(pack.id))
        .map(shown)
    }])
    routes.push([PACK_RETIRE, (request) => {
      const body = record(request)
      if (!atLeast(role, 'ADMIN')) return roleRefusal('ADMIN')
      const packId = segment(request.path, 1)
      if (state.deletedPacks.has(packId) || (!state.packRelease.has(packId) && !packOf(packId))) {
        return respond(404, { detail: 'Compliance pack not found' })
      }
      const held = packHolds({ id: packId, release_id: state.packRelease.get(packId) ?? null })
      if (held.length) return respond(409, { detail: { blockers: held.map(blockerOf) } })
      if (body.confirmation_id !== packId) return respond(422, { detail: 'confirmation_id must match the pack id exactly' })
      state.retiredPacks.set(packId, NOW.toISOString())
      return { pack_id: packId, retention_expires_at: NOW.toISOString(), purgeable_now: true }
    }, 'POST'])
    routes.push([PACK_ITEM, (request) => {
      record(request)
      if (!atLeast(role, 'ADMIN')) return roleRefusal('ADMIN')
      const packId = segment(request.path, 0)
      const known = packOf(packId)
      if (state.deletedPacks.has(packId) || (!state.packRelease.has(packId) && !known)) {
        return respond(404, { detail: 'Compliance pack not found' })
      }
      const held = packHolds({ id: packId, release_id: state.packRelease.get(packId) ?? null })
      const expires = state.retiredPacks.get(packId) ?? known?.retention_expires_at ?? state.packExpiry.get(packId)
      const blockers = held.map(blockerOf)
      if (expires && Date.parse(expires) > NOW.getTime()) {
        blockers.push(`retention window runs to ${pythonIso(expires)} — retire the pack early first if it really should go now`)
      }
      if (blockers.length) return respond(409, { detail: { blockers } })
      state.deletedPacks.add(packId)
      return respond(204)
    }, 'DELETE'])
  }
  return routes
}

/** Every handler in order: holds, the retention page, release / packs, the regression diff, `base`, `LAYOUT`. */
function assemble(ctx: WorldContext): RetentionHoldsWorld {
  const { options, state, writes, user, base, holdRoutes } = ctx
  const diff = options.regressionDiff
  const diffRoutes: ApiHandlers = diff
    ? [[REGRESSION_DIFF, ({ path }) => (typeof diff === 'function' ? diff(segment(path, 1)) : diff)]]
    : []
  const handlers: ApiHandlers = [
    ...holdRoutes,
    ...retentionPageRoutes(ctx),
    ...releaseRoutes(ctx),
    ...diffRoutes,
    ...base,
    ...LAYOUT,
  ]
  return { handlers, writes, state, user }
}
