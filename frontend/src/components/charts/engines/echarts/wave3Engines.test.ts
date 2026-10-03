/**
 * The two Wave-3 engine modules (FK0 stubs, filled by their owners) really
 * register their chart types on the shared base: a treemap and a scatter,
 * drawn by the REAL ECharts through the server-side SVG renderer (jsdom has no
 * canvas), come out as marks. Without the `use` call ECharts drops an unknown
 * series type silently, so "the module loads" alone would prove nothing.
 */
import { describe, expect, it, vi } from 'vitest'
import { SVGRenderer } from 'echarts/renderers'
import { echarts as treemapEngine } from './treemap'
import { echarts as scatterEngine } from './scatter'

treemapEngine.use([SVGRenderer])
treemapEngine.setPlatformAPI({ createCanvas: () => null as unknown as HTMLCanvasElement })

function draw(engine: typeof treemapEngine, option: object): string {
  const chart = engine.init(null, null, { renderer: 'svg', ssr: true, width: 200, height: 120 })
  chart.setOption({ animation: false, ...option })
  const svg = chart.renderToSVGString()
  chart.dispose()
  return svg
}

describe('Wave 3 engine modules', () => {
  it('both expose the ONE shared base, not a second copy of ECharts', () => {
    expect(scatterEngine).toBe(treemapEngine)
  })

  it('treemap.ts registers the treemap series', () => {
    const svg = draw(treemapEngine, {
      series: [{ type: 'treemap', nodeClick: false, breadcrumb: { show: false }, data: [{ name: 'a', value: 3 }, { name: 'b', value: 1 }] }],
    })
    // Two leaf rectangles at least (ECharts draws each node as a path).
    expect((svg.match(/<path/g) ?? []).length).toBeGreaterThanOrEqual(2)
  })

  it('scatter.ts registers the scatter series with its mark line', () => {
    const svg = draw(scatterEngine, {
      xAxis: { type: 'log' },
      yAxis: { type: 'value' },
      series: [{ type: 'scatter', data: [[10, 5], [100, 50]], markLine: { silent: true, symbol: 'none', data: [{ xAxis: 30 }] } }],
    })
    expect((svg.match(/<path/g) ?? []).length).toBeGreaterThanOrEqual(3)
  })

  // ECharts' brush preprocessor always writes `option.toolbox`; with no toolbox
  // component imported, a dev build then logs "Component toolbox is used but
  // not imported" on every scatter, which failed every page and gallery spec
  // that asserts a clean console (I-G, Wave 3). The scatter module drops that
  // synthetic toolbox again; the brush is driven by `takeGlobalCursor`, never a toolbox.
  it('a scatter with a toolbox-free brush logs nothing and keeps no toolbox in its model', () => {
    const logged: string[] = []
    const spies = (['warn', 'error', 'log'] as const).map((level) =>
      vi.spyOn(console, level).mockImplementation((...args: unknown[]) => void logged.push(args.map(String).join(' '))),
    )
    try {
      const chart = scatterEngine.init(null, null, { renderer: 'svg', ssr: true, width: 200, height: 120 })
      chart.setOption({
        animation: false,
        xAxis: { type: 'log' },
        yAxis: { type: 'value' },
        brush: { toolbox: [], xAxisIndex: 0, yAxisIndex: 0 },
        series: [{ type: 'scatter', data: [[10, 5], [100, 50]] }],
      })
      const selected: unknown[] = []
      chart.on('brushselected', (params) => void selected.push(params))
      chart.dispatchAction({ type: 'takeGlobalCursor', key: 'brush', brushOption: { brushType: 'rect', brushMode: 'single' } })
      chart.dispatchAction({ type: 'brush', areas: [{ brushType: 'rect', xAxisIndex: 0, yAxisIndex: 0, coordRange: [[5, 50], [0, 10]] }] })
      const model = chart.getOption() as { toolbox?: unknown }
      chart.dispose()
      expect(logged.filter((line) => /toolbox/i.test(line))).toEqual([])
      expect(model.toolbox).toBeUndefined()
      // The brush still works with no toolbox: the programmatic rectangle is reported.
      expect(selected.length).toBeGreaterThan(0)
    } finally {
      for (const spy of spies) spy.mockRestore()
    }
  })
})
