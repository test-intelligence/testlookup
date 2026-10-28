/**
 * exportModel — the pure copy and helpers of export-before-delete (W22b
 * ACT-03): the export step of the criteria preview, the execute paragraph and
 * toast, and the deletion jobs card (status, archive, reclaimed, collect).
 *
 * Every sentence lives here once, so a test imports one string. Pure
 * functions only: no React, no request, no `Intl`.
 *
 * Inputs are structural (`TierEstimateLike`, `ExportEstimateLike`, `JobLike`)
 * so the wire types, the shared mocks and a test's own object are all
 * accepted without a cast. Absence is not health: an unmeasured size reads
 * `not measured` (never 0), a job without a ratio shows none.
 *
 * Safety: a job's `error` is the server's own sentence and is rendered as
 * React text; NO storage key or URL is ever part of a job answer, and nothing
 * here builds one.
 *
 * The wire vocabulary (the archive sentences, the job error templates, the
 * byte formatter, the ratio) is re-exported from `@/types/retentionExport`,
 * the one mirror of `retention_vocab.py`.
 */
import {
  ARCHIVE_NOT_BACKUP,
  EXPORT_COLLECT_NOTE,
  EXPORT_COLLECT_TTL_HOURS_DEFAULT,
  EXPORT_FULL_WARNING,
  EXPORT_JOB_ERRORS,
  EXPORT_JOB_EXPIRED_ONE,
  EXPORT_RETAIN_NOTE,
  formatBytesApprox,
  reclaimRatio,
} from '@/types/retentionExport'
import { RESULT_STATUS_TOKEN, type StatusToken } from '@/components/ui/statusVocabulary'

export { ARCHIVE_NOT_BACKUP, EXPORT_COLLECT_NOTE, EXPORT_FULL_WARNING, EXPORT_RETAIN_NOTE }

export type TierKey = 'summary' | 'evidence' | 'full'
export type TargetKey = 'collect' | 'retain'
/** The export step's radio value: `none` = delete without an archive (the default, D-7). */
export type TargetChoice = 'none' | TargetKey

export interface ExportChoiceLike {
  tier: string
  target: string
}

export const TARGET_OPTIONS: ReadonlyArray<{ value: TargetChoice; label: string }> = Object.freeze([
  { value: 'none', label: 'Delete without an archive' },
  { value: 'collect', label: 'Download an archive first' },
  { value: 'retain', label: 'Keep an archive in storage' },
])

export const TIER_LABELS: Readonly<Record<TierKey, string>> = Object.freeze({
  summary: 'Summary',
  evidence: 'Evidence',
  full: 'Full',
})

export const NOT_MEASURED = 'not measured'
export const JOBS_LOAD_ERROR = 'Could not load the deletion jobs.'
export const JOBS_HEADING = 'Deletion jobs'
export const JOBS_EMPTY = 'No deletion jobs yet.'
export const COLLECT_LABEL = 'Collect'
export const DOWNLOAD_LABEL = 'Download archive'
export const SAVED_LABEL = 'I have saved it'
export const DOWNLOAD_INCOMPLETE = 'The download was incomplete; download it again.'
export const ARCHIVE_STEP_LABEL = 'Before deleting'
/** Heads the tier list, so it reads as the contents of either archive choice, not of the last radio above it. */
export const ARCHIVE_CONTENTS_LABEL = 'Archive contents'

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

function tierLabel(tier: string): string {
  return TIER_LABELS[tier as TierKey] ?? tier
}

/** An exact byte figure in the approximate formatter's units, without the `≈`. */
export function bytesText(bytes: number | null | undefined): string {
  if (bytes == null) return NOT_MEASURED
  return formatBytesApprox(bytes).replace(/^≈\s*/, '')
}

// ── The export step (criteria preview) ──────────────────────────────────────

export interface TierEstimateLike {
  tier: string
  archive_bytes?: number | null
  measured?: boolean
  over_cap?: boolean
}

export interface ExportEstimateLike {
  run_count: number
  exportable: boolean
  refusal?: string | null
  reclaim_bytes?: number | null
  tiers?: TierEstimateLike[]
  max_bytes: number
}

