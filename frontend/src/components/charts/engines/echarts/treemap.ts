/**
 * Treemap chart type (VIZ-502, the coverage map): registers `TreemapChart` on
 * the shared base. Loaded only through `loadChartEngine('treemap')`, so a page
 * without a coverage map never downloads it.
 *
 * Registration only. The option (no `formatter` of any kind, plan F6), the
 * colour ramps and the keyboard highlight (spike S1: keyboard index k is
 * `dataIndex: k + 1`, the virtual root is 0) are built elsewhere; this
 * module's owner adds any component the treemap needs to the `use` list.
 */
import { TreemapChart } from 'echarts/charts'
import { echarts } from './core'

echarts.use([TreemapChart])

export { echarts }
