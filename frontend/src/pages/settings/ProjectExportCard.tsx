/**
 * ProjectExportCard — "Export settings and catalog" on /settings/project-data (W24 ADM-05 phase 1).
 *
 * QA lead or ADMIN with access to the project. Export → `POST /projects/{id}/export` → the job polls
 * ("Exporting… 12 of 30 tables") → Download (2.1 MB) + "Available until …" + the signature line.
 *
 *   - The newest export comes from the PAGE's list read (`latest`; the page binds its load error, the
 *     routed-pages error guard reads the page). This card polls ONE job: the one it started or resumed,
 *     or the newest when it is still queued / running (a reload mid-export resumes the progress).
 *   - A 409 `export_running` names the running job: the card resumes it — no error.
 *   - Download streams through the API as a Blob (the token rides the Authorization header; no
 *     `window.open` of an API URL). A refused download's JSON sentence is read OUT OF THE BLOB, never
 *     "Request failed with status code …".
 *   - In the page, no modal or side panel; every name and sentence is React text.
 */
import { useEffect, useState } from 'react'
import { Download, PackageOpen } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useProjectDataJob } from '@/hooks/useProjectDataJobs'
import { projectDataErrorMessage, projectDataService, runningExportJobId } from '@/services/projectDataService'
import type { ProjectDataJob } from '@/types/projectData'
import {
  DOWNLOAD_FAILED,
  EXPORT_AGAIN_LABEL,
  EXPORT_START_FAILED,
  exportCardState,
  isActiveJob,
  UI_SENTENCES,
  type JobLike,
} from './projectDataModel'

interface Props {
  /** The page's export-list read failed (its SWR `error`). */
  loadError?: unknown
  projectId: string
  /** The newest export of the project; `undefined` while the page's read is in flight, `null` when none. */
  latest: ProjectDataJob | null | undefined
  /** Re-read the page's export list (after a start, and when a watched job ends). */
  onChanged: () => void
}

export default function ProjectExportCard({ loadError, projectId, latest, onChanged }: Props) {
  // A job this card started or resumed (a 409 `export_running` names one).
  const [startedId, setStartedId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const watchedId = startedId ?? (latest && isActiveJob(latest) ? latest.id : null)
  const { data: watched } = useProjectDataJob(watchedId)
  const polled = watched && watched.id === watchedId ? watched : null
  const job: JobLike | null = polled ?? (startedId ? { id: startedId, status: 'queued' } : latest ?? null)
  const state = exportCardState(job)

  // A watched job that reaches a terminal status refreshes the page's list once.
  const ended = polled && !isActiveJob(polled) ? `${polled.id}:${polled.status}` : null
  useEffect(() => {
    if (ended) onChanged()
  }, [ended, onChanged])

  async function startExport() {
    if (starting) return
    setStarting(true)
    setRefusal(null)
    try {
      const created = await projectDataService.startExport(projectId)
      setStartedId(created.id)
      onChanged()
    } catch (err: unknown) {
      const running = runningExportJobId(err)
      if (running) setStartedId(running)
      else setRefusal(await projectDataErrorMessage(err, EXPORT_START_FAILED))
    } finally {
      setStarting(false)
    }
  }

  async function download() {
    if (!job?.id || downloading) return
    setDownloading(true)
    setRefusal(null)
    try {
      await projectDataService.downloadExport(job.id)
    } catch (err: unknown) {
      setRefusal(await projectDataErrorMessage(err, DOWNLOAD_FAILED))
      onChanged()
    } finally {
      setDownloading(false)
    }
  }

  const loading = latest === undefined && !loadError && !startedId
  const busy = state.kind === 'running'
  const primaryDisabled = state.button.disabled || starting || downloading || loading
  const onPrimary = state.button.action === 'download' ? download : startExport

  return (
    <section
      className="card flex flex-col gap-3"
      data-project-export=""
      data-export-state={loading ? 'loading' : state.kind}
      aria-labelledby="project-export-heading"
    >
      <h2
        id="project-export-heading"
        className="m-0 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]"
      >
        <PackageOpen className="h-4 w-4 text-[var(--color-text-muted)]" aria-hidden="true" />
        {UI_SENTENCES.export_heading}
      </h2>
      <p className="m-0 text-xs text-[var(--color-text-muted)]">{UI_SENTENCES.export_intro}</p>

      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1" data-export-status="">
          {loadError ? (
            <p className="m-0 text-sm text-[var(--ui-danger-ink)]">{UI_SENTENCES.load_exports_failed}</p>
          ) : loading ? (
            <LoadingSpinner size="sm" />
          ) : (
            <p
              data-export-line=""
              className={
                state.kind === 'failed'
                  ? 'm-0 flex items-center gap-2 text-sm text-[var(--ui-danger-ink)]'
                  : 'm-0 flex items-center gap-2 text-sm text-[var(--color-text)]'
              }
            >
              {busy && <LoadingSpinner size="sm" />}
              <span>{state.line}</span>
            </p>
          )}
          {state.signature && !loadError && (
            <p data-export-signature="" className="m-0 text-xs text-[var(--color-text-muted)]">
              {state.signature}
            </p>
          )}
          {state.detail && !loadError && (
            <p data-export-expires="" className="m-0 text-xs text-[var(--color-text-muted)]">
              {state.detail}
            </p>
          )}
        </div>
        <div className="flex flex-shrink-0 items-center gap-2">
          {state.again && (
            <button type="button" className="btn-ghost" onClick={startExport} disabled={starting || downloading}>
              {EXPORT_AGAIN_LABEL}
            </button>
          )}
          <button
            type="button"
            className={
              state.button.action === 'download'
                ? 'btn-secondary inline-flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-50'
                : 'btn-primary inline-flex items-center gap-1.5'
            }
            onClick={onPrimary}
            disabled={primaryDisabled}
          >
            {starting || downloading ? (
              <LoadingSpinner size="sm" />
            ) : (
              <Download className="h-4 w-4" aria-hidden="true" />
            )}
            {state.button.label}
          </button>
        </div>
      </div>

      {refusal && (
        <p role="alert" data-export-refusal="" className="m-0 text-xs text-[var(--ui-danger-ink)]">
          {refusal}
        </p>
      )}
    </section>
  )
}
