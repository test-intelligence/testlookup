import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

/**
 * A tab selection kept in the URL (`?tab=<id>`), so a link opens the same tab
 * and Back does not step through every tab change (`replace`).
 *
 * `ids` is the page's own tab list: a value in the URL that is not one of them
 * reads as `fallback`, so a stale or hand-edited link never selects a tab that
 * does not exist. Choosing the fallback removes the key, so the default tab
 * has the clean URL.
 */
export function useTabParam<T extends string>(
  ids: readonly T[],
  fallback: T,
  key = 'tab',
): [T, (next: T) => void] {
  const [params, setParams] = useSearchParams()
  const raw = params.get(key)
  const value = raw !== null && (ids as readonly string[]).includes(raw) ? (raw as T) : fallback
  const setValue = useCallback(
    (next: T) => {
      setParams(
        (current) => {
          const out = new URLSearchParams(current)
          if (next === fallback) out.delete(key)
          else out.set(key, next)
          return out
        },
        { replace: true },
      )
    },
    [fallback, key, setParams],
  )
  return [value, setValue]
}
