/**
 * The Project data page's Export and Import cards (W24 ADM-05 phase 1): their copy and their states.
 *
 * Pure — no React, no request. Every sentence the two cards render lives here or in the vocabulary
 * it re-exports (`types/projectData.ts`, the mirror of the backend's `project_export_vocab.py`), so a
 * test imports ONE string and a wording change is one edit.
 *
 * The inputs are STRUCTURAL (`JobLike`, `ImportReportLike`): every field optional, the way the
 * backend may omit it; the API's `ProjectDataJob` fits them as it is.
 *
 * Rules kept here, not in the cards:
 *   - a count of 1 reads in the singular ("1 row", "1 table", "1 row was skipped: …");
 *   - an export is "ready" only while the server offers its download AND it has not expired by the
 *     viewer's clock; past `expires_at` it reads as the expired sentence, never as a dead button;
 *   - the 50 MB pre-check uses the server's own sentence, so the page and the API agree.
 * A refused request's sentence (also out of a Blob) is read by the service's `projectDataErrorMessage`.
 */
import {
  ACTIVE_STATUSES,
  ERROR_SENTENCES,
  fillSentence as fill,
  IMPORT_UPLOAD_MAX_BYTES,
  SKIP_REASONS,
  UI_SENTENCES,
} from '@/types/projectData'
import { formatCompactDateTime, formatNumber } from '@/utils/formatters'

export { ERROR_SENTENCES, SKIP_REASONS, UI_SENTENCES }

// ── Copy ─────────────────────────────────────────────────────────────────────

export const PAGE_SUBTITLE = 'Export settings and the test catalog, import them as a new project, or reset data.'
export const EXPORT_LABEL = 'Export'
export const EXPORT_AGAIN_LABEL = 'Export again'
export const DOWNLOAD_LABEL = 'Download'
export const SIGNED_HERE = 'Signed by this deployment'
export const SIGNED_ELSEWHERE = 'Signed by another deployment'
export const UNSIGNED = 'Unsigned'
export const EXPORT_QUEUED = 'Export queued — it starts in a moment.'
export const EXPORTING = 'Exporting…'
export const IMPORT_QUEUED = 'Import queued — it starts in a moment.'
export const IMPORTING = 'Importing…'
export const IMPORT_LABEL = 'Import'
export const FILE_LABEL = 'Export file (.zip)'
export const NAME_LABEL = 'Project name (optional)'
export const NAME_PLACEHOLDER = 'Empty: the exported name + (imported)'
export const IMPORTED_HEADING = 'Imported'
export const SWITCHED_OFF_HEADING = 'Switched off'
export const NOT_IMPORTED_HEADING = 'Skipped and cleared'
export const LAST_IMPORT_HEADING = 'Last import'
export const DOWNLOAD_FAILED = 'The export could not be downloaded.'
export const EXPORT_START_FAILED = 'The export could not be started.'
export const IMPORT_START_FAILED = 'The import could not be started.'
/** A failed import job that carries no sentence of its own (the server always sends one). */
export const IMPORT_FAILED_FALLBACK = 'The import failed and was rolled back; no project was created.'

/** The text after "<entity>: N rows " for an entity that arrived switched off / paused. */
const SWITCHED_OFF_SUFFIX: Record<string, string> = {
  webhook_subscriptions: 'switched off — secret required',
  github_integrations: 'switched off — secret required',
  gitlab_integrations: 'switched off — secret required',
  team_notification_channels: 'switched off',
  notification_transition_policies: 'switched off',
  quarantine_lifecycle_policies: 'automatic defects switched off',
  run_cadences: 'paused for 90 days',
  'releases.deactivated': 'set inactive — one active release is kept',
  'managed_test_cases.review_reset': 'returned to draft — review requests are not exported',
}
const SWITCHED_OFF_DEFAULT = 'switched off'

// ── Small formatters ─────────────────────────────────────────────────────────

/** `1 row` / `4,210 rows` (en-US grouping, whatever the browser locale). */
export function countNoun(count: number, singular: string, plural = `${singular}s`): string {
  return `${formatNumber(count)} ${count === 1 ? singular : plural}`
}

const MB = 1024 * 1024

/**
 * `2.1 MB`, `340 KB`, `812 B` — 1024-based with the server's unit names, so the 50 MiB cap reads
 * "50 MB" exactly as `upload_too_large` says it. One decimal for MB and above.
 */
