/**
 * The keyboard behaviour of an in-page container (a detail pane, a form
 * section, a confirmation strip) — what the overlays gave their users, minus
 * the trap (W27 PAN-01; the non-modal side panel's semantics, written fresh):
 *
 *  - on mount, when focus is not already inside, focus moves to the first
 *    enabled field (input, select, textarea), else to the first focusable
 *    control, and the container is scrolled into view (`block: 'nearest'`);
 *  - Escape while focus is INSIDE closes it (`onEscape`), unless closing is
 *    not allowed right now (`canEscape: false`, mid-submit), when the key is
 *    swallowed and nothing closes;
 *  - on unmount, focus goes back to the control that had it when the
 *    container opened, when focus was still inside (or had fallen to <body>):
 *    a user who has since moved focus elsewhere keeps it there.
 *
 * Escape is a native listener on the ROOT, not on `document`: it runs before
 * any document listener, and it calls preventDefault() + stopPropagation(),
 * so the innermost container owns the key (a form inside a pane closes the
 * form only) and a page's own document-level Escape (/test-management's
 * case pane) ignores a key that was already handled.
 *
 * No Tab trap, no scroll lock, no portal, no registry: the page stays usable
 * around the container.
 */
import { useLayoutEffect, useRef, useState, type RefObject } from 'react'

const FIELD =
  'input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled])'
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

export interface InPageFocusOptions {
  /** Escape while focus is INSIDE the root (a native keydown listener on the root, not on document):
   *  preventDefault() + stopPropagation(), then onEscape() when canEscape. The innermost container owns it. */
  onEscape?: () => void
  /** false mid-submit: Escape is swallowed and nothing closes (the modals' canClose). */
  canEscape?: boolean
  /** Mount: when focus is not already inside, focus the first enabled field (input, select, textarea), else the
   *  first focusable; then root.scrollIntoView({ block: 'nearest' }) when the function exists (jsdom has none).
   *  Default true. */
  autoFocus?: boolean
  /** Unmount (a layout-effect cleanup, while the root is still attached): focus returns to the element that had
   *  it when the container opened, when focus is inside the root or on <body>. Default true. */
  restoreFocus?: boolean
}

function usable(el: Element): el is HTMLElement {
  return el instanceof HTMLElement && !el.closest('[inert], [aria-hidden="true"]')
}

function firstFocusTarget(root: HTMLElement): HTMLElement | undefined {
  return (
    Array.from(root.querySelectorAll(FIELD)).find(usable) ??
    Array.from(root.querySelectorAll(FOCUSABLE)).find(usable)
  )
}

export function useInPageFocus<T extends HTMLElement>(options: InPageFocusOptions = {}): RefObject<T | null> {
  const { onEscape, canEscape = true, autoFocus = true, restoreFocus = true } = options
  const ref = useRef<T | null>(null)

  // Captured in the opening render, before an `autoFocus` field inside takes
  // focus during the commit.
  const [opener] = useState<HTMLElement | null>(() =>
    typeof document !== 'undefined' && document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  )
  // Focus moves in once, at mount: a later prop change never steals focus.
  const [focusOnMount] = useState(autoFocus)

  // The latest handlers, read at key / unmount time, so the listener is installed once.
  const latest = useRef({ onEscape, canEscape, restoreFocus })
  useLayoutEffect(() => {
    latest.current = { onEscape, canEscape, restoreFocus }
  })

  const handlesEscape = onEscape !== undefined
  useLayoutEffect(() => {
    const root = ref.current
    if (!root || !handlesEscape) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      event.preventDefault()
      event.stopPropagation()
      const { onEscape: close, canEscape: allowed } = latest.current
      if (allowed && close) close()
    }
    root.addEventListener('keydown', onKeyDown)
    return () => root.removeEventListener('keydown', onKeyDown)
  }, [handlesEscape])

  useLayoutEffect(() => {
    const root = ref.current
    if (root && focusOnMount) {
      if (!root.contains(document.activeElement)) firstFocusTarget(root)?.focus()
      if (typeof root.scrollIntoView === 'function') root.scrollIntoView({ block: 'nearest' })
    }
    // A LAYOUT-effect cleanup runs while the root is still in the document,
    // so "was focus inside it?" is still answerable; a passive cleanup runs
    // after removal, when focus has already fallen to <body>.
    return () => {
      if (!latest.current.restoreFocus) return
      const active = document.activeElement
      const focusWasOurs = !active || active === document.body || (root?.contains(active) ?? false)
      if (focusWasOurs && opener?.isConnected) opener.focus()
    }
  }, [focusOnMount, opener])

  return ref
}
