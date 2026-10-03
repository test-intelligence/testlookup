/**
 * The report chrome, props-driven (VIZ-301/302/303/304/305): context header,
 * filter bar, applied-filter chips, filtered-dataset summary and metrics
 * strip, in that order — or, `collapsible` and collapsed (OD-16), the context
 * on one line beside the filter bar, chips only when a release or suite is
 * chosen, the summary line, and no strip. `ReportChrome` connects it to the store and the one
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
import { useId, useRef, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import FilterChips, { type FilterChipsProps } from '@/components/filters/FilterChips'
import FilteredSummary from '@/components/filters/FilteredSummary'
import ReportFilterBar, { type ReportFilterBarProps } from '@/components/filters/ReportFilterBar'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import CompactReportContext from './CompactReportContext'
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
  /**
   * OD-16: open collapsed — the context on one line beside the filter bar, no
   * metrics strip — with a toggle to the full header and strip. The reader's
   * choice is remembered in this browser. Off (the default): always full.
   */
  collapsible?: boolean
  /** VIZ-609: controls beside the toggle (the saved-views menu). */
  actions?: ReactNode
  className?: string
  'data-testid'?: string
}

export const REPORT_CHROME_HEADING = 'Report context'
export const SKIP_CHROME_TEXT = 'Skip to report content'
export const SHOW_DETAILS_TEXT = 'Show details and metrics'
export const HIDE_DETAILS_TEXT = 'Hide details'

/** Where the collapsed/expanded choice is kept (per browser; a convenience, never required). */
export const CHROME_EXPANDED_KEY = 'testlookup.reportChrome.expanded'

function readExpanded(): boolean {
  try {
    return window.localStorage.getItem(CHROME_EXPANDED_KEY) === '1'
  } catch {
    return false
  }
}

function writeExpanded(expanded: boolean) {
  try {
    window.localStorage.setItem(CHROME_EXPANDED_KEY, expanded ? '1' : '0')
  } catch {
    // Storage blocked: the choice lasts for this page only.
  }
}

const TOGGLE =
  'inline-flex min-h-8 shrink-0 items-center gap-1 rounded-md px-2 text-sm font-medium text-[var(--color-accent-ink)] hover:bg-[var(--color-accent-bg-soft)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-ring)]'

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
  collapsible = false,
  actions,
  className = '',
  'data-testid': testId,
}: ReportChromeViewProps) {
  const barRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const endId = useId()
  const [expandedChoice, setExpandedChoice] = useState(readExpanded)
  const expanded = !collapsible || expandedChoice
  const toggle = collapsible ? (
    <button
      type="button"
      data-report-chrome-toggle=""
      aria-expanded={expanded}
      onClick={() => {
        writeExpanded(!expanded)
        setExpandedChoice(!expanded)
      }}
      className={TOGGLE}
    >
      {expanded ? HIDE_DETAILS_TEXT : SHOW_DETAILS_TEXT}
      {expanded ? <ChevronUp aria-hidden="true" className="h-4 w-4" /> : <ChevronDown aria-hidden="true" className="h-4 w-4" />}
    </button>
  ) : null
  // Collapsed, the chips are shown only when a release or suite is chosen
  // (the context line already says "All releases · All suites").
  const chipsApplied = chips.releases.length > 0 || chips.suites.length > 0
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
      {expanded ? (
        <>
          {(toggle || actions) && (
            <div className="-mb-2 flex justify-end gap-1">
              {actions}
              {toggle}
            </div>
          )}
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
        </>
      ) : (
        <>
          <div
            data-report-chrome-compact=""
            className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] px-3 py-2"
          >
            <CompactReportContext
              meta={meta}
              allProjects={allProjects}
              loading={loading}
              unavailableReason={unavailableReason}
              windowNote={windowNote}
            />
            {showFilterBar && <ReportFilterBar ref={barRef} {...bar} />}
            {actions}
            {toggle}
          </div>
          {chipsApplied && <FilterChips {...chips} ignoredFilters={meta?.ignored_filters ?? []} fallbackFocusRef={barRef} />}
          <FilteredSummary meta={meta} onClearFilters={chips.onClearAll} />
        </>
      )}
      {/* The skip target: the next Tab from here is the page itself. */}
      <div ref={endRef} id={endId} tabIndex={-1} data-report-chrome-end="" className="outline-none" />
    </div>
  )
}
