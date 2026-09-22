/**
 * Global release filter — the third axis, alongside project and time window.
 *
 * Why a store, given the standing "no new Zustand stores without strong
 * reason" rule: this is the same shape as ``timeWindowStore``, which that rule
 * names as the canonical strong reason. A release selection has to be read and
 * written from every windowed page, must follow the user across navigation, and
 * needs cross-page reactivity. Threading it through props or duplicating a
 * localStorage key per page is exactly what ``timeWindowStore`` was created to
 * stop.
 *
 * It holds only the *selection*. The release list itself is server data and
 * stays in SWR (``useReleases``), per the same rule.
 *
 * Two behaviours carry all the risk
 * ---------------------------------
 * **1. A release must not outlive its project.** Releases are project-scoped
 * (unique on ``(project_id, lower(name))``), so a release id from project A
 * means nothing in project B. If the selection survived a project switch, every
 * windowed page would filter by an id that matches no run in the new project
 * and render empty — with the picker still showing a release name, so the user
 * sees "no data" and nothing explaining why. That is indistinguishable from the
 * product being broken, and it is the same class as the 2026-05-15 incident
 * ``projectStore`` documents.
 *
 * **2. ALL_PROJECTS is the default AND the recovery state.** ``projectStore``
 * defaults to the sentinel and promotes any stale selection back to it, so it
 * is the state a user is most likely to be in — not an edge case. Releases
 * cannot be enumerated across projects (cross-project release trains are an
 * explicit non-goal), so the release filter is simply unavailable there. The
 * store models that as ``null``, which is also "no filter" — so the aggregate
 * view keeps behaving exactly as it does today.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { onLogoutReset, RELEASE_FILTER_STORAGE_KEY } from './logoutReset'

/** Selecting nothing. Not a sentinel — the genuine absence of a filter. */
export const NO_RELEASE = null

/** Most releases one selection may hold (contract C1, `release_cap`). */
export const RELEASE_CAP = 20

/**
 * Persisted-state version — deliberately still 0.
 *
 * VIZ-303 added `activeReleaseIds` to the saved entry. It is a SUPERSET of the
 * v0 shape (the scalar `activeReleaseId` is always the list's first id), so no
 * version bump: the pre-VIZ-303 store is `persist` at version 0 with no
 * `migrate`, and zustand DISCARDS an entry whose version differs from its own
 * (logging "couldn't be migrated") — a rollback would have silently dropped
 * every user's saved release filter. At version 0 a rolled-back build reads
 * the scalar and ignores the list. A v0 entry is upgraded on READ instead
 * (`mergeReleaseFilter`).
 */
export const RELEASE_STORE_VERSION = 0

interface ReleaseStore {
  /** Selected release, or ``null`` for "all releases" (the default).
   *
   *  Since v1 this is ALWAYS the first element of ``activeReleaseIds`` (or
   *  ``null`` when that is empty). Every pre-multi-select reader — the legacy
   *  picker, ``useReleaseScope`` with the flag off, saved views — keeps reading
   *  this field and keeps working, including after the flag is rolled back. */
  activeReleaseId: string | null
  /** The full multi-select selection (VIZ-303): distinct, at most
   *  ``RELEASE_CAP``, ``[]`` for no filter. Read it through
   *  ``selectReleaseIds``, which tolerates a writer that set only the scalar. */
  activeReleaseIds: string[]
  /**
   * The project the selection belongs to. Stored so a project switch can be
   * detected even when it happens in another tab or across a reload — the
   * selection alone cannot tell you which project it came from, and a
   * persisted id from a previous project is precisely the stale state that
   * empties every page.
   */
  scopedProjectId: string | null

  /** Select a release within *projectId*, or clear with ``null``. */
  setActiveRelease: (releaseId: string | null, projectId: string | null) => void
  /** Select several releases within *projectId* (``[]`` clears). Duplicates
   *  and anything past ``RELEASE_CAP`` are dropped; returns the values dropped
   *  for being over the cap, so a caller can say so. */
  setActiveReleases: (releaseIds: readonly string[], projectId: string | null) => string[]
  /** Clear the filter. */
  clearRelease: () => void
  /**
   * Reconcile against the currently active project. Returns true when a stale
   * selection was dropped, so a caller can tell the user rather than leaving
   * them wondering where their filter went.
   */
  syncToProject: (projectId: string | null) => boolean
}

/** Distinct non-empty values in first-seen order, split at the cap. */
export function capDistinct(
  values: readonly unknown[],
  cap: number,
): { kept: string[]; overCap: string[] } {
  const seen = new Set<string>()
  const kept: string[] = []
  const overCap: string[] = []
  for (const v of values) {
    if (typeof v !== 'string' || v === '' || seen.has(v)) continue
    seen.add(v)
    if (kept.length < cap) kept.push(v)
    else overCap.push(v)
  }
  return { kept, overCap }
}

