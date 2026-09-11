/**
 * Keyboard behaviour every modal dialog owes its user (audit M20):
 *
 *  - focus moves INTO the dialog when it opens (unless something inside,
 *    e.g. an `autoFocus` field, already took it);
 *  - Tab and Shift+Tab cycle inside the dialog instead of walking out into
 *    the page behind the backdrop;
 *  - Escape closes it, when closing is allowed (not mid-submit);
 *  - on close, focus returns to whatever opened it.
 *
 * Only the TOPMOST open dialog handles Escape and Tab (re-audit QA-B45-3).
 * Every dialog listens on `document`, and `stopPropagation` does not stop
 * another listener on the same node, so Escape in a nested dialog also
 * closed the one beneath it -- even while the inner one was mid-submit and
 * refused to close, unmounting its busy form. Open dialogs are kept in a
 * module-level registry keyed by the order of the RENDER IN WHICH THEY
 * OPENED: a parent renders before its children (through a portal too), and a
 * dialog opened later renders later, so the highest order is the one on top.
 * (Effects are no use for this: React runs a child's effects before its
 * parent's.)
 *
 * Only OPEN dialogs are in the registry (QA-B45-R2-5). A dialog component
 * that stays mounted while closed passes `open`; it then holds no place,
 * moves no focus and handles no keys, and takes a NEW place each time it
 * opens. And a registered dialog whose root is not in the document (the
 * hook called, then `if (!open) return null`) never counts as on top: it
 * used to block Escape for every dialog mounted before it.
 *
 * Attach the returned ref to the element carrying role="dialog".
 * TransitionReasonDialog implemented this by hand; this is that behaviour,
 * shared, so each hand-rolled modal gets one implementation rather than none.
 */
import { useEffect, useRef, useState, type RefObject } from 'react'

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute('inert') && el.getAttribute('aria-hidden') !== 'true',
  )
}

/** Opening-render order of every open dialog -> its root; the largest attached one is on top. */
let nextOrder = 0
const openDialogs = new Map<number, RefObject<HTMLElement | null>>()

function isTopmost(order: number): boolean {
  for (const [other, root] of openDialogs) {
    // A dialog whose root is not in the document is not on top of anything.
    if (other > order && root.current?.isConnected) return false
  }
  return true
}

/** One opening of a dialog: where it sits in the stack, and who opened it. */
interface Session {
  order: number
  opener: HTMLElement | null
}

function openSession(): Session {
  return {
    order: ++nextOrder,
    opener:
      typeof document !== 'undefined' && document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null,
  }
}

export interface ModalFocusOptions {
  /** Called on Escape. Omit for a dialog that must be closed explicitly. */
  onClose?: () => void
  /** false while closing is not allowed (e.g. a submit in flight). */
  canClose?: boolean
  /**
   * false while the dialog is closed but its component stays mounted. Omit
   * for a dialog that is mounted only while open (the default, true).
   */
  open?: boolean
}

export function useModalFocus<T extends HTMLElement = HTMLDivElement>(
  { onClose, canClose = true, open = true }: ModalFocusOptions = {},
): RefObject<T | null> {
  const ref = useRef<T | null>(null)
  // Latest callbacks, read at key time, so the listener is installed once.
  const closeRef = useRef({ onClose, canClose })
  useEffect(() => {
    closeRef.current = { onClose, canClose }
  })

  // The opener and the order are taken in the RENDER in which the dialog
  // opens (its first render, or the render where `open` turns true), not in
  // an effect: an `autoFocus` field inside the dialog takes focus during
  // commit, before any effect runs, and would be recorded as the "opener";
  // and effects run child-first (see the header). Adjusting state while
  // rendering re-renders this component before its children render.
  const [session, setSession] = useState<Session | null>(() => (open ? openSession() : null))
  if (open !== (session !== null)) {
    setSession(open ? openSession() : null)
  }
  const order = session?.order ?? null
  const opener = session?.opener ?? null

  // Open: move focus in. Close: give focus back to the opener.
  useEffect(() => {
    if (order === null) return
    const root = ref.current
    if (root && !root.contains(document.activeElement)) {
      const first = focusableIn(root)[0]
      if (first) {
        first.focus()
      } else {
        if (!root.hasAttribute('tabindex')) root.setAttribute('tabindex', '-1')
        root.focus()
      }
    }
    return () => {
      if (opener && opener.isConnected) opener.focus()
    }
  }, [order, opener])

  useEffect(() => {
    if (order === null) return
    const mine = order
    openDialogs.set(mine, ref as RefObject<HTMLElement | null>)
    function onKeyDown(event: KeyboardEvent) {
      const root = ref.current
      if (!root || !isTopmost(mine)) return
      if (event.key === 'Escape') {
        const { onClose: close, canClose: allowed } = closeRef.current
        // The top dialog owns Escape even while it cannot close: nothing
        // beneath it may act on the same key press.
        event.stopPropagation()
        if (close && allowed) close()
        return
      }
      if (event.key !== 'Tab') return

      const items = focusableIn(root)
      if (items.length === 0) {
        event.preventDefault()
        root.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      const outside = !root.contains(active)
      if (event.shiftKey && (active === first || active === root || outside)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || outside)) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      openDialogs.delete(mine)
    }
  }, [order])

  return ref
}
