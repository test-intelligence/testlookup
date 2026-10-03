/**
 * The drill ladder's model (VIZ-602 / 603, plan 3.4.2): which level the page's
 * drill path names, what that level asks the server, what each of its marks
 * offers, where a drill goes and which rows a mark opens. Pure: no React, no
 * DOM, no recharts.
 *
 * The ladder ("Results by suite", Failure analysis page) walks two
 * dimensions, in either order, then the tests, then the executions:
 *
 *   path               chart (chart-data)                                 a mark drills to
 *   []                 executions by suite x status (stacked)             a segment: suite + status; a bar: suite
 *   [suite]            executions by status, in that suite               the status
 *   [status]           <status> executions by suite                      the suite
 *   [suite, status]    <status> executions by test, top 20, in the suite  (leaf) the test's rows only
 *
 * Every value is the chart's KEY (a lower-cased suite, a status), never its
 * label: it is echoed to the server as `suite_name` / the metric / a
 * `bucket_<dimension>` selector. A suite level is a `suite_name` that REPLACES
 * the page's suite filter, so it must be one of the suites that filter allows
 * (a hand-edited link could name any suite: that level is cut, and said).
 *
 * The rows a mark opens (`rows=` in the URL) are this section's only when
 * their dimensions are the shape this level writes; another section on the
 * page (the scatter's `[test]`, a failure group's signature) owns any other
 * selection. The leaf's rows are `[test, status]` for exactly that reason, and
 * they are legal: `group_by=test&group_by=status` with one `suite_name`.
 */
import { VIZ_STATUSES, type DrillLevel, type EnvelopeMeta, type SeriesChart, type VizStatus } from '@/lib/viz/contracts'
import { normalizeScope, type ScopeValue } from '@/lib/scopeParams'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import { markSelectors, type ChartMark, type MarkIntent } from '@/components/charts/marks'
import { STATUS_ENCODING } from '@/components/charts/tokens'
import type { BarVariant } from '@/components/charts/BarChart'
import type { RowsChartSpec } from './RowsPanel.model'

/** The section's title at the top of the ladder: the EPIC's words ("Given 'Results by suite' is shown"). */
export const DRILL_TITLE = 'Results by suite'
/** The breadcrumb's root. */
export const ROOT_CRUMB = 'All suites'
/** How many tests the leaf level asks for. */
export const LADDER_TOP_N = 20
/** The notice's reason for a drill-down the page could not open (the values are the sentences). */
export const DRILL_NOTICE_REASON = 'Drill-down in this link'

export const LADDER_DROP_WORDS = {
  unsupported: 'This view drills by suite and status only, so the rest of the drill-down in this link was not applied.',
  outOfScope: 'A suite in this link’s drill-down is not in the page’s suite filter, so the view opens above it.',
} as const

/** chart-data's keys for "no suite" and for the roll-up of the rest: neither is one suite a request can name. */
const NO_SUITE = '(none)'
const OTHER = '__other__'

export type LadderLevel =
  | { kind: 'suites' }
  | { kind: 'statuses'; suite: string }
  | { kind: 'status-suites'; status: VizStatus }
  | { kind: 'tests'; suite: string; status: VizStatus }

export interface LadderRead {
  level: LadderLevel
  /** How many levels of the path the ladder applied (the rest were cut). */
  used: number
  /** Why levels were cut, as sentences for the page's notice. */
  dropped: string[]
}

const fold = (value: string) => value.trim().toLowerCase()
const STATUS_SET = new Set<string>(VIZ_STATUSES)

/** The page's suite filter as folded names (`[]` = no filter). */
function pageSuites(suiteFilter: ScopeValue): string[] {
  return normalizeScope(suiteFilter).map(fold)
}

/**
 * The level the (already C5-validated) drill path names, for a page whose
 * suite filter is `suiteFilter`. Read level by level and cut at the first
 * level this ladder cannot apply.
 */
