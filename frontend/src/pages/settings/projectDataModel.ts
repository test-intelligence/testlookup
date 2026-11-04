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
 *   - the file pre-check (2 GB since W30) uses the server's own sentence, so the page and the API agree.
 * A refused request's sentence (also out of a Blob) is read by the service's `projectDataErrorMessage`.
 *
 * W30 (ADM-05 run history) adds run history: the window form and its export body, the estimate line (a store
 * that could not be read says "not measured", never 0), the phase lines, the parted upload's part plan and
 * its "Uploading… n of m parts" line, and the history block of an import report.
 */
import {
  ACTIVE_STATUSES,
  ERROR_SENTENCES,
  fillSentence as fill,
  HISTORY_DEFAULT_DAYS,
  HISTORY_MAX_RUNS,
  HISTORY_WINDOWS_DAYS,
  IMPORT_ARCHIVE_MAX_BYTES,
  IMPORT_PART_MAX_BYTES,
  IMPORT_UPLOAD_MAX_BYTES,
  SKIP_REASONS,
  UI_SENTENCES,
} from '@/types/projectData'
import { formatCompactDateTime, formatDay, formatNumber } from '@/utils/formatters'

export { ERROR_SENTENCES, SKIP_REASONS, UI_SENTENCES }

// ── Copy ─────────────────────────────────────────────────────────────────────

/**
 * W30: 81 characters ≈ 624 px at 13 px under DejaVu — 112 px slack in the 736 px column at 1280 (the longer
 * "Export settings, the test catalog and run history, import them …" left 20 px, under the 40 px rule).
 */
export const PAGE_SUBTITLE = 'Export settings, catalog and run history, import as a new project, or reset data.'
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

// W30 (ADM-05 run history) — run history. The wire sentences are the vocabulary's (UI_SENTENCES.*); these are the
// card's own labels and the singular forms a count of 1 needs.
export const ESTIMATING = 'Estimating…'
export const ESTIMATE_FAILED = 'Could not load the size estimate.'
export const NOT_MEASURED = 'not measured'
export const ALL_HISTORY_LABEL = 'All history'
export const CHECKING_FILE = 'Checking the file…'
export const HISTORY_HEADING = 'History'
export const DOCUMENTS_HEADING = 'Stored documents'
export const ATTACHMENTS_HEADING = 'Attachments'
export const HISTORY_SKIPPED_HEADING = 'Not imported'
/** The pause the import wrote on the copy (§2.7) — the Retention page shows the same pause. */
export const RETENTION_PAUSED = 'The retention purge of the new project is paused until {date}.'
/** `UI_SENTENCES.recent_runs_note` for a count of 1. */
export const RECENT_RUN_NOTE_ONE = '1 imported run is from the last 7 days; an all-projects digest can list it.'
/** A history export whose archive is larger than this shows `UI_SENTENCES.large_download_hint` (R-2). */
export const LARGE_DOWNLOAD_BYTES = 512 * 1024 * 1024

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

// ── W30: run history — the form, the estimate, the phases, the parted upload ──

/** The window `<select>`'s value: a number of days, or every run. */
export type HistoryWindow = `${(typeof HISTORY_WINDOWS_DAYS)[number]}` | 'all'

export const HISTORY_DEFAULT_WINDOW = String(HISTORY_DEFAULT_DAYS) as HistoryWindow

/**
 * "30 days" / "90 days" / "365 days" / "All history". The longest, "All history" (11 characters ≈ 85 px at 12 px
 * under DejaVu, + the 24 px arrow), sits in a 144 px `w-36` select with 35 px to spare.
 */
export const HISTORY_WINDOW_OPTIONS: ReadonlyArray<{ value: HistoryWindow; label: string }> = [
  ...HISTORY_WINDOWS_DAYS.map((days) => ({ value: String(days) as HistoryWindow, label: `${days} days` })),
  { value: 'all', label: ALL_HISTORY_LABEL },
]

/** `'90'` → 90; `'all'` → null (every run). */
export function historyDaysOf(window: HistoryWindow): number | null {
  return window === 'all' ? null : Number(window)
}

export interface HistoryForm {
  includeHistory: boolean
  window: HistoryWindow
}

/** The body of `POST …/export` for the ticked form; `undefined` when unticked (the W24 body-less POST). */
export function historyOptionsFromForm(
  form: HistoryForm,
): { include_history: true; history_days: number | null; max_runs: number } | undefined {
  if (!form.includeHistory) return undefined
  return { include_history: true, history_days: historyDaysOf(form.window), max_runs: HISTORY_MAX_RUNS }
}

