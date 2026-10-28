/**
 * W22a ACT-05 / ACT-04: retention holds and the keep-last floor.
 *
 * The constants mirror `backend/app/services/retention_vocab.py` value for
 * value (pinned by `backend/tests/test_retention_vocab_frontend_parity.py`,
 * which reads this file as TEXT — keep every constant a plain literal).
 * Enum-like fields are plain strings on the wire; a value outside these lists
 * is refused by the server with one fixed sentence (`HOLD_ERROR_SENTENCES`).
 *
 * Every reason, label and name below is user text: render it as React text,
 * never as HTML or markdown, and never build an href from it.
 */

export const HOLD_ENTITY_TYPES = ['project', 'release', 'run', 'compliance_pack'] as const
export type HoldEntityType = (typeof HOLD_ENTITY_TYPES)[number]

/** Derived on read from released_at / expires_at / now; never stored. */
export const HOLD_STATES = ['live', 'expired', 'released'] as const
export type HoldState = (typeof HOLD_STATES)[number]
/** The list read's `state` filter also takes `all`. */
export type HoldStateFilter = HoldState | 'all'

/** Place and release reasons alike (trimmed length). */
export const HOLD_REASON_MIN = 3
export const HOLD_REASON_MAX = 500
/** expires_at ∈ (now, now + 3650 days]. */
export const HOLD_MAX_EXPIRY_DAYS = 3650
export const HOLD_LIST_DEFAULT_LIMIT = 50
export const HOLD_LIST_MAX_LIMIT = 200

/** The ONE refusal sentence (409s, the criteria preview's blocked reasons, a job error). */
export const HELD_SENTENCE = 'Held: {reason}'
/** entity_label of a project hold. */
export const PROJECT_HOLD_LABEL = 'the whole project'

/** detail = {code, message} on the hold routes' own 4xx. */
export const HOLD_ERROR_SENTENCES = {
  unknown_entity_type: 'Unknown entity type. Valid: project, release, run, compliance_pack.',
  entity_id_required: 'entity_id is required for a release, run or compliance_pack hold.',
  entity_id_forbidden: 'A project hold covers the whole project; leave entity_id empty.',
  entity_not_found: 'That release, run or compliance pack is not in this project.',
  reason_length: 'A reason of 3 to 500 characters is required.',
  expiry_not_future: 'expires_at must be in the future.',
  expiry_too_far: 'expires_at may be at most 3650 days ahead.',
  hold_not_found: 'Hold not found.',
  hold_not_live: 'This hold is no longer live; it was released or it expired.',
  unknown_state: 'Unknown state. Valid: live, expired, released, all.',
  interactive_login_required: 'Holds are placed and released by a signed-in person; API keys can only read them.',
} as const
export type HoldErrorCode = keyof typeof HOLD_ERROR_SENTENCES

export const KEEP_LAST_RUNS_DEFAULT = 10
export const KEEP_LAST_RUNS_MIN = 0
export const KEEP_LAST_RUNS_MAX = 1000
export const FLOOR_NOTE_EXPLICIT =
  'Explicit deletions are not limited by the keep-last floor; it applies to the scheduled purge only.'

/** Copy only — the FK list itself is derived on the server from the ORM metadata. */
export const REFERENCE_LABELS = {
  'run_baselines.baseline_run_id': '{count} runs lose their baseline link',
  'run_diffs.baseline_run_id': '{count} run diffs lose their baseline run',
  'compliance_packs.test_run_id': '{count} compliance packs lose their run link',
} as const
/** The same copy for a count of 1. */
export const REFERENCE_LABELS_ONE = {
  'run_baselines.baseline_run_id': '1 run loses its baseline link',
  'run_diffs.baseline_run_id': '1 run diff loses its baseline run',
  'compliance_packs.test_run_id': '1 compliance pack loses its run link',
} as const
/** Every other derived FK. */
export const REFERENCE_FALLBACK_LABEL = '{count} {table} rows lose their {column} link'
export const REFERENCE_FALLBACK_LABEL_ONE = '1 {table} row loses its {column} link'
/** The run page's Changes tab (date = the viewer's locale date). */
export const BASELINE_PURGED_SENTENCE = 'Baseline build {build} was purged on {date}.'

/** A live hold as it appears inside another answer (release detail, pack list, preview). */
export interface HoldRef {
  hold_id: string
  entity_type: HoldEntityType | string
  entity_id: string | null
  entity_label: string | null
  reason: string
  expires_at: string | null
}

/** One hold as the list / place / release routes return it. */
export interface RetentionHold {
  id: string
  project_id: string
  entity_type: HoldEntityType | string
  entity_id: string | null
  entity_label: string | null
  reason: string
  expires_at: string | null
  state: HoldState | string
  placed_by_name: string | null
  placed_at: string
  released_at?: string | null
  released_by_name?: string | null
  release_reason?: string | null
}

/** GET …/retention-holds: `total` ignores the limit; `counts` ignores the state filter. */
export interface RetentionHoldList {
  items: RetentionHold[]
  total: number
  counts: Partial<Record<HoldState, number>>
}

/** POST …/retention-holds body. */
export interface RetentionHoldCreate {
  entity_type: HoldEntityType
  entity_id?: string | null
  reason: string
  expires_at?: string | null
}

/** What a caller asks `holdsCovering` about. */
export type HoldTarget =
  | { kind: 'project' }
  | { kind: 'release'; id: string }
  | { kind: 'compliance_pack'; id: string; releaseId?: string | null }

/** The fields `holdsCovering` reads: a `RetentionHold` and a `HoldRef` both qualify. */
export interface CoverableHold {
  entity_type: string
  entity_id: string | null
  expires_at?: string | null
  state?: string
  released_at?: string | null
}

/**
 * LIVE only: a row with a `state` is live when it says `live`; a `HoldRef`
 * (no state — the server sends live holds only) is live until its expiry.
 */
function isLive(hold: CoverableHold, now: Date): boolean {
  if (hold.state !== undefined) return hold.state === 'live'
  if (hold.released_at) return false
  if (!hold.expires_at) return true
  const expires = Date.parse(hold.expires_at)
  return Number.isNaN(expires) || expires > now.getTime()
}

/**
 * The live holds that cover `target`: the project hold ∪ the target's own
 * hold ∪ (for a compliance pack) its release's hold. Order is kept.
 */
export function holdsCovering<T extends CoverableHold>(
  holds: readonly T[] | null | undefined,
  target: HoldTarget,
  now: Date = new Date(),
): T[] {
  if (!holds) return []
  return holds.filter((hold) => {
    if (!isLive(hold, now)) return false
    if (hold.entity_type === 'project') return true
    if (target.kind === 'project') return false
    if (target.kind === 'release') {
      return hold.entity_type === 'release' && hold.entity_id === target.id
    }
    if (hold.entity_type === 'compliance_pack') return hold.entity_id === target.id
    return Boolean(target.releaseId) && hold.entity_type === 'release' && hold.entity_id === target.releaseId
  })
}

/** `Held: <reason>` — the server's refusal sentence, for client-side copy. */
export function heldSentence(reason: string): string {
  return HELD_SENTENCE.replace('{reason}', () => reason)
}
