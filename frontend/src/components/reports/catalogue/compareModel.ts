/**
 * VIZ-605 — multi-criteria comparison: pick several suites and several
 * releases in the filter bar and compare them on one chart ("payments vs
 * cart, on R1 vs R2"). Pure: no React, no fetching.
 *
 * Three ways to compare, offered only when the selection makes them
 * meaningful (two or more lines):
 *   - by suite: one line per selected suite;
 *   - by release: one line per selected release;
 *   - by suite and release: one line per (suite, release) pair, named
 *     "payments · R1", COLOUR by suite and DASH by release, so the pairs read
 *     as a grid.
 *
 * `chart-data` takes at most two `group_by` (the day plus one series
 * dimension), so "suite and release" is one `day × suite` request per
 * release, merged here. Every channel is assigned from the SORTED key, never
 * from the order a response arrived in, so colours are stable across reloads.
 *
 * More than `COMPARE_MAX_SERIES` lines is not drawn: the section asks the
 * reader to narrow the selection instead of folding pairs into "Other",
 * which would hide exactly the pairs they picked.
 */
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import type { Comparability, MultiSeriesInputSeries, MultiSeriesMetricKind } from '@/components/charts/multiSeriesModel'
import type { EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'

export type CompareBy = 'suite' | 'release' | 'suite_release'

/** The most lines one comparison draws: one per colour/dash slot. */
export const COMPARE_MAX_SERIES = 8

export const COMPARE_TITLE = 'Compare'

export const COMPARE_BY_LABELS: Record<CompareBy, string> = {
  suite: 'Suite',
  release: 'Release',
  suite_release: 'Suite and release',
}

export type CompareMetric = 'pass_rate' | 'failures' | 'executions'

export const COMPARE_METRICS: Record<CompareMetric, { label: string; kind: MultiSeriesMetricKind; title: string }> = {
  pass_rate: { label: 'Pass rate', kind: 'rate', title: 'Pass rate %' },
  failures: { label: 'Failures', kind: 'count', title: 'Failures' },
  executions: { label: 'Executions', kind: 'count', title: 'Executions' },
}

export const PICK_MORE_REASON =
  'pick two or more suites or releases in the filter bar above to compare them on one chart'

/** "12 lines are too many …" — the reader narrows, nothing is folded away. */
export function tooManyReason(count: number): string {
  return `${count} lines are too many to compare on one chart; narrow the filter to ${COMPARE_MAX_SERIES} or fewer (fewer suites or fewer releases)`
}

export const SERIES_SEP = ' · '

const sorted = (values: readonly string[]) => [...new Set(values)].sort()

/** The comparisons the current selection supports, in menu order. */
export function compareOptions(suites: readonly string[], releases: readonly string[]): CompareBy[] {
  const s = sorted(suites).length
  const r = sorted(releases).length
  const options: CompareBy[] = []
  if (s >= 2) options.push('suite')
  if (r >= 2) options.push('release')
  if (s >= 1 && r >= 1 && s * r >= 2) options.push('suite_release')
  return options
}

/** The comparison to show: the reader's choice when the selection still supports it, else the richest one. */
export function effectiveCompareBy(choice: CompareBy | null, options: readonly CompareBy[]): CompareBy | null {
  if (choice && options.includes(choice)) return choice
  if (options.includes('suite_release')) return 'suite_release'
  return options[0] ?? null
}

/** How many lines a comparison draws. */
export function seriesCount(by: CompareBy, suites: readonly string[], releases: readonly string[]): number {
  const s = sorted(suites).length
  const r = sorted(releases).length
  return by === 'suite' ? s : by === 'release' ? r : s * r
}

export interface CompareRequest {
  /** Set when this request is one release's slice of a suite-and-release comparison. */
  releaseId: string | null
  params: CatalogParams
}

/**
 * The `chart-data` requests for a comparison: the page scope (`base`, from
 * `catalogueParams`, without `metric`/`group_by`) with the compared dimension
 * set to exactly the selection.
 */
export function compareRequests(
  by: CompareBy,
  base: CatalogParams,
  metric: CompareMetric,
  suites: readonly string[],
  releases: readonly string[],
): CompareRequest[] {
  const suiteList = sorted(suites)
  const releaseList = sorted(releases)
  const common = { ...base, metric }
  if (by === 'suite') {
    return [{ releaseId: null, params: { ...common, group_by: ['day', 'suite'], suite_name: suiteList } }]
  }
  if (by === 'release') {
    return [{ releaseId: null, params: { ...common, group_by: ['day', 'release'], release_id: releaseList } }]
  }
  return releaseList.map((releaseId) => ({
    releaseId,
    params: { ...common, group_by: ['day', 'suite'], suite_name: suiteList, release_id: releaseId },
  }))
}

/** One series' colour slot and dash slot, 0..7. */
export interface SeriesStyle {
  colour: number
  dash: number
}

/** The key of a suite-and-release line. */
export const pairKey = (suite: string, releaseId: string) => `${suite}|${releaseId}`

/**
 * Stable channels: by suite and release, colour = the suite's place among the
 * sorted suites, dash = the release's place among the sorted releases. A
 * one-dimension comparison gives each line its own colour AND dash, by its
 * sorted place.
 */
export function compareStyles(
  by: CompareBy,
  suites: readonly string[],
  releases: readonly string[],
): Record<string, SeriesStyle> {
  const suiteList = sorted(suites)
  const releaseList = sorted(releases)
  const styles: Record<string, SeriesStyle> = {}
  if (by === 'suite_release') {
    suiteList.forEach((suite, s) =>
      releaseList.forEach((releaseId, r) => {
        styles[pairKey(suite, releaseId)] = { colour: s % COMPARE_MAX_SERIES, dash: r % COMPARE_MAX_SERIES }
      }),
    )
    return styles
  }
  const keys = by === 'suite' ? suiteList : releaseList
  keys.forEach((key, i) => {
    styles[key] = { colour: i % COMPARE_MAX_SERIES, dash: i % COMPARE_MAX_SERIES }
  })
  return styles
}

export interface CompareSlice {
  /** The release this slice was filtered to (suite-and-release only). */
  releaseId: string | null
  chart: SeriesChart
  meta: EnvelopeMeta | null
}

/**
 * The lines to draw, from the slices: a one-request comparison is its own
 * series; a suite-and-release one renames each slice's suite lines
 * "suite · release" under a pair key. Series keys from the server are matched
 * case-insensitively to the selection (suites are compared by their key).
 */
export function compareSeries(
  by: CompareBy,
  slices: readonly CompareSlice[],
  releaseName: (id: string) => string,
): MultiSeriesInputSeries[] {
  if (by !== 'suite_release') {
    const chart = slices[0]?.chart
    return (chart?.series ?? []).map((s) => ({ key: s.key, label: s.label, points: s.points }))
  }
  return slices.flatMap((slice) =>
    slice.chart.series.map((s) => {
      const releaseId = slice.releaseId ?? ''
      return {
        key: pairKey(s.key, releaseId),
        label: `${s.label}${SERIES_SEP}${releaseName(releaseId)}`,
        points: s.points,
      }
    }),
  )
}

/**
 * Whether the compared slices may be read side by side. Any slice the server
 * flagged not comparable makes the whole comparison flagged, with that
 * slice's reason; slices it did not assess say nothing.
 */
export function compareComparability(slices: readonly CompareSlice[]): Comparability | null {
  const flagged = slices.find((slice) => slice.meta?.comparability && slice.meta.comparability.comparable === false)
  if (!flagged?.meta?.comparability) return null
  const { reason, reason_code } = flagged.meta.comparability as { reason?: string | null; reason_code?: string | null }
  return { comparable: false, reason: reason ?? null, reasonCode: reason_code ?? null }
}

/**
 * Styles keyed the way `compareSeries` keys the lines. One-dimension
 * comparisons key by the server's series key, which for suites is the
 * lower-cased suite name: matched case-insensitively to the selection.
 */
export function stylesForLines(
  by: CompareBy,
  lines: readonly MultiSeriesInputSeries[],
  suites: readonly string[],
  releases: readonly string[],
): Record<string, SeriesStyle> {
  const styles = compareStyles(by, suites, releases)
  const lower = new Map(Object.entries(styles).map(([key, style]) => [key.toLowerCase(), style]))
  const out: Record<string, SeriesStyle> = {}
  for (const line of lines) {
    const style = styles[line.key] ?? lower.get(line.key.toLowerCase())
    if (style) out[line.key] = style
  }
  return out
}
