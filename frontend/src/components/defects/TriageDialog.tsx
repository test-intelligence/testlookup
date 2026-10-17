/**
 * The frame of a defect triage form (W06 DEF-01), rendered IN THE PAGE (W27
 * PAN-04): the defects table puts it in a full-width row under the defect's
 * own row (`DefectRowActions`' `DefectActionForm` in an `InlineRowSection`).
 * It keeps the dialog's name (its heading), its test id and its keyboard
 * behaviour minus the trap: focus moves to the first field when it opens,
 * Escape inside closes it unless busy, and focus goes back to the opener.
 */
import { useId, type ReactNode } from 'react'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'

export default function TriageDialog({
  title,
  busy,
  onClose,
  children,
  testId,
  slot,
}: {
  title: string
  busy: boolean
  onClose: () => void
  children: ReactNode
  testId?: string
  /** The table's slot: one triage form open per table, and a typed form asks before it is replaced. */
  slot?: InlineSlot
}) {
  const id = useId()
  return (
    <InlineFormSection
      title={title}
      titleId={`${id}-title`}
      onClose={onClose}
      canClose={!busy}
      showClose={false}
      busy={busy}
      testId={testId}
      slot={slot}
      className="text-left"
    >
      <div className="max-w-md">{children}</div>
    </InlineFormSection>
  )
}

/** The inline 409 message (`[data-defect-conflict]`). */
export function TriageConflict({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p
      role="alert"
      data-defect-conflict=""
      className="mt-3 rounded-lg border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] px-3 py-2 text-xs text-[var(--status-failed)]"
    >
      {message}
    </p>
  )
}
