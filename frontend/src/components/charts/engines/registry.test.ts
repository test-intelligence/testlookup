import { afterEach, describe, expect, it, vi } from 'vitest'

// jsdom has no canvas: the engine module is mocked, and the test asserts the
// registry reaches it through a dynamic import and caches only success.
// `echarts` is a getter so each load is observable and can be made to fail
// (vitest caches a mock module's factory result across tests).
const heatmapModule = vi.hoisted(() => ({ loads: 0, fail: null as Error | null }))
vi.mock('./echarts/heatmap', () => ({
  get echarts() {
    heatmapModule.loads += 1
    if (heatmapModule.fail) throw heatmapModule.fail
    return { init: vi.fn(), marker: 'heatmap-engine' }
  },
}))

const wave3Modules = vi.hoisted(() => ({ loads: { treemap: 0, scatter: 0 } }))
vi.mock('./echarts/treemap', () => ({
  get echarts() {
    wave3Modules.loads.treemap += 1
    return { marker: 'treemap-engine' }
  },
}))
vi.mock('./echarts/scatter', () => ({
  get echarts() {
    wave3Modules.loads.scatter += 1
    return { marker: 'scatter-engine' }
  },
}))

describe('loadChartEngine', () => {
  afterEach(async () => {
    const { resetChartEngineCache } = await import('./registry')
    resetChartEngineCache()
    wave3Modules.loads = { treemap: 0, scatter: 0 }
    heatmapModule.fail = null
    vi.resetModules()
  })

  it('registers heatmap as a lazily loaded type', async () => {
    const { CHART_ENGINE_TYPES, loadChartEngine } = await import('./registry')
    expect(CHART_ENGINE_TYPES).toEqual(['heatmap', 'timeSeries', 'treemap', 'scatter', 'sankey'])
    const engine = await loadChartEngine('heatmap')
    expect((engine as unknown as { marker: string }).marker).toBe('heatmap-engine')
  })

  it('loads the Wave 3 types from their own modules, each on first use only', async () => {
    const { loadChartEngine } = await import('./registry')
    expect(wave3Modules.loads).toEqual({ treemap: 0, scatter: 0 })
    expect((await loadChartEngine('treemap')) as unknown).toEqual({ marker: 'treemap-engine' })
    expect(wave3Modules.loads).toEqual({ treemap: 1, scatter: 0 })
    expect((await loadChartEngine('scatter')) as unknown).toEqual({ marker: 'scatter-engine' })
    expect(wave3Modules.loads).toEqual({ treemap: 1, scatter: 1 })
  })

  it('loads a type once and hands every caller the same engine', async () => {
    const { loadChartEngine } = await import('./registry')
    const [a, b] = await Promise.all([loadChartEngine('heatmap'), loadChartEngine('heatmap')])
    expect(a).toBe(b)
    expect(await loadChartEngine('heatmap')).toBe(a)
  })

  it('does not cache a failed load, so the next attempt asks again', async () => {
    heatmapModule.fail = new Error('engine module failed to evaluate')
    const { loadChartEngine } = await import('./registry')
    await expect(loadChartEngine('heatmap')).rejects.toThrow()
    heatmapModule.fail = null
    const loadsBefore = heatmapModule.loads
    await expect(loadChartEngine('heatmap')).resolves.toBeDefined()
    expect(heatmapModule.loads).toBeGreaterThan(loadsBefore)
  })
})
