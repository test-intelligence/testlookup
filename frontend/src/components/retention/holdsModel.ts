/**
 * holdsModel — the pure copy and helpers of retention holds and the
 * keep-last floor (W22a, ACT-05 / ACT-04).
 *
 * Every sentence the Retention page, the release hold strip and the
 * compliance-pack controls show lives here, so a test imports one string
 * instead of re-typing it. Pure functions only: no React, no request.
 *
 * Safety: hold reasons, labels and names are returned as plain strings and
 * rendered as React text by the callers. A link is built ONLY from an id and
 * an entity type through a fixed map (`entityHref`), never from text.
 *
 * Inputs are structural (`HoldLike`, `PreviewKeptInput`, …) so the list row
 * (`RetentionHold`), the ref inside another answer (`HoldRef`) and the
 * preview's hold block are all accepted without a cast.
 *
 * The wire vocabulary (reason bounds, floor bounds, the server's sentences)
 * is NOT re-declared here: it is re-exported from `@/types/retentionHolds`,
 * the one mirror of `retention_vocab.py`.
 */
import {
  FLOOR_NOTE_EXPLICIT,
  HOLD_ERROR_SENTENCES,
  HOLD_REASON_MAX,
  HOLD_REASON_MIN,
  KEEP_LAST_RUNS_DEFAULT,
  KEEP_LAST_RUNS_MAX,
  KEEP_LAST_RUNS_MIN,
  PROJECT_HOLD_LABEL,
  heldSentence,
} from '@/types/retentionHolds'

export {
  FLOOR_NOTE_EXPLICIT,
  HOLD_REASON_MAX,
  HOLD_REASON_MIN,
  KEEP_LAST_RUNS_DEFAULT,
  KEEP_LAST_RUNS_MAX,
  KEEP_LAST_RUNS_MIN,
  PROJECT_HOLD_LABEL,
}

export const ON_HOLD_LABEL = 'On hold'
export const HELD_PREFIX = 'Held: '
export const HOLDS_LOAD_ERROR = 'Could not load the retention holds.'
/** The server's `reason_length` sentence. */
export const REASON_LENGTH_SENTENCE: string = HOLD_ERROR_SENTENCES.reason_length

export const ENTITY_TYPE_LABELS: Readonly<Record<string, string>> = {
  project: 'Project',
  release: 'Release',
  run: 'Run',
  compliance_pack: 'Compliance pack',
}

export const STATE_LABELS: Readonly<Record<string, string>> = {
  live: 'Live',
  expired: 'Expired',
  released: 'Released',
}

export const PROJECT_HELD_LINE =
  'The whole project is on hold — nothing it covers will be purged.'
export const REFERENCE_HEADING = 'Reference warnings:'
export const REFERENCES_UNMEASURED = 'Reference warnings were not measured.'
export const NOT_HELD_NOTE =
  'Holds do not cover analysis caches, search-index documents, search usage rows, ' +
  'unlabelled shadow pairs or expired agent memory.'

export const FLOOR_FIELD_LABEL = 'Always keep the last N runs (0–1000)'
export const FLOOR_FIELD_HELP =
  'The scheduled purge also always keeps the latest passing run of the project and of each suite. ' +
  '0 keeps only those.'
export const FLOOR_BOUNDS_SENTENCE = 'Keep the last 0 to 1000 runs (a whole number).'

/** A hold as any answer carries it (list row, ref, preview entry). */
export interface HoldLike {
  entity_type: string
  entity_id?: string | null
  entity_label?: string | null
  reason: string
  /** Only known for a pack whose release the caller knows. */
  release_id?: string | null
}

