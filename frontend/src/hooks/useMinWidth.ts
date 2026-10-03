/**
 * Whether the viewport is at least `px` wide (`(min-width: <px>px)`), kept in
 * step with resizes. For layouts set by inline styles that cannot carry a media
 * query (and where a new Tailwind breakpoint class would grow the EAGER
 * stylesheet, Wave 3 bundle rule). No `matchMedia` (jsdom, SSR): wide, the
 * desktop layout, so nothing changes where nothing can be measured.
 */
import { useCallback, useSyncExternalStore } from 'react'

const hasMatchMedia = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function'

export function useMinWidth(px: number): boolean {
  const query = `(min-width: ${px}px)`
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (!hasMatchMedia()) return () => {}
      const list = window.matchMedia(query)
      list.addEventListener('change', onChange)
      return () => list.removeEventListener('change', onChange)
    },
    [query],
  )
  const read = useCallback(() => (hasMatchMedia() ? window.matchMedia(query).matches : true), [query])
  return useSyncExternalStore(subscribe, read, () => true)
}

/** The report pages' body grid: two columns (1.65 : 1) from `md` (768 px), one column below it (Wave 3, X2/X3). */
export const BODY_GRID_TWO_COLUMNS = 'minmax(0, 1.65fr) minmax(0, 1fr)'
export const BODY_GRID_ONE_COLUMN = 'minmax(0, 1fr)'
export const BODY_GRID_MIN_WIDTH = 768