/** The estimate GET's options for the window (`useExportEstimate`). */
export function estimateOptionsOf(window: HistoryWindow): { historyDays: number | null } {
  return { historyDays: historyDaysOf(window) }
}

/** `{count} {plural}` in a template → "1 run" / "412 runs" (the vocabulary writes the plural). */
function countIn(template: string, key: string, count: number, singular: string, plural = `${singular}s`): string {
  return template.replace(`{${key}} ${plural}`, countNoun(count, singular, plural))
}

export interface EstimateLike {
  runs?: number | null
  documents?: Record<string, number> | null
  objects?: number | null
  estimated_bytes?: number | null
  over_limit?: boolean | null
  measured?: Record<string, boolean> | null
}

export interface EstimateLines {
  kind: 'measured' | 'unmeasured' | 'over'
  /** The first line: "About 412.3 MB · 412 runs · …", the unmeasured sentence, or the measured line when over. */
  line: string
  /** unmeasured: the counts that WERE read, a store that was not reading "not measured"; over: the over sentence. */
  detail: string | null
  /** Over the 2 GB cap: Export is disabled. */
  blocksExport: boolean
}

const sumOf = (record: Record<string, number> | null | undefined) =>
  Object.values(record && typeof record === 'object' ? record : {}).reduce((sum, n) => sum + (Number.isFinite(n) ? n : 0), 0)

const STORE_NAMES: Record<string, string> = { postgres: 'the database', documents: 'the document store', objects: 'attachment storage' }

/**
 * The estimate line. A store whose `measured` is false reads "not measured" and names itself in the
 * unmeasured sentence — never "0" (absence is not health). Over the cap: the measured line, the over
 * sentence, Export disabled.
 */
export function estimateLine(estimate: EstimateLike): EstimateLines {
  const measured = estimate.measured ?? {}
  const unmeasured = Object.keys(STORE_NAMES).filter((store) => measured[store] === false)
  const docs = estimate.documents ? sumOf(estimate.documents) : null
  const parts = [
    typeof estimate.runs === 'number' ? countNoun(estimate.runs, 'run') : `runs ${NOT_MEASURED}`,
    docs === null || measured.documents === false ? `documents ${NOT_MEASURED}` : countNoun(docs, 'document'),
    typeof estimate.objects === 'number' && measured.objects !== false
      ? countNoun(estimate.objects, 'attachment')
      : `attachments ${NOT_MEASURED}`,
  ]
  if (unmeasured.length > 0 || typeof estimate.estimated_bytes !== 'number') {
    const names = unmeasured.map((store) => STORE_NAMES[store])
    const store = names.length > 0 ? names.join(' and ') : STORE_NAMES.objects
    return { kind: 'unmeasured', line: fill(UI_SENTENCES.estimate_unmeasured, { store }), detail: parts.join(' · '), blocksExport: false }
  }
  let line = fill(UI_SENTENCES.estimate, { size: formatSize(estimate.estimated_bytes) })
  line = countIn(line, 'runs', estimate.runs ?? 0, 'run')
  line = countIn(line, 'documents', docs ?? 0, 'document')
  line = countIn(line, 'objects', estimate.objects ?? 0, 'attachment')
  if (estimate.over_limit) return { kind: 'over', line, detail: UI_SENTENCES.estimate_over, blocksExport: true }
  return { kind: 'measured', line, detail: null, blocksExport: false }
}

export interface PhaseProgressLike extends ProgressLike {
  phase?: string | null
  phase_done?: number | null
  phase_total?: number | null
}

const PHASE_LABELS: Record<string, string> = {
  catalog: UI_SENTENCES.phase_catalog,
  runs: UI_SENTENCES.phase_runs,
  documents: UI_SENTENCES.phase_documents,
  objects: UI_SENTENCES.phase_objects,
  verify: UI_SENTENCES.phase_verify,
  index: UI_SENTENCES.phase_index,
}

/**
 * The running line of a job with phases: the catalog phase (or a job without one — every W24 job) keeps
 * W24's "Exporting… 12 of 56 tables"; a later phase reads "Runs and results… 120 of 412" (≤ 40 characters),
 * or just "Stored documents…" before the worker has counted it.
 */
