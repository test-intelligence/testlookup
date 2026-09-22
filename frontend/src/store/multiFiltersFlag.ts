/**
 * Whether the `viz_multi_filters` rollout flag (VIZ-303 / VIZ-306) is on for
 * the active project — mirrored into a tiny synchronous store.
 *
 * Why not call `useFeatureEnabled` in every hook that branches on it: that is
 * an SWR request per call site, and the branching hooks (`useReleaseScope`,
 * every data hook, `ReleasePicker`) are rendered in unit tests that count
 * requests and never mock the flag endpoint. One reader — `useScopeUrlSync`,
 * mounted once in `AppLayout` (behind `ScopeUrlSyncGate`, which publishes
 * 'off' until the lazy sync loads) — resolves the flag and publishes it here; every
 * other reader is a plain synchronous selector.
 *
 * THREE states, not two
 * ---------------------
 * `resolved: false` is "the flag has not answered yet" — the state every page
 * load starts in. It is NOT "off": reading it as off let the legacy
 * single-release `ReleasePicker` loop run in that window and publish
 * `?release=<first id>`, which collapsed a saved multi-selection to one release
 * the moment the flag said "on" (E3 review, proof A). So:
 *
 *  - READS (request params, keys) use `enabled`, which is `false` until the
 *    flag answers — a request made in the window is exactly the flag-off
 *    request, which is exactly the pre-VIZ-303 request;
 *  - legacy SIDE EFFECTS that rewrite the selection or the URL (the
 *    `ReleasePicker` loop, a page's suite-change redirect) wait for
 *    `resolved`, via `useMultiFiltersState`.
 *
 * Once resolved it stays resolved: `useScopeUrlSync` publishes nothing while
 * a project's answer is in flight, so the store holds the last answer across a
 * project switch and the flag never flickers on -> off -> on (proof B).
 *
 * Deliberately NOT persisted: the flag is server state, and a stale "on" from
 * a previous session must never outlive a rollback.
 */
import { create } from 'zustand'

interface MultiFiltersFlagStore {
  /** The flag is on. `false` while unresolved. */
  enabled: boolean
  /** The flag has answered at least once (or failed, which reads as off). */
  resolved: boolean
  /** Publish an answer (marks the flag resolved). */
  setEnabled: (enabled: boolean) => void
}

export const useMultiFiltersFlagStore = create<MultiFiltersFlagStore>()((set) => ({
  enabled: false,
  resolved: false,
  setEnabled: (enabled) => set({ enabled, resolved: true }),
}))

/** True when multi-select release/suite filters and URL sync are active. */
export function useMultiFiltersEnabled(): boolean {
  return useMultiFiltersFlagStore((s) => s.enabled)
}

/** `'unknown'` until the flag answers, then `'on'` / `'off'`. */
export type MultiFiltersState = 'unknown' | 'on' | 'off'

export function multiFiltersState(s: Pick<MultiFiltersFlagStore, 'enabled' | 'resolved'>): MultiFiltersState {
  if (s.enabled) return 'on'
  return s.resolved ? 'off' : 'unknown'
}

/** The tri-state, for code that must do nothing while the flag is unknown. */
export function useMultiFiltersState(): MultiFiltersState {
  return useMultiFiltersFlagStore(multiFiltersState)
}
