/**
 * The frame of a defect triage dialog (W06 DEF-01): rendered into
 * `document.body` (the trigger sits in a right-aligned table cell, whose
 * alignment and stacking would otherwise leak into the dialog), above the row
 * menu, with the shared modal keyboard behaviour (`useModalFocus`: Escape
 * closes unless busy).
 */
import { useId, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useModalFocus } from '@/hooks/useModalFocus'

export default function TriageDialog({
  title,
  busy,
  onClose,
  children,
  testId,
}: {
  title: string
  busy: boolean
  onClose: () => void
  children: ReactNode
  testId?: string
}) {
  const id = useId()
  const titleId = `${id}-title`
  const dialogRef = useModalFocus({ onClose, canClose: !busy })
  return createPortal(
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-busy={busy || undefined}
      data-testid={testId}
      className="fixed inset-0 z-[70] flex items-center justify-center bg-[var(--color-bg)]/70 p-4 text-left"
    >
      <div className="w-full max-w-md rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] p-5 shadow-2xl">
        <h2 id={titleId} className="text-base font-semibold text-[var(--color-text)]">
          {title}
        </h2>
        {children}
      </div>
    </div>,
    document.body,
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
