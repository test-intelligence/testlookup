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
      activeProjectId: null,
      activeProject: null,
      projects: [],
      setActiveProject: (project) =>
        set({ activeProject: project, activeProjectId: project?.id ?? null }),
      setAllProjects: () =>
        set({ activeProject: null, activeProjectId: ALL_PROJECTS_ID }),
      refreshProjects: async () => {
        const list = await projectsService.list()
        set({ projects: list })
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
