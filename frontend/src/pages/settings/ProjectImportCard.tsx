/**
 * ProjectImportCard — "Import as a new project" on /settings/project-data (W24 ADM-05 phase 1). ADMIN only
 * (the page renders it for an ADMIN; the route refuses anyone else).
 *
 * Choose an export ZIP → the 50 MB / .zip pre-check in the server's words (and NO request when it fails)
 * → Import → `POST /projects/import` (multipart: `file`, `client_token`, an optional `name`) → the job polls
 * ("Importing… 5 of 30 tables") → "Created <name>." + Open <name> (selects the new project and lands on
 * Test Management) + what was imported, switched off, skipped and cleared.
 *
 *   - `client_token`: ONE UUID per chosen file, minted when the file is chosen. A double click, a retry of
 *     the same selection, or choosing the SAME file again re-sends the SAME token, so the server answers
 *     with the one job it already has (one project). After a FAILED job the next Import mints a new one.
 *   - On load, the newest import that is still queued / running resumes its progress; a finished one is
 *     shown collapsed under "Last import" (its link and counts).
 *   - The newest import comes from the PAGE's list read (`latest`, `loadError`), like the Export card.
 *   - In the page, no modal or side panel; names and sentences are React text.
 *
 * W30 (ADM-05 run history): a file up to 2 GB. Up to 50 MB it is W24's single POST, unchanged; above, the service's
 * `importArchive` checks it (SHA-256, one slice in memory) and sends it in 32 MB parts — start → one PUT per part
 * with `X-Part-Sha256` → complete — while the card reads "Uploading… 3 of 12 parts". The token per chosen file is
 * kept for the tab (sessionStorage), so after a reload an import still `uploading` reads "Choose the same file to
 * resume the upload." and the same file resumes it (the server answers the open job with the parts it has). A
 * proxy's HTML 413 on a part reads `A part is larger than 32 MB.`, never "Request failed …". A finished history
 * import adds the History block: the runs and their dates, the stored documents, the attachments, the skips, the
 * retention pause, the recent-runs (digest) note and the search rebuild.
 *
 * W37 (W24 F-2 / F-7): "Add members who have an account here (matched by e-mail)" and "Keep test case reviews", both
 * unticked by default and sent only when ticked (`add_members` / `keep_reviews` = "true" Form fields on the single
 * POST, booleans in the parted start). The report's new rows (members, suite owners, reviews, comments) read through
 * the same lists — a skipped member reads its own reason, e.g. "no active account here has this e-mail address" — a
 * case returned to Review requested is a Switched off line, and each option the file could not honour gets one line.
 */
import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { isAxiosError } from 'axios'
import { ArrowRight, Upload } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { PANEL_TITLE_CLASS } from '@/components/ui/PanelHeader'
import { useProjectDataJob } from '@/hooks/useProjectDataJobs'
import { projectDataErrorMessage, projectDataService, type ImportArchiveProgress } from '@/services/projectDataService'
import { useProjectStore } from '@/store/projectStore'
import type { ProjectDataJob } from '@/types/projectData'
import {
  ADD_MEMBERS_LABEL,
  ATTACHMENTS_HEADING,
  CHECKING_FILE,
  chosenFileLine,
  DOCUMENTS_HEADING,
  ERROR_SENTENCES,
  FILE_LABEL,
  HISTORY_HEADING,
  HISTORY_SKIPPED_HEADING,
  historyReportLines,
  IMPORT_LABEL,
  IMPORT_START_FAILED,
  IMPORTED_HEADING,
  importJobState,
  importOptionsFromForm,
  importReportLines,
  importSummaryLine,
  isActiveJob,
  isUploadingJob,
  KEEP_REVIEWS_LABEL,
  LAST_IMPORT_HEADING,
  NAME_LABEL,
  NAME_PLACEHOLDER,
  NOT_IMPORTED_HEADING,
  partPlan,
  SWITCHED_OFF_HEADING,
  UI_SENTENCES,
  uploadLine,
  validateImportFile,
  validateImportName,
  type HistoryReportLike,
  type ImportReportLike,
  type JobLike,
  type ReportLine,
} from './projectDataModel'

