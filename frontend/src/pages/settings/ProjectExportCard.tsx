/**
 * ProjectExportCard — "Export project data" on /settings/project-data (W24 ADM-05 phase 1; W30: run history).
 *
 * QA lead or ADMIN with access to the project. Export → `POST /projects/{id}/export` → the job polls
 * ("Exporting… 12 of 30 tables") → Download (2.1 MB) + "Available until …" + the signature line.
 *
 * W30: "Include run history" + "Runs from the last [30 / 90 / 365 days / All history]". Unticked, the card is W24's:
 * the POST has NO body (schema 1). Ticked, ONE estimate GET per window (the hook's key carries the window; nothing is
 * read before the tick) shows "About 412.3 MB · 412 runs · …" — a store that could not be read says so, never 0 — and
 * an estimate over 2 GB disables Export; Export sends `{include_history, history_days, max_runs}`; the job reads its
 * phase ("Runs and results… 120 of 412"); a finished history export adds "412 runs · 9,120 documents · 1,830
 * attachments" on its own line and, above 512 MB, the CLI hint.
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
import { useExportEstimate, useProjectDataJob } from '@/hooks/useProjectDataJobs'
import { projectDataErrorMessage, projectDataService, runningExportJobId } from '@/services/projectDataService'
import type { ProjectDataJob } from '@/types/projectData'
import {
  DOWNLOAD_FAILED,
  ESTIMATE_FAILED,
  ESTIMATING,
  estimateLine,
  estimateOptionsOf,
  EXPORT_AGAIN_LABEL,
  EXPORT_START_FAILED,
  exportCardState,
  HISTORY_DEFAULT_WINDOW,
  HISTORY_WINDOW_OPTIONS,
  historyOptionsFromForm,
  isActiveJob,
  largeDownloadHint,
  UI_SENTENCES,
  type EstimateLines,
  type HistoryWindow,
  type JobLike,
} from './projectDataModel'

/**
 * The history form: the checkbox row (≈ 146 px label + 139 px label + the 160 px select ≈ 493 px of the 704 px card at
 * 1280 under DejaVu), the intro, and — ticked — the estimate line (≈ 66 characters ≈ 475 px at 12 px).
 */
function HistoryOptions({
  includeHistory,
  selected,
  disabled,
  lines,
  error,
  onInclude,
  onWindow,
}: {
  includeHistory: boolean
  /** The chosen window. */
  selected: HistoryWindow
  disabled: boolean
  /** The estimate's lines once it answered (ticked only). */
  lines: EstimateLines | null
  /** The estimate GET failed. */
  error: unknown
  onInclude: (value: boolean) => void
  onWindow: (value: HistoryWindow) => void
}) {
  return (
    <div className="flex flex-col gap-2" data-export-history="">
      <div className="flex items-center gap-4">
        <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text)]">
          <input type="checkbox" checked={includeHistory} disabled={disabled} onChange={(e) => onInclude(e.target.checked)} />
          {UI_SENTENCES.include_history}
        </label>
        {includeHistory && (
          <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text)]">
            {UI_SENTENCES.history_window}
            <select
              className="input w-40 text-xs"
              value={selected}
              disabled={disabled}
              onChange={(e) => onWindow(e.target.value as HistoryWindow)}
            >
              {HISTORY_WINDOW_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      <p className="m-0 text-xs text-[var(--color-text-muted)]">{UI_SENTENCES.history_intro}</p>
      {includeHistory && (
        <div className="flex flex-col gap-0.5 text-xs" data-export-estimate={lines?.kind ?? (error ? 'error' : 'loading')}>
          {error ? (
            <p className="m-0 text-[var(--ui-danger-ink)]">{ESTIMATE_FAILED}</p>
          ) : !lines ? (
            // A <div>: the spinner is a <div>, which a <p> may not hold.
            <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
              <LoadingSpinner size="sm" />
              <span>{ESTIMATING}</span>
            </div>
          ) : (
            <>
              <p className="m-0 text-[var(--color-text)]" data-estimate-line="">{lines.line}</p>
              {lines.detail && (
                <p
                  data-estimate-detail=""
                  className={lines.kind === 'over' ? 'm-0 text-[var(--ui-danger-ink)]' : 'm-0 text-[var(--color-text-muted)]'}
                >
                  {lines.detail}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

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
  // W30: the history form. The estimate is read only while ticked, once per window (the hook's key).
  const [includeHistory, setIncludeHistory] = useState(false)
  const [historyWindow, setHistoryWindow] = useState<HistoryWindow>(HISTORY_DEFAULT_WINDOW)
  const { data: estimate, error: estimateError } = useExportEstimate(projectId, estimateOptionsOf(historyWindow), includeHistory)
  const estimated = includeHistory && estimate ? estimateLine(estimate) : null

  const watchedId = startedId ?? (latest && isActiveJob(latest) ? latest.id : null)
  const { data: watched } = useProjectDataJob(watchedId)
  const polled = watched && watched.id === watchedId ? watched : null
  const job: JobLike | null = polled ?? (startedId ? { id: startedId, status: 'queued' } : latest ?? null)
  const state = exportCardState(job)
  const hint = state.kind === 'ready' && state.history ? largeDownloadHint(job?.bytes, projectId) : null

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
      // Unticked: W24's body-less POST, exactly. Ticked: {include_history, history_days, max_runs}.
      const body = historyOptionsFromForm({ includeHistory, window: historyWindow })
      const created = body ? await projectDataService.startExport(projectId, body) : await projectDataService.startExport(projectId)
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
  // An estimate over 2 GB blocks starting an export with history (Export and Export again), never a Download.
  const overLimit = Boolean(estimated?.blocksExport)
  const primaryDisabled =
    state.button.disabled || starting || downloading || loading || (state.button.action === 'export' && overLimit)
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

      <HistoryOptions
        includeHistory={includeHistory}
        selected={historyWindow}
        disabled={busy || starting}
        lines={estimated}
        error={includeHistory ? estimateError : undefined}
        onInclude={setIncludeHistory}
        onWindow={setHistoryWindow}
      />

      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1" data-export-status="">
          {loadError ? (
            <p className="m-0 text-sm text-[var(--ui-danger-ink)]">{UI_SENTENCES.load_exports_failed}</p>
          ) : loading ? (
            <LoadingSpinner size="sm" />
          ) : (
            <div
              data-export-line=""
              className={
                state.kind === 'failed'
                  ? 'm-0 flex items-center gap-2 text-sm text-[var(--ui-danger-ink)]'
                  : 'm-0 flex items-center gap-2 text-sm text-[var(--color-text)]'
              }
            >
              {busy && <LoadingSpinner size="sm" />}
              <span>{state.line}</span>
            </div>
          )}
          {state.history && !loadError && (
            <p data-export-history-counts="" className="m-0 text-xs text-[var(--color-text)]">
              {state.history}
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
            <button type="button" className="btn-ghost" onClick={startExport} disabled={starting || downloading || overLimit}>
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

      {hint && (
        <p data-export-cli-hint="" className="m-0 text-xs text-[var(--color-text-muted)]">
          {hint}
        </p>
      )}
      {refusal && (
        <p role="alert" data-export-refusal="" className="m-0 text-xs text-[var(--ui-danger-ink)]">
          {refusal}
        </p>
      )}
    </section>
  )
}
