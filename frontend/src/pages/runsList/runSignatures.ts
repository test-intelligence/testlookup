/**
 * The Runs page's failure signatures (FAIL-03): the window's failed builds
 * grouped by the error MOST of their failing tests share.
 *
 * The server (`GET /runs/failure-signatures`, ≤ 50 run ids) groups each run's
 * FAILED / BROKEN rows by the normalised error signature (the one
 * `FAILURE_SIGNATURE_SQL`, the same normalisation every failure grouping in
 * TestLookup uses) and returns each run's top three, the most common first:
 * that first one is the run's DOMINANT signature. Builds group here by their
 * dominant signature, not by a hash of the top three: two builds sharing their
 * main error but differing in a minor third one are one regression. Two
 * builds with equal pass / fail / total counts but different errors are two
 * groups (the old `(passed, failed, total)` tuple put them in one).
 *
 * Pure: no React, no I/O.
 */
import type { RunSignatureBatch, RunSignatureEntry, RunSignatureItem, TestRun } from '@/types/runs'

/** The server's NO_MESSAGE_ID: the failing rows carried a blank message. */
export const NO_MESSAGE = '__NO_MESSAGE__'
/** A failed build with no FAILED / BROKEN row at all. */
export const NO_FAILING_TESTS = '__NO_FAILING_TESTS__'
/** `GET /runs/failure-signatures` takes at most this many ids. */
export const MAX_SIGNATURE_RUNS = 50

export interface SignatureGroup {
  key: string
  /** The dominant error's first line (text from CI files: render as text). */
  title: string
  members: TestRun[]
  /** Tests every member's dominant signature names; empty for a one-member group. */
  sharedTests: string[]
  chipLabel: string
}

export interface SignatureModel {
  /** Most members first, then key; `groups[0]` is the primary. */
  groups: SignatureGroup[]
  /** Failed builds in the window. */
  failedRuns: number
  /** Of those, the ones the batch answered (grouped). */
  groupedRuns: number
  /** Run id → its group's key. */
  groupOf: ReadonlyMap<string, string>
  /** Run id → its batch item (the panel's "grouped over N of M" note). */
  itemOf: ReadonlyMap<string, RunSignatureItem>
  /** `null` = not measured (loading / failed, or nothing grouped while builds failed). */
  diversityScore: number | null
}

/** A failed build (the page's one rule). */
export function isFailedRun(status: string): boolean {
  return /fail|broken|error/i.test(status)
}

const newestFirst = (a: TestRun, b: TestRun) => +new Date(b.created_at) - +new Date(a.created_at)

/**
 * The ids to ask for: the failed runs of the visible table page first, then
 * the other failed runs of the window newest first, de-duplicated, cut at 50,
 * then sorted (the request key is order-free).
 */
export function signatureRunIds(visibleRuns: readonly TestRun[], windowRuns: readonly TestRun[]): string[] {
  const ids: string[] = []
  const seen = new Set<string>()
  const add = (run: TestRun) => {
    if (seen.has(run.id) || !isFailedRun(run.status)) return
    seen.add(run.id)
    ids.push(run.id)
  }
  visibleRuns.forEach(add)
  ;[...windowRuns].sort(newestFirst).forEach(add)
  return ids.slice(0, MAX_SIGNATURE_RUNS).sort()
}

/** A stable short hash of a key (the chip's `abc-cluster` label: 11 characters). */
function shortHash(key: string): string {
  let hash = 0
  for (let i = 0; i < key.length; i++) hash = ((hash << 5) - hash) + key.charCodeAt(i)
  return (Math.abs(hash) >>> 0).toString(16).padStart(6, '0').slice(0, 3)
}

/** The table chip's words for a group: never wider than today's `abc-cluster`. */
export function chipLabelOf(key: string): string {
  if (key === NO_MESSAGE) return 'no message'
  if (key === NO_FAILING_TESTS) return 'no tests'
  return `${shortHash(key)}-cluster`
}

function dominantEntry(item: RunSignatureItem): RunSignatureEntry | undefined {
  if (item.dominant === null) return undefined
  return item.signatures.find((s) => s.signature === item.dominant) ?? item.signatures[0]
}

