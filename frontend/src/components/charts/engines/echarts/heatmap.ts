/**
 * Heatmap chart type: registers `HeatmapChart` and `VisualMapComponent` on the
 * shared base. Loaded only through `loadChartEngine('heatmap')`.
 */
import { HeatmapChart } from 'echarts/charts'
import { VisualMapComponent } from 'echarts/components'
import { echarts } from './core'

echarts.use([HeatmapChart, VisualMapComponent])

export { echarts }
