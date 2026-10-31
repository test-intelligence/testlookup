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
 */
import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { ArrowRight, Upload } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useProjectDataJob } from '@/hooks/useProjectDataJobs'
import { projectDataErrorMessage, projectDataService } from '@/services/projectDataService'
import { useProjectStore } from '@/store/projectStore'
import type { ProjectDataJob } from '@/types/projectData'
import {
  chosenFileLine,
  FILE_LABEL,
  IMPORT_LABEL,
  IMPORT_START_FAILED,
  IMPORTED_HEADING,
  importJobState,
  importReportLines,
  importSummaryLine,
  isActiveJob,
  LAST_IMPORT_HEADING,
  NAME_LABEL,
  NAME_PLACEHOLDER,
  NOT_IMPORTED_HEADING,
  SWITCHED_OFF_HEADING,
  UI_SENTENCES,
  validateImportFile,
  validateImportName,
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
      <ReportList id="inserted" heading={IMPORTED_HEADING} lines={lines.inserted} />
      <ReportList id="switched-off" heading={SWITCHED_OFF_HEADING} lines={lines.switchedOff} />
      <ReportList id="not-imported" heading={NOT_IMPORTED_HEADING} lines={lines.notImported} stacked />
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
    <p
      data-import-line=""
      className={
        state.kind === 'failed'
          ? 'm-0 flex items-center gap-2 text-sm text-[var(--ui-danger-ink)]'
          : 'm-0 flex items-center gap-2 text-sm text-[var(--color-text)]'
      }
    >
      {state.kind === 'running' && <LoadingSpinner size="sm" />}
      <span>{state.line}</span>
    </p>
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
  const [submitting, setSubmitting] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  // The job of this visit: started here, or the newest import resumed while it was still running.
  const [startedId, setStartedId] = useState<string | null>(null)
  const [followedId, setFollowedId] = useState<string | null>(null)
  // file → its client_token (minted when chosen); the token the last POST sent.
  const tokensRef = useRef(new Map<string, string>())
  const sentTokenRef = useRef<string | null>(null)

  const activeLatestId = latest && isActiveJob(latest) ? latest.id : null
  if (activeLatestId && !startedId && followedId !== activeLatestId) setFollowedId(activeLatestId)

  const watchedId = startedId ?? followedId
  const { data: watched } = useProjectDataJob(watchedId)
  const polled = watched && watched.id === watchedId ? watched : null
  const current: JobLike | null = polled ?? (watchedId ? { id: watchedId, status: 'queued' } : null)
  const running = isActiveJob(current)

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
    if (chosen && !validateImportFile(chosen) && !tokensRef.current.has(fileKey(chosen))) {
      tokensRef.current.set(fileKey(chosen), crypto.randomUUID())
    }
  }

  async function startImport() {
    if (!file || !canImport) return
    const key = fileKey(file)
    let clientToken = tokensRef.current.get(key) ?? crypto.randomUUID()
    // A failed job's token is spent: the next attempt is a new one.
    if (polled?.status === 'failed' && clientToken === sentTokenRef.current) clientToken = crypto.randomUUID()
    tokensRef.current.set(key, clientToken)
    sentTokenRef.current = clientToken
    setSubmitting(true)
    setRefusal(null)
    try {
      const trimmed = name.trim()
      const job = await projectDataService.startImport(file, { clientToken, ...(trimmed ? { name: trimmed } : {}) })
      setStartedId(job.id)
      onChanged()
    } catch (err: unknown) {
      setRefusal(await projectDataErrorMessage(err, IMPORT_START_FAILED))
    } finally {
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
      data-import-state={current ? importJobState(current).kind : 'idle'}
      aria-labelledby="project-import-heading"
    >
      <h2
        id="project-import-heading"
        className="m-0 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]"
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
        <div>
          <button type="button" className="btn-primary inline-flex items-center gap-1.5" onClick={startImport} disabled={!canImport}>
            {submitting ? <LoadingSpinner size="sm" /> : <Upload className="h-4 w-4" aria-hidden="true" />}
            {IMPORT_LABEL}
          </button>
        </div>
      </div>

      {refusal && <p role="alert" data-import-refusal="" className="m-0 text-xs text-[var(--ui-danger-ink)]">{refusal}</p>}
      {loadError ? <p className="m-0 text-sm text-[var(--ui-danger-ink)]">{UI_SENTENCES.load_imports_failed}</p> : null}
      {current ? (
        <ImportJobBlock job={current} onOpen={openProject} />
      ) : latest ? (
        <LastImport job={latest} onOpen={openProject} />
      ) : null}
    </section>
  )
}
