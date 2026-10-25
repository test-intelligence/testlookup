/**
 * W14 AGT-12: the Runs page's bulk-job panel, in words (pure; the panel and the page import one string).
 *
 *  - `filterFromPage` — the runs list's OWN request (the active project, the global release, the window, the
 *    status, the suites, the search, the branch, the AI verdict) as the `run_filter` "Re-trigger all matching"
 *    sends: the server resolves it with the predicates `GET /runs` applies, so the two can never disagree;
 *  - `progressLines` — a `BulkJob` as the panel's two lines. Progress is COUNTED from the pipeline rows on each
 *    read; a request record that expired says so (`counters_source: "postgres"`), never zeros;
 *  - `confirmSentence` — the `InlineConfirm`'s question, naming the matched count (and the cap).
 */
import { normalizeScope, type ScopeValue } from '@/lib/scopeParams'
import type { BulkJob, BulkRunFilter, BulkTriggerResult } from '@/types/agentOps'
import { formatNumber } from '@/utils/formatters'

/** `BULK_FILTER_CAP` (`core/agent_ops_vocab.py`): the most runs one bulk request queues. */
export const BULK_FILTER_CAP = 2_000

export const BULK_RECORD_EXPIRED = "Counted from the pipelines: the job's request record has expired."
export const BULK_LOAD_ERROR = 'Could not load the bulk job.'
export const BULK_JOB_GONE = 'This bulk job is no longer available: its record expired, or it is not yours.'
export const bulkUntrackedNote = (queued: number) =>
  `Queued ${count(queued)} pipeline${queued === 1 ? '' : 's'}. Progress cannot be tracked right now.`

const STATE_LABEL: Record<string, string> = { active: 'Running', finished: 'Finished', cancelled: 'Cancelled' }

/** 2000 → "2,000" (the shared number formatter's thousands separator). */
export const count = (n: number) => formatNumber(n)

/** The runs list's request, as `RunsPage` hands it to `useRuns` (plus the scope `useRuns` adds itself). */
export interface RunsListRequest {
  /** The active project; null in All Projects (the list sends no `project_id` then). */
  projectId: string | null
  /** The global release scope (`useReleaseScope`), as `useRuns` sends it. */
  release: ScopeValue
  /** The params object the page passes to `useRuns` (`page` / `size` are the list's paging, not a filter). */
  params: Record<string, unknown>
}

const scalar = (value: unknown): string | null =>
  typeof value === 'string' && value !== '' ? value : null

/** A repeatable list parameter (`suite_name`, `branch`): a scalar or a list → a list, absent when empty. */
const list = (value: unknown): string[] | null => {
  const items = normalizeScope(Array.isArray(value) ? (value as string[]) : scalar(value))
  return items.length > 0 ? items : null
}

/** The `run_filter` of "Re-trigger all matching": exactly the filter the list itself asked with. */
export function filterFromPage({ projectId, release, params }: RunsListRequest): BulkRunFilter {
  const releases = normalizeScope(release)
  const suites = list(params.suite_name)
  const branches = list(params.branch)
  const status = scalar(params.status)
  const q = scalar(params.q)
  const recommendation = scalar(params.recommendation)
  return {
    ...(projectId ? { project_id: projectId } : {}),
    ...(releases.length > 0 ? { release_id: releases } : {}),
    ...(typeof params.days === 'number' ? { days: params.days } : {}),
    ...(status ? { status } : {}),
    ...(suites ? { suite_name: suites } : {}),
    ...(q ? { q } : {}),
    ...(branches ? { branch: branches } : {}),
    ...(recommendation ? { recommendation } : {}),
  }
}

/** The ⋯ item's count: the list's COUNT, or `2,000+` past the cap. */
export const matchingLabel = (total: number) =>
  `Re-trigger all matching (${total > BULK_FILTER_CAP ? `${count(BULK_FILTER_CAP)}+` : count(total)})`

/** The confirm's question: the matched count, and the cap when the filter matches more. */
export function confirmSentence(matched: number, cap: number = BULK_FILTER_CAP): string {
  const n = Math.min(matched, cap)
  const tail = matched > cap ? `, the newest ${count(cap)} of ${count(matched)}` : ''
  return `Re-trigger the AI pipeline for ${count(n)} run${n === 1 ? '' : 's'} matching this filter${tail}?`
}

/** The panel's lines for one read of the job. */
export interface BulkJobLines {
  /** The job's state in words: "Running" / "Finished" / "Cancelled". */
  stateLabel: string
  /** What was requested and queued; null when the request record has expired. */
  request: string | null
  /** How the job's pipelines stand, counted from their rows. */
  progress: string
  /** `BULK_RECORD_EXPIRED` when the counts come from the pipelines alone. */
  recordNote: string | null
  /** The server's note for queued pipelines that never started (never counted as failed). */
  note: string | null
  /** The gauge: pipelines started of those queued; null when `queued` is unknown. */
  gauge: { started: number; queued: number } | null
}

function requestLine(job: BulkJob): string | null {
  if (job.counters_source === 'postgres' || job.requested == null) return null
  const parts: string[] = []
  if (job.source === 'filter' && job.truncated && job.matched != null) {
    parts.push(`${count(job.matched)} matched, the newest ${count(job.requested)} requested`)
  } else {
    parts.push(`${count(job.requested)} requested`)
  }
  parts.push(`${count(job.queued ?? 0)} queued`)
  parts.push(`${count(job.skipped_in_progress ?? 0)} already running`)
  // A filter names runs that exist: "not found" belongs to a selection (or to a filter that found one gone).
  if (job.source !== 'filter' || (job.not_found ?? 0) > 0) parts.push(`${count(job.not_found ?? 0)} not found`)
  return parts.join(' · ')
}

export function progressLines(job: BulkJob): BulkJobLines {
  const p = job.progress
  const progress = [
    `${count(p.completed)} completed`,
    `${count(p.passed)} passed`,
    `${count(p.failed)} failed`,
    `${count(p.in_progress)} running`,
    ...(p.not_started != null ? [`${count(p.not_started)} not started`] : []),
  ].join(' · ')
  const queued = job.counters_source === 'postgres' ? null : job.queued ?? null
  return {
    stateLabel: STATE_LABEL[job.state] ?? job.state,
    request: requestLine(job),
    progress,
    recordNote: job.counters_source === 'postgres' ? BULK_RECORD_EXPIRED : null,
    note: job.note ?? null,
    gauge: queued != null && queued > 0 ? { started: Math.min(p.started, queued), queued } : null,
  }
}

/** A job the panel no longer polls: finished or cancelled. */
export const isSettled = (state: string | undefined) => state === 'finished' || state === 'cancelled'

/** What the page does with a bulk answer: open the panel, say it cannot be tracked, or (an older server) toast. */
export type BulkOutcome =
  | { kind: 'panel'; jobId: string }
  | { kind: 'untracked'; note: string }
  | { kind: 'toast'; queued: number }

export function bulkOutcome(result: Partial<BulkTriggerResult> | null | undefined, asked: number): BulkOutcome {
  if (result?.bulk_job_id) return { kind: 'panel', jobId: result.bulk_job_id }
  const queued = result?.queued ?? asked
  if (result?.tracking === false) return { kind: 'untracked', note: bulkUntrackedNote(queued) }
  return { kind: 'toast', queued }
}
