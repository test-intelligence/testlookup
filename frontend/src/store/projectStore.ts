import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Project } from '@/types/projects'

/** Special sentinel value for "view all projects aggregated" mode */
export const ALL_PROJECTS_ID = 'all' as const

interface ProjectStore {
  activeProjectId: string | null
  activeProject: Project | null
  setActiveProject: (project: Project | null) => void
  /** Switch to aggregate "All Projects" mode — no specific project is active */
  setAllProjects: () => void
}

export const useProjectStore = create<ProjectStore>()(
  persist(
    (set) => ({
      activeProjectId: null,
      activeProject: null,
      setActiveProject: (project) =>
        set({ activeProject: project, activeProjectId: project?.id ?? null }),
      setAllProjects: () =>
        set({ activeProject: null, activeProjectId: ALL_PROJECTS_ID }),
    }),
    { name: 'testlookup-active-project' }
  )
)
