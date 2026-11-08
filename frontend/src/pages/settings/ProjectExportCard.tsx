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
 * W37: the window's 5th choice "One release" shows a release select (the top bar's cached list) on its own line; the
 * estimate and the POST then name `history_release_id` (no `history_days`) and Export waits until a release is
 * chosen. "Include members and suite owners …" / "Include test case reviews and comments" are independent of history
 * and add `include_members` / `include_reviews` only when ticked. Above 512 MB, Download asks for a one-time ticket
 * and navigates to it (the browser saves straight to disk); at or below, the Blob download is unchanged.
 *
 *   - The newest export comes from the PAGE's list read (`latest`; the page binds its load error, the
 *     routed-pages error guard reads the page). This card polls ONE job: the one it started or resumed,
 *     or the newest when it is still queued / running (a reload mid-export resumes the progress).
 *   - A 409 `export_running` names the running job: the card resumes it — no error.
 *   - Download streams through the API as a Blob (the token rides the Authorization header; no
 *     `window.open` of an API URL). A refused download's JSON sentence is read OUT OF THE BLOB, never
 *     "Request failed with status code …". W37: a large one's ticket refusal reads its sentence the same way.
 *   - In the page, no modal or side panel; every name and sentence is React text.
 */
import { useEffect, useMemo, useState } from 'react'
import { Download, PackageOpen } from 'lucide-react'
import { releaseOptions } from '@/components/digests/digestScopeModel'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useExportEstimate, useProjectDataJob } from '@/hooks/useProjectDataJobs'
import { useReleases } from '@/hooks/useReleases'
import { projectDataErrorMessage, projectDataService, runningExportJobId } from '@/services/projectDataService'
import type { ProjectDataJob } from '@/types/projectData'
import {
  CHOOSE_RELEASE,
  DOWNLOAD_FAILED,
  ESTIMATE_FAILED,
  ESTIMATING,
  estimateLine,
  estimateOptionsOf,
  EXPORT_AGAIN_LABEL,
  EXPORT_START_FAILED,
  exportBodyFromForm,
  exportCardState,
  exportFormBlocked,
  HISTORY_DEFAULT_WINDOW,
  HISTORY_WINDOW_OPTIONS,
  INCLUDE_MEMBERS_LABEL,
  INCLUDE_REVIEWS_LABEL,
  isActiveJob,
  isLargeDownload,
  largeDownloadHint,
  NO_RELEASES,
  RELEASE_LABEL,
  UI_SENTENCES,
  type EstimateLines,
  type HistoryWindow,
  type JobLike,
} from './projectDataModel'

/**
 * W37 (F-W30-1): the release a release-scoped history export is about — the top bar's own cached release list
 * (`useReleases(…, {cached: true})`, the ReleasePicker's SWR entry: no request of its own), ordered by the digests'
 * `releaseOptions` (the active release, then in-progress ones, then the rest). Mounted only once "One release" is
 * chosen. A 240 px select on its own line under the window row (that row has ≈ 211 px left of the 704 px card).
 */
function ReleaseSelect({ value, disabled, onChange }: { value: string; disabled: boolean; onChange: (id: string) => void }) {
  const { data } = useReleases(undefined, { cached: true })
  const options = useMemo(() => releaseOptions(Array.isArray(data?.items) ? data.items : []), [data])
  return (
    <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text)]" data-export-release="">
      {RELEASE_LABEL}
      <select className="input w-60 text-xs" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        <option value="">{options.length ? CHOOSE_RELEASE : NO_RELEASES}</option>
        {options.map((release) => (
          <option key={release.id} value={release.id}>{release.name}</option>
        ))}
      </select>
    </label>
  )
}

/**
 * W37 (W24 F-2 / F-7): two boxes independent of run history; each is a body key only when ticked. The longer label
 * (≈ 62 characters ≈ 430 px at 14 px under DejaVu, + the 20 px box) fits the 704 px card on one line.
 */
