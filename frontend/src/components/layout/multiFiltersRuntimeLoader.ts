/**
 * The lazy half of the `viz_multi_filters` UI (VIZ-303 / VIZ-306).
 *
 * Everything that only runs with the flag ON — the report-scope URL sync, the
 * TopBar's multi-release summary button, the report chrome slot — is one
 * chunk (`multiFiltersRuntime.ts`), requested by `ScopeUrlSyncGate` only once
 * the flag answers ON. With the flag off none of it (nor what it imports: the
 * URL codec and validation, the notice and settled-scope stores, the abort
 * tracker) is ever downloaded; that kept the eager bundle within its budget.
 *
 * The loaded module is published in a tiny store so the eager places that
 * render its parts (`ScopeUrlSyncGate`, `ReleasePicker`, `AppLayout`'s chrome
 * host) re-render when it arrives. In the app the flag reads 'on' only after
 * it has loaded — `useScopeUrlSync`, inside it, is what publishes 'on' — so
 * a flag-on render never waits on it.
 */
import { create } from 'zustand'

export type MultiFiltersRuntime = typeof import('./multiFiltersRuntime')

export const useMultiFiltersRuntimeStore = create<{ runtime: MultiFiltersRuntime | null }>()(() => ({
  runtime: null,
}))

let loading: Promise<MultiFiltersRuntime> | null = null

/** Load (once) and publish the runtime. A failed load is forgotten, so the
 *  next attempt retries. */
export function loadMultiFiltersRuntime(): Promise<MultiFiltersRuntime> {
  loading ??= import('./multiFiltersRuntime').then(
    (runtime) => {
      useMultiFiltersRuntimeStore.setState({ runtime })
      return runtime
    },
    (error: unknown) => {
      loading = null
      throw error
    },
  )
  return loading
}
