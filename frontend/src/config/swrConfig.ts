/**
 * App-wide SWR policy for a backgrounded tab (audit M19).
 *
 * SWR's interval timer keeps ticking in a hidden tab; what stops the network
 * traffic is `refreshWhenHidden: false`, which makes each tick skip its fetch
 * while `document.visibilityState` is "hidden". That is SWR's default, and it
 * is pinned here anyway so the policy is a decision in one place rather than
 * an accident of a library default.
 *
 * The other half is the return. SWR refetches on becoming visible only through
 * `revalidateOnFocus`, and most polling hooks here turn that off (it was
 * refetching on every alt-tab). Such a hook, returning from an hour in the
 * background, showed hour-old data for up to one more full interval. The
 * middleware below closes that gap: a hook that polls AND has opted out of
 * focus revalidation refetches once, immediately, when the tab becomes
 * visible. Hooks that keep `revalidateOnFocus` are left to SWR, so nothing is
 * fetched twice.
 */
import { useEffect, useRef } from 'react'
import type { Middleware, SWRConfiguration } from 'swr'

function isPolling(refreshInterval: SWRConfiguration['refreshInterval']): boolean {
  if (typeof refreshInterval === 'function') return true
  return typeof refreshInterval === 'number' && refreshInterval > 0
}

export const resumePollingWhenVisible: Middleware = (useSWRNext) => (key, fetcher, config) => {
  const swr = useSWRNext(key, fetcher, config)
  const needsResume = key != null && isPolling(config.refreshInterval) && config.revalidateOnFocus === false
  // The latest mutate, read inside the listener, so the subscription does not
  // churn on every render.
  const mutateRef = useRef(swr.mutate)
  useEffect(() => {
    mutateRef.current = swr.mutate
  })

  useEffect(() => {
    if (!needsResume || typeof document === 'undefined') return
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') void mutateRef.current()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [needsResume])

  return swr
}

/** Spread into the root `<SWRConfig value>` (main.tsx). */
export const APP_SWR_CONFIG: SWRConfiguration = {
  refreshWhenHidden: false,
  refreshWhenOffline: false,
  use: [resumePollingWhenVisible],
}
