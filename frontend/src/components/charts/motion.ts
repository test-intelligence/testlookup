/**
 * `prefers-reduced-motion` for charts (VIZ-105): both engines read this and
 * turn animation off when the reader asked for less motion.
 */
import { useSyncExternalStore } from 'react'

export const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

function mediaQuery(): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null
  return window.matchMedia(REDUCED_MOTION_QUERY) ?? null
}

function subscribe(onChange: () => void): () => void {
  const query = mediaQuery()
  if (!query) return () => {}
  if (typeof query.addEventListener === 'function') {
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }
  // Older Safari: the deprecated listener API.
  query.addListener?.(onChange)
  return () => query.removeListener?.(onChange)
}

export function prefersReducedMotion(): boolean {
  return mediaQuery()?.matches === true
}

export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, prefersReducedMotion, () => false)
}

/**
 * The `animate` value a chart should use: always off under reduced motion,
 * otherwise the caller's choice (`undefined` keeps the engine's default).
 */
export function useChartAnimation(requested: boolean | undefined): boolean | undefined {
  const reduced = usePrefersReducedMotion()
  return reduced ? false : requested
}
