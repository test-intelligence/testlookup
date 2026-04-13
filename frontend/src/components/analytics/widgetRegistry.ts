/**
 * Analytics Widget Registry — central catalog of visualization templates + instances.
 *
 * Templates (WidgetDef) define what visualizations are available.
 * Instances (VisualizationInstance) define what's placed on a page with per-instance config.
 * Existing widget IDs serve as template IDs for backward compatibility.
 */

export type ChartType = 'line' | 'bar' | 'area' | 'pie' | 'gauge' | 'metric' | 'table' | 'stacked_bar' | 'donut'

export interface WidgetDef {
  id: string
  label: string
  description: string
  pages: string[]
  chartType: ChartType
  allowedChartTypes?: ChartType[]   // alternate chart types user can pick
  dataSource: string
  defaultEnabled: boolean
  defaultHeight?: number
}

/** A placed visualization on a page — references a template with optional overrides. */
export interface VisualizationInstance {
  instanceId: string                          // unique per placement (UUID)
  templateId: string                          // references WidgetDef.id
  title?: string                              // user override (undefined = use template label)
  chartType?: ChartType                       // override template's default chartType
  metricVariant?: string                      // e.g. "sum" | "avg" | "rate"
  filters?: Record<string, unknown>           // instance-level filters
}

/**
 * Full widget catalog. Widgets are filtered by page at render time.
 */
