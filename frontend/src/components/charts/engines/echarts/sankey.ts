/**
 * Sankey chart type (VIZ-507, the run-compare status flows): registers
 * `SankeyChart` on the shared base. Loaded only through
 * `loadChartEngine('sankey')`, so a page without status flows never
 * downloads it. Registration only; the option is built in `statusSankeyModel`.
 */
import { SankeyChart } from 'echarts/charts'
import { echarts } from './core'

echarts.use([SankeyChart])

export { echarts }
