/**
 * W08 TRND-07 — the Coverage page's History tab: "Coverage history", the
 * project's daily coverage snapshots over the page's window, with the
 * project's timeline notes (TRND-04) drawn on the same days as on Trends.
 *
 * Asked only while the tab is open (`useCoverageHistory(days, true)` mounts
 * with the tab), never on the page's load. One project only: in All Projects
 * the section says so and asks nothing. Before the project's first snapshot
 * the server's reason is the message ("No coverage snapshot yet: the first is
 * taken at 00:05 UTC."). A missing or malformed answer reads "Coverage
 * history is not available" — the rest of the page is unaffected.
 *
 * A snapshot is project-wide: the page's suite and release filters do not
 * narrow it (the server states both in `meta.ignored_filters`), so the
 * section says so in its takeaway rather than pretend the line is filtered.
 */
import { useMemo } from 'react'
import { readyState, type ChartState } from '@/components/charts/chartStateCore'
import CoverageHistoryChartFrame from '@/components/charts/CoverageHistoryChart'
import { buildCoverageHistoryModel, historyAxis } from '@/components/charts/coverageHistoryModel'
import { useCoverageHistory } from '@/hooks/useCoverageHistory'
import { useTimelineNotes } from '@/hooks/useTimelineNotes'
import TimelineNotesChip from '@/components/charts/TimelineNotesChip'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { noteInputsFor, noteShowsForSuite } from '@/types/timelineNotes'
import { utcDayIso } from '@/utils/calendarDay'

export const HISTORY_TITLE = 'Coverage history'
export const HISTORY_UNAVAILABLE = 'Coverage history is not available'
export const HISTORY_FIRST_SNAPSHOT = 'No coverage snapshot yet: the first is taken at 00:05 UTC.'
export const HISTORY_PICK_PROJECT = 'Pick a project to see its coverage history'

const MESSAGE = 'rounded-xl border px-4 py-6 text-center text-[12.5px] text-[var(--color-text-muted)]'

export interface CoverageHistorySectionProps {
  days: number
  /** The page's ONE selected suite (a suite-scoped note shows only under it), or `null`. */
  suite: string | null
  /** Test seam for "today" (UTC). */
  todayIso?: string
}

export default function CoverageHistorySection({ days, suite, todayIso }: CoverageHistorySectionProps) {
  const projectId = useProjectStore((s) => s.activeProjectId)
  const pinned = projectId !== null && projectId !== ALL_PROJECTS_ID
  const today = todayIso ?? utcDayIso()
  const { history, malformed } = useCoverageHistory(days, pinned)

  const axis = useMemo(() => historyAxis(days, today), [days, today])
  const range = pinned && axis.length > 0 ? { from: axis[0], to: axis[axis.length - 1] } : null
  // TRND-04: the project's notes over the same days (asked with the tab; a malformed answer is no notes).
  const { notes, truncated } = useTimelineNotes(range, suite)
  const shownNotes = useMemo(() => notes.filter((note) => noteShowsForSuite(note, suite)), [notes, suite])
  const noteInputs = useMemo(() => noteInputsFor(shownNotes, suite), [shownNotes, suite])

  const model = useMemo(
    () => (history && history.measured ? buildCoverageHistoryModel({ points: history.points, days, todayIso: today, notes: noteInputs }) : null),
    [history, days, today, noteInputs],
  )

  if (!pinned) {
    return (
      <section aria-label={HISTORY_TITLE} data-coverage-history="" className={MESSAGE} style={{ borderColor: 'var(--color-border)' }}>
        {HISTORY_PICK_PROJECT}
      </section>
    )
  }
  if (malformed) {
    return (
      <section aria-label={HISTORY_TITLE} data-coverage-history="unavailable" className={MESSAGE} style={{ borderColor: 'var(--color-border)' }}>
        {HISTORY_UNAVAILABLE}
      </section>
    )
  }

  let state: ChartState<unknown>
  // `null` here is "not arrived yet" (a malformed answer returned above).
  if (!history) state = { status: 'loading' }
  else if (!history.measured) state = { status: 'not-measured', reason: history.reason || HISTORY_FIRST_SNAPSHOT, meta: null }
  else state = readyState(model)

  const stored = model?.stored ?? 0
  const takeaway = model
    ? `${stored} of ${axis.length} days have a snapshot · project-wide (the suite and release filters do not apply)`
    : undefined

  return (
    <section aria-label={HISTORY_TITLE} data-coverage-history={history ? (history.measured ? 'drawn' : 'empty') : 'loading'}>
      <CoverageHistoryChartFrame
        title={HISTORY_TITLE}
        headingLevel={3}
        state={state}
        model={model}
        takeaway={takeaway}
        toolbar={model && model.notes.length > 0 ? <TimelineNotesChip notes={shownNotes} truncated={truncated} /> : undefined}
      />
    </section>
  )
}
