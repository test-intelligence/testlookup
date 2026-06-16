import { useEffect, useState } from 'react'

/**
 * Pure-render clock. Returns a timestamp (ms) read from state so components
 * don't call the impure `Date.now()` *during render* (react-hooks/purity).
 *
 * - Omit `intervalMs` for one-shot "age" / "x ago" displays — the value is
 *   captured at mount and is stable across renders (pages re-render on their
 *   own data refresh, which re-mounts/re-evaluates these views anyway).
 * - Pass an `intervalMs` to keep it ticking for live elapsed / progress
 *   displays. The interval's setState runs from a timer callback (the
 *   "subscribe to an external source" pattern), not synchronously in render.
 */
export function useNow(intervalMs?: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!intervalMs) return
    const id = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return now
}
