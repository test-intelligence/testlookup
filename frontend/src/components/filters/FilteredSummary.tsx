/**
 * `FilteredSummary` (VIZ-305): one line saying how much data the filters kept.
 *
 * The text is `summarizeFilteredDataset(meta)` — pure, no request. A CHANGE
 * (never the first render) is announced once through the page's
 * `ChartAnnouncer` (Epic 1), which coalesces it with any chart updates from
 * the same filter change into ONE polite message — a filter change must not
 * set off one live region per widget. A page with no announcer mounted gets
 * a single debounced polite region of this component's own instead, so the
 * change is still heard.
 *
 * `matched = 0` hands over to the filtered-empty copy (VIZ-107) with its
 * "Clear filters" action; missing totals render nothing.
 */
import { useEffect, useRef, useState } from 'react'
import { useChartAnnouncer, ANNOUNCE_DEBOUNCE_MS } from '@/components/charts/ChartAnnouncer'
import { CHART_MESSAGES } from '@/components/charts/chartMessages'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import { summarizeFilteredDataset } from './summarizeFilteredDataset'

export interface FilteredSummaryProps {
  meta: Partial<EnvelopeMeta> | null
  onClearFilters?: () => void
  className?: string
}

/** The frame id this line reports under, and the title the announcer prefixes. */
export const SUMMARY_ANNOUNCE_ID = 'report-filtered-summary'
export const SUMMARY_ANNOUNCE_TITLE = 'Filtered data'

export default function FilteredSummary({ meta, onClearFilters, className = '' }: FilteredSummaryProps) {
  const summary = summarizeFilteredDataset(meta)
  const text = summary === null ? null : summary.kind === 'text' ? summary.text : CHART_MESSAGES.filteredEmpty
  const announcer = useChartAnnouncer()
  /** The last line that was on screen; `null` until the FIRST one has been seen. */
  const baseline = useRef<string | null>(null)
  const [fallbackLive, setFallbackLive] = useState('')

  useEffect(() => {
    // Nothing to say while there is no line (loading, no totals).
    if (text === null) return
    const before = baseline.current
    baseline.current = text
    // The first line a page shows is its initial load, not a change: the
    // chrome mounts with no response (null), and the arrival of the first
    // one must not be announced. Only a line that REPLACES a line is.
    if (before === null || before === text) return
    if (announcer) {
      announcer.report(SUMMARY_ANNOUNCE_ID, SUMMARY_ANNOUNCE_TITLE, text, 'summary')
      return
    }
    const timer = setTimeout(() => setFallbackLive(text), ANNOUNCE_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [text, announcer])

  if (summary === null) return null

  return (
    <div data-filtered-summary={summary.kind} className={`text-sm text-[var(--color-text-secondary)] ${className}`}>
      {summary.kind === 'text' ? (
        <p data-filtered-summary-text="">{summary.text}</p>
      ) : (
        <p className="flex flex-wrap items-center gap-2">
          <span data-filtered-summary-text="">{CHART_MESSAGES.filteredEmpty}</span>
          {summary.filtered && onClearFilters && (
            <button
              type="button"
              onClick={onClearFilters}
              className="inline-flex min-h-6 items-center rounded px-2 font-medium text-[var(--color-accent-ink)] hover:bg-[var(--color-accent-bg-soft)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-ring)]"
            >
              {CHART_MESSAGES.clearFilters}
            </button>
          )}
        </p>
      )}
      {!announcer && (
        <div role="status" className="sr-only" data-filtered-summary-live="">
          {fallbackLive}
        </div>
      )}
    </div>
  )
}
