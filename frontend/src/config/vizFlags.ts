/**
 * The six Visualization Upgrade rollout flags.
 *
 * The source of truth is `contracts/viz/flags.json` at the repo root.
 * `vizFlags.test.ts` holds this object to that file — same keys, same order —
 * and the backend's `app/core/viz_flags.py` is held to it the same way, so a
 * key cannot be renamed on one side only. Migration `0192` seeds every flag
 * DISABLED; nothing here turns one on.
 *
 * Keys use underscores, not dots: the flag API only accepts
 * `^[a-z][a-z0-9_]*$`, so a dotted key could never be recreated through it.
 *
 * Use the constant, never the string: `VIZ_FLAGS.multiFilters`, not
 * `'viz_multi_filters'`, so a rename is one edit and a typo is a type error.
 */
export const VIZ_FLAGS = {
  /** Report context header and metrics strip on report pages (VIZ-E3). */
  reportContext: 'viz_report_context',
  /** Multi-select release and suite filters, chips, summary and URL sync (VIZ-E3). */
  multiFilters: 'viz_multi_filters',
  /** Generic chart-data, heatmap, coverage-map, failure-groups and rows endpoints (VIZ-E2). */
  chartDataApi: 'viz_chart_data_api',
  /** Heatmap, coverage map, failure groups, scatter, Sankey and explorer views (VIZ-E5). */
  advancedCharts: 'viz_advanced_charts',
  /** Chart customisation panel and saved-views manager (VIZ-E6). */
  customize: 'viz_customize',
  /** Opt-in 3D scatter view (VIZ-508). */
  threeD: 'viz_three_d',
} as const

/** A flag key as the backend and the feature-flag API spell it. */
export type VizFlagKey = (typeof VIZ_FLAGS)[keyof typeof VIZ_FLAGS]

/** Every flag key, in `flags.json` order. */
export const VIZ_FLAG_KEYS: readonly VizFlagKey[] = Object.values(VIZ_FLAGS)
