/**
 * The shared ECharts base (VIZ-103, ADR decision 2): `echarts/core` plus ONLY
 * the canvas renderer, grid, tooltip, legend and aria.
 *
 * Chart TYPES are not registered here — each lives in its own module next to
 * this one (`heatmap.ts`, …) and registers itself, so a page with only a
 * heatmap never downloads treemap, sankey or scatter code.
 *
 * Never import this module statically from anything eager; reach it through
 * `loadChartEngine()` in `../registry.ts`. `npm run check:bundle` fails if
 * ECharts or zrender code lands in an eagerly preloaded chunk.
 */
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'

echarts.use([CanvasRenderer, GridComponent, TooltipComponent, LegendComponent, AriaComponent])

export { echarts }
