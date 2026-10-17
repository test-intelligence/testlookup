/**
 * A form (or a read-only detail) opened where its action lives (W27 PAN-01):
 * the in-page replacement for a modal form, rendered in the page's own flow.
 *
 * It keeps what the modal gave its user, minus the trap: focus moves to the
 * first field when it opens, Escape while focus is inside closes it (not
 * while `canClose` is false, mid-submit), and focus goes back to the control
 * that opened it. It keeps the modal's accessible name too: a `<section>`
 * (role region) or an `<aside>` (role complementary, for a former non-modal
 * side panel) labelled by its heading, so a test that found the dialog by
 * name finds the section by the same name — and pass the old heading id as
 * `titleId` so nothing that read it by id breaks.
 *
 * With a `slot` (useInlineSlot) it reports typed input (an input / change
 * event from a field inside it) and, while the slot asks, shows the discard
 * question at the top of its body.
 *
 * No width of its own: it is as wide as the place it opens in.
 */
import { X } from 'lucide-react'
import { useEffect, useId, type FormEvent, type ReactNode, type SyntheticEvent } from 'react'
import { InlineConfirm } from './InlineConfirm'
import type { InlineSlot } from './useInlineSlot'
import { useInPageFocus } from './useInPageFocus'

export interface InlineFormSectionProps {
  title: ReactNode
  /** KEEP the old dialog's heading id when it had one (release-form-title, link-test-run-title, …). */
  titleId?: string
  /** When the old dialog was named by aria-label and the heading text differs ('AI decision trail'). */
  ariaLabel?: string
  /** Default 2. */
  headingLevel?: 2 | 3
  description?: ReactNode
  /** The old Cancel / X / Escape handler, unchanged. */
  onClose: () => void
  /** false mid-submit (the old canClose): X disabled, Escape swallowed. */
  canClose?: boolean
  /** KEEP the old X's label ('Close release dialog', 'Close sync releases dialog', …); default 'Close'. */
  closeLabel?: string
  /** Default true. */
  showClose?: boolean
  /** The old Cancel / Save row, verbatim; absent for a read-only section. */
  footer?: ReactNode
  children: ReactNode
  /** One-form-per-slot + dirty + the discard question. */
  slot?: InlineSlot
  /** Default true (useInPageFocus). */
  autoFocus?: boolean
  /** Given: the section body is a <form onSubmit> (the old <form> dialogs: the claim correction). */
  onSubmit?: (event: FormEvent<HTMLFormElement>) => void
  /** 'section' (default, role region) replaces a modal dialog; 'aside' (role complementary) replaces a NON-modal
   *  side panel, so its tests' `complementary` queries keep working (Suspects, Failure signatures). */
  as?: 'section' | 'aside'
  /** aria-busy */
  busy?: boolean
  /** KEEP the old data-testid (assign-defect-dialog, link-jira-dialog, defect-status-dialog). */
  testId?: string
  /** KEEP the old data-* (data-phase-reason-dialog, data-share-dialog). */
  dataAttributes?: { [data: `data-${string}`]: string }
  className?: string
}

const CLOSE_BUTTON =
  'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--color-text-secondary)] ' +
  'hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)] ' +
  'disabled:opacity-40 disabled:pointer-events-none'

function isField(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLSelectElement ||
    target instanceof HTMLTextAreaElement
  )
}

export function InlineFormSection({
  title,
  titleId,
  ariaLabel,
  headingLevel = 2,
  description,
  onClose,
  canClose = true,
  closeLabel = 'Close',
  showClose = true,
  footer,
  children,
  slot,
  autoFocus = true,
  onSubmit,
  as = 'section',
  busy,
  testId,
  dataAttributes,
  className,
}: InlineFormSectionProps) {
  const generatedId = useId()
  const headingId = titleId ?? generatedId
  const ref = useInPageFocus<HTMLElement>({ onEscape: onClose, canEscape: canClose, autoFocus })

  // A form that goes away takes its typed input with it: nothing left to ask about.
  const setDirty = slot?.setDirty
  useEffect(() => () => setDirty?.(false), [setDirty])

  const markDirty = (event: SyntheticEvent) => {
    if (slot && !slot.dirty && isField(event.target)) slot.setDirty(true)
  }

  const Tag = as
  const Heading = headingLevel === 3 ? 'h3' : 'h2'
  const body = (
    <>
      {slot?.asking && (
        <div className="mt-3">
          <InlineConfirm
            message={<>Discard your changes to &quot;<strong>{title}</strong>&quot;?</>}
            confirmLabel="Discard changes"
            cancelLabel="Keep editing"
            tone="danger"
            onConfirm={() => slot.answer(true)}
            onCancel={() => slot.answer(false)}
          />
        </div>
      )}
      <div className="mt-3">{children}</div>
      {footer && <div className="mt-4 flex justify-end gap-3">{footer}</div>}
    </>
  )

  return (
    <Tag
      ref={ref}
      aria-labelledby={ariaLabel ? undefined : headingId}
      aria-label={ariaLabel}
      aria-busy={busy || undefined}
      data-in-page="form"
      data-testid={testId}
      {...dataAttributes}
      onInput={markDirty}
      onChange={markDirty}
      className={`rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4${className ? ` ${className}` : ''}`}
    >
      <div className="flex items-start justify-between gap-3">
        <Heading id={headingId} className="m-0 min-w-0 text-base font-semibold text-[var(--color-text)]">
          {title}
        </Heading>
        {showClose && (
          <button type="button" aria-label={closeLabel} onClick={onClose} disabled={!canClose} className={CLOSE_BUTTON}>
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        )}
      </div>
      {description && <div className="mt-1 text-sm text-[var(--color-text-muted)]">{description}</div>}
      {onSubmit ? <form onSubmit={onSubmit}>{body}</form> : body}
    </Tag>
  )
}

export default InlineFormSection
