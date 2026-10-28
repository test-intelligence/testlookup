/**
 * W22b ACT-03 / ACT-06 hermetic world: export before delete, the purge window / pause, per-field inheritance and
 * the instance retention defaults (`docs/deferred-scope-2026-10-13/work/W22b-contract.md` §6.2 G-A, §6.3 specs).
 *
 * `retentionExportWorld(options)` COMPOSES W22a's `retentionHoldsWorld` (its holds, storage, release and fail-closed
 * tail stay as they are) and answers FIRST, with the REAL server shapes:
 *  - `GET` / `PUT …/retention-policy` — the EFFECTIVE policy resolved per field (project ▸ instance ▸ code default)
 *    with `field_sources`, `inherit_values`, `purge_window_start_utc`, `purge_paused_until`,
 *    `next_scheduled_purge_at`; the PUT takes `inherit` / `pause_days` / `resume` / the window and refuses the three
 *    conflicts with `{"detail": {"code", "message"}}` (bounds and `audit >= runs` keep their plain-string 422);
 *  - `POST …/retention-policy/preview` (W22a's computed preview + `candidates.export_archives_expired`);
 *  - `POST …/deletion/preview` (W22a's computed criteria preview + `export_estimate`);
 *  - `POST …/deletion/execute` (records the body; validates `export`; a new job appears, already settled:
 *    `collect` → `awaiting_collection`, `retain` → `completed`, none → `completed`);
 *  - `GET …/deletion/jobs` → `{jobs, total}` (newest first, `limit`), `GET …/deletion/jobs/{id}`;
 *  - `POST …/deletion/jobs/{id}/collect` → a small REAL ZIP (`WORLD_ARCHIVE`, `manifest.json` last) with
 *    `X-Manifest-Sha256`, `X-Archive-Sha256`, `Content-Disposition`, `Content-Length`, `Cache-Control: no-store`
 *    — or a scripted `{"detail": {"code", "message"}}` refusal;
 *  - `POST …/deletion/jobs/{id}/acknowledge` (records the body; the server's 404 / 409 order; flips the job to
 *    `queued`);
 *  - `GET` / `PUT /api/v1/settings/retention-defaults`, `GET …/retention-defaults/enable-preview`,
 *    `POST …/retention-defaults/enable` (the exact phrase and the preview hash, `preview_stale`, `already_enabled`).
 * Every other request fails closed as today. Every route above is ADMIN (`Requires at least ADMIN role` otherwise).
 *
 * Fresh state per call; `state` is live and mutable (a spec may edit `jobs` or the instance between steps). The
 * clock is `NOW` (the pinned page clock). Sentinel names are non-hex words. Not a spec file.
 */
import { createHash } from 'node:crypto'

import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import {
  ARCHIVE_NOT_BACKUP,
  EXPORT_COLLECT_TTL_HOURS_DEFAULT,
  EXPORT_ERROR_SENTENCES,
  EXPORT_JOB_ERRORS,
  EXPORT_MAX_BYTES_DEFAULT,
  EXPORT_TARGETS,
  EXPORT_TIERS,
  INHERITABLE_FIELDS,
  POLICY_ERROR_SENTENCES,
  PURGE_WINDOW_DEFAULT_HOUR,
  enablePhrase,
  type ExportErrorCode,
  type FieldSource,
  type InheritableField,
  type PolicyErrorCode,
} from '../../../src/types/retentionExport'
import { NOW, PROJECT_ID, isoAgo } from './fixtures'
import { bodyOf, delegate, type RecordedWrite } from './fixtures-in-page'
import {
  ROLE_USERS,
  WORLD_ROLES,
  retentionHoldsWorld,
  worldPolicy,
  type RetentionHoldsWorld,
  type RetentionHoldsWorldOptions,
  type WorldRole,
} from './fixtures-retention'

export type { RecordedWrite }

const HOUR_MS = 3_600_000
const MB = 1024 * 1024
const atLeast = (role: WorldRole, min: WorldRole) => WORLD_ROLES.indexOf(role) >= WORLD_ROLES.indexOf(min)
const adminRefusal = () => respond(403, { detail: 'Requires at least ADMIN role' })
const exportError = (status: number, code: ExportErrorCode) =>
  respond(status, { detail: { code, message: EXPORT_ERROR_SENTENCES[code] } })
const policyError = (status: number, code: PolicyErrorCode) =>
  respond(status, { detail: { code, message: POLICY_ERROR_SENTENCES[code] } })
const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

