/**
 * usePageVisibility — pauses SWR polling when the browser tab is hidden (P4-8).
 *
 * Background tabs continue polling by default, wasting bandwidth and backend
 * resources.  This hook tracks the Page Visibility API and returns a
 * `refreshInterval` of 0 when the tab is hidden, effectively pausing polling.
 *
 * Usage:
 *   const refreshInterval = useVisibilityAwareInterval(REFRESH_INTERVALS.ACTIVE)
 *   useSWR(key, fetcher, { refreshInterval })
 */
import { useEffect, useState } from 'react'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

/**
 * Returns `true` when the current tab is visible, `false` when hidden.
 */
export function usePageVisibility(): boolean {
  const [visible, setVisible] = useState(() =>
    typeof document !== 'undefined' ? !document.hidden : true,
  )

  useEffect(() => {
    function handleChange() {
      setVisible(!document.hidden)
    }
    document.addEventListener('visibilitychange', handleChange)
    return () => document.removeEventListener('visibilitychange', handleChange)
  }, [])

  return visible
}

/**
 * Returns `interval` when the tab is visible, `0` when hidden.
 * Drop-in replacement for a hardcoded `refreshInterval` value.
 */
export function useVisibilityAwareInterval(interval: number): number {
  const visible = usePageVisibility()
  return visible ? interval : 0
}