export function phaseLine(progress: PhaseProgressLike | null | undefined, kind: 'export' | 'import' = 'export'): string {
  const phase = progress?.phase
  const label = phase ? PHASE_LABELS[phase] : undefined
  if (!label || phase === 'catalog') return formatProgress(progress, kind)
  const total = progress?.phase_total
  if (typeof total !== 'number' || total <= 0) return `${label}…`
  const done = Math.min(Math.max(progress?.phase_done ?? 0, 0), total)
  return fill(UI_SENTENCES.progress_phase, { phase: label, done: formatNumber(done), total: formatNumber(total) })
}

export interface UploadLike {
  parts?: number | null
  bytes?: number | null
  received?: readonly number[] | null
}

/** "Uploading… 3 of 12 parts" ("… 0 of 1 part" for a one-part upload). */
export function uploadLine(upload: UploadLike | null | undefined): string {
  const total = typeof upload?.parts === 'number' && upload.parts > 0 ? upload.parts : 0
  const done = Math.min(Array.isArray(upload?.received) ? upload.received.length : 0, total)
  const line = fill(UI_SENTENCES.uploading, { done: formatNumber(done), total: formatNumber(total) })
  return total === 1 ? line.replace(/ parts$/, ' part') : line
}

export interface PartPlan {
  /** single: the W24 multipart POST (≤ 50 MB); parted: start → PUT per part → complete. */
  mode: 'single' | 'parted'
  parts: number
  /** Every part but the last is exactly 32 MB (the server's rule: parts == ceil(bytes / 32 MB)). */
  sizes: number[]
}

/** How a file of `bytes` travels: the single POST up to 50 MB, else 32 MB parts. */
export function partPlan(bytes: number): PartPlan {
  if (!Number.isFinite(bytes) || bytes <= 0) return { mode: 'single', parts: 0, sizes: [] }
  if (bytes <= IMPORT_UPLOAD_MAX_BYTES) return { mode: 'single', parts: 1, sizes: [bytes] }
  const parts = Math.ceil(bytes / IMPORT_PART_MAX_BYTES)
  const sizes = Array.from({ length: parts }, (_, i) => (i < parts - 1 ? IMPORT_PART_MAX_BYTES : bytes - IMPORT_PART_MAX_BYTES * (parts - 1)))
  return { mode: 'parted', parts, sizes }
}

/** A history export's archive over 512 MB: the CLI line (`UI_SENTENCES.large_download_hint`), else null. */
export function largeDownloadHint(bytes: number | null | undefined, project: string): string | null {
  return typeof bytes === 'number' && bytes > LARGE_DOWNLOAD_BYTES ? fill(UI_SENTENCES.large_download_hint, { project }) : null
}

// ── The jobs (structural: the API's ProjectDataJob fits) ─────────────────────

export interface SkipLike {
  entity?: string | null
  column?: string | null
  reason?: string | null
  count?: number | null
}

/** `import_report.history` (W30): what a schema-2 import wrote besides the catalog. */
export interface HistoryReportLike {
  runs?: number | null
  test_cases?: number | null
  oldest_run_at?: string | null
  newest_run_at?: string | null
  /** collection → documents inserted (every collection present, 0 allowed). */
  documents?: Record<string, number> | null
  objects?: number | null
  object_bytes?: number | null
  skipped?: SkipLike[] | null
  retention_paused_until?: string | null
  runs_past_retention?: number | null
  /** The copy's effective runs window in days; null = retention off. Absent (an older server) = not said. */
  retention_days?: number | null
  recent_runs?: number | null
  /** 'queued' | 'not_queued'. */
  search_reindex?: string | null
}

export interface ImportReportLike {
  project_id?: string | null
  project_name?: string | null
  inserted?: Record<string, number> | null
  skipped?: SkipLike[] | null
  cleared?: SkipLike[] | null
  switched_off?: Record<string, number> | null
  signature?: string | null
  history?: HistoryReportLike | null
}

export interface JobLike {
  id?: string | null
  kind?: string | null
  status?: string | null
  project_id?: string | null
  progress?: PhaseProgressLike | null
  /** W30: the export's options (`include_history` …). */
  options?: { include_history?: boolean | null } | null
  /** W30: a parted import's upload, while it is `uploading`. */
  upload?: UploadLike | null
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
  /** ready, a history export: "412 runs · 9,120 documents · 1,830 attachments" (W30); otherwise null. */
  history: string | null
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
  const tables = positiveEntries(tableCounts(job.counts))
  const rows = tables.reduce((sum, [, n]) => sum + n, 0)
  return `${countNoun(rows, 'row')} in ${countNoun(tables.length, 'table')}`
}

