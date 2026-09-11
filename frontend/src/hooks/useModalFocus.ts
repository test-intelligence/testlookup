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
 * module-level registry keyed by the order in which they FIRST RENDERED: a
 * parent renders before its children (through a portal too), and a dialog
 * opened later renders later, so the highest order is the one on top.
 * (Effects are no use for this: React runs a child's effects before its
 * parent's.)
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

/** First-render order of every open dialog; the largest is on top. */
let nextOrder = 0
const openDialogs = new Set<number>()

function isTopmost(order: number): boolean {
  for (const other of openDialogs) if (other > order) return false
  return true
}

export interface ModalFocusOptions {
  /** Called on Escape. Omit for a dialog that must be closed explicitly. */
  onClose?: () => void
  /** false while closing is not allowed (e.g. a submit in flight). */
  canClose?: boolean
}

export function useModalFocus<T extends HTMLElement = HTMLDivElement>(
  { onClose, canClose = true }: ModalFocusOptions = {},
): RefObject<T | null> {
  const ref = useRef<T | null>(null)
  // Latest callbacks, read at key time, so the listener is installed once.
  const closeRef = useRef({ onClose, canClose })
  useEffect(() => {
    closeRef.current = { onClose, canClose }
  })

  // The opener is read during the FIRST RENDER, not in the effect: an
  // `autoFocus` field inside the dialog takes focus during commit, before any
  // effect runs, and the effect would record that field as the "opener".
  const [opener] = useState<HTMLElement | null>(() =>
    typeof document !== 'undefined' && document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  )
  // Also taken at first render, for the same reason: see the header.
  const [order] = useState(() => ++nextOrder)

  // Open: move focus in. Close: give focus back to the opener.
  useEffect(() => {
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
  }, [opener])

  useEffect(() => {
    openDialogs.add(order)
    function onKeyDown(event: KeyboardEvent) {
      const root = ref.current
      if (!root || !isTopmost(order)) return
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
      openDialogs.delete(order)
    }
  }, [order])

  return ref
}