export function ladderFromPath(path: readonly DrillLevel[], suiteFilter: ScopeValue): LadderRead {
  const allowed = pageSuites(suiteFilter)
  let suite: string | null = null
  let status: VizStatus | null = null
  let used = 0
  const dropped: string[] = []
  for (const step of path) {
    if (step.dimension === 'suite' && suite === null) {
      const key = fold(step.value)
      if (!key || key === NO_SUITE || key === OTHER || (allowed.length > 0 && !allowed.includes(key))) {
        dropped.push(LADDER_DROP_WORDS.outOfScope)
        break
      }
      suite = step.value
    } else if (step.dimension === 'status' && status === null && STATUS_SET.has(step.value)) {
      status = step.value as VizStatus
    } else {
      dropped.push(LADDER_DROP_WORDS.unsupported)
      break
    }
    used += 1
  }
  const level: LadderLevel =
    suite !== null && status !== null
      ? { kind: 'tests', suite, status }
      : suite !== null
        ? { kind: 'statuses', suite }
        : status !== null
          ? { kind: 'status-suites', status }
          : { kind: 'suites' }
  return { level, used, dropped }
}

/** The level's own suite (the `suite_name` it REPLACES the page's filter with), or `null` for the page's. */
export function levelSuite(level: LadderLevel): string | null {
  return level.kind === 'statuses' || level.kind === 'tests' ? level.suite : null
}

/** The chart-data parameters a level adds to the page scope (`catalogueParams` `extra`). */
export function levelRequest(level: LadderLevel): CatalogParams {
  switch (level.kind) {
    case 'suites':
      return { metric: 'executions', group_by: ['suite', 'status'] }
    case 'statuses':
      return { metric: 'executions', group_by: ['status'] }
    case 'status-suites':
      return { metric: level.status, group_by: ['suite'] }
    case 'tests':
      return { metric: level.status, group_by: ['test'], top_n: LADDER_TOP_N }
  }
}

/** The dimensions a level's response must carry: its `group_by`, in order. */
export function levelDimensions(level: LadderLevel): string[] {
  return [...(levelRequest(level).group_by as readonly string[])]
}

/**
 * Whether a drawn response is THIS level's. The chart keeps the previous
 * response on screen while the next loads (`keepPreviousData`); read as this
 * level, its keys would drill into the wrong bucket. The dimensions tell the
 * levels apart, and a suite-scoped level's response says which suite it
 * applied (`meta.scope.suites`, the server's own folded names).
 */
export function answersLevel(series: SeriesChart | null, meta: EnvelopeMeta | null, level: LadderLevel): boolean {
  if (!series || series.kind !== 'series') return false
  const want = levelDimensions(level)
  if (series.dimensions.length !== want.length || series.dimensions.some((d, i) => d !== want[i])) return false
  const suite = levelSuite(level)
  if (suite === null) return true
  const applied = meta?.scope?.suites ?? []
  return applied.length === 1 && fold(applied[0]) === fold(suite)
}

const statusWord = (status: VizStatus) => STATUS_ENCODING[status].label

export interface LevelChart {
  title: string
  variant: BarVariant
  /** The category axis's title. */
  dimension: string
  valueAxisLabel: string
  topN?: number
}

/** How a level is drawn. `suiteLabel` is the suite as the reader saw it (untrusted text). */
export function levelChart(level: LadderLevel, suiteLabel: string): LevelChart {
  switch (level.kind) {
    case 'suites':
      return { title: DRILL_TITLE, variant: 'stacked', dimension: 'Suite', valueAxisLabel: 'Executions' }
    case 'statuses':
      return { title: `Results in ${suiteLabel} by status`, variant: 'ranked', dimension: 'Status', valueAxisLabel: 'Executions' }
    case 'status-suites':
      return {
        title: `${statusWord(level.status)} results by suite`,
        variant: 'ranked',
        dimension: 'Suite',
        valueAxisLabel: `${statusWord(level.status)} executions`,
      }
    case 'tests':
      return {
        title: `${statusWord(level.status)} tests in ${suiteLabel}`,
        variant: 'ranked',
        dimension: 'Test',
        valueAxisLabel: `${statusWord(level.status)} executions`,
        topN: LADDER_TOP_N,
      }
  }
}

const canNameSuite = (mark: ChartMark) => mark.dimension === 'suite' && fold(mark.value) !== NO_SUITE && fold(mark.value) !== OTHER