/**
 * W30: a history export's `counts` also carries `documents.<collection>` and `objects` (contract E-H5) — they are
 * not table rows, so the rows line leaves them out and `exportHistoryLine` reads them.
 */
function tableCounts(counts: Record<string, number> | null | undefined): Record<string, number> {
  return Object.fromEntries(Object.entries(counts ?? {}).filter(([key]) => key !== 'objects' && !key.startsWith('documents.')))
}

/**
 * "412 runs · 9,120 documents · 1,830 attachments" — the second line of a finished HISTORY export (null for a
 * catalog-only one), so the first line stays ≤ 44 characters beside Download (412.3 MB) + Export again.
 */
export function exportHistoryLine(job: JobLike): string | null {
  const counts = job.counts ?? {}
  const keys = Object.keys(counts)
  const hasHistory = job.options?.include_history === true || keys.some((key) => key === 'objects' || key.startsWith('documents.'))
  if (!hasHistory) return null
  const documents = keys.filter((key) => key.startsWith('documents.')).reduce((sum, key) => sum + (counts[key] || 0), 0)
  return [
    countNoun(counts.test_runs ?? 0, 'run'),
    countNoun(documents, 'document'),
    countNoun(counts.objects ?? 0, 'attachment'),
  ].join(' · ')
}

