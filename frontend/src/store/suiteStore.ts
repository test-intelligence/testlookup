/**
 * Global test-suite filter (VIZ-303) — the suite axis, beside project, release
 * and time window.
 *
 * Before this, suite was page-local ``useState`` on every page that offered
 * it, so a suite picked on /trends was gone on /coverage. With the
 * ``viz_multi_filters`` flag on, report pages read this store instead; with it
 * off, they keep their local state and this store is never consulted.
 *
 * Shape and rules (contract C1)
 * -----------------------------
 * - Suites are matched by NAME, OR within the dimension, AND with release and
 *   window. At most ``SUITE_CAP`` names; ``[]`` is no filter and puts no
 *   ``suite_name`` parameter on the wire.
 * - Unlike a release, a suite name is meaningful in All Projects mode (it is a
 *   filter by name across projects), so ALL_PROJECTS does not clear it.
 * - It records the project it was chosen in, like ``releaseStore``, so a
 *   project switch can be detected — including across a reload — and names
 *   the new project has never run can be dropped out loud rather than
 *   filtering every page to nothing.
 *
 * Only the SELECTION lives here. The list of suites is server data (SWR).
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { capDistinct } from './releaseStore'
import { onLogoutReset, SUITE_FILTER_STORAGE_KEY } from './logoutReset'

/** Most suites one selection may hold (contract C1, `suite_cap`). */
export const SUITE_CAP = 50
/** Longest suite name the contract accepts (C1, `suite_name_length`). */
export const SUITE_NAME_MAX = 500

interface SuiteStore {
  /** Selected suite names, distinct, at most ``SUITE_CAP``. */
  activeSuiteNames: string[]
  /** The project the selection was made in (the ALL_PROJECTS sentinel counts
   *  as a project here), or ``null`` when nothing is selected. */
  scopedProjectId: string | null
  /** Replace the selection. Returns the names dropped for being over the cap
   *  or malformed, so a caller can say so. */
  setActiveSuites: (names: readonly string[], projectId: string | null) => string[]
  /** Clear the filter. */
  clearSuites: () => void
  /** Remove some names (after validating against a project's suite list). */
  removeSuites: (names: readonly string[]) => void
}

/** A suite name the contract accepts: 1–500 code points, not only whitespace. */
export function isValidSuiteName(name: unknown): name is string {
  if (typeof name !== 'string') return false
  const length = [...name].length
  return length >= 1 && length <= SUITE_NAME_MAX && name.trim() !== ''
}

export const useSuiteStore = create<SuiteStore>()(
  persist(
    (set, get) => ({
      activeSuiteNames: [],
      scopedProjectId: null,

      setActiveSuites: (names, projectId) => {
        const malformed = names.filter((n) => !isValidSuiteName(n)).map(String)
        const { kept, overCap } = capDistinct(names.filter(isValidSuiteName), SUITE_CAP)
        set({ activeSuiteNames: kept, scopedProjectId: kept.length > 0 ? projectId : null })
        return [...malformed, ...overCap]
      },

      clearSuites: () => set({ activeSuiteNames: [], scopedProjectId: null }),

      removeSuites: (names) => {
        const drop = new Set(names)
        const kept = get().activeSuiteNames.filter((n) => !drop.has(n))
        set({ activeSuiteNames: kept, scopedProjectId: kept.length > 0 ? get().scopedProjectId : null })
      },
    }),
    {
      name: SUITE_FILTER_STORAGE_KEY,
      version: 0,
      partialize: (s) => ({ activeSuiteNames: s.activeSuiteNames, scopedProjectId: s.scopedProjectId }),
      // A hand-edited or corrupted entry must not reach a request: keep only
      // names the contract accepts, still capped.
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as Partial<SuiteStore>
        const names = Array.isArray(p.activeSuiteNames) ? p.activeSuiteNames : []
        const { kept } = capDistinct(names.filter(isValidSuiteName), SUITE_CAP)
        return {
          ...current,
          activeSuiteNames: kept,
          scopedProjectId: kept.length > 0 && typeof p.scopedProjectId === 'string' ? p.scopedProjectId : null,
        }
      },
    },
  ),
)

// Logout forgets the selection (security M1; store/logoutReset.ts).
onLogoutReset(() => {
  useSuiteStore.getState().clearSuites()
  useSuiteStore.persist.clearStorage()
})
