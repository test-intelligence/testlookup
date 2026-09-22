/**
 * VIZ-303: the release store grows a multi-select list without breaking a
 * single reader of the scalar it has always had.
 *
 * The persisted `tl.release-filter` entry shipped at `version: 0` holding only
 * `activeReleaseId`. Reading it must ADD `activeReleaseIds` and leave the
 * scalar alone (on read, via `merge` — no version bump, see
 * releaseStore.restore.test.ts), and every writer must keep the scalar equal to the list's
 * first element — that equality is what lets the flag be rolled back with the
 * legacy picker reading migrated storage.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const PROJECT_A = 'aaaaaaaa-0000-4000-8000-000000000001'
const PROJECT_B = 'bbbbbbbb-0000-4000-8000-000000000002'
const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'

async function freshStore() {
  vi.resetModules()
  return import('./releaseStore')
}

describe('releaseStore v1 (multi-select)', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('migrates a version-0 scalar into the list and keeps the scalar', async () => {
    localStorage.setItem(
      'tl.release-filter',
      JSON.stringify({ state: { activeReleaseId: R1, scopedProjectId: PROJECT_A }, version: 0 }),
    )
    const { useReleaseStore } = await freshStore()
    await useReleaseStore.persist.rehydrate()

    const s = useReleaseStore.getState()
    expect(s.activeReleaseId).toBe(R1)
    expect(s.activeReleaseIds).toEqual([R1])
    expect(s.scopedProjectId).toBe(PROJECT_A)
  })

  it('migrates an empty version-0 entry to an empty list', async () => {
    localStorage.setItem(
      'tl.release-filter',
      JSON.stringify({ state: { activeReleaseId: null, scopedProjectId: null }, version: 0 }),
    )
    const { useReleaseStore } = await freshStore()
    await useReleaseStore.persist.rehydrate()

    expect(useReleaseStore.getState().activeReleaseIds).toEqual([])
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
  })

  it('persists (still at version 0, rollback-safe) with the list alongside the scalar', async () => {
    const { useReleaseStore } = await freshStore()
    useReleaseStore.getState().setActiveReleases([R1, R2], PROJECT_A)
    const raw = JSON.parse(localStorage.getItem('tl.release-filter') as string)
    expect(raw.version).toBe(0)
    expect(raw.state).toEqual({ activeReleaseId: R1, activeReleaseIds: [R1, R2], scopedProjectId: PROJECT_A })
  })

  it('keeps the scalar equal to the first id, so the legacy picker still reads it after a rollback', async () => {
    const { useReleaseStore } = await freshStore()
    useReleaseStore.getState().setActiveReleases([R2, R1], PROJECT_A)
    expect(useReleaseStore.getState().activeReleaseId).toBe(R2)

    useReleaseStore.getState().setActiveReleases([], PROJECT_A)
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
    expect(useReleaseStore.getState().scopedProjectId).toBeNull()
  })

  it('the legacy single setter resets the list to that one release', async () => {
    const { useReleaseStore } = await freshStore()
    useReleaseStore.getState().setActiveReleases([R1, R2], PROJECT_A)
    useReleaseStore.getState().setActiveRelease(R2, PROJECT_A)
    expect(useReleaseStore.getState().activeReleaseIds).toEqual([R2])
    useReleaseStore.getState().setActiveRelease(null, PROJECT_A)
    expect(useReleaseStore.getState().activeReleaseIds).toEqual([])
  })

  it('caps at 20, drops duplicates, and reports what the cap cut', async () => {
    const { useReleaseStore, RELEASE_CAP } = await freshStore()
    expect(RELEASE_CAP).toBe(20)
    const ids = Array.from({ length: 23 }, (_, i) => `r-${i}`)
    const overCap = useReleaseStore.getState().setActiveReleases([...ids, 'r-0', 'r-1'], PROJECT_A)
    expect(useReleaseStore.getState().activeReleaseIds).toHaveLength(20)
    expect(overCap).toEqual(['r-20', 'r-21', 'r-22'])
  })

  it('a project change drops the whole list, not just the scalar', async () => {
    const { useReleaseStore } = await freshStore()
    useReleaseStore.getState().setActiveReleases([R1, R2], PROJECT_A)
    expect(useReleaseStore.getState().syncToProject(PROJECT_B)).toBe(true)
    expect(useReleaseStore.getState().activeReleaseIds).toEqual([])
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
  })

  it('selectReleaseIds trusts the scalar when a legacy writer left the list stale', async () => {
    const { selectReleaseIds } = await freshStore()
    expect(selectReleaseIds({ activeReleaseId: R2, activeReleaseIds: [R1] })).toEqual([R2])
    expect(selectReleaseIds({ activeReleaseId: null, activeReleaseIds: [R1] })).toEqual([])
    const stored = [R1, R2]
    // Consistent → the STORED array, same identity.
    expect(selectReleaseIds({ activeReleaseId: R1, activeReleaseIds: stored })).toBe(stored)
  })
})
