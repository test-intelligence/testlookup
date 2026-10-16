/**
 * Time-series chart type (VIZ-403): registers `LineChart`, `BarChart`, the
 * `MarkLineComponent` (release markers, one-day timeline notes) and the
 * `MarkAreaComponent` (timeline-note ranges, TRND-04) on the shared base.
 * Loaded only through `loadChartEngine('timeSeries')`, and only for a series
 * past `SVG_POINT_LIMIT` points — a short series stays in Recharts and never
 * downloads any of this.
 */
import { BarChart, LineChart } from 'echarts/charts'
import { MarkAreaComponent, MarkLineComponent } from 'echarts/components'
import { echarts } from './core'

echarts.use([LineChart, BarChart, MarkLineComponent, MarkAreaComponent])

export { echarts }
