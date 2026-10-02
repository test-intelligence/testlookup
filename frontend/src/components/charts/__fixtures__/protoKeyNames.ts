/**
 * Category names that are also `Object.prototype` members, for the chart
 * gallery (wired by the integrator) and the kit's tests.
 *
 * Suite and test names come from ingested CI files, so `constructor` and
 * `toString` are real names a chart has to draw as themselves. Before the
 * own-property lookups (`ownLabel` in `chartText.ts`) the bar labels, the
 * table headers and the generated summary read the inherited members instead.
 * A plain category series with NO `x_labels`, exactly as `chart-data` sends
 * `group_by=suite`: every label lookup falls through to the name itself.
 */
import type { SeriesChart } from '@/lib/viz/contracts'

export const PROTOTYPE_KEY_NAMES = ['constructor', '__proto__', 'toString', 'hasOwnProperty'] as const

/** Ranked bars (failures by suite), one bar per prototype-member name. */
export const protoKeyNamesSeries: SeriesChart = {
  kind: 'series',
  dimensions: ['suite'],
  x_type: 'category',
  series: [
    {
      key: 'failures',
      label: 'Failures',
      points: PROTOTYPE_KEY_NAMES.map((x, i) => ({ x, y: 12 - i * 3, n: 12 - i * 3 })),
    },
  ],
}
