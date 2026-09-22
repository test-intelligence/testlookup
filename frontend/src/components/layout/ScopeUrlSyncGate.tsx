import { useEffect } from 'react'
import { VIZ_FLAGS } from '@/config/vizFlags'
import { useFeatureFlagStatus } from '@/hooks/useFeatureFlags'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { loadMultiFiltersRuntime, useMultiFiltersRuntimeStore } from './multiFiltersRuntimeLoader'

/**
 * The ONE mount of the report-scope URL sync (VIZ-306), split so the flag-off
 * path pays for none of it: this gate is eager and tiny; the sync itself
 * (`useScopeUrlSync` and everything it imports) is part of the lazy
 * multi-filters runtime, requested only once `viz_multi_filters` answers ON.
 *
 * Until the runtime is loaded this gate is the flag's only publisher, and it
 * publishes only 'off':
 *
 *  - unknown (the status request in flight): nothing — the store stays
 *    'unknown', so every legacy side effect keeps waiting (E3 review, proof A);
 *  - off: 'off' at once, and the runtime is never requested;
 *  - on: load the runtime and mount the sync; `useScopeUrlSync` publishes 'on'
 *    ITSELF. So 'on' is never visible while nothing owns the URL — the flag
 *    reads 'unknown' for the few ms the chunk takes, and neither loop writes
 *    in that window.
 *
 * Once mounted the sync stays mounted and owns the flag store (as it did when
 * `AppLayout` called the hook directly); this gate then only renders it. A
 * failed chunk load reads as 'off' (the legacy picker keeps working) and is
 * retried on the next flag answer.
 */
export default function ScopeUrlSyncGate() {
  const flag = useFeatureFlagStatus(VIZ_FLAGS.multiFilters)
  const setEnabled = useMultiFiltersFlagStore((s) => s.setEnabled)
  const runtime = useMultiFiltersRuntimeStore((s) => s.runtime)

  useEffect(() => {
    if (runtime !== null || flag === undefined) return
    if (!flag) {
      setEnabled(false)
      return
    }
    let current = true
    loadMultiFiltersRuntime().catch(() => {
      if (current) setEnabled(false)
    })
    return () => {
      current = false
    }
  }, [flag, runtime, setEnabled])

  return runtime ? <runtime.ScopeUrlSync /> : null
}