const NO_IDS: string[] = []

/**
 * The selection as a list, robust to a writer that only knows the scalar.
 *
 * ``setState({ activeReleaseId })`` from pre-v1 code (existing tests do exactly
 * that) leaves ``activeReleaseIds`` stale. The scalar is the one field every
 * writer maintains, so when the two disagree the scalar wins. Returns a STORED
 * array whenever it can, so a selector built on it keeps a stable identity.
 */
export function selectReleaseIds(
  s: Pick<ReleaseStore, 'activeReleaseId' | 'activeReleaseIds'>,
): string[] {
  const ids = Array.isArray(s.activeReleaseIds) ? s.activeReleaseIds : NO_IDS
  if (s.activeReleaseId === null) return NO_IDS
  if (ids[0] === s.activeReleaseId) return ids
  return [s.activeReleaseId]
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** A release id that may be restored: a UUID or the `unattributed` sentinel
 *  (must equal `UNATTRIBUTED` in `lib/scopeUrl` and the backend). */
export function isRestorableReleaseId(value: unknown): value is string {
  return typeof value === 'string' && (value === 'unattributed' || UUID_RE.test(value))
}

/**
 * Read a saved entry — ANY shape, trusting nothing (security N3). Saved ids are
 * later interpolated into URL paths (`/api/v1/releases/<id>`), so only UUIDs
 * and the sentinel survive; a non-string field reads as absent rather than
 * throwing. A pre-VIZ-303 entry (scalar only) becomes a one-element list; the
 * scalar is re-derived as the list's first id, the invariant every writer
 * keeps.
 */
export function mergeReleaseFilter<T extends object>(persisted: unknown, current: T): T {
  const state = (persisted && typeof persisted === 'object' ? persisted : {}) as Record<string, unknown>
  const listed: unknown[] = Array.isArray(state.activeReleaseIds)
    ? state.activeReleaseIds
    : state.activeReleaseId !== undefined && state.activeReleaseId !== null
      ? [state.activeReleaseId]
      : []
  const { kept } = capDistinct(listed.filter(isRestorableReleaseId), RELEASE_CAP)
  return {
    ...current,
    activeReleaseId: kept[0] ?? null,
    activeReleaseIds: kept,
    scopedProjectId: kept.length > 0 && typeof state.scopedProjectId === 'string' ? state.scopedProjectId : null,
  }
}

export const useReleaseStore = create<ReleaseStore>()(
  persist(
    (set, get) => ({
      // Default: no release filter. Every page then behaves exactly as it did
      // before the release axis existed, which is what makes this shippable
      // without touching a single page's default rendering.
      activeReleaseId: null,
      activeReleaseIds: [],
      scopedProjectId: null,

      setActiveRelease: (releaseId, projectId) =>
        set({
          activeReleaseId: releaseId,
          activeReleaseIds: releaseId ? [releaseId] : [],
          scopedProjectId: releaseId ? projectId : null,
        }),

      setActiveReleases: (releaseIds, projectId) => {
        const { kept, overCap } = capDistinct(releaseIds, RELEASE_CAP)
        set({
          activeReleaseId: kept[0] ?? null,
          activeReleaseIds: kept,
          scopedProjectId: kept.length > 0 ? projectId : null,
        })
        return overCap
      },

      clearRelease: () => set({ activeReleaseId: null, activeReleaseIds: [], scopedProjectId: null }),

      syncToProject: (projectId) => {
        const { activeReleaseId, scopedProjectId } = get()
        if (activeReleaseId === null) return false

        // No project pinned (the ALL_PROJECTS sentinel arrives here as null
        // from the caller) — a release cannot apply, so drop it.
        // Or the project changed — the id belongs to a different project.
        if (projectId === null || projectId !== scopedProjectId) {
          set({ activeReleaseId: null, activeReleaseIds: [], scopedProjectId: null })
          return true
        }
        return false
      },
    }),
    {
      name: RELEASE_FILTER_STORAGE_KEY,
      version: RELEASE_STORE_VERSION,
      // Only an unreleased local build of this branch ever wrote version 1
      // (same shape); take it as-is — `merge` validates every field.
      migrate: (persisted) => persisted,
      merge: mergeReleaseFilter,
      // Persist the pair, never the selection alone: a restored id with no
      // record of its project cannot be validated, and would silently filter
      // whichever project happened to be active on the next load.
      partialize: (s) => ({
        activeReleaseId: s.activeReleaseId,
        activeReleaseIds: s.activeReleaseIds,
        scopedProjectId: s.scopedProjectId,
      }),
    },
  ),
)

// Logout forgets the selection (security M1; store/logoutReset.ts).
onLogoutReset(() => {
  useReleaseStore.getState().clearRelease()
  useReleaseStore.persist.clearStorage()
})