/** `Summary — archive ≈ 2.1 MB`; `Full — archive not measured` when unmeasured. */
export function tierLine(estimate: TierEstimateLike): string {
  const size =
    estimate.measured === false || estimate.archive_bytes == null
      ? NOT_MEASURED
      : formatBytesApprox(estimate.archive_bytes)
  return `${tierLabel(estimate.tier)} — archive ${size}`
}

/** `Reclaims ≈ 180 MB`; `Reclaims not measured` when the listing failed. */
export function reclaimLine(estimate: Pick<ExportEstimateLike, 'reclaim_bytes'>): string {
  return estimate.reclaim_bytes == null
    ? `Reclaims ${NOT_MEASURED}`
    : `Reclaims ${formatBytesApprox(estimate.reclaim_bytes)}`
}

/** The title of an over-cap tier: `Over the 512 MB limit`. */
export function overCapTitle(maxBytes: number): string {
  return `Over the ${Math.round(maxBytes / (1024 * 1024))} MB limit`
}

function tierArticle(tier: string): string {
  const word = tierLabel(tier).toLowerCase()
  return `${/^[aeiou]/.test(word) ? 'An' : 'A'} ${word} archive`
}

/** The execute section's extra paragraph; null when no archive was chosen. */
export function executeParagraph(choice: ExportChoiceLike | null | undefined): string | null {
  if (!choice) return null
  if (choice.target === 'collect') {
    return `${tierArticle(choice.tier)} is built and verified first. Nothing is deleted until you download it and confirm you have saved it.`
  }
  return `${tierArticle(choice.tier)} is built and verified first, kept in storage, and then the runs are deleted.`
}

/** The success toast after execute with an archive; null keeps the panel's own toast. */
export function executeToast(count: number, choice: ExportChoiceLike | null | undefined): string | null {
  if (!choice) return null
  const runs = plural(count, 'run', 'runs')
  if (choice.target === 'collect') {
    return `Archiving ${runs} — download it from Deletion jobs when it is ready`
  }
  return `Archiving ${runs}, then deleting them — track it in Deletion jobs`
}

// ── The deletion jobs card ───────────────────────────────────────────────────

/** The job fields the card reads (a `DeletionJob` is one). */
export interface JobLike {
  id: string
  job_kind?: string
  status: string
  counts?: Record<string, unknown> | null
  bytes_reclaimed?: number | null
  error?: string | null
  export_tier?: string | null
  export_target?: string | null
  export_bytes?: number | null
  export_verified_at?: string | null
  collect_expires_at?: string | null
  export_summary?: Record<string, unknown> | null
}

export const JOB_STATUS_LABELS: Readonly<Record<string, string>> = Object.freeze({
  previewed: 'Previewed',
  queued: 'Queued',
  running: 'Running',
  awaiting_collection: 'Awaiting collection',
  completed: 'Completed',
  partial: 'Partial',
  failed: 'Failed',
  expired: 'Expired',
})

/** The status chip's word; an unknown status reads as itself. */
export function jobStatusLabel(status: string): string {
  return JOB_STATUS_LABELS[status] ?? status
}

/** The warning chip tone (`Awaiting collection`, `Paused`): UI tokens, not a result status. */
export const WARNING_TONE: StatusToken = Object.freeze({
  hue: 'var(--ui-warning)',
  tint: 'var(--ui-warning-bg)',
  border: 'var(--ui-warning-bd)',
  ink: 'var(--ui-warning-ink)',
})
export const AWAITING_TONE: StatusToken = WARNING_TONE

/** The inline style of a tone chip (the token object's own values, never a hand-spelled hue). */
export function toneStyle(tone: StatusToken): { color: string; background: string; borderColor: string } {
  return { color: tone.ink, background: tone.tint, borderColor: tone.border }
}

/** The chip tone through the ONE status vocabulary (never a hand-spelled status hue). */
export function jobStatusTone(status: string): StatusToken {
  switch (status) {
    case 'completed':
      return RESULT_STATUS_TOKEN.passed
    case 'failed':
      return RESULT_STATUS_TOKEN.failed
    case 'partial':
      return RESULT_STATUS_TOKEN.flaky
    case 'expired':
      return RESULT_STATUS_TOKEN.skipped
    case 'awaiting_collection':
      return AWAITING_TONE
    default:
      return RESULT_STATUS_TOKEN.unknown
  }
}

