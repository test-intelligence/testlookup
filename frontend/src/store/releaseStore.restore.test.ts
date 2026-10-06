/**
 * What the release store restores from `localStorage` is untrusted text: a
 * hand edit, another app on the origin, or a corrupted write. Release ids are
 * later interpolated into URL PATHS (`/api/v1/releases/<id>` lookups), so a
 * restored id must be a UUID or the `unattributed` sentinel before it is used
 * at all (security N3). Anything else is dropped at hydration.
 *
 * Also: entries written by the VIZ-303 multi-select builds (an
 * `activeReleaseIds` list beside the scalar, version 0, and a local version 1)
 * still restore the same release now the store is single-release again
 * (Phase D, M1-M3), and the store keeps writing version 0, which any build
 * reads.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
  it('a valid scalar restores with its project', async () => {
    const { useReleaseStore } = await restore({ activeReleaseId: R1, scopedProjectId: PROJECT })
    expect(useReleaseStore.getState().activeReleaseId).toBe(R1)
    expect(useReleaseStore.getState().scopedProjectId).toBe(PROJECT)
  })

  it('the unattributed sentinel restores', async () => {
    const { useReleaseStore } = await restore({ activeReleaseId: 'unattributed', scopedProjectId: PROJECT })
    expect(useReleaseStore.getState().activeReleaseId).toBe('unattributed')
  })

  it('a bad scalar with no list restores as no filter', async () => {
    const { useReleaseStore } = await restore({ activeReleaseId: 'x/../y', scopedProjectId: PROJECT })
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
    expect(useReleaseStore.getState().scopedProjectId).toBeNull()
  })

  it('a non-string scalar and a non-array list do not throw, and restore as no filter', async () => {
    const { useReleaseStore } = await restore({ activeReleaseId: 42, activeReleaseIds: { 0: R1 }, scopedProjectId: 5 })
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
    expect(useReleaseStore.getState().scopedProjectId).toBeNull()
  })
})

describe('entries the multi-select builds wrote still restore', () => {
  it('the scalar they kept equal to the first id wins', async () => {
    const { useReleaseStore } = await restore({ activeReleaseId: R2, activeReleaseIds: [R2, R1], scopedProjectId: PROJECT })
    expect(useReleaseStore.getState().activeReleaseId).toBe(R2)
    expect(useReleaseStore.getState().scopedProjectId).toBe(PROJECT)
  })

  it('a bad scalar falls back to the first VALID id of the list', async () => {
    const { useReleaseStore } = await restore({
      activeReleaseId: '../../admin',
      activeReleaseIds: ['../../admin', 'not-a-uuid', 7, R1, R2],
      scopedProjectId: PROJECT,
    })
    expect(useReleaseStore.getState().activeReleaseId).toBe(R1)
    expect(useReleaseStore.getState().scopedProjectId).toBe(PROJECT)
  })

  it('an entry a local version-1 build wrote is still read', async () => {
    const { useReleaseStore } = await restore({ activeReleaseId: R1, activeReleaseIds: [R1, R2], scopedProjectId: PROJECT }, 1)
    expect(useReleaseStore.getState().activeReleaseId).toBe(R1)
  })

  it('writes version 0 with only the pair: what every build reads', async () => {
    vi.resetModules()
    const { useReleaseStore } = await import('./releaseStore')
    useReleaseStore.getState().setActiveRelease(R2, PROJECT)
    const raw = JSON.parse(localStorage.getItem('tl.release-filter') as string)
    expect(raw.version).toBe(0)
    expect(raw.state).toEqual({ activeReleaseId: R2, scopedProjectId: PROJECT })
  })
})