interface Props {
  /** The page's imports read failed (its SWR `error`). */
  loadError?: unknown
  /** The caller's newest import; `undefined` while the page's read is in flight, `null` when none. */
  latest: ProjectDataJob | null | undefined
  /** Re-read the page's imports list (after a start, and when a watched job ends). */
  onChanged: () => void
}

/** The same file chosen again (name, size, modification time) keeps its token. */
const fileKey = (file: File) => `${file.name}\u0000${file.size}\u0000${file.lastModified}`

/**
 * W30: file → client_token for THIS TAB (sessionStorage, so a reload mid-upload re-sends the same token and the
 * server answers the open upload with the parts it has). A token is a UUID scoped to its requester — no secret.
 */
const TOKENS_KEY = 'testlookup-project-import-tokens'
const MAX_TOKENS = 20

function loadTokens(): Map<string, string> {
  try {
    const parsed: unknown = JSON.parse(window.sessionStorage.getItem(TOKENS_KEY) ?? '[]')
    return new Map(Array.isArray(parsed) ? parsed.filter((e): e is [string, string] => Array.isArray(e) && e.length === 2) : [])
  } catch {
    return new Map()
  }
}

function saveTokens(tokens: Map<string, string>): void {
  try {
    window.sessionStorage.setItem(TOKENS_KEY, JSON.stringify([...tokens].slice(-MAX_TOKENS)))
  } catch {
    // Storage full or blocked: the token still lives for this visit.
  }
}

/** A proxy's 413 is an HTML page, not `{detail}`: on a part it means the part was over the proxy's limit. */
function isProxyRefusal(error: unknown): boolean {
  if (!isAxiosError(error) || error.response?.status !== 413) return false
  const data: unknown = error.response?.data
  return !(data && typeof data === 'object' && 'detail' in data)
}

/**
 * What the card shows while it sends a parted file: the service's `ImportArchiveProgress` — `hashing` (bytes read:
 * "Checking the file…") then `uploading` (parts received of the plan: "Uploading… 3 of 12 parts").
 */
type SendProgress = ImportArchiveProgress

/**
 * One report list. `stacked` puts the table (and column) above its sentence: a skipped / cleared sentence beside
 * `managed_test_cases.assignee_id` measured 666 px of 678 at 1280 under DejaVu, so it gets its own line.
 */
function ReportList({ id, heading, lines, stacked = false }: { id: string; heading: string; lines: ReportLine[]; stacked?: boolean }) {
  if (lines.length === 0) return null
  return (
    <div className="flex flex-col gap-1" data-report-list={id}>
      <h3 className="m-0 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">{heading}</h3>
      <ul className="m-0 flex list-disc flex-col gap-0.5 pl-5 text-xs text-[var(--color-text)]">
        {lines.map((line) =>
          stacked ? (
            <li key={line.key}>
              <span className="block font-mono text-[var(--color-text-muted)]">{line.entity}</span>
              <span className="block">{line.text}</span>
            </li>
          ) : (
            <li key={line.key}>
              <span className="font-mono">{line.entity}</span>: <span>{line.text}</span>
            </li>
          ),
        )}
      </ul>
    </div>
  )
}

/**
 * W30: a history import's block — "412 runs from Aug 3, 2026 to Nov 1, 2026", the stored documents and attachments
 * (W24's mono list rows, non-zero only), the skips, then the notes (the retention pause, the runs past the window,
 * the recent-runs digest note, the search rebuild). Every line is prose in text-xs or a list row ≤ 52 characters.
 */
function HistoryReport({ history }: { history: HistoryReportLike }) {
  const lines = historyReportLines(history)
  return (
    <div className="flex flex-col gap-2" data-import-history="">
      <div className="flex flex-col gap-1">
        <h3 className="m-0 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">{HISTORY_HEADING}</h3>
        <p className="m-0 text-xs text-[var(--color-text)]" data-history-runs="">{lines.imported}</p>
      </div>
      <ReportList id="history-documents" heading={DOCUMENTS_HEADING} lines={lines.documents} />
      <ReportList id="history-attachments" heading={ATTACHMENTS_HEADING} lines={lines.attachments} />
      <ReportList id="history-skipped" heading={HISTORY_SKIPPED_HEADING} lines={lines.skipped} stacked />
      {lines.notes.length > 0 && (
        <div className="flex flex-col gap-1" data-history-notes="">
          {lines.notes.map((note) => (
            <p key={note} className="m-0 text-xs text-[var(--color-text-muted)]">{note}</p>
          ))}
        </div>
      )}
    </div>
  )
}

