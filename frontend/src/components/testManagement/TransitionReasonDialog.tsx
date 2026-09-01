import { useEffect, useId, useRef, useState } from 'react'

interface TransitionReasonDialogProps {
  title: string
  description: string
  confirmLabel: string
  onCancel: () => void
  onConfirm: (reason: string) => Promise<void> | void
  busy?: boolean
  fieldLabel?: string
  placeholder?: string
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
}: TransitionReasonDialogProps) {
  const [reason, setReason] = useState('')
  const trimmedReason = reason.trim()
  const id = useId()
  const titleId = `${id}-title`
  const descriptionId = `${id}-description`
  const fieldId = `${id}-reason`
  const countId = `${id}-count`
  const returnFocusRef = useRef<HTMLElement | null>(
    typeof document === 'undefined' ? null : document.activeElement as HTMLElement | null,
  )
  const panelRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const returnFocus = returnFocusRef.current
    return () => returnFocus?.focus()
  }, [])

  useEffect(() => {
    function handleEscape(event: KeyboardEvent) {
      if (event.key === 'Escape' && !busy) {
        onCancel()
        return
      }
      if (event.key !== 'Tab') return

      const panel = panelRef.current
      if (!panel) return
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ))
      if (focusable.length === 0) {
        event.preventDefault()
        panel.focus()
        return
      }

      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || !panel.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || !panel.contains(active))) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [busy, onCancel])

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      aria-busy={busy}
      className="fixed inset-0 z-[70] flex items-center justify-center bg-[var(--color-bg)]/70 p-4"
    >
      <div ref={panelRef} tabIndex={-1} className="w-full max-w-md rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] p-5 shadow-2xl">
        <h2 id={titleId} className="text-base font-semibold text-[var(--color-text)]">
          {title}
        </h2>
        <p id={descriptionId} className="mt-1 text-sm text-[var(--color-text-muted)]">{description}</p>
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
        <div className="mt-4 flex justify-end gap-2">
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
      </div>
    </div>
  )
}