/** The fields `placedLine` reads from a list row. */
export interface HoldTimelineLike {
  placed_by_name?: string | null
  placed_at?: string | null
  expires_at?: string | null
  state?: string | null
  released_at?: string | null
  released_by_name?: string | null
  release_reason?: string | null
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/** `Held: <reason>` — the ONE refusal sentence (mirrors HELD_SENTENCE). */
export function heldLine(hold: { reason: string }): string {
  return heldSentence(hold.reason)
}

/** The entity type as a chip label; an unknown type reads as itself. */
export function entityTypeLabel(entityType: string): string {
  return ENTITY_TYPE_LABELS[entityType] ?? entityType
}

/**
 * The entity's display label: the snapshot taken at placement, else a
 * fallback built from the type and the id (never another text field).
 */
export function entityLabel(hold: HoldLike): string {
  const label = (hold.entity_label ?? '').trim()
  if (label) return label
  if (hold.entity_type === 'project') return PROJECT_HOLD_LABEL
  const id = hold.entity_id ? hold.entity_id.slice(0, 8) : ''
  return id ? `${entityTypeLabel(hold.entity_type)} ${id}` : entityTypeLabel(hold.entity_type)
}

/**
 * The in-app link of a held entity, from its id and type only:
 * release → `/releases/<uuid>`, run → `/runs/<uuid>`, a pack →
 * `/releases/<release uuid>` when its release is known. Anything else
 * (a project hold, an unknown type, an id that is not a uuid) → null.
 */
export function entityHref(hold: HoldLike): string | null {
  const id = hold.entity_id ?? ''
  if (hold.entity_type === 'release' && UUID_RE.test(id)) return `/releases/${id}`
  if (hold.entity_type === 'run' && UUID_RE.test(id)) return `/runs/${id}`
  if (hold.entity_type === 'compliance_pack') {
    const releaseId = hold.release_id ?? ''
    return UUID_RE.test(releaseId) ? `/releases/${releaseId}` : null
  }
  return null
}

/**
 * Line 2 of a hold row: `Placed by <name> on <date> · Expires <date>`
 * (`No expiry`, or `Expired <date>` once the hold has expired); a released
 * row reads `Released by <name> on <date>: <release reason>`.
 */
export function placedLine(
  hold: HoldTimelineLike,
  formatDate: (iso: string) => string,
): string {
  if (hold.state === 'released' || hold.released_at) {
    const by = hold.released_by_name ? ` by ${hold.released_by_name}` : ''
    const on = hold.released_at ? ` on ${formatDate(hold.released_at)}` : ''
    const why = hold.release_reason ? `: ${hold.release_reason}` : ''
    return `Released${by}${on}${why}`
  }
  const by = hold.placed_by_name ? ` by ${hold.placed_by_name}` : ''
  const on = hold.placed_at ? ` on ${formatDate(hold.placed_at)}` : ''
  let expiry = 'No expiry'
  if (hold.expires_at) {
    const verb = hold.state === 'expired' ? 'Expired' : 'Expires'
    expiry = `${verb} ${formatDate(hold.expires_at)}`
  }
  return `Placed${by}${on} · ${expiry}`
}

/** The preview's floor block (RetentionPreviewFloor). */
export interface FloorLike {
  keep_last_runs: number
  runs_kept: number
  latest_passed_kept: boolean
  suites_kept: number
}

/** One `references_broken` entry (ReferenceBrokenRead). */
export interface ReferenceLike {
  table: string
  column: string
  count: number
  label?: string | null
}

/** One entry of the preview's `holds.active` list (RetentionPreviewHold). */
export interface PreviewHoldLike extends HoldLike {
  hold_id?: string
  runs_kept?: number
  packs_kept?: number
}

/** The parts of the purge preview `keptLines` reads (all optional: an older server omits them). */
export interface PreviewKeptInput {
  holds?: {
    project_held?: boolean
    runs_kept?: number
    packs_kept?: number
    active?: PreviewHoldLike[] | null
  } | null
  floor?: FloorLike | null
  references_broken?: ReferenceLike[] | null
  references_measured?: boolean
}

export type KeptLineKind =
  | 'project'
  | 'hold'
  | 'floor'
  | 'reference-heading'
  | 'reference'
  | 'reference-unmeasured'
  | 'not-held-note'

export interface KeptLine {
  key: string
  kind: KeptLineKind
  /** The exact sentence. */
  text: string
  /** A muted detail shown beside the sentence (a hold's entity label). */
  detail?: string
}

/**
 * `Kept by the floor: <n> runs (the newest <N>, the latest passing run, the
 * latest passing run of <s> suites)` — each part only when it applies; null
 * when the floor keeps nothing.
 */
export function floorLine(floor: FloorLike | null | undefined): string | null {
  if (!floor || !(floor.runs_kept > 0)) return null
  const parts: string[] = []
  if (floor.keep_last_runs > 0) parts.push(`the newest ${floor.keep_last_runs}`)
  if (floor.latest_passed_kept) parts.push('the latest passing run')
  if (floor.suites_kept > 0) {
    parts.push(`the latest passing run of ${plural(floor.suites_kept, 'suite', 'suites')}`)
  }
  const head = `Kept by the floor: ${plural(floor.runs_kept, 'run', 'runs')}`
  return parts.length ? `${head} (${parts.join(', ')})` : head
}

/** The server's label, else the fallback copy (REFERENCE_FALLBACK_LABEL). */
export function referenceLabel(ref: ReferenceLike): string {
  const label = (ref.label ?? '').trim()
  if (label) return label
  return ref.count === 1
    ? `1 ${ref.table} row loses its ${ref.column} link`
    : `${ref.count} ${ref.table} rows lose their ${ref.column} link`
}

/**
 * `Reference warnings:` + one line per broken link; ONE `…were not
 * measured.` line when the count failed (never "none"); nothing when it was
 * measured and nothing breaks.
 */
export function referenceLines(
  refs: ReferenceLike[] | null | undefined,
  measured: boolean | undefined,
): string[] {
  if (measured === false) return [REFERENCES_UNMEASURED]
  const items = Array.isArray(refs) ? refs.filter((r) => r.count > 0) : []
  if (!items.length) return []
  return [REFERENCE_HEADING, ...items.map(referenceLabel)]
}

/** `Kept by hold: <reason> — <n> runs, <m> packs`. */
export function keptByHoldLine(hold: PreviewHoldLike): string {
  const runs = plural(hold.runs_kept ?? 0, 'run', 'runs')
  const packs = plural(hold.packs_kept ?? 0, 'pack', 'packs')
  return `Kept by hold: ${hold.reason} — ${runs}, ${packs}`
}

/** Every line of the purge preview's kept block, in display order. */
export function keptLines(preview: PreviewKeptInput | null | undefined): KeptLine[] {
  if (!preview) return []
  const lines: KeptLine[] = []
  const holds = preview.holds ?? null
  const active = Array.isArray(holds?.active) ? holds.active : []
  if (holds?.project_held) {
    lines.push({ key: 'project', kind: 'project', text: PROJECT_HELD_LINE })
  }
  active.forEach((hold, i) => {
    lines.push({
      key: `hold-${hold.hold_id ?? i}`,
      kind: 'hold',
      text: keptByHoldLine(hold),
      detail: entityLabel(hold),
    })
  })
  const floor = floorLine(preview.floor)
  if (floor) lines.push({ key: 'floor', kind: 'floor', text: floor })
  referenceLines(preview.references_broken, preview.references_measured).forEach((text, i) => {
    let kind: KeptLineKind = 'reference'
    if (text === REFERENCES_UNMEASURED) kind = 'reference-unmeasured'
    else if (i === 0) kind = 'reference-heading'
    lines.push({ key: `ref-${i}`, kind, text })
  })
  if (holds?.project_held || active.length > 0) {
    lines.push({ key: 'not-held', kind: 'not-held-note', text: NOT_HELD_NOTE })
  }
  return lines
}

/**
 * A `<input type="date">` value (`YYYY-MM-DD`) as the END of that day in the
 * viewer's zone (23:59:59 local), ISO; null for an empty or invalid value.
 */
export function expiryToIso(dateInput: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((dateInput ?? '').trim())
  if (!m) return null
  const y = Number(m[1])
  const mo = Number(m[2])
  const d = Number(m[3])
  const end = new Date(y, mo - 1, d, 23, 59, 59, 0)
  if (end.getFullYear() !== y || end.getMonth() !== mo - 1 || end.getDate() !== d) return null
  return end.toISOString()
}

/**
 * The server's sentence out of a failed request: `detail.message` (the hold
 * routes' `{code, message}`), a string `detail` (the role guards), or the
 * first `msg` of a FastAPI 422 list; `fallback` for anything else. Text only.
 */
export function serverSentence(err: unknown, fallback: string): string {
  const response = err && typeof err === 'object' ? (err as { response?: unknown }).response : undefined
  const data = response && typeof response === 'object' ? (response as { data?: unknown }).data : undefined
  const detail = data && typeof data === 'object' ? (data as { detail?: unknown }).detail : undefined
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const msg = (detail[0] as { msg?: unknown } | null)?.msg
    if (typeof msg === 'string' && msg.trim()) return msg.replace(/^Value error,\s*/, '')
  }
  return fallback
}