/**
 * What a mark on this level offers, in order (the first is a click's and
 * Enter's). The leaf offers "View rows" only (EPIC edge "leaf level reached");
 * "no suite" cannot be named in a request, so it offers its rows only; the
 * page filter is offered for suite marks, and only when the host says the
 * page has one (`viz_multi_filters`, `useCrossFilter`).
 */
export function ladderIntents(level: LadderLevel, mark: ChartMark, filterOffered: boolean): MarkIntent[] {
  if (fold(mark.value) === OTHER) return []
  if (level.kind === 'tests') return mark.dimension === 'test' ? ['rows'] : []
  const filter: MarkIntent[] = filterOffered ? ['filter'] : []
  if (level.kind === 'statuses') return mark.dimension === 'status' ? ['drill', 'rows'] : []
  if (mark.dimension !== 'suite') return []
  return canNameSuite(mark) ? ['drill', 'rows', ...filter] : ['rows', ...filter]
}

/** The levels a drill on `mark` appends to the path: the mark's own, then its context (a segment's status). */
export function drillLevels(level: LadderLevel, mark: ChartMark): DrillLevel[] {
  if (level.kind === 'tests') return []
  return markSelectors(mark).filter((step) => step.dimension === 'suite' || step.dimension === 'status')
}

/** The chart a level's rows are drilled from: its metric and group_by (the rows endpoint validates both). */
export function rowsChart(level: LadderLevel): RowsChartSpec {
  switch (level.kind) {
    case 'suites':
      return { metric: 'executions', groupBy: ['suite', 'status'] }
    case 'statuses':
      return { metric: 'executions', groupBy: ['status'] }
    case 'status-suites':
      return { metric: level.status, groupBy: ['suite'] }
    case 'tests':
      // The test, AND the status: `[test]` alone is the scatter's selection.
      return { metric: level.status, groupBy: ['test', 'status'] }
  }
}

/** The rows selectors a mark opens on this level. */
export function rowsSelectors(level: LadderLevel, mark: ChartMark): DrillLevel[] {
  if (level.kind === 'tests') {
    return [
      { dimension: 'test', value: mark.value },
      { dimension: 'status', value: level.status },
    ]
  }
  return markSelectors(mark)
}

/**
 * The open panel's selectors when they are THIS level's (`[]` otherwise): the
 * shapes `rowsSelectors` writes, nothing else. Another section on the page
 * owns any other selection.
 */
export function ownRows(level: LadderLevel, rows: readonly DrillLevel[]): DrillLevel[] {
  const dims = rows.map((r) => r.dimension).join(',')
  const mine =
    level.kind === 'suites'
      ? dims === 'suite' || dims === 'suite,status'
      : level.kind === 'statuses'
        ? dims === 'status'
        : level.kind === 'status-suites'
          ? dims === 'suite'
          : dims === 'test,status' && rows[1].value === level.status
  return mine ? rows.map(({ dimension, value }) => ({ dimension, value })) : []
}

/** The breadcrumb's (and the rows title's) key for a level's label. */
export const labelKey = ({ dimension, value }: DrillLevel) => `${dimension}~${value}`

/** A level as the reader saw it: the learned label, else the key (a status reads as its key, the AC's "failed"). */
export function stepLabel(labels: Readonly<Record<string, string>>, step: DrillLevel): string {
  return Object.prototype.hasOwnProperty.call(labels, labelKey(step)) ? labels[labelKey(step)] : step.value
}

/** The rows panel's title: each selector as the reader saw it ("payments, failed"). */
export function rowsTitle(labels: Readonly<Record<string, string>>, selectors: readonly DrillLevel[]): string {
  return selectors.map((step) => stepLabel(labels, step)).join(', ')
}

/** The suite labels a stacked or ranked suite response teaches the breadcrumb (`x_labels`, key -> label). */
export function learnSuiteLabels(series: SeriesChart | null): Record<string, string> {
  if (!series || series.kind !== 'series' || series.dimensions[0] !== 'suite') return {}
  const out: Record<string, string> = {}
  for (const [key, label] of Object.entries(series.x_labels ?? {})) out[labelKey({ dimension: 'suite', value: key })] = label
  return out
}
