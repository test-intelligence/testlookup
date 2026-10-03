/**
 * The test scatter section's requests, words and data rules (VIZ-506), apart
 * from the component so each is unit-tested on its own and the section file
 * exports components only. Pure: no React.
 */
import type { DrillLevel, PointsChart } from '@/lib/viz/contracts'
import type { CatalogParams, CatalogResponse } from '@/components/charts/chartCatalogSources'
import { CHART_RESPONSE_ACCESSORS, isChartSeriesEmpty, type ChartAccessors } from '@/components/charts/chartState'
import { excludedTotal } from '@/components/charts/testScatter.model'
import type { RowsChartSpec } from './RowsPanel.model'
import type { ScatterSectionProps } from './sectionContracts'

// ── Requests ─────────────────────────────────────────────────────────────────

/** The fewest executions a test needs to be placed (the EPIC's 5; sent explicitly so the words match). */
export const SCATTER_MIN_EXECUTIONS = 5

/** The scatter's own parameters: the most failing tests survive the 2,000-point default limit. */
export const SCATTER_PARAMS: CatalogParams = { min_executions: SCATTER_MIN_EXECUTIONS, order: 'failures' }

/**
 * The chart a point's rows are drilled from: the failure rate per test. Its
 * rows are the EVALUATED executions, so their total reconciles with the
 * point's `n` (the endpoint's `reconciliation.mark_field` is `n` for a rate).
 * `group_by=test` needs a bound (one suite, or a `top_n`): Suite detail pins
 * its one suite; project-wide, `top_n` is that bound — the rows endpoint
 * validates it but selects by the test alone.
 */
export function scatterRowsChart(placement: ScatterSectionProps['placement']): RowsChartSpec {
  return placement === 'suite' ? { metric: 'failure_rate', groupBy: ['test'] } : { metric: 'failure_rate', groupBy: ['test'], topN: 1 }
}

// ── Words ────────────────────────────────────────────────────────────────────

export const SCATTER_TITLE = 'Test duration vs failure rate'
export const SCATTER_CHART_TYPE = 'Scatter chart'
export const ALL_PROJECTS_REASON = 'the scatter compares the tests of one project; pick a project to see it'
export const NO_TESTS_MESSAGE = 'No test ran in this window.'

/** The plot's height, px; the host's lazy placeholder adds the chrome (`SCATTER_SECTION_MIN_HEIGHT`). */
export const SCATTER_HEIGHT = 320

/**
 * The body's height when every test was left out: the one sentence that says
 * why (two or three lines on a phone), not a plot-sized empty band (F-14).
 */
export const SCATTER_NOTHING_HEIGHT = 120

/** The frame body's height: the plot's, unless a response placed no test at all. */
export function scatterFrameHeight(chart: PointsChart | null): number {
  return chart !== null && chart.points.length === 0 ? SCATTER_NOTHING_HEIGHT : SCATTER_HEIGHT
}

/**
 * The response's accessors with ONE change: a scatter whose every test was
 * EXCLUDED is not "empty" (its exclusion counts are the whole answer).
 */
export const SCATTER_ACCESSORS: ChartAccessors<CatalogResponse<'test-scatter'>> = {
  ...CHART_RESPONSE_ACCESSORS,
  // Empty only when NOTHING was in scope: no point AND no excluded test.
  isEmpty: (value) => isChartSeriesEmpty(value.series) && excludedTotal(value.series.excluded) === 0,
}

/** The open rows panel's selectors when they are THIS section's: exactly one test. */
export function scatterRowsSelectors(rows: readonly DrillLevel[]): DrillLevel[] {
  return rows.length === 1 && rows[0].dimension === 'test' ? [rows[0]] : []
}
