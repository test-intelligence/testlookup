import { describe, expect, it } from 'vitest'
import {
  STALE_BUILD_ACTION,
  STALE_BUILD_MESSAGE,
  StaleBuildError,
  isChunkLoadFailure,
  isStaleBuildError,
  lazyChartEngine,
} from './lazyChartEngine'

// The exact rejections each browser gives a dynamic import whose chunk 404s.
const CHUNK_404 = [
  new TypeError('Failed to fetch dynamically imported module: https://app/assets/heatmap-OLD.js'),
  new TypeError('error loading dynamically imported module: https://app/assets/heatmap-OLD.js'),
  new TypeError('Importing a module script failed.'),
  new Error('Unable to preload CSS for /assets/heatmap-OLD.css'),
  Object.assign(new Error('Loading chunk 12 failed.'), { name: 'ChunkLoadError' }),
]

describe('lazyChartEngine', () => {
  it('passes a loaded module straight through', async () => {
    const engine = { init: () => null }
    await expect(lazyChartEngine(async () => engine)).resolves.toBe(engine)
  })

  it.each(CHUNK_404.map((e) => [e.message, e] as const))(
    'turns a missing chunk into a typed stale-build error (%s)',
    async (_message, error) => {
      const rejection = lazyChartEngine(() => Promise.reject(error))
      await expect(rejection).rejects.toBeInstanceOf(StaleBuildError)
      const caught = await rejection.catch((e: unknown) => e)
      expect(isStaleBuildError(caught)).toBe(true)
      expect((caught as StaleBuildError).kind).toBe('stale-build')
      expect((caught as StaleBuildError).message).toBe(STALE_BUILD_MESSAGE)
      expect((caught as { cause?: unknown }).cause).toBe(error)
    },
  )

  it('re-throws a module that loaded but threw — that is a bug, not a stale build', async () => {
    const bug = new ReferenceError('heatmapOption is not defined')
    const caught = await lazyChartEngine(() => Promise.reject(bug)).catch((e: unknown) => e)
    expect(caught).toBe(bug)
    expect(isStaleBuildError(caught)).toBe(false)
  })

  it('classifies only Error instances', () => {
    expect(isChunkLoadFailure('Failed to fetch dynamically imported module')).toBe(false)
    expect(isChunkLoadFailure(null)).toBe(false)
    expect(isStaleBuildError(new Error(STALE_BUILD_MESSAGE))).toBe(false)
  })

  it('names the frame copy the chart shell renders', () => {
    expect(STALE_BUILD_MESSAGE).toBe('A new version is available')
    expect(STALE_BUILD_ACTION).toBe('Reload')
  })
})
