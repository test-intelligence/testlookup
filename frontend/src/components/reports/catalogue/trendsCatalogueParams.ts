/**
 * The Trends catalogue's request parameters and the words the page shares
 * with it, in a module of their own (TRND-06).
 *
 * Why not in `TrendsCatalogue.tsx`: that module is a LAZY chunk the page
 * loads only when a tab draws it. `TrendsPage` needs the suite series'
 * parameters for the Suite pass rates sparklines (the SAME request, so SWR
 * serves both from one answer), and a static import of the catalogue module
 * would make its chunk eager. `TrendsCatalogue.tsx` re-exports everything here,
 * so its importers and tests are unchanged.
 */
import type { CatalogParams } from '@/components/charts/chartCatalogSources'

/** "Pass rate by suite"'s request: pass rate, day x suite, the top 7 + Other. */
export const SUITE_SERIES_PARAMS: CatalogParams = { metric: 'pass_rate', group_by: ['day', 'suite'], top_n: 7 }
export const DURATION_P50_PARAMS: CatalogParams = { metric: 'duration_p50', group_by: 'day' }
export const DURATION_P95_PARAMS: CatalogParams = { metric: 'duration_p95', group_by: 'day' }

export const SUITE_SERIES_TITLE = 'Pass rate by suite'

/** TRND-10: the two customisable Trends charts, as `filters.charts` keys (`SAVED_VIEW_CHART_KEY`). */
export const SUITE_SERIES_CHART_KEY = 'trends.suite-series'
export const COMPARE_CHART_KEY = 'trends.compare'
export const TRENDS_CHART_KEYS: readonly string[] = [SUITE_SERIES_CHART_KEY, COMPARE_CHART_KEY]

/** How many lines "Pass rate by suite" draws before "Other" (`SUITE_SERIES_PARAMS.top_n`). */
export const SUITE_SERIES_TOP_N = 7

export const ONE_DAY_SUITES_REASON =
  'a one-day window has one point per suite, which is not a trend; pick 7 days or more to compare suites over time'

/** TRND-06: why a Suite pass rates row draws "—" instead of its sparkline. */
export const NOT_AMONG_BUSIEST = `Not among the ${SUITE_SERIES_TOP_N} busiest suites drawn in "${SUITE_SERIES_TITLE}"`
