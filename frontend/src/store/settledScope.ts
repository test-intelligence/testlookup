/**
 * The SETTLED report scope — what data requests are built from (VIZ-303,
 * "filter changes debounced 250 ms; in-flight requests aborted").
 *
 * Two readers, two clocks:
 *
 *  - The UI (pickers, chips, the address bar, `useReportScope`) reads the
 *    release and suite stores directly and updates on every click.
 *  - Data hooks read THIS store, through `useReleaseScope` and
 *    `useSuiteScope`. It follows the selection stores `SCOPE_SETTLE_MS` after
 *    the LAST change, so ticking five suites in quick succession produces one
 *    settled scope and one request per panel, not five.
 *
 * Each time it settles on a new value it tells `services/scopeAbort.ts`,
 * which aborts every in-flight request built from the scope it replaces.
 *
 * Not debounced: a discrete jump that is not a burst of refinement — a pasted
 * link or Back/Forward adopted by `useScopeUrlSync`, or a project-change
 * reconciliation — calls `settleScopeNow()`, so a link is not answered first
 * with the persisted scope and 250 ms later with its own.
 *
 * The release selection settles together with the project it belongs to
 * (`releaseProjectId`), so `useReleaseScope` can apply its "a release belongs
 * to one project" guard to the settled pair and never pair project B with a
 * release of project A still waiting to settle.
 *
 * With the `viz_multi_filters` flag off no reader consults this store and no
 * request is tracked for aborting — legacy behaviour is untouched.
 */
import { create } from 'zustand'
import { normalizeScope, scopeKey } from '@/lib/scopeParams'
import { setSettledScopeFingerprint } from '@/services/scopeAbort'
import { onLogoutReset } from './logoutReset'
import { useMultiFiltersFlagStore } from './multiFiltersFlag'
import { selectReleaseIds, useReleaseStore } from './releaseStore'
import { useSuiteStore } from './suiteStore'

/** Quiet period after the last filter change before data follows it. */
export const SCOPE_SETTLE_MS = 250

export interface SettledScope {
  /** Sorted, distinct; `[]` for no release filter. */
  releaseIds: string[]
  /** The project `releaseIds` were chosen in (null with none). */
  releaseProjectId: string | null
  /** Sorted, distinct; `[]` for no suite filter. */
  suiteNames: string[]
}

function readSelection(): SettledScope {
  const release = useReleaseStore.getState()
  return {
    releaseIds: normalizeScope(selectReleaseIds(release)),
    releaseProjectId: release.scopedProjectId,
    suiteNames: normalizeScope(useSuiteStore.getState().activeSuiteNames),
  }
}

const sameList = (a: readonly string[], b: readonly string[]) => scopeKey(a) === scopeKey(b)

export const useSettledScopeStore = create<SettledScope>()(() => readSelection())

function publishFingerprint(): void {
  const s = useSettledScopeStore.getState()
  setSettledScopeFingerprint(
    useMultiFiltersFlagStore.getState().enabled
      ? { release: scopeKey(s.releaseIds), suite: scopeKey(s.suiteNames) }
      : null,
  )
}

let timer: ReturnType<typeof setTimeout> | null = null

/** Settle on the current selection immediately (cancels a pending settle). */
export function settleScopeNow(): void {
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }
  const prev = useSettledScopeStore.getState()
  const next = readSelection()
  const releasesSame = sameList(prev.releaseIds, next.releaseIds) && prev.releaseProjectId === next.releaseProjectId
  const suitesSame = sameList(prev.suiteNames, next.suiteNames)
  if (releasesSame && suitesSame) return
  // Keep the unchanged half's identity: it sits in dependency lists.
  useSettledScopeStore.setState({
    releaseIds: releasesSame ? prev.releaseIds : next.releaseIds,
    releaseProjectId: next.releaseProjectId,
    suiteNames: suitesSame ? prev.suiteNames : next.suiteNames,
  })
  publishFingerprint()
}

/** (Re)start the quiet period. */
function scheduleSettle(): void {
  if (timer !== null) clearTimeout(timer)
  timer = setTimeout(settleScopeNow, SCOPE_SETTLE_MS)
}

/** Whether a settle is waiting for the quiet period (test seam). */
export function isScopeSettlePending(): boolean {
  return timer !== null
}

useReleaseStore.subscribe((state, prev) => {
  if (
    state.activeReleaseId !== prev.activeReleaseId ||
    state.activeReleaseIds !== prev.activeReleaseIds ||
    state.scopedProjectId !== prev.scopedProjectId
  ) {
    scheduleSettle()
  }
})
useSuiteStore.subscribe((state, prev) => {
  if (state.activeSuiteNames !== prev.activeSuiteNames) scheduleSettle()
})
// Logout: the release and suite stores (imported above, so registered
// first) have cleared themselves; follow them at once, not 250 ms later.
onLogoutReset(settleScopeNow)
useMultiFiltersFlagStore.subscribe((state, prev) => {
  if (state.enabled !== prev.enabled) publishFingerprint()
})
publishFingerprint()
