/**
 * The reason a phase is completed against its gate, or skipped (REL-10).
 *
 * With `release_phase_gate_enforcement` on, the server answers 409 to
 * completing a phase whose gate is not GO, and to skipping one, unless a
 * reason travels with the request. The release page opens this form on that
 * 409, quoting the server's own sentence (which names what is failing), takes
 * the reason, and resends with the exact field name. The reason is recorded in
 * the audit log and the activity feed.
 *
 * The page tells the form nothing about the flag: both transitions are
 * 409-driven, so a project with enforcement off never sees it.
 *
 * It renders IN THE PAGE (W27 PAN-02), right under the phase row it is about:
 * an `InlineFormSection`, not a modal over the page. Its name, its heading id,
 * `data-phase-reason-dialog` and its close label are the dialog's, kept; Cancel,
 * the X and Escape inside it close it (not mid-submit), as before.
 */
import { useState } from 'react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'

/** The server's floor (`PhaseUpdate.gate_override_reason` / `skip_reason`, `min_length=3`). */
export const PHASE_REASON_MIN = 3
/** The server's ceiling (`max_length=1000`). */
export const PHASE_REASON_MAX = 1000

export interface PhaseReasonDialogProps {
  phaseName: string
  action: 'complete' | 'skip'
  /** The 409's `detail`, verbatim. */
  serverMessage: string
  busy?: boolean
  /** Called with the TRIMMED reason. */
  onSubmit: (reason: string) => void
  onCancel: () => void
  /** The release's form slot: one open form at a time in a release's detail. */
  slot?: InlineSlot
}

export default function PhaseReasonDialog({
  phaseName, action, serverMessage, busy = false, onSubmit, onCancel, slot,
}: PhaseReasonDialogProps) {
  const [reason, setReason] = useState('')
  const trimmed = reason.trim()
  const canSubmit = trimmed.length >= PHASE_REASON_MIN && !busy
  const title = action === 'complete' ? `Complete "${phaseName}" anyway?` : `Skip "${phaseName}"?`
  const submitLabel = action === 'complete' ? 'Complete with override' : 'Skip phase'

  function submit() {
    if (!canSubmit) return
    onSubmit(trimmed)
  }

  return (
    <InlineFormSection
      title={title}
      titleId="phase-reason-title"
      closeLabel="Close phase reason dialog"
      onClose={onCancel}
      canClose={!busy}
      slot={slot}
      dataAttributes={{ 'data-phase-reason-dialog': action }}
      footer={(
        <>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="px-4 py-2 text-sm bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 disabled:cursor-not-allowed text-[var(--color-btn-primary-text)] rounded-lg font-medium flex items-center gap-2"
          >
            {busy && <LoadingSpinner size="sm" />}
            {submitLabel}
          </button>
        </>
      )}
    >
      <div className="max-w-2xl">
        <blockquote
          data-phase-gate-detail=""
          className="mb-4 border-l-2 border-[var(--status-broken)] bg-[var(--color-bg-secondary)] px-3 py-2 text-[12.5px] text-[var(--color-text-secondary)] whitespace-pre-wrap break-words"
        >
          {serverMessage}
        </blockquote>
        <label htmlFor="phase-reason-text" className="block text-xs text-[var(--color-text-muted)] mb-1">
          Reason (recorded in the audit log)
        </label>
        <textarea
          id="phase-reason-text"
          value={reason}
          onChange={e => setReason(e.target.value)}
          rows={3}
          maxLength={PHASE_REASON_MAX}
          aria-describedby="phase-reason-hint"
          className="input w-full resize-y"
          autoFocus
        />
        <p id="phase-reason-hint" className="mt-1 text-[11px] text-[var(--color-text-faint)]">At least 3 characters.</p>
      </div>
    </InlineFormSection>
  )
}
