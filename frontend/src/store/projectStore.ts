import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Project } from '@/types/projects'
import { projectsService } from '@/services/projectsService'

/** Special sentinel value for "view all projects aggregated" mode */
export const ALL_PROJECTS_ID = 'all' as const

interface ProjectStore {
  activeProjectId: string | null
  activeProject: Project | null
  /** Shared project list — populated via refreshProjects() and reused by TopBar + pages. */
  projects: Project[]
  setActiveProject: (project: Project | null) => void
  /** Switch to aggregate "All Projects" mode — no specific project is active */
  setAllProjects: () => void
  /** Fetch the latest project list from the server and update all subscribers. */
  refreshProjects: () => Promise<Project[]>
}

export const useProjectStore = create<ProjectStore>()(
  persist(
    (set) => ({
      // Default to ALL_PROJECTS_ID so fresh sessions (no persisted localStorage)
      // can browse aggregate data instead of getting stuck on "Select a project"
      // empty states. The TopBar dropdown lets the user narrow to a specific
      // project from there. Originally this was ``null``, but every page that
      // gated on ``!project && !isAllProjects`` (Overview, Runs, Failures,
      // Deep Investigation) became unusable for first-time users — a regression
      // surfaced 2026-05-15.
      activeProjectId: ALL_PROJECTS_ID,
      activeProject: null,
      projects: [],
      setActiveProject: (project) =>
        // ``null`` from the picker maps to ALL_PROJECTS_ID so we never land
        // back in the locked "no project selected" state.
        set({
          activeProject: project,
          activeProjectId: project?.id ?? ALL_PROJECTS_ID,
        }),
      setAllProjects: () =>
        set({ activeProject: null, activeProjectId: ALL_PROJECTS_ID }),
      refreshProjects: async () => {
        const list = await projectsService.list()
        set((state) => {
          const next: Partial<ProjectStore> = { projects: list }
          // Validate the persisted active selection against the fresh list.
          // Stale localStorage (e.g. project_id from a different deployment's
          // database) would otherwise cause every mutation to fail with a
          // foreign-key violation on the backend. When stale, fall back to
          // ALL_PROJECTS_ID (NOT null) — null leaves the project-gated pages
          // showing "No project selected" with no way for the user to
          // self-recover beyond manually picking from the TopBar dropdown.
          if (
            state.activeProjectId &&
            state.activeProjectId !== ALL_PROJECTS_ID &&
            !list.find((p) => p.id === state.activeProjectId)
          ) {
            next.activeProject = null
            next.activeProjectId = ALL_PROJECTS_ID
          } else if (state.activeProjectId && state.activeProjectId !== ALL_PROJECTS_ID) {
            // Refresh the cached project (name may have changed)
            const fresh = list.find((p) => p.id === state.activeProjectId)
            if (fresh) next.activeProject = fresh
          } else if (state.activeProjectId == null) {
            // Persisted state from a prior version may carry literal null —
            // promote it to ALL_PROJECTS_ID on read so the user lands on a
            // working page instead of the locked empty state.
            next.activeProjectId = ALL_PROJECTS_ID
            next.activeProject = null
          }
          return next as ProjectStore
        })
        return list
      },
    }),
    {
      name: 'testlookup-active-project',
      // Only persist the active selection; always refetch the list on load
      // so the dropdown reflects server truth after create/archive.
      partialize: (state) => ({
        activeProjectId: state.activeProjectId,
        activeProject: state.activeProject,
      }),
    }
  )
)