export function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${Math.round(bytes)} B`
  if (bytes < MB) return `${Math.round(bytes / 1024)} KB`
  if (bytes < 1024 * MB) return `${(bytes / MB).toFixed(1)} MB`
  return `${(bytes / (1024 * MB)).toFixed(1)} GB`
}

export interface ProgressLike {
  done?: number | null
  total?: number | null
}

/**
 * "Exporting… 12 of 30 tables" / "Importing… 5 of 30 tables" ("… 0 of 1 table" for a plan of one);
 * just "Exporting…" while the worker has not reported a plan yet.
 */
export function formatProgress(progress: ProgressLike | null | undefined, kind: 'export' | 'import' = 'export'): string {
  const total = progress?.total
  if (typeof total !== 'number' || total <= 0) return kind === 'export' ? EXPORTING : IMPORTING
  const done = Math.min(Math.max(progress?.done ?? 0, 0), total)
  const template = kind === 'export' ? UI_SENTENCES.export_running : UI_SENTENCES.import_running
  const line = fill(template, { done: formatNumber(done), total: formatNumber(total) })
  return total === 1 ? line.replace(/ tables$/, ' table') : line
}

// ── The jobs (structural: the API's ProjectDataJob fits) ─────────────────────

export interface SkipLike {
  entity?: string | null
  column?: string | null
  reason?: string | null
  count?: number | null
}

export interface ImportReportLike {
  project_id?: string | null
  project_name?: string | null
  inserted?: Record<string, number> | null
  skipped?: SkipLike[] | null
  cleared?: SkipLike[] | null
  switched_off?: Record<string, number> | null
  signature?: string | null
}

export interface JobLike {
  id?: string | null
  kind?: string | null
  status?: string | null
  project_id?: string | null
  progress?: ProgressLike | null
  bytes?: number | null
  counts?: Record<string, number> | null
  signature?: string | null
  expires_at?: string | null
  error?: { code?: string | null; message?: string | null } | null
  download_path?: string | null
  import_report?: ImportReportLike | null
}

/** Queued or running: the card shows a spinner and polls. */
export function isActiveJob(job: JobLike | null | undefined): boolean {
  return (ACTIVE_STATUSES as readonly string[]).includes(job?.status ?? '')
}

/**
 * The newest job of a newest-first list read: `undefined` while the read is in flight (no answer yet),
 * `null` when the list is empty.
 */
export function newestJob<T>(list: { items?: readonly T[] | null; total?: number } | null | undefined): T | null | undefined {
  if (list === undefined) return undefined
  return list?.items?.[0] ?? null
}

/** The newest queued / running job of a newest-first list (a reload mid-job resumes it), or null. */
export function activeJobOf<T extends JobLike>(items: readonly T[] | null | undefined): T | null {
  return (Array.isArray(items) ? items : []).find((job) => isActiveJob(job)) ?? null
}

// ── The Export card ──────────────────────────────────────────────────────────

export type ExportCardKind = 'none' | 'running' | 'ready' | 'failed' | 'expired'

export interface ExportCardState {
  kind: ExportCardKind
  /** The status line (left of the action). */
  line: string
  /** ready: "Signed by this deployment" / "Unsigned" (the second line); otherwise null. */
  signature: string | null
  /** ready: "Available until …" (the third line; null when the server sent no expiry); otherwise null. */
  detail: string | null
  /** The action button: Export (start one) or Download (2.1 MB). */
  button: { label: string; action: 'export' | 'download'; disabled: boolean }
  /** ready: the "Export again" text button is offered. */
  again: boolean
}

function timeOf(iso: string | null | undefined): number | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  return Number.isNaN(t) ? null : t
}

/**
 * "4,210 rows in 5 tables" — the first line of a finished export; the signature and the expiry are the second and
 * third. Measured under DejaVu at 1280: "4,210 rows in 30 tables · Signed by this deployment" on one line beside
 * Export again + Download (2.1 MB) left 41 px of the 698 px row (a 7-digit count would wrap it); three short lines
 * leave ≈ 200 px.
 *
 * W25a (W24 F-8): only the tables that hold rows are counted, as the import line does — `job.counts` carries EVERY
 * exported table (zeros included), and "4,210 rows in 30 tables" read as if all 30 had data. Nothing exported:
 * "0 rows in 0 tables".
 */
export function exportSummaryLine(job: JobLike): string {
  const tables = positiveEntries(job.counts)
  const rows = tables.reduce((sum, [, n]) => sum + n, 0)
  return `${countNoun(rows, 'row')} in ${countNoun(tables.length, 'table')}`
}

/** What the Export card shows for the newest export of the project (`null`: none yet). */
export function exportCardState(job: JobLike | null | undefined, now: number = Date.now()): ExportCardState {
  const exportButton = (disabled: boolean) => ({ label: EXPORT_LABEL, action: 'export' as const, disabled })
  if (!job) return { kind: 'none', line: UI_SENTENCES.no_export, signature: null, detail: null, button: exportButton(false), again: false }
  if (job.status === 'queued' || job.status === 'running') {
    const line = job.status === 'queued' && !job.progress ? EXPORT_QUEUED : formatProgress(job.progress, 'export')
    return { kind: 'running', line, signature: null, detail: null, button: exportButton(true), again: false }
  }
  if (job.status === 'failed') {
    const line = job.error?.message?.trim() || ERROR_SENTENCES.export_failed
    return { kind: 'failed', line, signature: null, detail: null, button: exportButton(false), again: false }
  }
  const expiresAt = timeOf(job.expires_at)
  const ready = job.status === 'succeeded' && Boolean(job.download_path) && (expiresAt === null || now < expiresAt)
  if (!ready) {
    return { kind: 'expired', line: ERROR_SENTENCES.export_expired, signature: null, detail: null, button: exportButton(false), again: false }
  }
  const size = typeof job.bytes === 'number' ? formatSize(job.bytes) : ''
  return {
    kind: 'ready',
    line: exportSummaryLine(job),
    signature: job.signature === 'signed' ? SIGNED_HERE : UNSIGNED,
    detail: expiresAt === null ? null : fill(UI_SENTENCES.export_expires, { date: formatCompactDateTime(expiresAt) }),
    button: { label: size ? fill(UI_SENTENCES.export_ready, { size }) : DOWNLOAD_LABEL, action: 'download', disabled: false },
    again: true,
  }
}

// ── The Import card ──────────────────────────────────────────────────────────

/** Whether a File looks like a ZIP by its name or its type (the server checks the magic bytes). */
function looksLikeZip(file: { name?: string; type?: string }): boolean {
  const type = (file.type ?? '').toLowerCase()
  return /\.zip$/i.test(file.name ?? '') || type === 'application/zip' || type === 'application/x-zip-compressed'
}

/**
 * The client's pre-check, in the server's words: not a .zip (or empty) → `upload_empty`; over
 * 50 MB → `upload_too_large` — and the card sends NO request. `null` = acceptable.
 */
export function validateImportFile(file: { name?: string; size?: number; type?: string } | null | undefined): string | null {
  if (!file || !looksLikeZip(file) || !file.size) return ERROR_SENTENCES.upload_empty
  if (file.size > IMPORT_UPLOAD_MAX_BYTES) return ERROR_SENTENCES.upload_too_large
  return null
}

/** The optional name: empty → the server's default; otherwise 2–255 characters after trimming (`name_invalid`). */
export function validateImportName(name: string): string | null {
  const trimmed = name.trim()
  if (!trimmed) return null
  return trimmed.length < 2 || trimmed.length > 255 ? ERROR_SENTENCES.name_invalid : null
}

/** "payment-export.zip · 2.1 MB" — the chosen file under the field. */
export function chosenFileLine(file: { name: string; size: number }): string {
  return `${file.name} · ${formatSize(file.size)}`
}

export interface ReportLine {
  key: string
  /** The table (and column) the line is about, rendered in mono. */
  entity: string
  text: string
}

export interface ImportReportLines {
  /** "Created Payment Service (imported)." */
  created: string
  /** "Open Payment Service (imported)" — the button that selects the new project. */
  openLabel: string
  /** Signed by this / another deployment, or Unsigned. */
  signature: string
  /** "test_plans: 3 rows" — non-zero only, in the server's order. */
  inserted: ReportLine[]
  /** "webhook_subscriptions: 1 row switched off — secret required". */
  switchedOff: ReportLine[]
  /** "1 row was skipped: it pointed at a row that is not in the export" / "2 assignments were cleared: …". */
  notImported: ReportLine[]
}

function reasonText(reason: string | null | undefined): string {
  if (!reason) return ''
  return (SKIP_REASONS as Record<string, string>)[reason] ?? reason
}

/** "1 row was skipped: <reason>" / "3 rows were skipped: <reason>". */
export function skippedSentence(count: number, reason: string | null | undefined): string {
  return count === 1
    ? fill(UI_SENTENCES.skipped_one, { reason: reasonText(reason) })
    : fill(UI_SENTENCES.skipped_many, { count: formatNumber(count), reason: reasonText(reason) })
}

/** "1 assignment was cleared: <reason>" / "2 assignments were cleared: <reason>". */
export function clearedSentence(count: number, reason: string | null | undefined): string {
  const head = count === 1 ? UI_SENTENCES.cleared_one : fill(UI_SENTENCES.cleared_many, { count: formatNumber(count) })
  const why = reasonText(reason)
  return why ? `${head}: ${why}` : head
}

/** The signature an export carries / an import accepted, as a reader's sentence. */
export function signatureLine(signature: string | null | undefined): string {
  if (signature === 'valid' || signature === 'signed') return SIGNED_HERE
  if (signature === 'unknown_key') return SIGNED_ELSEWHERE
  return UNSIGNED
}

function entityOf(skip: SkipLike): string {
  return skip.column ? `${skip.entity ?? ''}.${skip.column}` : skip.entity ?? ''
}

function positiveEntries(record: Record<string, number> | null | undefined): [string, number][] {
  return Object.entries(record && typeof record === 'object' ? record : {}).filter(([, n]) => Number.isFinite(n) && n > 0)
}

function skipLines(
  list: SkipLike[] | null | undefined,
  prefix: string,
  sentence: (count: number, reason: string | null | undefined) => string,
): ReportLine[] {
  return (Array.isArray(list) ? list : [])
    .filter((skip) => typeof skip.count === 'number' && skip.count > 0)
    .map((skip, i) => ({
      key: `${prefix}:${entityOf(skip)}:${skip.reason ?? ''}:${i}`,
      entity: entityOf(skip),
      text: sentence(skip.count as number, skip.reason),
    }))
}

/** Every line of a finished import's report, singular for 1. */
export function importReportLines(report: ImportReportLike, fallbackName = ''): ImportReportLines {
  const name = report.project_name?.trim() || fallbackName
  return {
    created: fill(UI_SENTENCES.import_done, { name }),
    openLabel: `Open ${name}`,
    signature: signatureLine(report.signature),
    inserted: positiveEntries(report.inserted).map(([entity, n]) => ({ key: `i:${entity}`, entity, text: countNoun(n, 'row') })),
    switchedOff: positiveEntries(report.switched_off).map(([entity, n]) => ({
      key: `o:${entity}`,
      entity,
      text: `${countNoun(n, 'row')} ${SWITCHED_OFF_SUFFIX[entity] ?? SWITCHED_OFF_DEFAULT}`,
    })),
    notImported: [...skipLines(report.skipped, 's', skippedSentence), ...skipLines(report.cleared, 'c', clearedSentence)],
  }
}

/** "4,210 rows in 30 tables" — a finished import, collapsed under "Last import". */
export function importSummaryLine(report: ImportReportLike): string {
  const inserted = positiveEntries(report.inserted)
  const rows = inserted.reduce((sum, [, n]) => sum + n, 0)
  return `${countNoun(rows, 'row')} in ${countNoun(inserted.length, 'table')}`
}

export type ImportJobKind = 'running' | 'done' | 'failed'

/** What the Import card shows for one import job. */
export function importJobState(job: JobLike): { kind: ImportJobKind; line: string } {
  if (job.status === 'queued' && !job.progress) return { kind: 'running', line: IMPORT_QUEUED }
  if (isActiveJob(job)) return { kind: 'running', line: formatProgress(job.progress, 'import') }
  if (job.status === 'succeeded' && job.import_report) {
    return { kind: 'done', line: fill(UI_SENTENCES.import_done, { name: job.import_report.project_name ?? '' }) }
  }
  return { kind: 'failed', line: job.error?.message?.trim() || IMPORT_FAILED_FALLBACK }
}