/** A finished import's report: the created line + Open, the signature, and the three lists. */
function ImportReport({ report, onOpen }: { report: ImportReportLike; onOpen: () => void }) {
  const lines = importReportLines(report)
  return (
    <div className="flex flex-col gap-3" data-import-report="">
      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <p className="m-0 text-sm text-[var(--color-text)]" data-import-created="">{lines.created}</p>
          <p className="m-0 text-xs text-[var(--color-text-muted)]">{lines.signature}</p>
        </div>
        <button type="button" className="btn-secondary inline-flex flex-shrink-0 items-center gap-1.5" onClick={onOpen}>
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
          {lines.openLabel}
        </button>
      </div>
      {lines.optionsIgnored.length > 0 && (
        // W37: an option the file could not honour — said, never an error (≤ 82 characters, one text-xs line).
        <div className="flex flex-col gap-0.5" data-import-options-ignored="">
          {lines.optionsIgnored.map((line) => (
            <p key={line} className="m-0 text-xs text-[var(--color-text)]">{line}</p>
          ))}
        </div>
      )}
      <ReportList id="inserted" heading={IMPORTED_HEADING} lines={lines.inserted} />
      <ReportList id="switched-off" heading={SWITCHED_OFF_HEADING} lines={lines.switchedOff} />
      <ReportList id="not-imported" heading={NOT_IMPORTED_HEADING} lines={lines.notImported} stacked />
      {report.history && <HistoryReport history={report.history} />}
    </div>
  )
}

/** A job of this visit (started here, or resumed while running): progress, the full report, or its failure. */
function ImportJobBlock({ job, onOpen }: { job: JobLike; onOpen: (report: ImportReportLike) => void }) {
  const state = importJobState(job)
  if (state.kind === 'done' && job.import_report) {
    const report = job.import_report
    return <ImportReport report={report} onOpen={() => onOpen(report)} />
  }
  return (
    <div
      data-import-line=""
      className={
        state.kind === 'failed'
          ? 'm-0 flex items-center gap-2 text-sm text-[var(--ui-danger-ink)]'
          : 'm-0 flex items-center gap-2 text-sm text-[var(--color-text)]'
      }
    >
      {(state.kind === 'running' || state.kind === 'uploading') && <LoadingSpinner size="sm" />}
      <span>{state.line}</span>
    </div>
  )
}

/** W30: an upload left `uploading` (a reload, a closed tab): its parts so far and how to resume it. */
function UnfinishedUpload({ job }: { job: JobLike }) {
  return (
    <div className="flex flex-col gap-1" data-import-unfinished="">
      <p className="m-0 text-sm text-[var(--color-text)]" data-import-line="">{uploadLine(job.upload)}</p>
      <p className="m-0 text-xs text-[var(--color-text-muted)]">{UI_SENTENCES.resume_upload}</p>
    </div>
  )
}

/** A finished import from an earlier visit, collapsed: its line, its counts and its link. */
function LastImport({ job, onOpen }: { job: JobLike; onOpen: (report: ImportReportLike) => void }) {
  const state = importJobState(job)
  const report = state.kind === 'done' ? job.import_report : null
  return (
    <div className="flex flex-col gap-1" data-last-import="">
      <h3 className="m-0 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
        {LAST_IMPORT_HEADING}
      </h3>
      <div className="flex items-center justify-between gap-4">
        <p className={report ? 'm-0 text-xs text-[var(--color-text)]' : 'm-0 text-xs text-[var(--ui-danger-ink)]'}>
          {report ? `${state.line} ${importSummaryLine(report)}.` : state.line}
        </p>
        {report && (
          <button type="button" className="btn-secondary btn-sm flex-shrink-0" onClick={() => onOpen(report)}>
            {importReportLines(report).openLabel}
          </button>
        )}
      </div>
    </div>
  )
}

