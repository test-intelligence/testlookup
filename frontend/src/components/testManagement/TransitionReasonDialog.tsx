/**
 * The reason (or the review notes) a governed lifecycle change records: a
 * deprecation, a delete, an automation retirement, an unlink, a review
 * decision, a defect rejection.
 *
 * It renders IN THE PAGE (W27b PAN-05), where the control that asked for it
 * lives (under its table row, inside its card, under the page header): an
 * `InlineFormSection` named by its title (heading id `${id}-title`), not a
 * modal over the page. The description is a paragraph in the body
 * (`${id}-description`) that the field's `aria-describedby` still names, with
 * the character count. No X (the dialog had none); Cancel, and Escape while
 * focus is inside, close it unless it is busy; focus goes back to the control
 * that opened it. The field keeps `autoFocus`, `required` and its 500-character
 * cap.
 */
import { useId, useState } from 'react'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'

interface TransitionReasonDialogProps {
  title: string
  description: string
  confirmLabel: string
  onCancel: () => void
  onConfirm: (reason: string) => Promise<void> | void
  busy?: boolean
  fieldLabel?: string
  placeholder?: string
  /** The host's form slot: one open form per section, and the discard question before another replaces it. */
  slot?: InlineSlot
}

export default function TransitionReasonDialog({
  title,
  description,
  confirmLabel,
  onCancel,
  onConfirm,
  busy = false,
  fieldLabel = 'Reason',
  placeholder = 'Explain why this lifecycle change is needed',
  slot,
}: TransitionReasonDialogProps) {
  const [reason, setReason] = useState('')
  const trimmedReason = reason.trim()
  const id = useId()
  const titleId = `${id}-title`
  const descriptionId = `${id}-description`
  const fieldId = `${id}-reason`
  const countId = `${id}-count`

  return (
    <InlineFormSection
      title={title}
      titleId={titleId}
      onClose={onCancel}
      canClose={!busy}
      showClose={false}
      busy={busy}
      slot={slot}
    >
      {/* A long test name breaks rather than widen a narrow host. */}
      <p id={descriptionId} className="m-0 text-sm text-[var(--color-text-muted)] [overflow-wrap:anywhere]">{description}</p>
      <label htmlFor={fieldId} className="mt-4 block text-xs font-medium text-[var(--color-text-secondary)]">
        {fieldLabel} <span aria-hidden="true">*</span>
      </label>
      <textarea
        id={fieldId}
        autoFocus
        required
        maxLength={500}
        rows={4}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        aria-describedby={`${descriptionId} ${countId}`}
        className="input mt-1 w-full resize-none"
        placeholder={placeholder}
      />
      <div id={countId} aria-live="polite" className="mt-1 text-right text-[11px] text-[var(--color-text-muted)]">
        {reason.length}/500
      </div>
      {/* The buttons wrap in a narrow host (the evidence-gap card is ~316 px wide at 1280). */}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className="btn-primary"
          onClick={() => void onConfirm(trimmedReason)}
          disabled={busy || !trimmedReason}
        >
          {busy ? 'Saving…' : confirmLabel}
        </button>
      </div>
    </InlineFormSection>
  )
}
