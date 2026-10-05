/**
 * The six Visualization Upgrade rollout flags.
 *
 * The source of truth is `contracts/viz/flags.json` at the repo root.
 * `vizFlags.test.ts` holds this object to that file — same keys, same order —
 * and the backend's `app/core/viz_flags.py` is held to it the same way, so a
 * key cannot be renamed on one side only. Migration `0192` seeds every flag
 * DISABLED; `0195` turns on `chartDataApi`, `advancedCharts` and `threeD`
 * (`reportContext` and `multiFilters` stay off: the panel was removed).
 * Nothing here turns one on.
 *
 * Since Phase D (S6) the frontend READS only `reportContext` and
 * `multiFilters`. `chartDataApi`, `advancedCharts` and `threeD` shipped: no
 * chart asks them any more (the `useCatalogueRollout` seam is deleted), and
 * `customize` is not read either. All six keys stay here because this object
 * mirrors the rows migration 0192 seeded, and `vizFlags.test.ts` holds it to
 * `flags.json`; the rows are retired by a later migration (Phase D F1),
 * which removes the unread keys from both sides together. Do not add a new
 * reader of the three shipped keys.
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
  /** Generic chart-data, heatmap, coverage-map, failure-groups and rows endpoints (VIZ-E2). Shipped; unread since Phase D. */
  chartDataApi: 'viz_chart_data_api',
  /** Heatmap, coverage map, failure groups, scatter, Sankey and explorer views (VIZ-E5). Shipped; unread since Phase D. */
  advancedCharts: 'viz_advanced_charts',
  /** Chart customisation panel and saved-views manager (VIZ-E6). Not read. */
  customize: 'viz_customize',
  /** Opt-in 3D scatter view (VIZ-508). Shipped; unread since Phase D. */
  threeD: 'viz_three_d',
} as const

/** A flag key as the backend and the feature-flag API spell it. */
export type VizFlagKey = (typeof VIZ_FLAGS)[keyof typeof VIZ_FLAGS]

/** Every flag key, in `flags.json` order. */
export const VIZ_FLAG_KEYS: readonly VizFlagKey[] = Object.values(VIZ_FLAGS)
