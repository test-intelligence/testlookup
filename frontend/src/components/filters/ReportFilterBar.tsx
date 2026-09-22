/**
 * The report filter bar (VIZ-303, presentation half): Release and Suite
 * multi-selects and the window select, under the page header — not in the
 * fixed-height TopBar, where three multi-selects would starve the search box.
 *
 * Props-driven; the connected wrapper (`ReportChrome`) feeds it from
 * `useReportScope()`.
 *
 *   - Caps come from the store constants; at the cap `MultiSelect` disables the
 *     rest with "Limit reached (20)".
 *   - An active control is highlighted: `MultiSelect` draws itself filled with
 *     a count badge; the window select is filled when off its default.
 *   - All-Projects mode: a release belongs to one project, so the release
 *     control is disabled and says why, visibly. Suites filter by name and
 *     stay available.
 *   - Values the store dropped (a project change) are named in a dismissible
 *     notice, not silently discarded. The notice TEXT is written into a live
 *     region that is always mounted (and empty without a notice) — a region
 *     inserted together with its text is often not announced. Dismissing it
 *     moves focus to the Release trigger (or, disabled, the bar), never to
 *     <body> with the button that had it.
 *   - The window select has a VISIBLE "Window" label.
 *   - The Release trigger carries `data-report-filter="release"`: the TopBar
 *     picker hands focus to it when the chrome owns the release filter.
 *
 * The bar itself is focusable (`tabIndex=-1`) so chip removal can hand focus
 * back to it when nothing else is left.
 */
import { X } from 'lucide-react'
import { forwardRef, useId, useRef, type MutableRefObject, type Ref } from 'react'
import MultiSelect, { type MultiSelectOption } from '@/components/ui/MultiSelect'
import { REPORT_WINDOW_OPTIONS } from './filterOptions'

export interface DroppedFilterNotice {
  dimension: 'release' | 'suite'
  values: string[]
  reason: string
}

export const ALL_PROJECTS_RELEASE_REASON = 'Pick one project to filter by release: a release belongs to one project.'

export interface ReportFilterBarProps {
  releaseOptions: MultiSelectOption[]
  suiteOptions: MultiSelectOption[]
  releaseIds: string[]
  suiteNames: string[]
  windowDays: number
  defaultWindowDays: number
  releaseCap: number
  suiteCap: number
  allProjects: boolean
  onReleaseChange: (ids: string[]) => void
  onSuiteChange: (names: string[]) => void
  onWindowChange: (days: number) => void
  droppedNotice?: DroppedFilterNotice[]
  onDismissNotice?: () => void
  windowOptions?: readonly number[]
  className?: string
}

const windowLabel = (days: number) => (days <= 0 ? 'All time' : days === 1 ? 'Last 24 hours' : `Last ${days} days`)

/** One ref, two holders (the caller's forwarded ref and the bar's own). */
function assignRef<T>(ref: Ref<T> | undefined, node: T | null) {
  if (typeof ref === 'function') ref(node)
  else if (ref) (ref as MutableRefObject<T | null>).current = node
}

const FOCUS =
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--color-ring)]'

function noticeText(notice: DroppedFilterNotice, releaseOptions: MultiSelectOption[]): string {
  const names =
    notice.dimension === 'release'
      ? notice.values.map((v) => releaseOptions.find((o) => o.value === v)?.label ?? v)
      : notice.values
  const noun = notice.dimension === 'release' ? 'release' : 'suite'
  const what = `${names.length === 1 ? noun : `${noun}s`} ${names.join(', ')}`
  return `Removed ${what} from the filter: ${notice.reason}`
}