function IncludeOptions({
  includeMembers,
  includeReviews,
  disabled,
  onMembers,
  onReviews,
}: {
  includeMembers: boolean
  includeReviews: boolean
  disabled: boolean
  onMembers: (value: boolean) => void
  onReviews: (value: boolean) => void
}) {
  return (
    <div className="flex flex-col gap-1" data-export-include="">
      <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text)]">
        <input type="checkbox" checked={includeMembers} disabled={disabled} onChange={(e) => onMembers(e.target.checked)} />
        {INCLUDE_MEMBERS_LABEL}
      </label>
      <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text)]">
        <input type="checkbox" checked={includeReviews} disabled={disabled} onChange={(e) => onReviews(e.target.checked)} />
        {INCLUDE_REVIEWS_LABEL}
      </label>
    </div>
  )
}

/**
 * The history form: the checkbox row (≈ 146 px label + 139 px label + the 160 px select ≈ 493 px of the 704 px card at
 * 1280 under DejaVu), the intro, and — ticked — the estimate line (≈ 66 characters ≈ 475 px at 12 px).
 */
function HistoryOptions({
  includeHistory,
  selected,
  releaseId,
  disabled,
  lines,
  error,
  onInclude,
  onWindow,
  onRelease,
}: {
  includeHistory: boolean
  /** The chosen window. */
  selected: HistoryWindow
  /** W37: the chosen release (`''` = none yet) when the window is "One release". */
  releaseId: string
  disabled: boolean
  /** The estimate's lines once it answered (ticked only). */
  lines: EstimateLines | null
  /** The estimate GET failed. */
  error: unknown
  onInclude: (value: boolean) => void
  onWindow: (value: HistoryWindow) => void
  onRelease: (releaseId: string) => void
}) {
  // W37: "One release" with no release chosen reads no estimate — nothing to show under the intro yet.
  const awaitingRelease = selected === 'release' && !releaseId
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
      {includeHistory && selected === 'release' && <ReleaseSelect value={releaseId} disabled={disabled} onChange={onRelease} />}
      <p className="m-0 text-xs text-[var(--color-text-muted)]">{UI_SENTENCES.history_intro}</p>
      {includeHistory && !awaitingRelease && (
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
  // W37: the release of a "One release" window ('' = none chosen) and the two include boxes.
  const [releaseId, setReleaseId] = useState('')
  const [includeMembers, setIncludeMembers] = useState(false)
  const [includeReviews, setIncludeReviews] = useState(false)
  const form = { includeHistory, window: historyWindow, releaseId, includeMembers, includeReviews }
  // "One release" with no release yet: Export waits and no estimate is read (there is nothing to estimate).
  const awaitingRelease = exportFormBlocked(form)
  const { data: estimate, error: estimateError } = useExportEstimate(
    projectId,
    estimateOptionsOf(historyWindow, releaseId),
    includeHistory && !awaitingRelease,
  )
  const estimated = includeHistory && !awaitingRelease && estimate ? estimateLine(estimate) : null

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
      // Nothing ticked: W24's body-less POST, exactly. History: {include_history, history_days, max_runs} — or, for
      // one release, {include_history, max_runs, history_release_id}; W37's include boxes add a key only when ticked.
      const body = exportBodyFromForm(form)
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
      // W37 (F-W30-6): over 512 MB, a one-time ticket and a navigation straight to disk; otherwise W24's Blob, unchanged.
      if (isLargeDownload(job.bytes)) await projectDataService.downloadLargeExport(job.id)
      else await projectDataService.downloadExport(job.id)
    } catch (err: unknown) {
      setRefusal(await projectDataErrorMessage(err, DOWNLOAD_FAILED))
      onChanged()
    } finally {
      setDownloading(false)
    }
  }

  const loading = latest === undefined && !loadError && !startedId
  const busy = state.kind === 'running'
  // An estimate over 2 GB — or (W37) "One release" with no release chosen — blocks starting an export (Export and
  // Export again), never a Download.
  const overLimit = Boolean(estimated?.blocksExport) || awaitingRelease
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
        releaseId={releaseId}
        disabled={busy || starting}
        lines={estimated}
        error={includeHistory && !awaitingRelease ? estimateError : undefined}
        onInclude={setIncludeHistory}
        onWindow={setHistoryWindow}
        onRelease={setReleaseId}
      />
      <IncludeOptions
        includeMembers={includeMembers}
        includeReviews={includeReviews}
        disabled={busy || starting}
        onMembers={setIncludeMembers}
        onReviews={setIncludeReviews}
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
