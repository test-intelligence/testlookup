/**
 * The report chrome, props-driven (VIZ-301/302/303/304/305): context header,
 * filter bar, applied-filter chips, filtered-dataset summary and metrics
 * strip, in that order. `ReportChrome` connects it to the store and the one
 * `/metrics/summary` request; the dev gallery feeds it fixtures.
 *
 * The layout mounts it ABOVE the page, and the report pages render their
 * `<h1>` inline (some through `PageHeader`, some by hand), so there is no one
 * page-header outlet to render it under. So the chrome announces itself:
 * it opens with a "Skip to report content" link (visible on focus)
 * and an `<h2>` "Report context", and ends with the skip target, a focusable
 * marker just before the page — a keyboard or screen-reader user can pass the
 * whole chrome in one step, and heading navigation finds it by name.
 *
 * `data-report-chrome` marks the one mount per report route (the ratchet
 * counts it).
 */
import { useId, useRef } from 'react'
import FilterChips, { type FilterChipsProps } from '@/components/filters/FilterChips'
import FilteredSummary from '@/components/filters/FilteredSummary'
import ReportFilterBar, { type ReportFilterBarProps } from '@/components/filters/ReportFilterBar'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import MetricsStrip from './MetricsStrip'
import type { MetricId, ReportMetricsInput } from './metricsModel'
import ReportContextHeader from './ReportContextHeader'

export interface ReportChromeViewProps {
  meta: EnvelopeMeta | null
  allProjects: boolean
  loading?: boolean
  unavailableReason?: string
  /** "(max for summary)" on the header's Window when it is not the page's own window. */
  windowNote?: string
  bar: ReportFilterBarProps
  chips: Omit<FilterChipsProps, 'fallbackFocusRef' | 'ignoredFilters'>
  metrics: ReportMetricsInput
  include?: readonly MetricId[]
  /** Hide the filter bar (the chips stay): for a page with its own filter UI. */
  showFilterBar?: boolean
  className?: string
  'data-testid'?: string
}

export const REPORT_CHROME_HEADING = 'Report context'
export const SKIP_CHROME_TEXT = 'Skip to report content'

const SKIP_LINK =
  'sr-only rounded-md bg-[var(--color-btn-primary-bg)] px-3 py-1.5 text-sm font-semibold text-[var(--color-btn-primary-text)] focus:not-sr-only focus:self-start focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-ring)]'

export default function ReportChromeView({
  meta,
  allProjects,
  loading = false,
  unavailableReason,
  windowNote,
  bar,
  chips,
  metrics,
  include,
  showFilterBar = true,
  className = '',
  'data-testid': testId,
}: ReportChromeViewProps) {
  const barRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const endId = useId()
  return (
    // A plain container, not a landmark: the header and the strip are the
    // regions inside it; the <h2> is what heading navigation finds.
    <div
      data-report-chrome=""
      data-testid={testId}
      className={`flex min-w-0 flex-col gap-3 ${className}`}
    >
      <a
        href={`#${endId}`}
        data-skip-report-chrome=""
        className={SKIP_LINK}
        onClick={(event) => {
          // Focus, not navigation: a hash would enter the router's history.
          event.preventDefault()
          endRef.current?.focus()
        }}
      >
        {SKIP_CHROME_TEXT}
      </a>
      <h2 className="sr-only">
        {REPORT_CHROME_HEADING}
      </h2>
      <ReportContextHeader
        meta={meta}
        allProjects={allProjects}
        loading={loading}
        unavailableReason={unavailableReason}
        windowNote={windowNote}
      />
      {showFilterBar && <ReportFilterBar ref={barRef} {...bar} />}
      <FilterChips {...chips} ignoredFilters={meta?.ignored_filters ?? []} fallbackFocusRef={barRef} />
      <FilteredSummary meta={meta} onClearFilters={chips.onClearAll} />
      <MetricsStrip input={metrics} include={include} loading={loading} />
      {/* The skip target: the next Tab from here is the page itself. */}
      <div ref={endRef} id={endId} tabIndex={-1} data-report-chrome-end="" className="outline-none" />
    </div>
  )
}