export const WIDGET_CATALOG: WidgetDef[] = [
  // ── Dashboard widgets ────────────────────────────────────────
  { id: 'pass_fail_trend',     label: 'Pass/Fail Trend',          description: 'Execution trend showing passed, failed, and skipped tests over time',              pages: ['dashboard', 'trends'], chartType: 'line',        dataSource: 'trends',   defaultEnabled: true },
  { id: 'execution_volume',    label: 'Test Volume Growth',       description: 'Cumulative test execution volume as area chart',                                   pages: ['dashboard', 'trends'], chartType: 'area',        dataSource: 'trends',   defaultEnabled: true },
  { id: 'total_executions_kpi', label: 'Total Executions',        description: 'Total test executions in the selected period',                                     pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  { id: 'avg_pass_rate_kpi',   label: 'Avg Pass Rate',            description: 'Average pass rate across all runs',                                                pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  { id: 'active_defects_kpi',  label: 'Active Defects',           description: 'Number of unresolved defects',                                                     pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  { id: 'flaky_tests_kpi',     label: 'Flaky Tests',              description: 'Tests exhibiting non-deterministic pass/fail behavior',                             pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  { id: 'new_failures_kpi',    label: 'New Failures (24h)',       description: 'New test failures in the last 24 hours',                                           pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  { id: 'avg_duration_kpi',    label: 'Avg Run Duration',         description: 'Average test run execution time',                                                  pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  { id: 'readiness_summary',   label: 'Release Readiness',        description: 'Current release readiness signal (GO/AMBER/RED)',                                  pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  { id: 'pass_rate_gauge',     label: 'Pass Rate Gauge',          description: 'Radial gauge showing current pass rate',                                           pages: ['dashboard'],           chartType: 'gauge',       dataSource: 'summary',  defaultEnabled: false },

  // ── Trends widgets ───────────────────────────────────────────
  { id: 'daily_breakdown',     label: 'Daily Breakdown',          description: 'Stacked bar of passed/failed/skipped per day',                                     pages: ['trends'],              chartType: 'stacked_bar', dataSource: 'trends',   defaultEnabled: true },
  { id: 'pass_rate_trend',     label: 'Pass Rate Trend',          description: 'Daily pass rate % line chart',                                                     pages: ['trends'],              chartType: 'line',        dataSource: 'trends',   defaultEnabled: true },
  { id: 'cumulative_volume',   label: 'Cumulative Volume',        description: 'Total test volume growth area chart',                                              pages: ['trends'],              chartType: 'area',        dataSource: 'trends',   defaultEnabled: true },
  { id: 'failure_rate',        label: 'Failure Rate',             description: 'Daily failure rate % over time',                                                   pages: ['trends'],              chartType: 'line',        dataSource: 'trends',   defaultEnabled: false },
  { id: 'broken_trend',        label: 'Broken Tests',             description: 'Broken test count trend bar chart',                                                pages: ['trends'],              chartType: 'bar',         dataSource: 'trends',   defaultEnabled: false },
  { id: 'skipped_trend',       label: 'Skipped Trend',            description: 'Skipped test count trend over time',                                               pages: ['trends'],              chartType: 'line',        dataSource: 'trends',   defaultEnabled: false },
  { id: 'status_pie',          label: 'Status Distribution',      description: 'Pie chart of overall status distribution',                                         pages: ['trends'],              chartType: 'pie',         dataSource: 'trends',   defaultEnabled: false },

  // ── Coverage widgets ─────────────────────────────────────────
  { id: 'top_suites_bar',      label: 'Top Suites by Volume',     description: 'Horizontal bar chart of top test suites by execution count',                       pages: ['coverage'],            chartType: 'bar',         dataSource: 'coverage', defaultEnabled: true },
  { id: 'pass_rate_by_suite',  label: 'Pass Rate by Suite',       description: 'Suite-level pass rates as horizontal bars',                                        pages: ['coverage'],            chartType: 'bar',         dataSource: 'coverage', defaultEnabled: true },
  { id: 'coverage_kpis',       label: 'Coverage Summary',         description: 'Key coverage metrics: unique tests, suites, executions, avg pass rate',            pages: ['coverage'],            chartType: 'metric',      dataSource: 'coverage', defaultEnabled: true },

  // ── Defects widgets ──────────────────────────────────────────
  { id: 'open_vs_resolved_pie', label: 'Open vs Resolved',        description: 'Pie chart showing defect resolution status distribution',                          pages: ['defects'],             chartType: 'pie',         dataSource: 'defect_summary', defaultEnabled: true },
  { id: 'defect_category_bar',  label: 'Category Breakdown',      description: 'Bar chart of defect counts by failure category',                                   pages: ['defects'],             chartType: 'bar',         dataSource: 'defect_summary', defaultEnabled: true },
  { id: 'ai_confidence_dist',   label: 'AI Confidence Distribution', description: 'Distribution of AI analysis confidence scores across defects',                  pages: ['defects'],             chartType: 'bar',         dataSource: 'defect_summary', defaultEnabled: false },

  // ── Failures widgets ─────────────────────────────────────────
  { id: 'failure_category_pie',    label: 'Failure Categories',       description: 'Pie chart of failure category distribution',                                     pages: ['failures'],            chartType: 'pie',         dataSource: 'failure_categories', defaultEnabled: true },
  { id: 'top_failing_bar',         label: 'Top Failing Tests',        description: 'Bar chart of tests with highest failure counts',                                 pages: ['failures'],            chartType: 'bar',         dataSource: 'top_failing',        defaultEnabled: true, allowedChartTypes: ['bar', 'table'] },
  { id: 'flaky_leaderboard_table', label: 'Flaky Test Leaderboard',   description: 'Ranked list of flaky tests with failure rate',                                  pages: ['failures'],            chartType: 'table',       dataSource: 'flaky_tests',        defaultEnabled: true },
  { id: 'failures_kpis',           label: 'Failure Summary',          description: 'Key failure metrics: flaky count, top failing, categories, worst flakiness',    pages: ['failures'],            chartType: 'metric',      dataSource: 'failures_summary',   defaultEnabled: true },
  { id: 'failure_hotspot_bar',     label: 'Failure Hotspots',         description: 'Most common error patterns across test failures',                                pages: ['failures'],            chartType: 'bar',         dataSource: 'top_failing',        defaultEnabled: false },
  { id: 'flaky_trend_line',        label: 'Flaky Test Trend',         description: 'Flaky test count trend over time',                                              pages: ['failures'],            chartType: 'line',        dataSource: 'flaky_tests',        defaultEnabled: false },
]

/** Get widget definitions for a specific page. */
export function getPageWidgets(page: string): WidgetDef[] {
  return WIDGET_CATALOG.filter(w => w.pages.includes(page))
}

/** Get default enabled widget IDs for a page. */
export function getDefaultWidgetIds(page: string): string[] {
  return getPageWidgets(page).filter(w => w.defaultEnabled).map(w => w.id)
}

/** Look up a single widget definition by ID. */
export function getWidgetDef(id: string): WidgetDef | undefined {
  return WIDGET_CATALOG.find(w => w.id === id)
}

/**
 * crypto.randomUUID() only exists in secure contexts (HTTPS or localhost).
 * Homelab/plain-HTTP deployments (e.g. http://testlookup.local) don't expose
 * it, so fall back to a manual RFC-4122 v4 generator. crypto.getRandomValues
 * is available in insecure contexts too, and Math.random is the last resort.
 */
function randomUUID(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  const bytes = new Uint8Array(16)
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80 // variant 10
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

/** Create a new visualization instance from a template. */
export function createInstance(
  templateId: string,
  overrides?: Partial<Omit<VisualizationInstance, 'instanceId' | 'templateId'>>,
): VisualizationInstance {
  return {
    instanceId: randomUUID(),
    templateId,
    ...overrides,
  }
}

/** Get the effective label for an instance (user title or template label). */
export function getInstanceLabel(instance: VisualizationInstance): string {
  if (instance.title) return instance.title
  return getWidgetDef(instance.templateId)?.label ?? instance.templateId
}

/** Get the effective chart type for an instance. */
export function getInstanceChartType(instance: VisualizationInstance): ChartType | undefined {
  if (instance.chartType) return instance.chartType
  return getWidgetDef(instance.templateId)?.chartType
}

/**
 * Backward compat: convert a legacy widget ID array to instances.
 * Each ID becomes a default instance with no user overrides.
 */
export function migrateWidgetIds(ids: string[]): VisualizationInstance[] {
  return ids.map(id => createInstance(id))
}

/** Get default instances for a page (from defaultEnabled templates). */
export function getDefaultInstances(page: string): VisualizationInstance[] {
  return getPageWidgets(page)
    .filter(w => w.defaultEnabled)
    .map(w => createInstance(w.id))
}

/** Max visualization instances per page. */
export const MAX_INSTANCES_PER_PAGE = 12
