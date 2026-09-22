import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { useReleaseStore } from '@/store/releaseStore'
import { useMultiFiltersEnabled } from '@/store/multiFiltersFlag'
import { useSettledScopeStore } from '@/store/settledScope'
import { useActiveProjectId } from './useProjectScopedSWR'

/**
 * The release id a request should actually be scoped to, or `null` for none.
 *
 * Every data hook that supports the release axis reads this rather than the
 * store directly, for two reasons.
 *
 * **Releases belong to exactly one project.** In All Projects mode
 * `useProjectScopedSWR` deliberately fetches with no project filter; sending a
 * release id alongside that would filter every project's data by one project's
 * release, which is not a narrower answer but a wrong one. The same applies
 * before a project has resolved (`null`). `ReleasePicker` already disables
 * itself in both states, but a disabled control is a UI convention, not an
 * enforcement — the store can still hold a value from before the switch, and a
 * request built from it would be silently wrong. This is the enforcement.
 *
 * **The result must reach the SWR key, not just the params.** A release id that
 * changes the request but not the key makes SWR serve the previous release's
 * response for the new one — the reader sees another release's numbers under
 * this release's name, with nothing to indicate it. Callers therefore put this
 * value in their `deps` array as well as passing it to the service.
 */
export function useReleaseScope(): ReleaseScope {
  const projectId = useActiveProjectId()
  const releaseId = useReleaseStore(s => s.activeReleaseId)
  const scopedProjectId = useReleaseStore(s => s.scopedProjectId)
  const multi = useMultiFiltersEnabled()
  // Flag ON reads the SETTLED selection (store/settledScope.ts): data follows
  // the pickers 250 ms after the last change, as one request, and the pair
  // (ids, project) settles together so the guard below holds for it too.
  const settledIds = useSettledScopeStore(s => s.releaseIds)
  const settledProjectId = useSettledScopeStore(s => s.releaseProjectId)
  if (projectId === null || projectId === ALL_PROJECTS_ID) return null
  if (multi) {
    // The guard below, on the SETTLED pair: the active project changes at
    // once, so while project A's selection is still settling nothing is sent
    // for B. (Not the raw pair: that would let a clear-then-reselect burst
    // through the debounce as an extra request.)
    if (settledProjectId !== projectId) return null
    // The whole selection, sorted and identity-stable (OR within the
    // dimension, so order is not meaning), or null for none — never `[]`,
    // which is truthy and would read as "filtered" to `if (releaseId)`.
    return settledIds.length > 0 ? settledIds : null
  }
  // The selection must belong to the project being asked about.
  //
  // `ReleasePicker` clears a selection that outlived its project, but it does
  // so in a passive `useEffect`, and SWR revalidates from a LAYOUT effect —
  // which runs first in the same commit. So between a project switch and
  // reconciliation there is one render where `activeProjectId` is already B
  // while the store still holds A's release, and every mounted hook fires a
  // request for project B scoped by project A's release. The response is
  // discarded once reconciliation lands, so nothing wrong is displayed; what
  // remains is a wasted cross-scope query per hook per switch, and a 403 toast
  // if the release belongs to a project this user cannot read.
  //
  // `scopedProjectId` is the field the store exists to record. Comparing it
  // here makes the guard depend on the pairing rather than on effect ordering.
  if (scopedProjectId !== projectId) return null
  // Flag OFF: exactly the pre-multi-select answer — the scalar, or null.
  return releaseId
}

/**
 * What `useReleaseScope` hands a consumer.
 *
 *  - flag off: `string | null`, exactly as before VIZ-303;
 *  - flag on:  `string[] | null` (sorted, non-empty, stable identity).
 *
 * Consumers never branch on the shape: they pass it to a service through
 * `scopeArg` (one value becomes the legacy scalar on the wire) and into an SWR
 * key through `keyPart` (a sorted joined string, never an array).
 */
export type ReleaseScope = string | readonly string[] | null
