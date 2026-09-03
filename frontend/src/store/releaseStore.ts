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

/** Selecting nothing. Not a sentinel — the genuine absence of a filter. */
export const NO_RELEASE = null

interface ReleaseStore {
  /** Selected release, or ``null`` for "all releases" (the default). */
  activeReleaseId: string | null
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
  /** Clear the filter. */
  clearRelease: () => void
  /**
   * Reconcile against the currently active project. Returns true when a stale
   * selection was dropped, so a caller can tell the user rather than leaving
   * them wondering where their filter went.
   */
  syncToProject: (projectId: string | null) => boolean
}

export const useReleaseStore = create<ReleaseStore>()(
  persist(
    (set, get) => ({
      // Default: no release filter. Every page then behaves exactly as it did
      // before the release axis existed, which is what makes this shippable
      // without touching a single page's default rendering.
      activeReleaseId: null,
      scopedProjectId: null,

      setActiveRelease: (releaseId, projectId) =>
        set({ activeReleaseId: releaseId, scopedProjectId: releaseId ? projectId : null }),

      clearRelease: () => set({ activeReleaseId: null, scopedProjectId: null }),

      syncToProject: (projectId) => {
        const { activeReleaseId, scopedProjectId } = get()
        if (activeReleaseId === null) return false

        // No project pinned (the ALL_PROJECTS sentinel arrives here as null
        // from the caller) — a release cannot apply, so drop it.
        // Or the project changed — the id belongs to a different project.
        if (projectId === null || projectId !== scopedProjectId) {
          set({ activeReleaseId: null, scopedProjectId: null })
          return true
        }
        return false
      },
    }),
    {
      name: 'tl.release-filter',
      // Persist the pair, never the selection alone: a restored id with no
      // record of its project cannot be validated, and would silently filter
      // whichever project happened to be active on the next load.
      partialize: (s) => ({
        activeReleaseId: s.activeReleaseId,
        scopedProjectId: s.scopedProjectId,
      }),
    },
  ),
)