const KIND_LABELS: Readonly<Record<string, string>> = Object.freeze({
  criteria: 'criteria',
  scheduled: 'scheduled purge',
  single_entity: 'single run',
})

function numberAt(record: Record<string, unknown> | null | undefined, ...path: string[]): number | null {
  let value: unknown = record
  for (const key of path) {
    if (value === null || typeof value !== 'object') return null
    value = (value as Record<string, unknown>)[key]
  }
  return typeof value === 'number' ? value : null
}

/** The run count a job knows: the archive's, else the deleted runs'; null when unknown. */
export function jobRunCount(job: JobLike): number | null {
  return (
    numberAt(job.export_summary, 'run_count') ??
    numberAt(job.counts, 'postgres', 'runs') ??
    numberAt(job.counts, 'runs')
  )
}

/** Line 2 of the Job column: `criteria · 12 runs` (the count only when known). */
export function jobKindLine(job: JobLike): string {
  const kind = KIND_LABELS[job.job_kind ?? ''] ?? job.job_kind ?? ''
  const count = jobRunCount(job)
  return count === null ? kind : `${kind} · ${plural(count, 'run', 'runs')}`
}

/** The Archive column: `Evidence · collect` / `verified`; `—` without an export. */
export function archiveLines(job: JobLike): { primary: string; secondary: string | null } {
  if (!job.export_tier) return { primary: '—', secondary: null }
  const target = job.export_target ? ` · ${job.export_target}` : ''
  return {
    primary: `${tierLabel(job.export_tier)}${target}`,
    secondary: job.export_verified_at ? 'verified' : 'not verified',
  }
}

/** `ratio 1:13` (archive : reclaimed); null when either side is unknown. */
export function ratioLine(job: JobLike): string | null {
  const ratio = reclaimRatio(job as Parameters<typeof reclaimRatio>[0])
  if (ratio == null || !Number.isFinite(ratio) || ratio <= 0) return null
  return ratio < 1 ? `ratio 1:${Math.round(1 / ratio)}` : `ratio ${Math.round(ratio)}:1`
}

/** The Reclaimed column: the exact bytes (or `not measured`), the retained net, the ratio. */
export function reclaimedLines(job: JobLike): string[] {
  const lines = [bytesText(job.bytes_reclaimed)]
  if (job.export_target === 'retain' && job.bytes_reclaimed != null && job.export_bytes != null) {
    lines.push(`net ${bytesText(Math.max(0, job.bytes_reclaimed - job.export_bytes))}`)
  }
  const ratio = ratioLine(job)
  if (ratio) lines.push(ratio)
  return lines
}

/** `Not collected within 72 hours — nothing was deleted; the archive was removed.` */
export function expiredLine(hours: number = EXPORT_COLLECT_TTL_HOURS_DEFAULT): string {
  return hours === 1 ? EXPORT_JOB_EXPIRED_ONE : EXPORT_JOB_ERRORS.expired.replace('{hours}', String(hours))
}

/** The full-width line under a failed / expired row: the server's sentence, else the expiry one. */
export function jobErrorLine(job: JobLike): string | null {
  if (job.error) return job.error
  return job.status === 'expired' ? expiredLine() : null
}

/** `Collect by <date> — after that nothing is deleted and the archive is removed.` */
export function collectByLine(job: JobLike, format: (iso: string) => string): string | null {
  if (!job.collect_expires_at) return null
  return `Collect by ${format(job.collect_expires_at)} — after that nothing is deleted and the archive is removed.`
}

/** `Download archive (14.2 MB)`; the bare label when the size is unknown. */
export function downloadLabel(job: JobLike): string {
  return job.export_bytes == null ? DOWNLOAD_LABEL : `${DOWNLOAD_LABEL} (${bytesText(job.export_bytes)})`
}

/** The saved blob is the whole archive: its size equals the job's `export_bytes`. */
export function downloadComplete(blob: { size: number } | null | undefined, job: JobLike): boolean {
  return blob != null && job.export_bytes != null && blob.size === job.export_bytes
}

/** `Showing 3 of 5`. */
export function showingLine(shown: number, total: number): string {
  return `Showing ${shown} of ${total}`
}

/** The fallback file name when the server sent no `Content-Disposition`. */
export function fallbackArchiveName(jobId: string): string {
  return `deletion-archive-${jobId.slice(0, 8)}.zip`
}
