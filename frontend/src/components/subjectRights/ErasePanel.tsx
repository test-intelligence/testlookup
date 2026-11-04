/**
 * ErasePanel — "Erase this person", in the Users row's Personal data section (W31 STRAT-12, contract §2.3, §4.1).
 *
 * Preview first (`GET …/erasure-preview`, read only): the pseudonym sentence, the blockers (each sentence; the button
 * stays disabled), "What changes" and "Kept (Art. 17(3))" side by side (`grid grid-cols-2 gap-4`, ≈ 331 px each at
 * 1280 under DejaVu), the open items a lead may reassign, then the typed confirm — an input (`w-64`) + "Erase
 * permanently", enabled ONLY on the exact username with no blocker. Erase → `POST …/erasure` → the job's phase line
 * polled by `useSubjectJob` ("Erasure in progress — signed out and disabled. Step 2 of 4: …") → `erase_done`. A job
 * at `lockout` reads "Erasure in progress", never "Erased" (E-11b). An unfinished erasure offers "Resume erasure"
 * (the same confirm, E-8).
 *
 * In the page: no modal, no `role="dialog"`, no portal, no native confirm dialog. Names, sentences and counts are React text.
 */
import { useEffect, useId, useRef, useState } from 'react'
import { ShieldAlert } from 'lucide-react'

import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useErasurePreview, useSubjectJob } from '@/hooks/useSubjectRights'
import { subjectRightsErrorCode, subjectRightsErrorMessage, subjectRightsService } from '@/services/subjectRightsService'
import { isActiveJob, type ErasureLocationCount, type ErasurePreview, type SubjectJob } from '@/types/subjectRights'
import {
  canErase,
  CHANGES_TITLE,
  confirmLabel,
  CONFIRM_MISMATCH,
  effectiveBlockers,
  ERASE_BUTTON,
  ERASE_FAILED,
  ERASE_TITLE,
  eraseBody,
  erasureView,
  KEPT_TITLE,
  LOAD_PREVIEW_FAILED,
  locationLine,
  NOT_COUNTED,
  OPEN_ITEMS_TITLE,
  previewSummary,
  RESUME_BUTTON,
  type UserRowLike,
} from './erasureModel'

