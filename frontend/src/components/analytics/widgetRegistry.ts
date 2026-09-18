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
// Module-private: every caller goes through getPageWidgets/getDefaultWidgetIds,
// so the catalog itself is not part of this registry's public surface.
const WIDGET_CATALOG: WidgetDef[] = [
  // ── Dashboard widgets ────────────────────────────────────────
  { id: 'total_executions_kpi', label: 'Total Executions',        description: 'Total test executions in the selected period',                                     pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  { id: 'avg_pass_rate_kpi',   label: 'Avg Pass Rate',            description: 'Average pass rate across all runs',                                                pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  { id: 'active_defects_kpi',  label: 'Active Defects',           description: 'Number of unresolved defects',                                                     pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  { id: 'flaky_tests_kpi',     label: 'Flaky Tests',              description: 'Tests exhibiting non-deterministic pass/fail behavior',                             pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  { id: 'new_failures_kpi',    label: 'New Failures (24h)',       description: 'New test failures in the last 24 hours',                                           pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  { id: 'infra_failures_kpi',  label: 'Infra-Caused Failures',    description: 'Share of window failures AI-classified as infrastructure-caused (failure-kind triad)', pages: ['dashboard'],      chartType: 'metric',      dataSource: 'failure_categories', defaultEnabled: true },
  { id: 'avg_duration_kpi',    label: 'Avg Run Duration',         description: 'Average test run execution time',                                                  pages: ['dashboard'],           chartType: 'metric',      dataSource: 'summary',  defaultEnabled: true },
  // ── Trends widgets ───────────────────────────────────────────
  { id: 'daily_breakdown',     label: 'Daily Breakdown',          description: 'Stacked bar of passed/failed/skipped per day',                                     pages: ['trends'],              chartType: 'stacked_bar', dataSource: 'trends',   defaultEnabled: true },
  { id: 'trends_kpis',         label: 'Trend Metrics',            description: 'Pass rate, cadence, execution, suite, and latest-run metrics',                    pages: ['trends'],              chartType: 'metric',      dataSource: 'trends',   defaultEnabled: true },
  { id: 'pass_rate_trend',     label: 'Pass Rate Trend',          description: 'Daily pass rate % line chart',                                                     pages: ['trends'],              chartType: 'line',        dataSource: 'trends',   defaultEnabled: true },
  // ── Coverage widgets ─────────────────────────────────────────
  { id: 'pass_rate_by_suite',  label: 'Suite Coverage Breakdown', description: 'Suite-level volume and pass-rate bars',                                             pages: ['coverage'],            chartType: 'bar',         dataSource: 'coverage', defaultEnabled: true },
  { id: 'coverage_kpis',       label: 'Coverage Summary',         description: 'Key coverage metrics: unique tests, suites, executions, avg pass rate',            pages: ['coverage'],            chartType: 'metric',      dataSource: 'coverage', defaultEnabled: true },

  // ── Defects widgets ──────────────────────────────────────────
  { id: 'defect_kpis',          label: 'Defect Summary',          description: 'Key defect totals and resolution metrics',                                          pages: ['defects'],             chartType: 'metric',      dataSource: 'defect_summary', defaultEnabled: true },
  { id: 'defect_category_bar',  label: 'Category Breakdown',      description: 'Bar chart of defect counts by failure category',                                   pages: ['defects'],             chartType: 'bar',         dataSource: 'defect_summary', defaultEnabled: true },

  // ── Failures widgets ─────────────────────────────────────────
  { id: 'failure_category_pie',    label: 'Failure Categories',       description: 'Pie chart of failure category distribution',                                     pages: ['failures'],            chartType: 'pie',         dataSource: 'failure_categories', defaultEnabled: true },
  { id: 'top_failing_bar',         label: 'Top Failing Tests',        description: 'Bar chart of tests with highest failure counts',                                 pages: ['failures'],            chartType: 'bar',         dataSource: 'top_failing',        defaultEnabled: true, allowedChartTypes: ['bar', 'table'] },
  { id: 'flaky_leaderboard_table', label: 'Flaky Test Leaderboard',   description: 'Ranked list of flaky tests with failure rate',                                  pages: ['failures'],            chartType: 'table',       dataSource: 'flaky_tests',        defaultEnabled: true },
  { id: 'failures_kpis',           label: 'Failure Summary',          description: 'Key failure metrics: flaky count, top failing, categories, worst flakiness',    pages: ['failures'],            chartType: 'metric',      dataSource: 'failures_summary',   defaultEnabled: true },
]

/** Get widget definitions for a specific page. */
export function getPageWidgets(page: string): WidgetDef[] {
  return WIDGET_CATALOG.filter(w => w.pages.includes(page))
}

/** Get default enabled widget IDs for a page. */
export function getDefaultWidgetIds(page: string): string[] {
  return getPageWidgets(page).filter(w => w.defaultEnabled).map(w => w.id)
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

/** Get default instances for a page (from defaultEnabled templates). */
export function getDefaultInstances(page: string): VisualizationInstance[] {
  return getPageWidgets(page)
    .filter(w => w.defaultEnabled)
    .map(w => createInstance(w.id))
}

/** Max visualization instances per page. */
export const MAX_INSTANCES_PER_PAGE = 12

const CHART_TYPES = new Set<ChartType>([
  'line', 'bar', 'area', 'pie', 'gauge', 'metric', 'table', 'stacked_bar', 'donut',
])

/**
 * Turn stored layout JSON into renderable instances for one page.
 *
 * Existing rows predate the v2 instance format, and stored JSON can outlive a
 * removed widget. Keep valid entries, drop foreign templates, repair duplicate
 * or missing instance IDs, and enforce the same limit as the picker. A wholly
 * malformed non-empty layout falls back to defaults instead of making a healthy
 * dashboard look empty. An intentional empty array remains empty.
 */
export function normalizeInstances(page: string, raw: unknown): VisualizationInstance[] {
  if (!Array.isArray(raw)) return getDefaultInstances(page)

  const catalog = new Map(getPageWidgets(page).map(widget => [widget.id, widget]))
  const seen = new Set<string>()
  const normalized: VisualizationInstance[] = []

  for (const entry of raw.slice(0, MAX_INSTANCES_PER_PAGE)) {
    const value = typeof entry === 'string'
      ? { templateId: entry }
      : entry && typeof entry === 'object'
        ? entry as Record<string, unknown>
        : null
    if (!value || typeof value.templateId !== 'string') continue

    const template = catalog.get(value.templateId)
    if (!template) continue

    let instanceId = typeof value.instanceId === 'string' && value.instanceId
      ? value.instanceId
      : randomUUID()
    if (seen.has(instanceId)) instanceId = randomUUID()
    seen.add(instanceId)

    const allowedChartTypes = new Set<ChartType>([
      template.chartType,
      ...(template.allowedChartTypes ?? []),
    ])
    const chartType = typeof value.chartType === 'string' && CHART_TYPES.has(value.chartType as ChartType)
      && allowedChartTypes.has(value.chartType as ChartType)
      ? value.chartType as ChartType
      : undefined
    const filters = value.filters && typeof value.filters === 'object' && !Array.isArray(value.filters)
      ? value.filters as Record<string, unknown>
      : undefined

    normalized.push({
      instanceId,
      templateId: value.templateId,
      ...(typeof value.title === 'string' ? { title: value.title } : {}),
      ...(chartType ? { chartType } : {}),
      ...(typeof value.metricVariant === 'string' ? { metricVariant: value.metricVariant } : {}),
      ...(filters ? { filters } : {}),
    })
  }

  return normalized.length > 0 || raw.length === 0 ? normalized : getDefaultInstances(page)
}
