/**
 * What the Summary report's catalogue frames SAY, and the boxes they hold:
 * shared by the real sections (`SummaryCatalogue`, its own chunk, with the
 * chart kit) and the page's stand-in for them while that chunk loads
 * (`SummaryCatalogueShell`, in the page's chunk, with no chart code).
 *
 * Why one module: the stand-in draws each frame's title and takeaway in the
 * frame's own header markup, so that when the real frame replaces it the
 * header lays out at exactly the same size. That is what keeps the swap from
 * moving the tables under it (R1-2), and what keeps the takeaway the stand-in
 * painted with the page's data the page's Largest Contentful Paint: the real
 * frame's identical paragraph is not a larger element, so it is not a new
 * LCP entry (R1 (a)). Two copies of a sentence drift apart; one cannot.
 *
 * Plain data only (strings, numbers, types): importing this from the page
 * pulls nothing from the chart kit into the page's chunk.
 */
import type { SummaryReport, SummaryReportMode } from '@/types/summaryReport'

/** Plot heights: the story's 240 px floor for the donut and the trend; bars get room for labels. */
export const SUMMARY_DONUT_HEIGHT = 240
export const SUMMARY_BARS_HEIGHT = 280
export const SUMMARY_TREND_HEIGHT = 240
/**
 * The trend frame's box around its plot, px (padding, header, gap, border),
 * for the lazy trend's placeholder: the placeholder holds the frame's height
 * so nothing below it moves when the trend mounts.
 */
export const SUMMARY_TREND_CHROME_PX = 110

/** The headline part's layout: the donut and the suite bars side by side at xl, the trend under them. */
export const SUMMARY_HEADLINE_CLASS = 'mb-5 flex flex-col gap-4'
export const SUMMARY_HEADLINE_ROW_CLASS = 'grid grid-cols-1 xl:[grid-template-columns:minmax(0,1fr)_minmax(0,1.6fr)] gap-4'
/** The top-failing part's box. */
export const SUMMARY_TOP_FAILING_CLASS = 'mb-5 min-w-0'

export const STATUS_TITLE = 'Status breakdown'
export const SUITES_TITLE = 'Results by suite'
export const FAILURES_TITLE = 'Failures by test'
export const TREND_TITLE = 'Pass rate trend'

export const windowWords = (days: number) => (days === 1 ? 'the last 24 hours' : `the last ${days} days`)

/** What the Aggregation toggle means, in the words a caption can carry. */
export const MODE_WORDS: Record<SummaryReportMode, string> = {
  latest: 'latest run per suite',
  window: 'all runs in the window',
}

/** "per unique test · latest run per suite": the population, as the tiles name it, and the toggle. */
export function populationWords(report: SummaryReport, mode: SummaryReportMode): string {
  const basis = report.totals.pass_rate_basis_label?.trim() || 'per unique test'
  return `${basis} · ${MODE_WORDS[mode]}`
}

export const statusTakeaway = (report: SummaryReport, mode: SummaryReportMode) =>
  `Tests by status, ${populationWords(report, mode)}. This report does not count an unknown status.`

export const suitesTakeaway = (report: SummaryReport, mode: SummaryReportMode) =>
  `Tests by status in each suite, most failed and broken first, ${populationWords(report, mode)}`

export const failuresTakeaway = (days: number, mode: SummaryReportMode) =>
  `The tests that failed most in ${windowWords(days)}, ${MODE_WORDS[mode]}`

/**
 * The trend's caption. It names the population (executions) because every
 * other number on this page counts unique tests; the mutation check "caption
 * without the basis" is pinned by a test on these words.
 */
export const summaryTrendCaption = (days: number) =>
  `All runs in ${windowWords(days)}, whichever aggregation is selected; pass rate as a percent of test executions, not of unique tests`