const ReportFilterBar = forwardRef<HTMLDivElement, ReportFilterBarProps>(function ReportFilterBar(
  {
    releaseOptions,
    suiteOptions,
    releaseIds,
    suiteNames,
    windowDays,
    defaultWindowDays,
    releaseCap,
    suiteCap,
    allProjects,
    onReleaseChange,
    onSuiteChange,
    onWindowChange,
    droppedNotice = [],
    onDismissNotice,
    windowOptions = REPORT_WINDOW_OPTIONS,
    className = '',
  },
  ref,
) {
  const windowId = useId()
  const barRef = useRef<HTMLDivElement | null>(null)
  const releaseTriggerRef = useRef<HTMLButtonElement | null>(null)
  const noticeLines = droppedNotice.map((notice) => noticeText(notice, releaseOptions))

  const dismissNotice = () => {
    // The dismiss button unmounts with the notice: move focus first, to the
    // first control of the bar, so it never falls to <body>.
    const release = releaseTriggerRef.current
    if (release && release.getAttribute('aria-disabled') !== 'true') release.focus()
    else barRef.current?.focus()
    onDismissNotice?.()
  }

  const windowActive = windowDays !== defaultWindowDays
  // The current value is always selectable, even when it is not a listed option.
  const choices = windowOptions.includes(windowDays) ? windowOptions : [...windowOptions, windowDays].sort((a, b) => a - b)

  return (
    <div
      ref={(node) => {
        barRef.current = node
        assignRef(ref, node)
      }}
      role="group"
      aria-label="Report filters"
      tabIndex={-1}
      data-report-filter-bar=""
      className={`flex min-w-0 flex-col gap-2 rounded-lg outline-none ${FOCUS} ${className}`}
    >
      <div className="flex min-w-0 flex-wrap items-start gap-2">
        <MultiSelect
          label="Release"
          options={releaseOptions}
          value={allProjects ? [] : releaseIds}
          onChange={onReleaseChange}
          max={releaseCap}
          disabled={allProjects}
          disabledReason={allProjects ? ALL_PROJECTS_RELEASE_REASON : undefined}
          data-testid="report-filter-release"
          triggerRef={releaseTriggerRef}
          triggerData={{ 'data-report-filter': 'release' }}
        />
        <MultiSelect
          label="Suite"
          options={suiteOptions}
          value={suiteNames}
          onChange={onSuiteChange}
          max={suiteCap}
          data-testid="report-filter-suite"
          triggerData={{ 'data-report-filter': 'suite' }}
        />
        <span className="inline-flex items-center gap-2">
          <label htmlFor={windowId} className="text-sm text-[var(--color-text-secondary)]">
            Window
          </label>
          <select
            id={windowId}
            value={windowDays}
            data-active={windowActive ? 'true' : 'false'}
            data-testid="report-filter-window"
            onChange={(event) => onWindowChange(Number(event.target.value))}
            className={`min-h-8 rounded-md border px-3 py-1 text-sm text-[var(--color-text)] ${FOCUS} ${
              windowActive
                ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg-soft)]'
                : 'border-[var(--color-border)] bg-[var(--color-bg-input)]'
            }`}
          >
            {choices.map((days) => (
              <option key={days} value={days}>
                {windowLabel(days)}
              </option>
            ))}
          </select>
        </span>
      </div>
      {/* Always mounted, empty without a notice: the text is WRITTEN into it. */}
      <div role="status" className="sr-only" data-dropped-notice-live="">
        {noticeLines.join('. ')}
      </div>
      {droppedNotice.length > 0 && (
        <div
          data-dropped-notice=""
          className="flex items-start gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
        >
          <ul className="min-w-0 flex-1">
            {noticeLines.map((line, index) => (
              <li key={`${droppedNotice[index].dimension}-${index}`} className="break-words">
                {line}
              </li>
            ))}
          </ul>
          {onDismissNotice && (
            <button
              type="button"
              aria-label="Dismiss notice"
              onClick={dismissNotice}
              className={`inline-flex h-6 min-h-6 w-6 min-w-6 shrink-0 items-center justify-center rounded-full text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] ${FOCUS}`}
            >
              <X aria-hidden="true" className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      )}
    </div>
  )
})

export default ReportFilterBar
