/**
 * The Visualization Upgrade rollout flags whose rows still exist.
 *
 * The source of truth is `contracts/viz/flags.json` at the repo root: the six
 * keys migration `0192` seeded. Migration `0196` (Phase D, F1) deleted the rows
 * of the four nothing reads (`viz_chart_data_api`, `viz_advanced_charts` and
 * `viz_three_d` shipped; `viz_customize` was never read), and the file marks
 * them `"retired_by": "0196"`. `vizFlags.test.ts` holds this object to the
 * file's UNRETIRED entries — same keys, same order — and the backend's
 * `app/core/viz_flags.py` is held to it the same way.
 *
 * The two left are off for good (owner decision 2026-10-04): the report-chrome
 * slot and the multi-filter runtime still read them, and they go with that
 * code. Nothing here turns one on.
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
} as const

/** A flag key as the backend and the feature-flag API spell it. */
export type VizFlagKey = (typeof VIZ_FLAGS)[keyof typeof VIZ_FLAGS]

/** Every live flag key, in `flags.json` order. */
export const VIZ_FLAG_KEYS: readonly VizFlagKey[] = Object.values(VIZ_FLAGS)
