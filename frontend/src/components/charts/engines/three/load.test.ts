/**
 * `loadScatter3D` (VIZ-508): the registry's rules for the 3D engine — loaded
 * once through `lazyChartEngine` (which turns a missing chunk into a
 * `StaleBuildError`, tested in its own file), and a failure not cached.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('./scatter3d', () => ({ mountScatter3D: vi.fn() }))

const lazy = vi.hoisted(() => ({ calls: 0, fail: null as Error | null }))
vi.mock('../lazyChartEngine', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lazyChartEngine')>()),
  lazyChartEngine: (loader: () => Promise<unknown>) => {
    lazy.calls += 1
    return lazy.fail ? Promise.reject(lazy.fail) : loader()
  },
}))

import { StaleBuildError } from '../lazyChartEngine'
import { loadScatter3D, resetScatter3DCache } from './load'

afterEach(() => {
  resetScatter3DCache()
  lazy.calls = 0
  lazy.fail = null
})

describe('loadScatter3D', () => {
  it('loads once, through lazyChartEngine, and hands back the engine', async () => {
    const first = loadScatter3D()
    expect(loadScatter3D()).toBe(first)
    expect(typeof (await first).mountScatter3D).toBe('function')
    expect(lazy.calls).toBe(1)
  })

  it('a failed load (a stale build) is not cached: the next attempt asks again', async () => {
    lazy.fail = new StaleBuildError(new Error('Failed to fetch dynamically imported module'))
    const failed = loadScatter3D()
    await expect(failed).rejects.toBeInstanceOf(StaleBuildError)
    lazy.fail = null
    const again = loadScatter3D()
    expect(again).not.toBe(failed)
    await expect(again).resolves.toHaveProperty('mountScatter3D')
    expect(lazy.calls).toBe(2)
  })
})
