import { useScopeUrlSync } from '@/hooks/useScopeUrlSync'

/**
 * The report-scope URL sync, as a component — the LAZY chunk behind
 * `ScopeUrlSyncGate`. Mounted only once `viz_multi_filters` has answered ON;
 * from then on it stays mounted and `useScopeUrlSync` owns the flag store
 * exactly as it did when it was called from `AppLayout` directly: it publishes
 * 'on' itself (so 'on' is never visible before the sync runs), follows later
 * answers (off / on per project), and holds the last answer while a new
 * project's is in flight.
 */
export default function ScopeUrlSyncRuntime() {
  useScopeUrlSync()
  return null
}