export default function ProjectImportCard({ loadError, latest, onChanged }: Props) {
  const navigate = useNavigate()
  const refreshProjects = useProjectStore((s) => s.refreshProjects)
  const setActiveProject = useProjectStore((s) => s.setActiveProject)

  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  // W37: Add members (W24 F-2) / Keep reviews (W24 F-7) — unticked by default, sent only when ticked.
  const [addMembers, setAddMembers] = useState(false)
  const [keepReviews, setKeepReviews] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  // The job of this visit: started here, or the newest import resumed while it was still running.
  const [startedId, setStartedId] = useState<string | null>(null)
  const [followedId, setFollowedId] = useState<string | null>(null)
  // W30: a parted file being checked / sent by THIS tab (null otherwise).
  const [sending, setSending] = useState<SendProgress | null>(null)
  // file → its client_token (minted when chosen; kept for the tab, W30); the token the last POST sent.
  const tokensRef = useRef<Map<string, string> | null>(null)
  const sentTokenRef = useRef<string | null>(null)
  const tokens = () => (tokensRef.current ??= loadTokens())

  // A queued / running import is followed; one still `uploading` is not — only this tab's file can finish it.
  const activeLatestId = latest && isActiveJob(latest) && !isUploadingJob(latest) ? latest.id : null
  if (activeLatestId && !startedId && followedId !== activeLatestId) setFollowedId(activeLatestId)

  const watchedId = startedId ?? followedId
  const { data: watched } = useProjectDataJob(watchedId)
  const polled = watched && watched.id === watchedId ? watched : null
  const current: JobLike | null = polled ?? (watchedId ? { id: watchedId, status: 'queued' } : null)
  const running = isActiveJob(current) || isUploadingJob(current) || sending !== null

  // A watched job that reaches a terminal status refreshes the page's list once.
  const ended = polled && !isActiveJob(polled) ? `${polled.id}:${polled.status}` : null
  useEffect(() => {
    if (ended) onChanged()
  }, [ended, onChanged])

  const fileError = file ? validateImportFile(file) : null
  const nameError = validateImportName(name)
  const canImport = Boolean(file) && !fileError && !nameError && !submitting && !running

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0] ?? null
    setFile(chosen)
    setRefusal(null)
    if (chosen && !validateImportFile(chosen) && !tokens().has(fileKey(chosen))) {
      tokens().set(fileKey(chosen), crypto.randomUUID())
      saveTokens(tokens())
    }
  }

  async function startImport() {
    if (!file || !canImport) return
    const key = fileKey(file)
    let clientToken = tokens().get(key) ?? crypto.randomUUID()
    // A failed job's token is spent: the next attempt is a new one.
    if (polled?.status === 'failed' && clientToken === sentTokenRef.current) clientToken = crypto.randomUUID()
    tokens().set(key, clientToken)
    saveTokens(tokens())
    sentTokenRef.current = clientToken
    const parted = partPlan(file.size).mode === 'parted'
    setSubmitting(true)
    setRefusal(null)
    try {
      const trimmed = name.trim()
      const options = { clientToken, ...(trimmed ? { name: trimmed } : {}), ...importOptionsFromForm({ addMembers, keepReviews }) }
      let job: ProjectDataJob
      if (parted) {
        // W30: over 50 MB — checked, then sent in 32 MB parts; the service reports each step.
        setSending({ phase: 'hashing', done: 0, total: file.size })
        job = await projectDataService.importArchive(file, { ...options, onProgress: setSending })
      } else {
        job = await projectDataService.startImport(file, options)
      }
      setStartedId(job.id)
      onChanged()
    } catch (err: unknown) {
      setRefusal(
        parted && isProxyRefusal(err)
          ? ERROR_SENTENCES.part_too_large
          : await projectDataErrorMessage(err, IMPORT_START_FAILED),
      )
      if (parted) onChanged()
    } finally {
      setSending(null)
      setSubmitting(false)
    }
  }

  async function openProject(report: ImportReportLike) {
    try {
      const projects = await refreshProjects()
      const created = projects.find((p) => p.id === report.project_id)
      if (created) setActiveProject(created)
    } catch {
      // The list read toasts its own failure; Test Management still opens.
    }
    navigate('/test-management')
  }

  return (
    <section
      className="card flex flex-col gap-3"
      data-project-import=""
      data-import-state={sending ? 'uploading' : current ? importJobState(current).kind : 'idle'}
      aria-labelledby="project-import-heading"
    >
      <h2
        id="project-import-heading"
        className={`${PANEL_TITLE_CLASS} flex items-center gap-2`}
      >
        <Upload className="h-4 w-4 text-[var(--color-text-muted)]" aria-hidden="true" />
        {UI_SENTENCES.import_heading}
      </h2>
      <p className="m-0 text-xs text-[var(--color-text-muted)]">{UI_SENTENCES.import_intro}</p>
      <p className="m-0 text-xs text-[var(--color-text-muted)]" data-import-inert="">{UI_SENTENCES.inert_note}</p>

      <div className="flex flex-col gap-3" data-import-form="">
        <div className="flex flex-col items-start gap-1">
          <label htmlFor="project-import-file" className="text-xs font-medium text-[var(--color-text)]">{FILE_LABEL}</label>
          <input
            id="project-import-file"
            type="file"
            accept=".zip,application/zip"
            onChange={onFileChange}
            disabled={submitting || running}
            className="text-xs text-[var(--color-text)]"
          />
          {file && !fileError && <p className="m-0 text-xs text-[var(--color-text-muted)]" data-import-file="">{chosenFileLine(file)}</p>}
          {fileError && <p className="m-0 text-xs text-[var(--ui-danger-ink)]" data-import-file-error="">{fileError}</p>}
        </div>
        <div className="flex flex-col items-start gap-1">
          <label htmlFor="project-import-name" className="text-xs font-medium text-[var(--color-text)]">{NAME_LABEL}</label>
          <input
            id="project-import-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={NAME_PLACEHOLDER}
            maxLength={255}
            disabled={submitting || running}
            className="input w-80 text-xs"
          />
          {nameError && <p className="m-0 text-xs text-[var(--ui-danger-ink)]">{nameError}</p>}
        </div>
        {/* W37: both off by default — "an archive never grants access" unless the admin ticks Add members. */}
        <div className="flex flex-col gap-1" data-import-options="">
          <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text)]">
            <input type="checkbox" checked={addMembers} disabled={submitting || running} onChange={(e) => setAddMembers(e.target.checked)} />
            {ADD_MEMBERS_LABEL}
          </label>
          <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text)]">
            <input type="checkbox" checked={keepReviews} disabled={submitting || running} onChange={(e) => setKeepReviews(e.target.checked)} />
            {KEEP_REVIEWS_LABEL}
          </label>
        </div>
        <div>
          <button type="button" className="btn-primary inline-flex items-center gap-1.5" onClick={startImport} disabled={!canImport}>
            {submitting ? <LoadingSpinner size="sm" /> : <Upload className="h-4 w-4" aria-hidden="true" />}
            {IMPORT_LABEL}
          </button>
        </div>
      </div>

      {refusal && <p role="alert" data-import-refusal="" className="m-0 text-xs text-[var(--ui-danger-ink)]">{refusal}</p>}
      {loadError ? <p className="m-0 text-sm text-[var(--ui-danger-ink)]">{UI_SENTENCES.load_imports_failed}</p> : null}
      {sending ? (
        // A <div>: the spinner is a <div>, which a <p> may not hold.
        <div data-import-line="" data-import-sending={sending.phase} className="flex items-center gap-2 text-sm text-[var(--color-text)]">
          <LoadingSpinner size="sm" />
          <span>
            {sending.phase === 'hashing'
              ? CHECKING_FILE
              : uploadLine({ parts: sending.total, received: Array.from({ length: sending.done }, (_, i) => i + 1) })}
          </span>
        </div>
      ) : current ? (
        <ImportJobBlock job={current} onOpen={openProject} />
      ) : latest && isUploadingJob(latest) ? (
        <UnfinishedUpload job={latest} />
      ) : latest ? (
        <LastImport job={latest} onOpen={openProject} />
      ) : null}
    </section>
  )
}