function titleOf(key: string, entry: RunSignatureEntry | undefined): string {
  if (key === NO_MESSAGE) return 'No message recorded'
  if (key === NO_FAILING_TESTS) return 'No failing tests recorded'
  return entry?.label || entry?.signature || key
}

/** Byte order, like the server's `COLLATE "C"`. */
const byteOrder = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

export function buildSignatureModel(windowRuns: readonly TestRun[], batch: RunSignatureBatch | undefined): SignatureModel {
  const failed = windowRuns.filter((r) => isFailedRun(r.status))
  const failedRuns = failed.length
  const groupOf = new Map<string, string>()
  const itemOf = new Map<string, RunSignatureItem>()
  if (!batch) {
    return { groups: [], failedRuns, groupedRuns: 0, groupOf, itemOf, diversityScore: failedRuns === 0 ? 100 : null }
  }
  for (const item of batch.items) itemOf.set(item.run_id, item)

  const byKey = new Map<string, { title: string; members: TestRun[]; tests: string[][] }>()
  let groupedRuns = 0
  for (const run of failed) {
    const item = itemOf.get(run.id)
    if (!item) continue
    groupedRuns += 1
    const key = item.dominant ?? NO_FAILING_TESTS
    const entry = dominantEntry(item)
    groupOf.set(run.id, key)
    const group = byKey.get(key)
    if (group) {
      group.members.push(run)
      group.tests.push(entry?.tests ?? [])
    } else {
      byKey.set(key, { title: titleOf(key, entry), members: [run], tests: [entry?.tests ?? []] })
    }
  }

  const groups: SignatureGroup[] = [...byKey.entries()]
    .map(([key, g]) => ({
      key,
      title: g.title,
      members: g.members,
      sharedTests: g.members.length < 2
        ? []
        : g.tests.reduce((shared, names) => shared.filter((n) => names.includes(n))),
      chipLabel: chipLabelOf(key),
    }))
    .sort((a, b) => b.members.length - a.members.length || byteOrder(a.key, b.key))

  const diversityScore = groupedRuns > 0
    ? Math.min(100, (groups.length / groupedRuns) * 100)
    : failedRuns === 0 ? 100 : null
  return { groups, failedRuns, groupedRuns, groupOf, itemOf, diversityScore }
}

// ── A table row's Signature cell ──────────────────────────────────────────

/** How the signatures request stands, for the cells it did not answer. */
export interface SignatureRequestState {
  /** The ids the page asked for (≤ 50). */
  requested: ReadonlySet<string>
  loading: boolean
  failed: boolean
}

/** What a row's Signature cell shows. */
export type SignatureCell =
  | { kind: 'chip'; group: SignatureGroup; isOutlier: boolean }
  | { kind: 'none'; title?: string }

export const SIGNATURE_NOT_LOADED_TITLE = `Signature not loaded: older than the newest ${MAX_SIGNATURE_RUNS} failed builds`

/**
 * A failed, grouped run → its group's chip (tinted when the group is not the
 * primary one); a passed or in-progress run → a plain "—"; a failed run the
 * batch did not group → "—" with why (not asked, the request failed, still
 * loading, or the run is not available to the reader).
 */
export function signatureCellOf(
  run: Pick<TestRun, 'id' | 'status'>,
  model: SignatureModel,
  request: SignatureRequestState,
): SignatureCell {
  if (!isFailedRun(run.status)) return { kind: 'none' }
  const key = model.groupOf.get(run.id)
  const group = key === undefined ? undefined : model.groups.find((g) => g.key === key)
  if (group) return { kind: 'chip', group, isOutlier: group !== model.groups[0] }
  if (!request.requested.has(run.id)) return { kind: 'none', title: SIGNATURE_NOT_LOADED_TITLE }
  if (request.failed) return { kind: 'none', title: 'Signature unavailable: the request failed' }
  if (request.loading) return { kind: 'none', title: 'Loading the signature…' }
  return { kind: 'none', title: 'Signature unavailable: the run is not available to you' }
}
