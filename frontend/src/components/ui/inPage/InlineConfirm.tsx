/**
 * A confirmation asked where its action lives (W27 PAN-01): the in-page
 * replacement for the browser's native confirmation prompt.
 *
 * The trigger that opened the strip is the FIRST click and sends nothing; the
 * strip's confirm button is the second click and is the ONLY thing that sends
 * the request. Focus moves to Cancel when the strip appears, Escape inside it
 * is Cancel, and focus goes back to the trigger when it closes.
 *
 * `message` is React nodes: a name is `{name}` inside `<strong>`, never a
 * string handed to an HTML sink, so a name like `<img src=x onerror=…>` is
 * shown as text.
 */
import { useId, type ReactNode } from 'react'
import LoadingSpinner from '../LoadingSpinner'
import { useInPageFocus } from './useInPageFocus'

export interface InlineConfirmProps {
  /** React nodes; a name is {name} inside <strong>, never a string handed to an HTML sink. */
  message: ReactNode
  /** 'Delete', 'Remove', 'Make active' */
  confirmLabel: string
  /** Default 'Cancel'. */
  cancelLabel?: string
  /** 'danger' for an irreversible action (the confirm button uses the --status-failed tokens). */
  tone?: 'danger' | 'default'
  /** Both buttons disabled; the confirm button shows LoadingSpinner size="sm". */
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
  testId?: string
}

const CONFIRM_DEFAULT = 'btn-primary inline-flex items-center gap-1.5 text-xs'
const CONFIRM_DANGER =
  'inline-flex items-center gap-1.5 rounded-full border border-[var(--status-failed-bd)] ' +
  'bg-[var(--status-failed-bg)] px-4 py-2 text-xs font-semibold text-[var(--status-failed)] ' +
  'transition-colors hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50'

export function InlineConfirm({
  message,
  confirmLabel,
  cancelLabel = 'Cancel',
  tone = 'default',
  busy = false,
  onConfirm,
  onCancel,
  testId,
}: InlineConfirmProps) {
  const messageId = useId()
  const ref = useInPageFocus<HTMLDivElement>({ onEscape: onCancel, canEscape: !busy })
  return (
    <div
      ref={ref}
      role="group"
      aria-labelledby={messageId}
      data-inline-confirm=""
      data-testid={testId}
      className="flex flex-wrap items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-[12.5px]"
    >
      <p id={messageId} className="m-0 min-w-0 flex-1">
        {message}
      </p>
      <button type="button" className="btn-secondary text-xs" onClick={onCancel} disabled={busy}>
        {cancelLabel}
      </button>
      <button
        type="button"
        data-inline-confirm-action=""
        className={tone === 'danger' ? CONFIRM_DANGER : CONFIRM_DEFAULT}
        onClick={onConfirm}
        disabled={busy}
      >
        {busy && <LoadingSpinner size="sm" />}
        {confirmLabel}
      </button>
    </div>
  )
}

export default InlineConfirm
