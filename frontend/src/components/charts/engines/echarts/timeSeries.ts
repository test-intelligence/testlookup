/**
 * Time-series chart type (VIZ-403): registers `LineChart`, `BarChart` and the
 * `MarkLineComponent` (release markers) on the shared base. Loaded only through
 * `loadChartEngine('timeSeries')`, and only for a series past
 * `SVG_POINT_LIMIT` points — a short series stays in Recharts and never
 * downloads any of this.
 */
import { BarChart, LineChart } from 'echarts/charts'
import { MarkLineComponent } from 'echarts/components'
import { echarts } from './core'

echarts.use([LineChart, BarChart, MarkLineComponent])

export { echarts }