/** One list of the preview: label + count right-aligned; an uncounted row and a kept row's reason on a second line. */
function LocationList({ id, title, items }: { id: string; title: string; items: ErasureLocationCount[] }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5" data-erase-list={id}>
      <h4 className="m-0 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">{title}</h4>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {items.map((item) => {
          const line = locationLine(item)
          const counted = line.count !== NOT_COUNTED
          return (
            <li key={line.key} className="flex flex-col" data-erase-location={line.key}>
              <div className="flex items-baseline justify-between gap-3 text-sm text-[var(--color-text)]">
                <span className="min-w-0">{line.label}</span>
                {counted && <span className="shrink-0 tabular-nums">{line.count}</span>}
              </div>
              {!counted && <span className="text-xs italic text-[var(--color-text-muted)]">{NOT_COUNTED}</span>}
              {line.reason && <span className="text-xs text-[var(--color-text-muted)]">{line.reason}</span>}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

interface ConfirmProps {
  preview: ErasurePreview | undefined
  typed: string
  onType: (value: string) => void
  busy: boolean
  label: string
  mismatch: string | null
  onSubmit: () => void
}

/** "Type <username> to confirm" over the input and the danger button; `confirm_mismatch` sits under the input. */
function ConfirmRow({ preview, typed, onType, busy, label, mismatch, onSubmit }: ConfirmProps) {
  const inputId = useId()
  const enabled = canErase(preview, typed) && !busy
  return (
    <div className="flex flex-col gap-1" data-erase-confirm="">
      {preview && (
        <label htmlFor={inputId} className="text-xs text-[var(--color-text-secondary)]">
          {confirmLabel(preview.username)}
        </label>
      )}
      <div className="flex items-center gap-2">
        <input
          id={inputId}
          type="text"
          className="input w-64"
          value={typed}
          autoComplete="off"
          spellCheck={false}
          disabled={!preview || busy}
          onChange={(event) => onType(event.target.value)}
        />
        <button type="button" className="btn-danger inline-flex items-center gap-1.5" disabled={!enabled} onClick={onSubmit}>
          {busy ? <LoadingSpinner size="sm" /> : <ShieldAlert className="h-4 w-4" aria-hidden="true" />}
          {label}
        </button>
      </div>
      {mismatch && (
        <p className="m-0 text-xs text-[var(--ui-danger-ink)]" data-erase-mismatch="">
          {mismatch}
        </p>
      )}
    </div>
  )
}

interface Props {
  user: UserRowLike
  /** The person's newest erasure job (the section's job list), or null. */
  latestErasure: SubjectJob | null
  /** Re-read the person's job list (after the POST, and when the erasure reaches `done`). */
  onChanged: () => void
  /** Re-read the users list: after the lock-out the row reads Inactive; at `done` it reads Erased. */
  onUsersChanged: () => void
  /**
   * The person's newest export as `<id>:<status>` (the section polls its jobs), or null. A change re-reads the
   * preview: an export started beside the panel blocks the erasure, and one that finishes no longer does.
   */
  exportState?: string | null
}

export default function ErasePanel({ user, latestErasure, onChanged, onUsersChanged, exportState = null }: Props) {
  const [typed, setTyped] = useState('')
  const [erasing, setErasing] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [mismatch, setMismatch] = useState<string | null>(null)
  // The job the POST answered (phase `lockout`), or a resumed one.
  const [started, setStarted] = useState<SubjectJob | null>(null)
  const { data: preview, error: previewError, mutate: refreshPreview } = useErasurePreview(user.id, true)

  // The preview's export blocker follows the person's export: re-read it when that export starts or finishes
  // (local demo, 2026-11-04: a blocker read while an export ran stayed after the export was ready).
  const seenExport = useRef(exportState)
  useEffect(() => {
    if (seenExport.current === exportState) return
    seenExport.current = exportState
    void refreshPreview()
  }, [exportState, refreshPreview])

  // An unfinished erasure the preview or the job list names (a reload mid-erasure resumes watching it).
  const pending = preview?.erasure_job ?? (latestErasure && isActiveJob(latestErasure) ? latestErasure : null)
  const watchedId = started?.id ?? (pending && isActiveJob(pending) ? pending.id : null)
  const { data: watched } = useSubjectJob(watchedId)
  const polled = watched && watched.id === watchedId ? watched : null
  const job = polled ?? started ?? pending ?? null
  const view = erasureView(job, preview?.pseudonym ?? user.username)

  // `done` re-reads the job list and the users list ONCE per job (the row then renders "Erased user").
  const doneKey = view.kind === 'done' && job ? job.id : null
  const announced = useRef<string | null>(null)
  useEffect(() => {
    if (!doneKey || announced.current === doneKey) return
    announced.current = doneKey
    onChanged()
    onUsersChanged()
  }, [doneKey, onChanged, onUsersChanged])

  async function erase() {
    if (!preview || erasing || !canErase(preview, typed)) return
    setErasing(true)
    setRefusal(null)
    setMismatch(null)
    try {
      const created = await subjectRightsService.eraseUser(user.id, typed)
      setStarted(created)
      setTyped('')
      onChanged()
      onUsersChanged()
    } catch (err: unknown) {
      const code = await subjectRightsErrorCode(err)
      const message = await subjectRightsErrorMessage(err, ERASE_FAILED)
      if (code === 'confirm_mismatch') setMismatch(message || CONFIRM_MISMATCH)
      else setRefusal(message)
      void refreshPreview()
    } finally {
      setErasing(false)
    }
  }

  const blockers = effectiveBlockers(preview)
  const state =
    view.kind !== 'none' ? view.kind : previewError ? 'error' : !preview ? 'loading' : blockers.length ? 'blocked' : 'ready'
  const confirmRow = (label: string) => (
    <ConfirmRow
      preview={preview}
      typed={typed}
      onType={setTyped}
      busy={erasing}
      label={label}
      mismatch={mismatch}
      onSubmit={erase}
    />
  )

  return (
    <div className="flex flex-col gap-3" data-erase-panel="" data-erase-state={state}>
      <h3 className="m-0 text-sm font-semibold text-[var(--color-text)]">{ERASE_TITLE}</h3>
      {view.kind !== 'none' ? (
        <>
          <div data-erase-phase="" className="flex items-center gap-2 text-sm text-[var(--color-text)]">
            {view.kind === 'in_progress' && <LoadingSpinner size="sm" />}
            <span>{view.line}</span>
          </div>
          {view.detail && (
            <p className="m-0 text-xs text-[var(--ui-danger-ink)]" data-erase-incomplete="">
              {view.detail}
            </p>
          )}
          {view.resumable && confirmRow(RESUME_BUTTON)}
        </>
      ) : previewError ? (
        <p className="m-0 text-sm text-[var(--ui-danger-ink)]">{LOAD_PREVIEW_FAILED}</p>
      ) : !preview ? (
        <LoadingSpinner size="sm" />
      ) : (
        <>
          <p className="m-0 text-sm text-[var(--color-text-secondary)]">{eraseBody(preview.pseudonym)}</p>
          {blockers.length > 0 && (
            <ul className="m-0 flex list-none flex-col gap-1 p-0" data-erase-blockers="">
              {blockers.map((blocker) => (
                <li key={blocker.code} className="text-sm text-[var(--ui-danger-ink)]">
                  {blocker.message}
                </li>
              ))}
            </ul>
          )}
          <p className="m-0 text-xs text-[var(--color-text-muted)]" data-erase-summary="">
            {previewSummary(preview)}
          </p>
          <div className="grid grid-cols-2 gap-4">
            <LocationList id="changes" title={CHANGES_TITLE} items={preview.changes ?? []} />
            <LocationList id="kept" title={KEPT_TITLE} items={preview.kept ?? []} />
          </div>
          {(preview.open_items?.length ?? 0) > 0 && (
            <LocationList id="open" title={OPEN_ITEMS_TITLE} items={preview.open_items ?? []} />
          )}
          {confirmRow(ERASE_BUTTON)}
        </>
      )}
      {refusal && (
        <p role="alert" className="m-0 text-xs text-[var(--ui-danger-ink)]" data-erase-refusal="">
          {refusal}
        </p>
      )}
    </div>
  )
}
