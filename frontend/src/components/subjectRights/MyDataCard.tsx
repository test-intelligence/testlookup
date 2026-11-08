/**
 * MyDataCard — "Your data" on Settings › Profile (W31 STRAT-12, contract §4.1): the person's own export (Art. 15 / 20).
 *
 * "Download my data" → `POST /api/v1/me/data-export` → the job polls ("Preparing your archive…") → "Ready — 1.4 MB,
 * signed. Available until …" + Download. Below (W38, W31 F-1): the erasure REQUEST block — "Ask to have your account
 * erased" → an inline confirm → `POST /api/v1/me/erasure-request`; pending (withdraw) / declined (the reason's
 * sentence) / withdrawn. An administrator reviews the request and runs the erasure; the person never erases.
 * With a ready export the start button reads "Prepare a new copy" (W32, OR-10 (5)): it starts a NEW export, and
 * Download beside it fetches the ready one.
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
import { Download, FileLock2, UserX } from 'lucide-react'

import InlineConfirm from '@/components/ui/inPage/InlineConfirm'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useMyErasureRequest } from '@/hooks/useErasureRequests'
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
  ERASURE_REQUEST_CONFIRM,
  ERASURE_REQUEST_SEND,
  ERASURE_REQUEST_START,
  ERASURE_REQUEST_WITHDRAW,
  erasureRequestView,
  exportView,
  FILE_REQUEST_FAILED,
  LOAD_EXPORTS_FAILED,
  MY_DATA_BODY,
  MY_DATA_START,
  MY_DATA_TITLE,
  START_FAILED,
  watchedJobId,
  WITHDRAW_CONFIRM,
  WITHDRAW_REQUEST_FAILED,
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
          {/* A failed list read hides Download, so the button must not promise a "new" copy of one it does not show. */}
          {listFailed ? MY_DATA_START : view.startLabel}
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

      <ErasureRequestBlock />
    </section>
  )
}

type Confirming = 'file' | 'withdraw' | null

/**
 * W38 (W31 F-1, contract §4): the erasure block under the export — the person ASKS an administrator to erase the
 * account (`POST /api/v1/me/erasure-request`, no body), sees the pending request (and may withdraw it) or the
 * decline's sentence. Both questions are asked in the card (`InlineConfirm`); the strip's button is the only sender.
 */
function ErasureRequestBlock() {
  const { data: request, error: loadError, mutate } = useMyErasureRequest()
  const [confirming, setConfirming] = useState<Confirming>(null)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const view = erasureRequestView(request, loadError)

  async function send(action: 'file' | 'withdraw') {
    if (busy) return
    setBusy(true)
    setRefusal(null)
    try {
      const updated =
        action === 'file'
          ? await subjectRightsService.fileErasureRequest()
          : await subjectRightsService.withdrawErasureRequest()
      setConfirming(null)
      await mutate(updated, { revalidate: false })
    } catch (err: unknown) {
      setConfirming(null)
      setRefusal(await subjectRightsErrorMessage(err, action === 'file' ? FILE_REQUEST_FAILED : WITHDRAW_REQUEST_FAILED))
      // A request filed (or decided) in another tab: re-read, so the block shows what is true now.
      void mutate()
    } finally {
      setBusy(false)
    }
  }

  function open(action: 'file' | 'withdraw') {
    setRefusal(null)
    setConfirming(action)
  }

  return (
    <div
      className="flex flex-col gap-2 border-t border-[var(--color-border)] pt-3"
      data-my-erasure=""
      data-erasure-request-state={view.kind}
    >
      {view.kind === 'loading' && <LoadingSpinner size="sm" />}
      {view.line && (
        <p
          data-my-erasure-line=""
          className={`m-0 text-xs ${view.kind === 'error' ? 'text-[var(--ui-danger-ink)]' : 'text-[var(--color-text)]'}`}
        >
          {view.line}
        </p>
      )}
      {(view.canAsk || view.canWithdraw) && (
        <div className="flex items-center gap-3">
          {view.canAsk && (
            <button type="button" className="btn-secondary inline-flex items-center gap-1.5" onClick={() => open('file')} disabled={busy}>
              <UserX className="h-4 w-4" aria-hidden="true" />
              {ERASURE_REQUEST_START}
            </button>
          )}
          {view.canWithdraw && (
            <button type="button" className="btn-secondary inline-flex items-center gap-1.5" onClick={() => open('withdraw')} disabled={busy}>
              {ERASURE_REQUEST_WITHDRAW}
            </button>
          )}
        </div>
      )}
      {confirming === 'file' && view.canAsk && (
        <InlineConfirm
          message={ERASURE_REQUEST_CONFIRM}
          confirmLabel={ERASURE_REQUEST_SEND}
          busy={busy}
          onConfirm={() => void send('file')}
          onCancel={() => setConfirming(null)}
          testId="my-erasure-confirm"
        />
      )}
      {confirming === 'withdraw' && view.canWithdraw && (
        <InlineConfirm
          message={WITHDRAW_CONFIRM}
          confirmLabel={ERASURE_REQUEST_WITHDRAW}
          busy={busy}
          onConfirm={() => void send('withdraw')}
          onCancel={() => setConfirming(null)}
          testId="my-erasure-withdraw-confirm"
        />
      )}
      {refusal && (
        <p role="alert" data-my-erasure-refusal="" className="m-0 text-xs text-[var(--ui-danger-ink)]">
          {refusal}
        </p>
      )}
    </div>
  )
}
