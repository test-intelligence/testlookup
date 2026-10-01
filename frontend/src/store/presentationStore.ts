/**
 * Presentation mode (VIZ-106): a per-browser preference for a wall monitor or
 * a projector. Off until someone turns it on; kept in this browser profile's
 * localStorage (not synced to the account — a presenter's laptop and their
 * desk are different screens).
 *
 * While it is on, <html> carries `data-presentation="on"`, which swaps the type
 * and text-contrast tokens (`index.css`, the `[data-presentation]` block), and
 * `usePresentationScale()` scales every Recharts drawing to 16 px axis text.
 *
 * The attribute is written HERE, at import time, the way `themeStore` writes
 * `data-theme`: this module is in the eager graph (AppLayout -> Sidebar ->
 * PresentationToggle), which runs before React renders, so a presenter never
 * sees the desk-sized page flash before the room-sized one. Writing it from a
 * component effect would paint one frame at desk size first.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export const PRESENTATION_ATTRIBUTE = 'data-presentation'
export const PRESENTATION_STORAGE_KEY = 'testlookup-presentation'

interface PresentationState {
  enabled: boolean
  setEnabled: (enabled: boolean) => void
}

export function applyPresentation(enabled: boolean): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  // Removed, not set to "off": with the mode off <html> is exactly what it was
  // before the mode existed.
  if (enabled) root.setAttribute(PRESENTATION_ATTRIBUTE, 'on')
  else root.removeAttribute(PRESENTATION_ATTRIBUTE)
}

export const usePresentationStore = create<PresentationState>()(
  persist(
    (set) => ({
      enabled: false,
      setEnabled: (enabled) => {
        set({ enabled })
        applyPresentation(enabled)
      },
    }),
    {
      name: PRESENTATION_STORAGE_KEY,
      partialize: (state) => ({ enabled: state.enabled }),
      // Anything but a literal `true` in storage (a hand-edited or corrupted
      // value) is off: the mode is opt-in.
      merge: (persisted, current) => ({
        ...current,
        enabled: (persisted as Partial<PresentationState> | undefined)?.enabled === true,
      }),
    },
  ),
)

// localStorage hydration is synchronous, so the persisted value is already in
// the store here: apply it before the first paint.
applyPresentation(usePresentationStore.getState().enabled)
