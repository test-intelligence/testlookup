/**
 * The mutate bound to the app's SWR cache provider.
 *
 * `main.tsx` renders `<SWRConfig value={{ ..., provider: () => new Map() }}>`
 * so each session generation gets a fresh cache. That makes the `mutate`
 * exported from the `swr` module USELESS here: it is bound at import time to
 * SWR's own default cache, while every key this app writes lives in the
 * provider's Map. A matcher-mutate over the default cache iterates an empty
 * map, matches nothing, and resolves having done nothing at all.
 *
 * Not hypothetical. `hooks/useUserManagement.ts` has shipped `refreshUsers()`
 * and `refreshApiKeys()` on the module-level `mutate` — both silent no-ops,
 * which is why those lists did not refresh after a write.
 *
 * `SwrMutateBridge`, rendered inside `SWRConfig`, registers the provider-bound
 * mutate here; module-scope helpers delegate through `appMutate`.
 */
import type { ScopedMutator } from 'swr'

let bound: ScopedMutator | null = null

export function registerAppMutate(mutate: ScopedMutator): void {
  bound = mutate
}

/** Test seam — also used by the bridge on unmount. */
export function resetAppMutate(): void {
  bound = null
}

export function hasAppMutate(): boolean {
  return bound !== null
}

/**
 * Delegate to the provider-bound mutate.
 *
 * Before the bridge mounts there is no cache to invalidate, so resolving is
 * correct rather than merely convenient.
 */
export const appMutate: ScopedMutator = ((...args: Parameters<ScopedMutator>) => {
  if (!bound) return Promise.resolve(undefined)
  return bound(...args)
}) as ScopedMutator