/** What the Export card shows for the newest export of the project (`null`: none yet). */
export function exportCardState(job: JobLike | null | undefined, now: number = Date.now()): ExportCardState {
  const exportButton = (disabled: boolean) => ({ label: EXPORT_LABEL, action: 'export' as const, disabled })
  const idle = { signature: null, detail: null, history: null, again: false }
  if (!job) return { kind: 'none', line: UI_SENTENCES.no_export, ...idle, button: exportButton(false) }
  if (job.status === 'queued' || job.status === 'running') {
    const line = job.status === 'queued' && !job.progress ? EXPORT_QUEUED : phaseLine(job.progress, 'export')
    return { kind: 'running', line, ...idle, button: exportButton(true) }
  }
  if (job.status === 'failed') {
    const line = job.error?.message?.trim() || ERROR_SENTENCES.export_failed
    return { kind: 'failed', line, ...idle, button: exportButton(false) }
  }
  const expiresAt = timeOf(job.expires_at)
  const ready = job.status === 'succeeded' && Boolean(job.download_path) && (expiresAt === null || now < expiresAt)
  if (!ready) {
    return { kind: 'expired', line: ERROR_SENTENCES.export_expired, ...idle, button: exportButton(false) }
  }
  const size = typeof job.bytes === 'number' ? formatSize(job.bytes) : ''
  return {
    kind: 'ready',
    line: exportSummaryLine(job),
    history: exportHistoryLine(job),
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
 * 2 GB → `upload_too_large_history` (W30: a file over 50 MB travels in 32 MB parts; W24's 50 MB
 * sentence keeps its meaning for the single route) — and the card sends NO request. `null` = acceptable.
 */
export function validateImportFile(file: { name?: string; size?: number; type?: string } | null | undefined): string | null {
  if (!file || !looksLikeZip(file) || !file.size) return ERROR_SENTENCES.upload_empty
  if (file.size > IMPORT_ARCHIVE_MAX_BYTES) return ERROR_SENTENCES.upload_too_large_history
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

/**
 * "1 row was skipped: <reason>" / "3 rows were skipped: <reason>". W30: a history skip names what it skipped —
 * "2 attachments were skipped: it is larger than 256 MB", "1 document was skipped: …".
 */
export function skippedSentence(
  count: number,
  reason: string | null | undefined,
  noun: 'row' | 'document' | 'attachment' = 'row',
): string {
  const sentence =
    count === 1
      ? fill(UI_SENTENCES.skipped_one, { reason: reasonText(reason) })
      : fill(UI_SENTENCES.skipped_many, { count: formatNumber(count), reason: reasonText(reason) })
  return noun === 'row' ? sentence : sentence.replace(/^(\S+) rows? /, (_whole, n: string) => `${n} ${count === 1 ? noun : `${noun}s`} `)
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
    inserted: positiveEntries(tableCounts(report.inserted)).map(([entity, n]) => ({ key: `i:${entity}`, entity, text: countNoun(n, 'row') })),
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
  const inserted = positiveEntries(tableCounts(report.inserted))
  const rows = inserted.reduce((sum, [, n]) => sum + n, 0)
  return `${countNoun(rows, 'row')} in ${countNoun(inserted.length, 'table')}`
}

export interface HistoryReportLines {
  /** "412 runs from Aug 3, 2026 to Nov 1, 2026" (`UI_SENTENCES.history_imported`, singular for 1). */
  imported: string
  /** "raw_allure_json: 9,120 documents" — non-zero only. */
  documents: ReportLine[]
  /** "objects: 1,830 attachments · 412.3 MB" — when any was copied. */
  attachments: ReportLine[]
  /** The history skips: "2 attachments were skipped: it is larger than 256 MB". */
  skipped: ReportLine[]
  /** The retention pause, the runs past the window (or "retention is off"), the recent-runs note, the reindex line. */
  notes: string[]
}

/** Every line of a history import's report (`import_report.history`), singular for 1. */
export function historyReportLines(history: HistoryReportLike): HistoryReportLines {
  const runs = history.runs ?? 0
  const collections = new Set(Object.keys(history.documents ?? {}))
  const nounOf = (entity: string) => (entity === 'objects' ? 'attachment' : collections.has(entity) ? 'document' : 'row')
  let imported = countIn(UI_SENTENCES.history_imported, 'runs', runs, 'run')
  if (history.oldest_run_at && history.newest_run_at) {
    imported = fill(imported, { oldest: formatDay(history.oldest_run_at), newest: formatDay(history.newest_run_at) })
  } else {
    imported = countNoun(runs, 'run')
  }
  const objects = history.objects ?? 0
  const notes: string[] = []
  if (history.retention_paused_until) notes.push(fill(RETENTION_PAUSED, { date: formatDay(history.retention_paused_until) }))
  if (history.retention_days === null) notes.push(UI_SENTENCES.retention_off_note)
  else if (typeof history.retention_days === 'number' && (history.runs_past_retention ?? 0) > 0) {
    notes.push(fill(UI_SENTENCES.retention_note, { days: formatNumber(history.retention_days) }))
  }
  const recent = history.recent_runs ?? 0
  if (recent === 1) notes.push(RECENT_RUN_NOTE_ONE)
  else if (recent > 1) notes.push(fill(UI_SENTENCES.recent_runs_note, { count: formatNumber(recent) }))
  if (history.search_reindex === 'queued') notes.push(UI_SENTENCES.reindex_queued)
  else if (history.search_reindex === 'not_queued') notes.push(UI_SENTENCES.reindex_not_queued)
  return {
    imported,
    documents: positiveEntries(history.documents).map(([entity, n]) => ({ key: `d:${entity}`, entity, text: countNoun(n, 'document') })),
    attachments:
      objects > 0
        ? [{ key: 'a:objects', entity: 'objects', text: `${countNoun(objects, 'attachment')} · ${formatSize(history.object_bytes ?? 0)}` }]
        : [],
    skipped: (Array.isArray(history.skipped) ? history.skipped : [])
      .filter((skip) => typeof skip.count === 'number' && skip.count > 0)
      .map((skip, i) => ({
        key: `h:${entityOf(skip)}:${skip.reason ?? ''}:${i}`,
        entity: entityOf(skip),
        text: skippedSentence(skip.count as number, skip.reason, nounOf(skip.entity ?? '')),
      })),
    notes,
  }
}

export type ImportJobKind = 'uploading' | 'running' | 'done' | 'failed'

/** A parted import whose parts are still arriving (W30 `uploading`). */
export function isUploadingJob(job: JobLike | null | undefined): boolean {
  return job?.status === 'uploading'
}

/** What the Import card shows for one import job. */
export function importJobState(job: JobLike): { kind: ImportJobKind; line: string } {
  if (isUploadingJob(job)) return { kind: 'uploading', line: uploadLine(job.upload) }
  if (job.status === 'queued' && !job.progress) return { kind: 'running', line: IMPORT_QUEUED }
  if (isActiveJob(job)) return { kind: 'running', line: phaseLine(job.progress, 'import') }
  if (job.status === 'succeeded' && job.import_report) {
    return { kind: 'done', line: fill(UI_SENTENCES.import_done, { name: job.import_report.project_name ?? '' }) }
  }
  return { kind: 'failed', line: job.error?.message?.trim() || IMPORT_FAILED_FALLBACK }
}
