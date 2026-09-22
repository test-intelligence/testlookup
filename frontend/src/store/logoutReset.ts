/**
 * Forget the report scope on logout (security M1) WITHOUT making the eager
 * `authStore` import the scope machinery.
 *
 * `authStore` is on the critical path of every page, the login page included.
 * Importing `settledScope` from it (to clear the scope on logout) pulled the
 * suite store, the settled-scope store, the abort tracker and the scope
 * helpers into the eager bundle. Instead:
 *
 *  - the two saved entries are removed here, synchronously, by key — a store
 *    that was never loaded has nothing in memory, and its next load hydrates
 *    from the (now empty) entry;
 *  - each scope store that IS loaded registers its own in-memory reset when
 *    its module evaluates (`releaseStore`, `suiteStore`, then `settledScope`,
 *    which settles at once). Module evaluation order is import order, so the
 *    stores reset before the settled scope follows them.
 *
 * Every reset runs synchronously inside `logout()`, so nothing is requested
 * under the old scope.
 */

/** `persist` names of the release and suite filter stores. */
export const RELEASE_FILTER_STORAGE_KEY = 'tl.release-filter'
export const SUITE_FILTER_STORAGE_KEY = 'tl.suite-filter'

const resets: Array<() => void> = []

/** Register an in-memory reset to run on logout (called at module load). */
export function onLogoutReset(reset: () => void): void {
  resets.push(reset)
}

/** Clear the report scope: every registered reset, then both saved entries. */
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
