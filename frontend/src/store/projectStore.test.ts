/**
 * projectStore — regression pin for the 2026-05-15 "no project selected"
 * fix. The store used to default ``activeProjectId`` to ``null`` and reset
 * it to ``null`` when ``refreshProjects`` couldn't find the persisted UUID
 * in the fresh list. Every page that gated on ``!project && !isAllProjects``
 * (Overview, Runs, Failures, Deep Investigation) then became unreachable
 * until the user manually picked from the TopBar dropdown.
 *
 * The fix promotes both code paths (fresh + stale) to ALL_PROJECTS_ID so
 * pages render in aggregate mode by default. These tests pin that contract.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockList = vi.fn()
vi.mock('@/services/projectsService', () => ({
  projectsService: { list: (...a: unknown[]) => mockList(...a) },
}))

// Re-import the store fresh per test so each starts from a clean module state.
// Zustand persists to ``localStorage`` via the ``persist`` middleware, so we
// also reset that between tests.
async function loadStore() {
  vi.resetModules()
  const mod = await import('./projectStore')
  return mod
}

describe('projectStore — ALL_PROJECTS_ID fallback', () => {
  beforeEach(() => {
    mockList.mockReset()
    localStorage.clear()
  })

  it('initial state is ALL_PROJECTS_ID, not null', async () => {
    const { useProjectStore, ALL_PROJECTS_ID } = await loadStore()
    expect(useProjectStore.getState().activeProjectId).toBe(ALL_PROJECTS_ID)
    expect(useProjectStore.getState().activeProject).toBeNull()
  })

  it('setActiveProject(null) lands on ALL_PROJECTS_ID, never null', async () => {
    const { useProjectStore, ALL_PROJECTS_ID } = await loadStore()
    useProjectStore.getState().setActiveProject(null)
    expect(useProjectStore.getState().activeProjectId).toBe(ALL_PROJECTS_ID)
  })

  it('refreshProjects: stale persisted UUID falls back to ALL_PROJECTS_ID', async () => {
    // Seed localStorage as if a prior session left a stale project pinned.
    localStorage.setItem(
      'testlookup-active-project',
      JSON.stringify({
        state: {
          activeProjectId: 'stale-uuid-that-no-longer-exists',
          activeProject: { id: 'stale-uuid-that-no-longer-exists', name: 'Gone', slug: 'gone' },
        },
        version: 0,
      }),
    )
    mockList.mockResolvedValue([
      { id: 'live-uuid-aaaa', name: 'Live Project', slug: 'live', is_active: true, created_at: '' },
    ])

    const { useProjectStore, ALL_PROJECTS_ID } = await loadStore()
    await useProjectStore.getState().refreshProjects()

    expect(useProjectStore.getState().activeProjectId).toBe(ALL_PROJECTS_ID)
    expect(useProjectStore.getState().activeProject).toBeNull()
  })

  it('refreshProjects: persisted UUID still in list is preserved', async () => {
    localStorage.setItem(
      'testlookup-active-project',
      JSON.stringify({
        state: {
          activeProjectId: 'live-uuid-aaaa',
          activeProject: { id: 'live-uuid-aaaa', name: 'Outdated Name', slug: 'live' },
        },
        version: 0,
      }),
    )
    const fresh = { id: 'live-uuid-aaaa', name: 'Renamed Live', slug: 'live', is_active: true, created_at: '' }
    mockList.mockResolvedValue([fresh])

    const { useProjectStore } = await loadStore()
    await useProjectStore.getState().refreshProjects()

    expect(useProjectStore.getState().activeProjectId).toBe('live-uuid-aaaa')
    // The cached project row is refreshed to reflect renames etc.
    expect(useProjectStore.getState().activeProject?.name).toBe('Renamed Live')
  })

  it('refreshProjects: legacy persisted null is promoted to ALL_PROJECTS_ID', async () => {
    // A user whose localStorage was written BEFORE the fix carries
    // activeProjectId=null. On first refresh, we should heal it.
    localStorage.setItem(
      'testlookup-active-project',
      JSON.stringify({
        state: { activeProjectId: null, activeProject: null },
        version: 0,
      }),
    )
    mockList.mockResolvedValue([])

    const { useProjectStore, ALL_PROJECTS_ID } = await loadStore()
    await useProjectStore.getState().refreshProjects()

    expect(useProjectStore.getState().activeProjectId).toBe(ALL_PROJECTS_ID)
  })
})
