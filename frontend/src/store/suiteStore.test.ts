import { beforeEach, describe, expect, it, vi } from 'vitest'

const PROJECT_A = 'aaaaaaaa-0000-4000-8000-000000000001'

async function freshStore() {
  vi.resetModules()
  return import('./suiteStore')
}

describe('suiteStore (VIZ-303)', () => {
  beforeEach(() => localStorage.clear())

  it('defaults to no filter', async () => {
    const { useSuiteStore } = await freshStore()
    expect(useSuiteStore.getState().activeSuiteNames).toEqual([])
    expect(useSuiteStore.getState().scopedProjectId).toBeNull()
  })

  it('caps at 50 and reports the 51st', async () => {
    const { useSuiteStore, SUITE_CAP } = await freshStore()
    expect(SUITE_CAP).toBe(50)
    const names = Array.from({ length: 51 }, (_, i) => `suite-${i}`)
    const dropped = useSuiteStore.getState().setActiveSuites(names, PROJECT_A)
    expect(useSuiteStore.getState().activeSuiteNames).toHaveLength(50)
    expect(dropped).toEqual(['suite-50'])
  })

  it('drops malformed names (empty, whitespace-only, over 500 code points) and duplicates', async () => {
    const { useSuiteStore } = await freshStore()
    const long = 'x'.repeat(501)
    const dropped = useSuiteStore.getState().setActiveSuites(['payments', '   ', long, 'payments', 'cart'], PROJECT_A)
    expect(useSuiteStore.getState().activeSuiteNames).toEqual(['payments', 'cart'])
    expect(dropped).toEqual(['   ', long])
  })

  it('records the project the selection was made in, and forgets it when cleared', async () => {
    const { useSuiteStore } = await freshStore()
    useSuiteStore.getState().setActiveSuites(['payments'], PROJECT_A)
    expect(useSuiteStore.getState().scopedProjectId).toBe(PROJECT_A)
    useSuiteStore.getState().clearSuites()
    expect(useSuiteStore.getState().scopedProjectId).toBeNull()
  })

  it('sanitises a corrupted persisted entry instead of sending it', async () => {
    localStorage.setItem(
      'tl.suite-filter',
      JSON.stringify({ state: { activeSuiteNames: ['ok', 7, '', 'ok', null], scopedProjectId: PROJECT_A }, version: 0 }),
    )
    const { useSuiteStore } = await freshStore()
    await useSuiteStore.persist.rehydrate()
    expect(useSuiteStore.getState().activeSuiteNames).toEqual(['ok'])
  })

  it('removeSuites removes only the named ones', async () => {
    const { useSuiteStore } = await freshStore()
    useSuiteStore.getState().setActiveSuites(['a', 'b', 'c'], PROJECT_A)
    useSuiteStore.getState().removeSuites(['b'])
    expect(useSuiteStore.getState().activeSuiteNames).toEqual(['a', 'c'])
  })
})
