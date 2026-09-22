/**
 * A panel that slides in from the edge (VIZ-109; drill-down details, VIZ-602).
 *
 * Two shapes, deliberately separate components under one API:
 *
 *  - NON-MODAL (default): the page behind stays usable — a user reads a
 *    chart and the details side by side. It is a labelled `<aside>` landmark,
 *    not a dialog: nothing is trapped. Focus moves into it when it opens,
 *    Escape (while focus is inside) closes it, and focus goes back to the
 *    control that opened it — unless the user has since moved focus
 *    elsewhere on purpose, which is theirs to keep.
 *
 *    A page the user can keep tabbing through must never hide the focused
 *    control behind the panel (WCAG 2.4.11 Focus Not Obscured). So it never
 *    OVERLAYS the page: while open it RESERVES its width — `reserveSpaceIn`
 *    (default <body>) gets an inline padding of the panel's width on the
 *    panel's side, the page reflows beside it, and the exact previous inline
 *    padding comes back on close. A caller whose layout scrolls in its own
 *    container passes that container. Below the `sm` breakpoint (640 px)
 *    there is no width to share: the panel is rendered in the MODAL shape
 *    instead, whatever `modal` says.
 *  - MODAL (`modal`, or any non-modal panel on a narrow screen): a `fixed
 *    inset-0` backdrop (`role="presentation"`) with the panel as
 *    `role="dialog" aria-modal="true"`, on the shared `useModalFocus` hook —
 *    the same trap, Escape and focus return as every other modal (and the
 *    `frontend.modal-dialog-role` gate's shape). The page behind is
 *    scroll-locked while it is open, with the scrollbar's width added back as
 *    padding so nothing shifts; both are restored exactly on close.
 *
 * Both render only while `open`; the caller mounts it conditionally or
 * passes `open`. Named by its heading via `aria-labelledby`.
 */