// ── The archive: a small REAL ZIP (stored entries, fixed 1980-01-01 dates, manifest.json LAST) ──────────────

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n += 1) {
    let c = n
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

function crc32(data: Buffer): number {
  let c = 0xffffffff
  for (const byte of data) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

/** A STORED (uncompressed) ZIP of `members`, in order; every entry dated 1980-01-01 00:00 like the server's. */
function zipStored(members: Array<[string, Buffer]>): Buffer {
  const parts: Buffer[] = []
  const central: Buffer[] = []
  let offset = 0
  for (const [name, data] of members) {
    const nameBytes = Buffer.from(name, 'utf8')
    const crc = crc32(data)
    const local = Buffer.alloc(30)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(20, 4)
    local.writeUInt16LE(0x0800, 6) // UTF-8 names
    local.writeUInt16LE(0x0021, 12) // 1980-01-01
    local.writeUInt32LE(crc, 14)
    local.writeUInt32LE(data.length, 18)
    local.writeUInt32LE(data.length, 22)
    local.writeUInt16LE(nameBytes.length, 26)
    const entry = Buffer.alloc(46)
    entry.writeUInt32LE(0x02014b50, 0)
    entry.writeUInt16LE(20, 4)
    entry.writeUInt16LE(20, 6)
    entry.writeUInt16LE(0x0800, 8)
    entry.writeUInt16LE(0x0021, 14)
    entry.writeUInt32LE(crc, 16)
    entry.writeUInt32LE(data.length, 20)
    entry.writeUInt32LE(data.length, 24)
    entry.writeUInt16LE(nameBytes.length, 28)
    entry.writeUInt32LE(offset, 42)
    parts.push(local, nameBytes, data)
    central.push(entry, nameBytes)
    offset += local.length + nameBytes.length + data.length
  }
  const directory = Buffer.concat(central)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(members.length, 8)
  end.writeUInt16LE(members.length, 10)
  end.writeUInt32LE(directory.length, 12)
  end.writeUInt32LE(offset, 16)
  return Buffer.concat([...parts, directory, end])
}

const sha256 = (data: Buffer | string) => createHash('sha256').update(data).digest('hex')

export interface WorldArchive {
  bytes: Buffer
  /** What `X-Manifest-Sha256` carries and the acknowledgement must send back. */
  manifestSha256: string
  /** What `X-Archive-Sha256` carries: the whole object. */
  archiveSha256: string
}

/** The ONE archive every collect answers: README, deletion.json, then manifest.json (hashing every member). */
function buildWorldArchive(): WorldArchive {
  const members: Array<[string, Buffer]> = [
    ['README.md', Buffer.from(`# Deletion archive (evidence)\n\n${ARCHIVE_NOT_BACKUP}\n`, 'utf8')],
    ['deletion.json', Buffer.from(`${JSON.stringify({ project: 'Payments sentinel', target: 'collect', tier: 'evidence' }, null, 2)}\n`, 'utf8')],
  ]
  const manifest = Buffer.from(
    `${JSON.stringify({ files: Object.fromEntries(members.map(([name, data]) => [name, sha256(data)])), tier: 'evidence' }, null, 2)}\n`,
    'utf8',
  )
  const bytes = zipStored([...members, ['manifest.json', manifest]])
  return { bytes, manifestSha256: sha256(manifest), archiveSha256: sha256(bytes) }
}

export const WORLD_ARCHIVE: WorldArchive = buildWorldArchive()

// ── Default answers (the REAL shapes; every W22b field present) ──────────────

/** `ExportEstimate` over `runCount` deletable runs: summary ≈ 2.1 MB, evidence ≈ 14 MB, full ≈ 1.2 GB (over the cap). */
export function worldExportEstimate(runCount = 3, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const exportable = runCount > 0 && runCount <= 2000
  return {
    run_count: runCount,
    exportable,
    refusal: exportable ? null : EXPORT_ERROR_SENTENCES[runCount > 0 ? 'too_many_runs' : 'nothing_to_export'],
    reclaim_bytes: 180 * MB,
    reclaim_object_bytes: 172 * MB,
    reclaim_mongo_bytes: 8 * MB,
    reclaim_measured: true,
    tiers: [
      { tier: 'summary', archive_bytes: Math.round(2.1 * MB), measured: true, over_cap: false },
      { tier: 'evidence', archive_bytes: 14 * MB, measured: true, over_cap: false },
      { tier: 'full', archive_bytes: Math.round(1.2 * 1024 * MB), measured: true, over_cap: true },
    ],
    max_bytes: EXPORT_MAX_BYTES_DEFAULT,
    collect_ttl_hours: EXPORT_COLLECT_TTL_HOURS_DEFAULT,
    note: ARCHIVE_NOT_BACKUP,
    ...overrides,
  }
}

/** A deletion job as `GET …/deletion/jobs` answers it (`DeletionJobResponse`, every W22b field present). */
export type WorldJob = Record<string, unknown> & { id: string; status: string }

let jobSequence = 0

/** A canonical (version 4 shaped) uuid, distinct per call. */
export function worldJobId(): string {
  jobSequence += 1
  return `00000000-0000-4000-8000-${String(0xb000 + jobSequence).padStart(12, '0')}`
}

/** A finished criteria job without an export (12 runs, 180 MB reclaimed). */
export function worldJob(overrides: Record<string, unknown> = {}): WorldJob {
  return {
    id: worldJobId(),
    project_id: PROJECT_ID,
    job_kind: 'criteria',
    status: 'completed',
    criteria: { statuses: ['FAILED'], older_than_days: 30 },
    counts: { runs: 12 },
    bytes_reclaimed: 180 * MB,
    holds_honoured: null,
    references_broken: null,
    error: null,
    requested_at: isoAgo(0, 6),
    started_at: isoAgo(0, 6),
    finished_at: isoAgo(0, 5),
    export_tier: null,
    export_target: null,
    export_bytes: null,
    export_manifest_sha256: null,
    export_verified_at: null,
    collect_expires_at: null,
    collected_at: null,
    acknowledged_at: null,
    export_summary: null,
    ...overrides,
  } as WorldJob
}

/** A verified `collect` archive waiting for its download: its size and digest are `WORLD_ARCHIVE`'s. */
export function worldAwaitingJob(overrides: Record<string, unknown> = {}): WorldJob {
  return worldJob({
    status: 'awaiting_collection',
    counts: null,
    bytes_reclaimed: null,
    requested_at: isoAgo(0, 2),
    started_at: isoAgo(0, 2),
    finished_at: null,
    export_tier: 'evidence',
    export_target: 'collect',
    export_bytes: WORLD_ARCHIVE.bytes.length,
    export_manifest_sha256: WORLD_ARCHIVE.manifestSha256,
    export_verified_at: isoAgo(0, 1),
    collect_expires_at: new Date(NOW.getTime() + 70 * HOUR_MS).toISOString(),
    export_summary: { run_count: 12, file_count: 3, objects_not_archived: 0, missing_runs: 0, excluded_runs: 0, signing_state: 'signed' },
    ...overrides,
  })
}

/** A finished `retain` export: the archive stays in storage (14 MB of 180 MB reclaimed: net 166 MB, ratio 1:13). */
export function worldRetainedJob(overrides: Record<string, unknown> = {}): WorldJob {
  return worldJob({
    requested_at: isoAgo(1),
    started_at: isoAgo(1),
    finished_at: isoAgo(1, -1),
    export_tier: 'full',
    export_target: 'retain',
    export_bytes: 14 * MB,
    export_manifest_sha256: WORLD_ARCHIVE.manifestSha256,
    export_verified_at: isoAgo(1, -1),
    export_summary: { run_count: 12, file_count: 40, objects_not_archived: 3, missing_runs: 0, excluded_runs: 1, signing_state: 'signed' },
    ...overrides,
  })
}

/** A `collect` job nobody confirmed in time: expired, nothing deleted, the archive removed. */
export function worldExpiredJob(overrides: Record<string, unknown> = {}): WorldJob {
  return worldAwaitingJob({
    status: 'expired',
    requested_at: isoAgo(4),
    started_at: isoAgo(4),
    finished_at: isoAgo(1),
    export_verified_at: isoAgo(4),
    collect_expires_at: isoAgo(1),
    error: EXPORT_JOB_ERRORS.expired.replace('{hours}', String(EXPORT_COLLECT_TTL_HOURS_DEFAULT)),
    ...overrides,
  })
}

/** An export whose stored archive could not be verified: failed, nothing deleted. */
export function worldFailedExportJob(overrides: Record<string, unknown> = {}): WorldJob {
  return worldJob({
    status: 'failed',
    counts: null,
    bytes_reclaimed: null,
    requested_at: isoAgo(2),
    started_at: isoAgo(2),
    finished_at: isoAgo(2, -1),
    export_tier: 'summary',
    export_target: 'collect',
    error: EXPORT_JOB_ERRORS.unverified.replace('{reason}', 'digest mismatch'),
    ...overrides,
  })
}

/** The four rows of the jobs card's visual: awaiting, completed retain (ratio + net), expired, failed export. */
export function worldJobs(): WorldJob[] {
  return [worldAwaitingJob(), worldRetainedJob(), worldExpiredJob(), worldFailedExportJob()]
}

/** The seven code defaults (what a NULL means). */
export const CODE_DEFAULTS: Record<InheritableField, boolean | number> = {
  enabled: false,
  raw_events_days: 90,
  runs_days: 365,
  artifacts_days: 180,
  audit_days: 2555,
  keep_last_runs: 10,
  purge_window_start_utc: PURGE_WINDOW_DEFAULT_HOUR,
}

/** The stored instance row: OFF (D12), every value NULL (= the code default). */
export interface WorldInstance {
  enabled: boolean
  raw_events_days: number | null
  runs_days: number | null
  artifacts_days: number | null
  audit_days: number | null
  keep_last_runs: number | null
  purge_window_start_utc: number | null
  enabled_at: string | null
  enabled_by_name: string | null
  updated_at: string | null
  /** Per field: projects inheriting it (`InstanceRetentionPolicyRead.inheriting_projects`); default 4 each. */
  inheriting_projects?: Partial<Record<InheritableField, number>>
}

export function worldInstance(overrides: Partial<WorldInstance> = {}): WorldInstance {
  return {
    enabled: false,
    raw_events_days: null,
    runs_days: null,
    artifacts_days: null,
    audit_days: null,
    keep_last_runs: null,
    purge_window_start_utc: null,
    enabled_at: null,
    enabled_by_name: null,
    updated_at: isoAgo(3),
    ...overrides,
  }
}

/** `InstanceEnablePreviewResponse` for `n` affected projects (≤ 500 listed, `total` always n). */
export function worldEnablePreview(n = 3, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const names = ['Payments sentinel', 'Checkout sentinel', 'Ledger sentinel', 'Search sentinel']
  const listed = Math.min(n, 500)
  return {
    items: Array.from({ length: listed }, (_, i) => ({
      project_id: `22222222-2222-4222-8222-${String(0xa100 + i).padStart(12, '0')}`,
      name: i < names.length ? names[i] : `Project sentinel ${i + 1}`,
      is_active: i % 4 !== 3,
      has_policy_row: i % 2 === 1,
      effective_after: { raw_events_days: 90, runs_days: 365, artifacts_days: 180, audit_days: 2555, keep_last_runs: 10, purge_window_start_utc: 2 },
      runs_past_window: [42, 7, 0, 3][i % 4],
    })),
    total: n,
    truncated: n > listed,
    preview_hash: 'feed'.repeat(16),
    confirmation_phrase: enablePhrase(n),
    already_enabled: false,
    ...overrides,
  }
}

// ── The per-field resolver (the server's get_effective_policy, W-1) ──────────

/** The project's STORED policy row: each inheritable field's column (null = inherit), the pause, the AI caps. */
export type StoredPolicy = Record<InheritableField, boolean | number | null> & {
  purge_paused_until: string | null
  ai_versions_per_run: number | null
  ai_versions_per_test: number | null
  /** A row exists (`source: "custom"`); without one every column reads null. */
  has_row: boolean
}

function storedFrom(policy: Record<string, unknown> | undefined): StoredPolicy {
  const merged = worldPolicy(policy ?? {})
  const hasRow = merged.source !== 'default'
  const column = (field: string, fallback: boolean | number | null = null) =>
    hasRow && field in merged ? (merged[field] as boolean | number | null) : fallback
  return {
    enabled: column('enabled'),
    raw_events_days: column('raw_events_days'),
    runs_days: column('runs_days'),
    artifacts_days: column('artifacts_days'),
    audit_days: column('audit_days'),
    keep_last_runs: column('keep_last_runs'),
    purge_window_start_utc: column('purge_window_start_utc'),
    purge_paused_until: (merged.purge_paused_until as string | null | undefined) ?? null,
    ai_versions_per_run: (merged.ai_versions_per_run as number | null | undefined) ?? 20,
    ai_versions_per_test: (merged.ai_versions_per_test as number | null | undefined) ?? 10,
    has_row: hasRow,
  }
}

/** What a field reads when inherited: the instance's value (for `enabled`: only when on), else the code default. */
function inheritedValue(field: InheritableField, instance: WorldInstance): boolean | number {
  if (field === 'enabled') return instance.enabled ? true : (CODE_DEFAULTS.enabled as boolean)
  const value = instance[field]
  return value === null || value === undefined ? CODE_DEFAULTS[field] : value
}

function inheritedSource(field: InheritableField, instance: WorldInstance): FieldSource {
  if (field === 'enabled') return instance.enabled ? 'instance' : 'default'
  return instance[field] === null || instance[field] === undefined ? 'default' : 'instance'
}

/** The first `HH:00` UTC (HH = the window) strictly after max(now, paused_until); null while disabled. */
function nextScheduledPurge(enabled: boolean, window: number, pausedUntil: string | null): string | null {
  if (!enabled) return null
  const from = Math.max(NOW.getTime(), pausedUntil ? new Date(pausedUntil).getTime() : 0)
  const candidate = new Date(from)
  candidate.setUTCMinutes(0, 0, 0)
  candidate.setUTCHours(window)
  while (candidate.getTime() <= from) candidate.setUTCDate(candidate.getUTCDate() + 1)
  return candidate.toISOString()
}

/** `RetentionPolicyRead`: the effective values, their sources, what each reads if inherited, the schedule. */
function effectivePolicy(stored: StoredPolicy, instance: WorldInstance, lastPurge: unknown): Record<string, unknown> {
  const values = {} as Record<InheritableField, boolean | number>
  const sources = {} as Record<InheritableField, FieldSource>
  const inherit = {} as Record<InheritableField, boolean | number>
  for (const field of INHERITABLE_FIELDS) {
    inherit[field] = inheritedValue(field, instance)
    const own = stored[field]
    values[field] = own === null || own === undefined ? inherit[field] : own
    sources[field] = own === null || own === undefined ? inheritedSource(field, instance) : 'project'
  }
  // A mix of sources never resolves an audit window shorter than the runs window (D-9).
  values.audit_days = Math.max(Number(values.audit_days), Number(values.runs_days))
  const paused = stored.purge_paused_until && new Date(stored.purge_paused_until).getTime() > NOW.getTime()
    ? stored.purge_paused_until
    : null
  return {
    ...values,
    ai_versions_per_run: stored.ai_versions_per_run ?? 20,
    ai_versions_per_test: stored.ai_versions_per_test ?? 10,
    source: stored.has_row ? 'custom' : 'default',
    last_purge: lastPurge ?? null,
    purge_paused_until: paused,
    next_scheduled_purge_at: nextScheduledPurge(Boolean(values.enabled), Number(values.purge_window_start_utc), paused),
    field_sources: sources,
    inherit_values: inherit,
  }
}

/** `InstanceRetentionPolicyRead`. */
function instanceRead(instance: WorldInstance): Record<string, unknown> {
  const effective = {} as Record<InheritableField, boolean | number>
  for (const field of INHERITABLE_FIELDS) effective[field] = inheritedValue(field, instance)
  const counts = instance.inheriting_projects ?? {}
  return {
    enabled: instance.enabled,
    raw_events_days: instance.raw_events_days,
    runs_days: instance.runs_days,
    artifacts_days: instance.artifacts_days,
    audit_days: instance.audit_days,
    keep_last_runs: instance.keep_last_runs,
    purge_window_start_utc: instance.purge_window_start_utc,
    code_defaults: { ...CODE_DEFAULTS },
    effective,
    inheriting_projects: Object.fromEntries(INHERITABLE_FIELDS.map((field) => [field, counts[field] ?? 4])),
    enabled_at: instance.enabled_at,
    enabled_by_name: instance.enabled_by_name,
    updated_at: instance.updated_at,
  }
}

/** The day bounds of `retention_service.FIELD_BOUNDS` and the floor / window bounds of the schemas. */
const BOUNDS: Partial<Record<InheritableField, [number, number]>> = {
  raw_events_days: [7, 3650],
  runs_days: [30, 3650],
  artifacts_days: [7, 3650],
  audit_days: [365, 3650],
  keep_last_runs: [0, 1000],
  purge_window_start_utc: [0, 23],
}

/** FastAPI's 422 for a value outside a schema bound (`detail` is a list of `{loc, msg, type}`). */
function boundsRefusal(body: Record<string, unknown>): ReturnType<typeof respond> | null {
  for (const [field, [lo, hi]] of Object.entries(BOUNDS) as Array<[InheritableField, [number, number]]>) {
    const value = body[field]
    if (value === undefined || value === null) continue
    if (typeof value !== 'number' || !Number.isInteger(value) || value < lo || value > hi) {
      const over = typeof value === 'number' && value > hi
      const msg = over ? `Input should be less than or equal to ${hi}` : `Input should be greater than or equal to ${lo}`
      return respond(422, { detail: [{ type: over ? 'less_than_equal' : 'greater_than_equal', loc: ['body', field], msg, input: value }] })
    }
  }
  return null
}

/** `_validate_merged`'s cross-field sentence (a plain-string 422). */
function auditRefusal(runs: number, audit: number): ReturnType<typeof respond> | null {
  if (audit >= runs) return null
  return respond(422, {
    detail: `audit_days must be >= runs_days — audit records must outlive the runs they describe (got audit_days=${audit}, runs_days=${runs})`,
  })
}

// ── The world ────────────────────────────────────────────────────────────────

export interface RetentionExportWorldOptions extends Omit<RetentionHoldsWorldOptions, 'policy' | 'writes'> {
  /** The project's STORED policy row, merged over W22a's `worldPolicy()` (explicit everywhere, enabled); a `null`
   * field inherits; `source: 'default'` = no row. The GET answers the resolved policy. */
  policy?: Record<string, unknown>
  /** The jobs of the project (any order; answered newest first). Default none. */
  jobs?: WorldJob[]
  /** The stored instance row, merged over `worldInstance()` (off, every value NULL). */
  instance?: Partial<WorldInstance>
  /** The enable preview while the instance is off; default `worldEnablePreview(3)`. */
  enablePreview?: Record<string, unknown>
  /** The collect answer: a scripted refusal instead of the archive, or fewer bytes than the job's `export_bytes`. */
  archive?: { error?: { status: number; code: ExportErrorCode }; truncate?: boolean }
  /** Scripted refusals (`{"detail": {"code", "message"}}`) of execute / acknowledge / the instance enable. */
  executeError?: { status: number; code: ExportErrorCode }
  acknowledgeError?: { status: number; code: ExportErrorCode }
  enableError?: { status: number; code: PolicyErrorCode }
  /** `candidates.export_archives_expired` of the purge preview (default 0). */
  archivesExpired?: number
  /** Where answered writes are recorded; default a fresh array. */
  writes?: RecordedWrite[]
}

/** The world's live state (mutable between steps). */
export interface RetentionExportWorldState {
  stored: StoredPolicy
  instance: WorldInstance
  jobs: WorldJob[]
  /** The last criteria preview answered (execute reads its run count and estimate). */
  lastCriteriaPreview: Record<string, unknown> | null
}

export interface RetentionExportWorld {
  handlers: ApiHandlers
  writes: RecordedWrite[]
  state: RetentionExportWorldState
  /** The composed W22a world (its `state.holds` stays editable). */
  holds: RetentionHoldsWorld
  /** The `user` to sign in as. */
  user: RetentionHoldsWorld['user']
  /** What every collect answers. */
  archive: WorldArchive
}

/**
 * The W22b world (see the module docstring). Handlers in order: the W22b routes, then every W22a handler (holds,
 * the retention page's other reads, release / packs, `base`, `LAYOUT`).
 */
export function retentionExportWorld(options: RetentionExportWorldOptions = {}): RetentionExportWorld {
  const role: WorldRole = options.role ?? 'ADMIN'
  const user = ROLE_USERS[role]
  const projectId = options.projectId ?? PROJECT_ID
  const writes = options.writes ?? []
  const state: RetentionExportWorldState = {
    stored: storedFrom(options.policy),
    instance: worldInstance(options.instance),
    jobs: (options.jobs ?? []).map((job) => ({ ...job })),
    lastCriteriaPreview: null,
  }
  const lastPurge = worldPolicy(options.policy ?? {}).last_purge
  const resolved = () => effectivePolicy(state.stored, state.instance, lastPurge)
  const holds = retentionHoldsWorld({ ...options, policy: resolved(), writes })
  const record = (request: ApiRequest): Record<string, unknown> => {
    const body = bodyOf(request)
    writes.push({ line: `${request.method} ${request.path}`, body })
    return isObject(body) ? body : {}
  }
  const base = `/api/v1/projects/${projectId}`
  const routes: ApiHandlers = [
    ...policyRoutes(),
    ...deletionRoutes(),
    ...jobRoutes(),
    ...instanceRoutes(),
  ]
  return { handlers: [...routes, ...holds.handlers], writes, state, holds, user, archive: WORLD_ARCHIVE }

  /** GET / PUT the policy (resolved per field) and the purge preview (+ `export_archives_expired`). */
  function policyRoutes(): ApiHandlers {
    return [
      [`${base}/retention-policy`, () => (atLeast(role, 'ADMIN') ? resolved() : adminRefusal())],
      [`${base}/retention-policy`, (request) => {
        const body = record(request)
        if (!atLeast(role, 'ADMIN')) return adminRefusal()
        const inherit = body.inherit
        if (inherit !== undefined && inherit !== null) {
          if (!Array.isArray(inherit) || inherit.some((f) => !(INHERITABLE_FIELDS as readonly unknown[]).includes(f))) {
            return policyError(422, 'unknown_inherit_field')
          }
          if (inherit.some((f) => body[f as string] !== undefined && body[f as string] !== null)) {
            return policyError(422, 'inherit_conflict')
          }
        }
        if (body.pause_days !== undefined && body.pause_days !== null && body.resume) return policyError(422, 'pause_conflict')
        const bounds = boundsRefusal(body)
        if (bounds) return bounds
        if (body.pause_days !== undefined && body.pause_days !== null) {
          const days = body.pause_days
          if (typeof days !== 'number' || !Number.isInteger(days) || days < 1 || days > 90) {
            return respond(422, { detail: [{ type: 'less_than_equal', loc: ['body', 'pause_days'], msg: 'Input should be between 1 and 90', input: days }] })
          }
        }
        const next: StoredPolicy = { ...state.stored, has_row: true }
        for (const field of INHERITABLE_FIELDS) {
          if (body[field] !== undefined && body[field] !== null) next[field] = body[field] as boolean | number
        }
        for (const field of (Array.isArray(inherit) ? inherit : []) as InheritableField[]) next[field] = null
        for (const cap of ['ai_versions_per_run', 'ai_versions_per_test'] as const) {
          if (typeof body[cap] === 'number') next[cap] = body[cap] as number
        }
        if (typeof body.pause_days === 'number') {
          next.purge_paused_until = new Date(NOW.getTime() + body.pause_days * 24 * HOUR_MS).toISOString()
        }
        if (body.resume === true) next.purge_paused_until = null
        const merged = effectivePolicy(next, state.instance, lastPurge)
        const audit = auditRefusal(Number(merged.runs_days), Number(next.audit_days ?? inheritedValue('audit_days', state.instance)))
        if (audit) return audit
        state.stored = next
        holds.state.policy = resolved()
        return holds.state.policy
      }, 'PUT'],
      [`${base}/retention-policy/preview`, (request) => {
        const answer = delegate(holds.handlers, request)
        if (!isObject(answer) || !isObject(answer.candidates)) return answer
        return { ...answer, candidates: { ...answer.candidates, export_archives_expired: options.archivesExpired ?? 0 } }
      }, 'POST'],
    ]
  }

  /** The criteria preview (+ `export_estimate`) and execute (+ `export`). */
  function deletionRoutes(): ApiHandlers {
    return [
      [`${base}/deletion/preview`, (request) => {
        const answer = delegate(holds.handlers, request)
        if (!isObject(answer)) return answer
        const scripted = Object.prototype.hasOwnProperty.call(options.criteriaPreview ?? {}, 'export_estimate')
        const preview = scripted ? answer : { ...answer, export_estimate: worldExportEstimate(Number(answer.run_count ?? 0)) }
        state.lastCriteriaPreview = preview
        return preview
      }, 'POST'],
      [`${base}/deletion/execute`, (request) => {
        const body = record(request)
        if (!atLeast(role, 'ADMIN')) return adminRefusal()
        if (options.executeError) return exportError(options.executeError.status, options.executeError.code)
        const preview = state.lastCriteriaPreview ?? {}
        const jobId = typeof body.job_id === 'string' ? body.job_id : worldJobId()
        const runCount = Number(preview.run_count ?? 0)
        const choice = isObject(body.export) ? body.export : null
        if (choice) {
          if (!(EXPORT_TIERS as readonly unknown[]).includes(choice.tier)) return exportError(422, 'unknown_tier')
          if (!(EXPORT_TARGETS as readonly unknown[]).includes(choice.target)) return exportError(422, 'unknown_target')
          const estimate = isObject(preview.export_estimate) ? preview.export_estimate : null
          if (runCount > 2000) return exportError(409, 'too_many_runs')
          if (runCount === 0) return exportError(409, 'nothing_to_export')
          const tiers = Array.isArray(estimate?.tiers) ? (estimate.tiers as Array<Record<string, unknown>>) : []
          if (tiers.some((t) => t.tier === choice.tier && t.over_cap === true)) return exportError(409, 'over_cap')
        }
        const tier = choice ? String(choice.tier) : null
        const target = choice ? String(choice.target) : null
        const settled = target === 'collect'
          ? worldAwaitingJob({
              id: jobId,
              export_tier: tier,
              requested_at: NOW.toISOString(),
              started_at: NOW.toISOString(),
              export_verified_at: NOW.toISOString(),
              collect_expires_at: new Date(NOW.getTime() + EXPORT_COLLECT_TTL_HOURS_DEFAULT * HOUR_MS).toISOString(),
            })
          : worldJob({
              id: jobId,
              counts: { runs: runCount },
              requested_at: NOW.toISOString(),
              started_at: NOW.toISOString(),
              finished_at: NOW.toISOString(),
              ...(target === 'retain'
                ? { export_tier: tier, export_target: 'retain', export_bytes: 14 * MB, export_manifest_sha256: WORLD_ARCHIVE.manifestSha256, export_verified_at: NOW.toISOString() }
                : {}),
            })
        if (settled.export_summary && isObject(settled.export_summary)) {
          settled.export_summary = { ...settled.export_summary, run_count: runCount }
        }
        state.jobs = [settled, ...state.jobs.filter((job) => job.id !== jobId)]
        return respond(202, {
          job_id: jobId,
          run_count: runCount,
          status: 'accepted',
          ...(choice ? { export_tier: tier, export_target: target } : {}),
        })
      }, 'POST'],
    ]
  }

  /** The jobs list / one job, collect (the archive) and acknowledge. */
  function jobRoutes(): ApiHandlers {
    const jobPath = (suffix: string) => new RegExp(`^${base}/deletion/jobs/([^/]+)${suffix}$`)
    const jobFrom = (path: string, suffix: string) => {
      const id = jobPath(suffix).exec(path)?.[1]
      return state.jobs.find((job) => job.id === id && (job.project_id ?? projectId) === projectId) ?? null
    }
    /** `expired`, or still awaiting past its `collect_expires_at` (the beat has not run yet). */
    const expired = (job: WorldJob) =>
      job.status === 'expired'
      || (job.status === 'awaiting_collection' && typeof job.collect_expires_at === 'string'
        && new Date(job.collect_expires_at).getTime() <= NOW.getTime())
    const newestFirst = () =>
      [...state.jobs].sort((a, b) => String(b.requested_at).localeCompare(String(a.requested_at)) || b.id.localeCompare(a.id))
    return [
      [`${base}/deletion/jobs`, ({ url }) => {
        if (!atLeast(role, 'ADMIN')) return adminRefusal()
        const limit = Number(url.searchParams.get('limit') ?? 50)
        return { jobs: newestFirst().slice(0, Number.isInteger(limit) && limit > 0 ? limit : 50), total: state.jobs.length }
      }],
      [jobPath(''), ({ path }) => {
        if (!atLeast(role, 'ADMIN')) return adminRefusal()
        return jobFrom(path, '') ?? respond(404, { detail: 'Deletion job not found' })
      }],
      [jobPath('/collect'), async (request) => {
        record(request)
        if (!atLeast(role, 'ADMIN')) return adminRefusal()
        if (options.archive?.error) return exportError(options.archive.error.status, options.archive.error.code)
        const job = jobFrom(request.path, '/collect')
        if (!job) return exportError(404, 'job_not_found')
        if (expired(job)) return exportError(409, 'collect_expired')
        if (job.status !== 'awaiting_collection') return exportError(409, 'not_awaiting')
        job.collected_at = job.collected_at ?? NOW.toISOString()
        const bytes = options.archive?.truncate ? WORLD_ARCHIVE.bytes.subarray(0, WORLD_ARCHIVE.bytes.length - 7) : WORLD_ARCHIVE.bytes
        const day = NOW.toISOString().slice(0, 10).replace(/-/g, '')
        // The shared router serialises JSON (or a string without headers), so the archive is fulfilled here, as
        // bytes with its headers; the promise below never settles so the router does not answer a second time
        // (Playwright does not wait on an in-flight route callback when the page closes).
        await request.route.fulfill({
          status: 200,
          contentType: 'application/zip',
          headers: {
            'Content-Disposition': `attachment; filename="testlookup-deletion-archive-${job.id.slice(0, 8)}-${day}.zip"`,
            'Content-Length': String(bytes.length),
            'X-Manifest-Sha256': WORLD_ARCHIVE.manifestSha256,
            'X-Archive-Sha256': WORLD_ARCHIVE.archiveSha256,
            'Cache-Control': 'no-store',
          },
          body: bytes,
        })
        return new Promise<never>(() => {})
      }, 'POST'],
      [jobPath('/acknowledge'), (request) => {
        const body = record(request)
        if (!atLeast(role, 'ADMIN')) return adminRefusal()
        const digest = body.manifest_sha256
        if (typeof digest !== 'string' || !/^[0-9a-fA-F]{64}$/.test(digest)) {
          return respond(422, { detail: [{ type: 'string_pattern_mismatch', loc: ['body', 'manifest_sha256'], msg: "String should match pattern '^[0-9a-fA-F]{64}$'", input: digest ?? null }] })
        }
        if (options.acknowledgeError) return exportError(options.acknowledgeError.status, options.acknowledgeError.code)
        const job = jobFrom(request.path, '/acknowledge')
        if (!job) return exportError(404, 'job_not_found')
        if (expired(job)) return exportError(409, 'collect_expired')
        if (job.status !== 'awaiting_collection') return exportError(409, 'not_awaiting')
        if (!job.collected_at) return exportError(409, 'not_collected')
        if (digest.toLowerCase() !== String(job.export_manifest_sha256)) return exportError(409, 'manifest_mismatch')
        job.status = 'queued'
        job.acknowledged_at = NOW.toISOString()
        return job
      }, 'POST'],
    ]
  }

  /** The instance defaults: read, write (never `enabled: true`), the enable preview and the typed enable. */
  function instanceRoutes(): ApiHandlers {
    const path = '/api/v1/settings/retention-defaults'
    /** Like the server: the hash covers the affected ids AND the instance values, so a save in between is stale. */
    const preview = (): Record<string, unknown> => {
      if (state.instance.enabled) {
        return { items: [], total: 0, truncated: false, preview_hash: 'feed'.repeat(16), confirmation_phrase: enablePhrase(0), already_enabled: true }
      }
      const answer = worldEnablePreview(3, options.enablePreview ?? {})
      if (options.enablePreview && 'preview_hash' in options.enablePreview) return answer
      const ids = (answer.items as Array<{ project_id: string }>).map((item) => item.project_id).sort()
      const values = Object.fromEntries(INHERITABLE_FIELDS.map((field) => [field, inheritedValue(field, state.instance)]))
      return { ...answer, preview_hash: sha256(JSON.stringify({ project_ids: ids, values })) }
    }
    return [
      [path, () => (atLeast(role, 'ADMIN') ? instanceRead(state.instance) : adminRefusal())],
      [path, (request) => {
        const body = record(request)
        if (!atLeast(role, 'ADMIN')) return adminRefusal()
        if (body.enabled === true) return policyError(422, 'enable_via_flow')
        const clear = Array.isArray(body.clear) ? body.clear : []
        const clearable = INHERITABLE_FIELDS.filter((field) => field !== 'enabled') as readonly unknown[]
        if (body.clear !== undefined && body.clear !== null && (!Array.isArray(body.clear) || clear.some((f) => !clearable.includes(f)))) {
          return policyError(422, 'unknown_inherit_field')
        }
        const bounds = boundsRefusal(body)
        if (bounds) return bounds
        const next: WorldInstance = { ...state.instance }
        for (const field of INHERITABLE_FIELDS) {
          if (field === 'enabled') continue
          if (typeof body[field] === 'number') next[field] = body[field] as number
        }
        for (const field of clear as Array<Exclude<InheritableField, 'enabled'>>) next[field] = null
        if (body.enabled === false) next.enabled = false
        const audit = auditRefusal(
          Number(inheritedValue('runs_days', next)),
          Number(inheritedValue('audit_days', next)),
        )
        if (audit) return audit
        state.instance = { ...next, updated_at: NOW.toISOString() }
        holds.state.policy = resolved()
        return instanceRead(state.instance)
      }, 'PUT'],
      [`${path}/enable-preview`, () => (atLeast(role, 'ADMIN') ? preview() : adminRefusal())],
      [`${path}/enable`, (request) => {
        const body = record(request)
        if (!atLeast(role, 'ADMIN')) return adminRefusal()
        if (options.enableError) return policyError(options.enableError.status, options.enableError.code)
        if (state.instance.enabled) return policyError(409, 'already_enabled')
        const current = preview()
        if (body.preview_hash !== current.preview_hash) return policyError(409, 'preview_stale')
        if (body.confirmation !== current.confirmation_phrase) return policyError(422, 'confirmation_mismatch')
        state.instance = {
          ...state.instance,
          enabled: true,
          enabled_at: NOW.toISOString(),
          enabled_by_name: user.full_name,
          updated_at: NOW.toISOString(),
        }
        holds.state.policy = resolved()
        return instanceRead(state.instance)
      }, 'POST'],
    ]
  }
}
