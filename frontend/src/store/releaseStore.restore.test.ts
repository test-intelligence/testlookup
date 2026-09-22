/**
 * What the release store restores from `localStorage` is untrusted text: a
 * hand edit, another app on the origin, or a corrupted write. Release ids are
 * later interpolated into URL PATHS (`/api/v1/releases/<id>` lookups), so a
 * restored id must be a UUID or the `unattributed` sentinel before it is used
 * at all (security N3). Anything else is dropped at hydration.
 *
 * Also: the saved entry stays readable by the pre-VIZ-303 store (rollback).
 * That store is `persist` at version 0 with no `migrate`; zustand DISCARDS an
 * entry whose version differs and logs an error. So the multi-select store
 * keeps writing version 0 in a superset shape — the scalar `activeReleaseId`
 * is always the first id — and upgrades a v0 entry on read (`merge`), not by
 * a version bump.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

const PROJECT = 'aaaaaaaa-0000-4000-8000-000000000001'
const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'

async function restore(state: Record<string, unknown>, version = 0) {
  localStorage.setItem('tl.release-filter', JSON.stringify({ state, version }))
  vi.resetModules()
  const mod = await import('./releaseStore')
  await mod.useReleaseStore.persist.rehydrate()
  return mod
}

beforeEach(() => {
  localStorage.clear()
})

describe('restored release ids are validated (N3)', () => {
  it('drops ids that are not a UUID or the sentinel; keeps the rest in order', async () => {
    const { useReleaseStore, selectReleaseIds } = await restore({
      activeReleaseId: '../../admin',
      activeReleaseIds: ['../../admin', R1, 'unattributed', 'not-a-uuid', R2, 7],
      scopedProjectId: PROJECT,
    })
    const s = useReleaseStore.getState()
    expect(s.activeReleaseIds).toEqual([R1, 'unattributed', R2])
    expect(s.activeReleaseId).toBe(R1)
    expect(selectReleaseIds(s)).toEqual([R1, 'unattributed', R2])
    expect(s.scopedProjectId).toBe(PROJECT)
  })

  it('a pre-VIZ-303 (v0) entry with a bad scalar restores as no filter', async () => {
    const { useReleaseStore } = await restore({ activeReleaseId: 'x/../y', scopedProjectId: PROJECT })
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
    expect(useReleaseStore.getState().activeReleaseIds).toEqual([])
    expect(useReleaseStore.getState().scopedProjectId).toBeNull()
  })

  it('a non-string scalar does not throw and restores as no filter', async () => {
    const { useReleaseStore } = await restore({ activeReleaseId: 42, activeReleaseIds: { 0: R1 }, scopedProjectId: 5 })
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
    expect(useReleaseStore.getState().activeReleaseIds).toEqual([])
    expect(useReleaseStore.getState().scopedProjectId).toBeNull()
  })

  it('a v0 entry with a valid scalar is upgraded to a one-element list', async () => {
    const { useReleaseStore } = await restore({ activeReleaseId: R1, scopedProjectId: PROJECT })
    expect(useReleaseStore.getState().activeReleaseIds).toEqual([R1])
    expect(useReleaseStore.getState().activeReleaseId).toBe(R1)
  })
})

describe('rollback: the pre-VIZ-303 store still reads what this one writes', () => {
  it('writes version 0 with the scalar first id', async () => {
    vi.resetModules()
    const { useReleaseStore } = await import('./releaseStore')
    useReleaseStore.getState().setActiveReleases([R2, R1], PROJECT)
    const raw = JSON.parse(localStorage.getItem('tl.release-filter') as string)
    expect(raw.version).toBe(0)
    expect(raw.state.activeReleaseId).toBe(R2)
    expect(raw.state.scopedProjectId).toBe(PROJECT)

    // main's store, verbatim in shape: persist v0, no migrate, no merge.
    type Legacy = { activeReleaseId: string | null; scopedProjectId: string | null }
    const legacy = create<Legacy>()(
      persist((): Legacy => ({ activeReleaseId: null, scopedProjectId: null }), {
        name: 'tl.release-filter',
        partialize: (s) => ({ activeReleaseId: s.activeReleaseId, scopedProjectId: s.scopedProjectId }),
      }),
    )
    await legacy.persist.rehydrate()
    expect(legacy.getState().activeReleaseId).toBe(R2)
    expect(legacy.getState().scopedProjectId).toBe(PROJECT)
  })

  it('an entry a v1 build of this branch already wrote is still read', async () => {
    const { useReleaseStore } = await restore(
      { activeReleaseId: R1, activeReleaseIds: [R1, R2], scopedProjectId: PROJECT },
      1,
    )
    expect(useReleaseStore.getState().activeReleaseIds).toEqual([R1, R2])
  })
})