import { X } from 'lucide-react'
import { useId, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { useModalFocus } from '@/hooks/useModalFocus'

export interface SidePanelProps {
  open: boolean
  onClose: () => void
  /** Heading text; also the panel's accessible name. */
  title: string
  children: ReactNode
  /** Trap focus and block the page behind. Default false (but see the narrow-screen rule). */
  modal?: boolean
  side?: 'left' | 'right'
  /** Panel width in px (capped at the viewport). Default 420. */
  width?: number
  /**
   * Non-modal only: the element that makes room for the panel while it is
   * open (it gets `width` px of extra padding on the panel's side). Default
   * <body>. Pass the page's own scroll container when the page scrolls in one.
   */
  reserveSpaceIn?: RefObject<HTMLElement | null>
  /** Pinned below the scrolling body (actions). */
  footer?: ReactNode
  /** Accessible name of the close button. Default "Close panel". */
  closeLabel?: string
  className?: string
}

/** At and above this width a non-modal panel sits beside the page; below it, it is modal. */
export const SIDE_PANEL_WIDE_QUERY = '(min-width: 640px)'

type PanelBodyProps = Omit<SidePanelProps, 'open' | 'modal'> & { titleId: string }

const CLOSE_BUTTON =
  'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-ring)]'

function subscribeWide(onChange: () => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {}
  const query = window.matchMedia(SIDE_PANEL_WIDE_QUERY)
  query.addEventListener?.('change', onChange)
  return () => query.removeEventListener?.('change', onChange)
}

/** No matchMedia (jsdom, SSR): assume a wide screen, the pre-breakpoint behaviour. */
function isWide(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return true
  return window.matchMedia(SIDE_PANEL_WIDE_QUERY).matches
}

// ── page scroll lock for the modal shape (counted: panels can nest) ────────
let scrollLocks = 0
let savedBodyStyle: { attribute: string | null; overflow: string; paddingRight: string } | null = null

function lockPageScroll(): () => void {
  const body = document.body
  if (scrollLocks++ === 0) {
    savedBodyStyle = {
      attribute: body.getAttribute('style'),
      overflow: body.style.overflow,
      paddingRight: body.style.paddingRight,
    }
    // Hiding the scrollbar would widen the page by its width; pad it back —
    // unless the page already reserves the gutter itself.
    const scrollbar = window.innerWidth - document.documentElement.clientWidth
    const gutter = getComputedStyle(document.documentElement).getPropertyValue('scrollbar-gutter')
    // (Bounded: an environment without layout reports clientWidth 0.)
    if (scrollbar > 0 && scrollbar < 100 && !gutter.includes('stable')) {
      const padding = parseFloat(getComputedStyle(body).paddingRight) || 0
      body.style.paddingRight = `${padding + scrollbar}px`
    }
    body.style.overflow = 'hidden'
  }
  let released = false
  return () => {
    if (released) return
    released = true
    if (--scrollLocks > 0 || !savedBodyStyle) return
    body.style.overflow = savedBodyStyle.overflow
    body.style.paddingRight = savedBodyStyle.paddingRight
    // "Exactly": no `style=""` left behind on a body that had no attribute.
    if (savedBodyStyle.attribute === null && body.getAttribute('style') === '') body.removeAttribute('style')
    savedBodyStyle = null
  }
}

/** Pad `target` by `width` px on `side`; the returned cleanup restores its exact inline style. */
function reserveInlineSpace(target: HTMLElement, side: 'left' | 'right', width: number): () => void {
  const property = side === 'right' ? 'paddingRight' : 'paddingLeft'
  const hadAttribute = target.hasAttribute('style')
  const previous = target.style[property]
  const base = previous || getComputedStyle(target)[property] || '0px'
  target.style[property] = `calc(${base} + ${width}px)`
  return () => {
    target.style[property] = previous
    if (!hadAttribute && target.getAttribute('style') === '') target.removeAttribute('style')
  }
}

function PanelContents({ title, titleId, onClose, children, footer, closeLabel = 'Close panel' }: PanelBodyProps) {
  return (
    <>
      <header className="flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-3">
        <h2 id={titleId} className="min-w-0 flex-1 truncate text-base font-semibold" title={title}>
          {title}
        </h2>
        <button type="button" onClick={onClose} aria-label={closeLabel} className={CLOSE_BUTTON}>
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">{children}</div>
      {footer && <footer className="border-t border-[var(--color-border)] px-4 py-3">{footer}</footer>}
    </>
  )
}

const panelFrame = (side: 'left' | 'right') =>
  `flex h-full max-w-full flex-col bg-[var(--color-bg-card)] text-[var(--color-text)] shadow-2xl ${
    side === 'right' ? 'border-l' : 'border-r'
  } border-[var(--color-border)]`

function NonModalSidePanel({ side = 'right', width = 420, className = '', reserveSpaceIn, ...rest }: PanelBodyProps) {
  const panelRef = useRef<HTMLElement>(null)
  // Captured in the opening render, before anything inside takes focus.
  const [opener] = useState<HTMLElement | null>(() =>
    typeof document !== 'undefined' && document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  )

  // Make room instead of covering the page (2.4.11). Before the focus move
  // below, so focus never lands on a page that is still reflowing.
  useLayoutEffect(() => {
    const target = reserveSpaceIn ? reserveSpaceIn.current : document.body
    return target ? reserveInlineSpace(target, side, width) : undefined
  }, [reserveSpaceIn, side, width])

  useLayoutEffect(() => {
    const panel = panelRef.current
    if (panel && !panel.contains(document.activeElement)) {
      panel.querySelector<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')?.focus()
    }
    // A LAYOUT-effect cleanup runs while the panel is still in the document,
    // so "was focus inside it?" is still answerable; a passive cleanup runs
    // after removal, when focus has already fallen to <body>.
    return () => {
      const active = document.activeElement
      const focusWasOurs = !active || active === document.body || (panel?.contains(active) ?? false)
      if (focusWasOurs && opener?.isConnected) opener.focus()
    }
  }, [opener])

  return (
    <aside
      ref={panelRef}
      aria-labelledby={rest.titleId}
      data-side-panel="non-modal"
      onKeyDown={(event) => {
        if (event.key !== 'Escape') return
        event.stopPropagation()
        rest.onClose()
      }}
      className={`fixed inset-y-0 z-40 ${side === 'right' ? 'right-0' : 'left-0'} ${panelFrame(side)} ${className}`}
      style={{ width }}
    >
      <PanelContents {...rest} />
    </aside>
  )
}

// `reserveSpaceIn` is non-modal only: taken out so it is not spread anywhere.
function ModalSidePanel({ side = 'right', width = 420, className = '', reserveSpaceIn: _reserve, ...rest }: PanelBodyProps) {
  const dialogRef = useModalFocus<HTMLDivElement>({ onClose: rest.onClose })
  useLayoutEffect(() => lockPageScroll(), [])
  return createPortal(
    <div
      role="presentation"
      className={`fixed inset-0 z-[70] flex bg-black/50 ${side === 'right' ? 'justify-end' : 'justify-start'}`}
      onMouseDown={(event) => {
        // Only a press on the backdrop itself; a drag that starts in the panel
        // and ends outside must not close it.
        if (event.target === event.currentTarget) rest.onClose()
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={rest.titleId}
        data-side-panel="modal"
        className={`${panelFrame(side)} ${className}`}
        style={{ width }}
      >
        <PanelContents {...rest} />
      </div>
    </div>,
    document.body,
  )
}

export default function SidePanel({ open, modal = false, ...props }: SidePanelProps) {
  const titleId = useId()
  const wide = useSyncExternalStore(subscribeWide, isWide, () => true)
  if (!open) return null
  return modal || !wide ? (
    <ModalSidePanel {...props} titleId={titleId} />
  ) : (
    <NonModalSidePanel {...props} titleId={titleId} />
  )
}
