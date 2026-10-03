/**
 * Scatter chart type (VIZ-506, the test scatter): registers `ScatterChart`,
 * `MarkLineComponent` (the median guides) and `BrushComponent` (the rectangle
 * selection, driven by `takeGlobalCursor` with NO toolbox: spike S2) on the
 * shared base. Loaded only through `loadChartEngine('scatter')`.
 *
 * Registration only. Spike S2 is binding on the option: never ECharts'
 * `large` mode (its size callback draws nothing and `brushSelected` is always
 * empty); plain points up to the 5,000 cap.
 */
import { ScatterChart } from 'echarts/charts'
import { BrushComponent, MarkLineComponent } from 'echarts/components'
import { echarts } from './core'

echarts.use([ScatterChart, MarkLineComponent, BrushComponent])

// ECharts' brush preprocessor always writes an `option.toolbox` (for the
// brush's toolbox buttons, which we never show: the brush is driven by
// `takeGlobalCursor`). With no toolbox component imported, a dev build then
// logs "Component toolbox is used but not imported" on every scatter, and the
// specs that assert a clean console fail. Preprocessors run in registration
// order, so this one runs after the brush's and drops that synthetic toolbox
// from any option that carries a brush. Importing the toolbox to hush the
// warning would cost about 11 kB gzip (spike S2).
echarts.registerPreprocessor((option) => {
  const raw = option as { brush?: unknown; toolbox?: unknown } | undefined
  if (raw && raw.brush) delete raw.toolbox
})

export { echarts }