/** The HTTP status of a failed request, or null. */
export function errorStatus(err: unknown): number | null {
  const response = err && typeof err === 'object' ? (err as { response?: unknown }).response : undefined
  const status = response && typeof response === 'object' ? (response as { status?: unknown }).status : undefined
  return typeof status === 'number' ? status : null
}

/** The server's reason rule (trimmed 3-500): its sentence, or null when valid. */
export function validateReason(text: string): string | null {
  const n = (text ?? '').trim().length
  return n >= HOLD_REASON_MIN && n <= HOLD_REASON_MAX ? null : REASON_LENGTH_SENTENCE
}

/** The floor field's rule: a whole number 0-1000, else its sentence. */
export function validateKeepLastRuns(raw: string): string | null {
  const text = (raw ?? '').trim()
  if (!/^\d+$/.test(text)) return FLOOR_BOUNDS_SENTENCE
  const n = Number(text)
  return n >= KEEP_LAST_RUNS_MIN && n <= KEEP_LAST_RUNS_MAX ? null : FLOOR_BOUNDS_SENTENCE
}

/**
 * The run page's fact (BASELINE_PURGED_SENTENCE):
 * `Baseline build 4471 was purged on 2 Mar 2026.`
 */
export function baselinePurgedLine(
  purged: { build_number?: string | null; purged_at: string },
  formatDate: (iso: string) => string,
): string {
  const build = (purged.build_number ?? '').trim()
  const date = formatDate(purged.purged_at)
  return build
    ? `Baseline build ${build} was purged on ${date}.`
    : `The baseline run was purged on ${date}.`
}
