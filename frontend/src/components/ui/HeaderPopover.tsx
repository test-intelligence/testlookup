import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'
import type { ReactNode, RefObject } from 'react'
import { createPortal } from 'react-dom'

/**
 * A header-anchored popover rendered into `document.body`.
 *
 * WHY A PORTAL: the TopBar sets `backdrop-blur`, and a filtered element creates
 * a stacking context. A menu inside the header therefore competes only with the
 * header's OWN children — its `z-50` is meaningless against page content, so any
 * later-in-DOM panel that forms its own stacking context (sticky toolbars,
 * charts, modals) paints straight over the open menu. Escaping to `document.body`
 * removes the menu from that trap entirely; `z-[60]` then clears every page
 * panel (which top out at z-50) while staying under the z-[70] blocking dialog.
 *
 * Positioned `fixed` from the trigger's `getBoundingClientRect()` — viewport
 * coordinates, so they map onto `position: fixed` directly — and recomputed on
 * scroll and resize so the panel tracks its trigger.
 *
 * OUTSIDE-CLICK OWNERSHIP: this component owns dismissal. Once portaled, the
 * panel is NOT a DOM descendant of the trigger, so a caller's
 * `ref.contains(e.target)` check would treat every click INSIDE the menu as an
 * outside click and close it before the item fired. Both nodes are tested here.
 */
export function HeaderPopover({
  anchorRef,
  open,
  onClose,
  width,
  children,
  ariaLabel,
  role = 'menu',
}: {
  /** The trigger element. The panel is right-aligned to its right edge. */
  anchorRef: RefObject<HTMLElement | null>
  open: boolean
  onClose: () => void
  /** Panel width in px — needed up front to right-align before first paint. */
  width: number
  children: ReactNode
  ariaLabel: string
  role?: 'menu' | 'listbox' | 'dialog'
}) {
  const panelRef = useRef<HTMLDivElement>(null)

  /**
   * Written straight to the node rather than held in state: positioning from a
   * measurement is a DOM read→write, and routing it through `setState` inside a
   * layout effect is both an extra render and a cascading-render lint error.
   */
  const reposition = useCallback(() => {
    const anchor = anchorRef.current
    const panel = panelRef.current
    if (!anchor || !panel) return
    const r = anchor.getBoundingClientRect()
    // Right-align to the trigger, then clamp so the panel never leaves the
    // viewport on narrow screens (an 8px gutter keeps it off the edge).
    const left = Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8))
    panel.style.top = `${r.bottom + 8}px`
    panel.style.left = `${left}px`
  }, [anchorRef, width])

  // Runs after mount but BEFORE paint, so the off-screen initial coordinates
  // below are never visible.
  useLayoutEffect(() => {
    if (!open) return
    reposition()
  }, [open, reposition])

  useEffect(() => {
    if (!open) return
    // `true` captures scrolls from any nested scroll container, not just window.
    window.addEventListener('scroll', reposition, true)
    window.addEventListener('resize', reposition)
    return () => {
      window.removeEventListener('scroll', reposition, true)
      window.removeEventListener('resize', reposition)
    }
  }, [open, reposition])

  useEffect(() => {
    if (!open) return
    const onMouseDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (anchorRef.current?.contains(t)) return   // trigger toggles itself
      if (panelRef.current?.contains(t)) return    // clicks inside the menu
      onClose()
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', onMouseDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open, onClose, anchorRef])

  if (!open) return null

  return createPortal(
    <div
      ref={panelRef}
      role={role}
      aria-label={ariaLabel}
      className="fixed z-[60] max-h-[70vh] overflow-y-auto rounded-xl border shadow-2xl"
      style={{
        // Parked off-screen for the single pre-paint frame before the layout
        // effect measures; never rendered at 0,0 in the corner.
        top: -9999,
        left: -9999,
        width,
        background: 'var(--color-bg-card)',
        borderColor: 'var(--color-border)',
      }}
    >
      {children}
    </div>,
    document.body,
  )
}
