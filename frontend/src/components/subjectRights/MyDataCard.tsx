/**
 * MyDataCard — "Your data" on Settings › Profile (W31 STRAT-12, contract §4.1): the person's own export (Art. 15 / 20).
 *
 * "Download my data" → `POST /api/v1/me/data-export` → the job polls ("Preparing your archive…") → "Ready — 1.4 MB,
 * signed. Available until …" + Download. Below: erasure is asked of an administrator (never self-service, §2.3).
 *
 *   - The newest export comes from the PAGE's list read (`latest`; the page binds `useMyExports().error`, which the
 *     routed-pages error guard reads there, and hands it in as `loadError`). The card polls ONE job: the one it
 *     started (or a 409 `export_running` named), or the newest while it is still queued / running.
 *   - Download streams through the API as a Blob (the token rides the Authorization header, never a URL); a refused
 *     download's sentence is read out of the Blob.
 *   - Layout `flex flex-col gap-3` (Tailwind v4: `space-y-*` loses to the children's `m-0`): the buttons row first,
 *     the `text-xs` status on its own line (≈ 432 px of the ≈ 704 px card at 1280 under DejaVu).
 *   - No modal, no native confirm dialog; every name and sentence is React text.
 */
import { useEffect, useState } from 'react'
import { Download, FileLock2 } from 'lucide-react'

import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useSubjectJob } from '@/hooks/useSubjectRights'
import {
  runningSubjectExportJobId,
  subjectRightsErrorMessage,
  subjectRightsService,
} from '@/services/subjectRightsService'
import { isActiveJob, type SubjectJob } from '@/types/subjectRights'
import {
  DOWNLOAD_FAILED,
  DOWNLOAD_LABEL,
  ERASURE_BY_ADMIN,
  exportView,
  LOAD_EXPORTS_FAILED,
  MY_DATA_BODY,
  MY_DATA_START,
  MY_DATA_TITLE,
  START_FAILED,
  watchedJobId,
} from './myDataModel'

interface Props {
  /** The newest export of my own data; `undefined` while the page's read is in flight, `null` when none. */
  latest: SubjectJob | null | undefined
  /** The page's list read failed (its SWR `error`). */
  loadError?: unknown
  /** Re-read the page's list (after a start, and when a watched job ends). */
  onChanged: () => void
}

/** A job the card has just started, before its first read answers. */
function startedPlaceholder(id: string): SubjectJob {
  return { id, kind: 'export', status: 'queued', source: 'self', requested_at: '' }
}

export default function MyDataCard({ latest, loadError, onChanged }: Props) {
  const [startedId, setStartedId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const watchedId = watchedJobId(startedId, latest)
  const { data: watched } = useSubjectJob(watchedId)
  const polled = watched && watched.id === watchedId ? watched : null
  const job = polled ?? (startedId ? startedPlaceholder(startedId) : latest ?? null)
  const view = exportView(job)

  // A watched job that reaches a terminal status refreshes the page's list once.
  const ended = polled && !isActiveJob(polled) ? `${polled.id}:${polled.status}` : null
  useEffect(() => {
    if (ended) onChanged()
  }, [ended, onChanged])

  async function start() {
    if (starting) return
    setStarting(true)
    setRefusal(null)
    try {
      const created = await subjectRightsService.startMyExport()
      setStartedId(created.id)
      onChanged()
    } catch (err: unknown) {
      const running = runningSubjectExportJobId(err)
      if (running) setStartedId(running)
      else setRefusal(await subjectRightsErrorMessage(err, START_FAILED))
    } finally {
      setStarting(false)
    }
  }

  async function download() {
    if (!job?.id || downloading) return
    setDownloading(true)
    setRefusal(null)
    try {
      await subjectRightsService.downloadExport(job.id)
    } catch (err: unknown) {
      setRefusal(await subjectRightsErrorMessage(err, DOWNLOAD_FAILED))
      onChanged()
    } finally {
      setDownloading(false)
    }
  }

  const loading = latest === undefined && !loadError && !startedId
  // A failed list read hides nothing the card started itself (that job is polled on its own).
  const listFailed = Boolean(loadError) && !startedId
  const state = loading ? 'loading' : listFailed ? 'error' : view.kind

  return (
    <section className="card flex flex-col gap-3" data-my-data="" data-export-state={state} aria-labelledby="my-data-heading">
      <h2
        id="my-data-heading"
        className="m-0 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]"
      >
        <FileLock2 className="h-4 w-4 text-[var(--color-text-muted)]" aria-hidden="true" />
        {MY_DATA_TITLE}
      </h2>
      <p className="m-0 text-sm text-[var(--color-text-secondary)]">{MY_DATA_BODY}</p>

      <div className="flex items-center gap-3">
        <button
          type="button"
          className="btn-primary inline-flex items-center gap-1.5"
          onClick={start}
          disabled={view.startDisabled || starting || loading}
        >
          {starting ? <LoadingSpinner size="sm" /> : <FileLock2 className="h-4 w-4" aria-hidden="true" />}
          {MY_DATA_START}
        </button>
        {view.download && !listFailed && (
          <button
            type="button"
            className="btn-secondary inline-flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-50"
            onClick={download}
            disabled={downloading}
          >
            {downloading ? <LoadingSpinner size="sm" /> : <Download className="h-4 w-4" aria-hidden="true" />}
            {DOWNLOAD_LABEL}
          </button>
        )}
      </div>

      <div className="flex min-w-0 flex-col gap-1" data-my-data-status="">
        {listFailed ? (
          <p className="m-0 text-xs text-[var(--ui-danger-ink)]">{LOAD_EXPORTS_FAILED}</p>
        ) : loading ? (
          <LoadingSpinner size="sm" />
        ) : (
          <div
            data-my-data-line=""
            className={
              view.kind === 'failed'
                ? 'm-0 flex items-center gap-2 text-xs text-[var(--ui-danger-ink)]'
                : 'm-0 flex items-center gap-2 text-xs text-[var(--color-text)]'
            }
          >
            {view.kind === 'running' && <LoadingSpinner size="sm" />}
            <span>{view.line}</span>
          </div>
        )}
        {refusal && (
          <p role="alert" data-my-data-refusal="" className="m-0 text-xs text-[var(--ui-danger-ink)]">
            {refusal}
          </p>
        )}
      </div>

      <p className="m-0 text-xs text-[var(--color-text-muted)]" data-my-data-erasure="">
        {ERASURE_BY_ADMIN}
      </p>
    </section>
  )
}
