/**
 * Forget the report scope on logout (security M1) WITHOUT making the eager
 * `authStore` import the scope stores.
 *
 * `authStore` is on the critical path of every page, the login page included,
 * so it does not import the scope stores. Instead:
 *
 *  - the saved entries are removed here, synchronously, by key — a store that
 *    was never loaded has nothing in memory, and its next load hydrates from
 *    the (now empty) entry;
 *  - each scope store that IS loaded registers its own in-memory reset when
 *    its module evaluates (`releaseStore`).
 *
 * Every reset runs synchronously inside `logout()`, so nothing is requested
 * under the old scope.
 */

/** `persist` name of the release filter store. */
export const RELEASE_FILTER_STORAGE_KEY = 'tl.release-filter'
/** The suite filter store's entry (VIZ-303's multi-select, removed in Phase D).
 *  No store writes it any more; logout still removes one an older build left. */
export const SUITE_FILTER_STORAGE_KEY = 'tl.suite-filter'

const resets: Array<() => void> = []

/** Register an in-memory reset to run on logout (called at module load). */
export function onLogoutReset(reset: () => void): void {
  resets.push(reset)
}

/** Clear the report scope: every registered reset, then the saved entries. */
export function resetReportScopeOnLogout(): void {
  for (const reset of resets) reset()
  // After the resets: a store's `set` re-writes its entry.
  for (const key of [RELEASE_FILTER_STORAGE_KEY, SUITE_FILTER_STORAGE_KEY]) {
    try {
      localStorage.removeItem(key)
    } catch {
      // Storage unavailable (private mode, blocked site data): nothing was saved.
    }
  }
}
