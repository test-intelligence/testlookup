/**
 * `TimeSeriesChart` inside `ChartFrame` (VIZ-403) — the component a page mounts.
 *
 * The frame owns every non-data state, the table view, the announcer and the
 * export ref; this only decides what the frame is given. The two things worth
 * noticing:
 *
 *   - the frame's `series` is built FROM the drawn model
 *     (`timeSeriesToChartSeries`), not fetched separately, so the plot, the
 *     screen-reader summary and the table cannot disagree — a gap in the line is
 *     a "—" in the table;
 *   - the release markers ride along as `tableExtras`, because a marker is drawn
 *     over the plot rather than being a value in the series, and a table reader
 *     must not lose the release boundaries.
 *
 * `measured: false` never becomes a zero here: the frame's own `not-measured`
 * state shows "—" with the server's reason, and inside a drawn chart a
 * `measured: false` bucket is a gap carrying its reason into the tooltip.
 *
 * VIZ-405: `trendAnalysis` turns on the trend overlays. The frame then owns
 * which overlays are shown (its takeaway follows them), computes ONE analysis
 * that the chart, the takeaway and the table all read, and adds the analysis
 * to the table view. Without the prop, the frame is exactly the VIZ-403 frame.
 *
 * VIZ-407: `zoom` adds the range brush under the plot. The DRAWN model is then
 * the full model sliced to the zoomed days (`sliceTimeSeriesModel`), and the
 * summary, the table and the export follow it and say so (`zoomNote`). Two
 * things deliberately do NOT follow the slice:
 *
 *   - the trend analysis is computed on the FULL model and only its per-day
 *     values are cut to the range — its anomaly rule looks back four weeks and
 *     a moving average needs the six days before a day, so a value recomputed
 *     on a ten-day slice would be a different (and wrong) number;
 *   - the release table keeps every marker, marking the ones the zoom took off
 *     the plot rather than dropping them.
 *
 * Without the prop, nothing about the frame changes.
 *
 * VIZ-104 K2: `rateTarget` draws a dashed target line on the rate axis
 * (`TimeSeriesChart`) and says so in ONE sentence, in the summary (after the
 * y axis's name) and under the table view — where the line is, or why it is
 * not drawn. Without the prop, the summary, the table and the DOM are exactly
 * what they were.
 *
 * TRND-04: timeline notes ride in the model (`model.notes`); the table view
 * lists them (dates, text, author) beside the release markers. No notes, no
 * table: the frame is what it was. The host's `Notes (n)` chip goes in the
 * frame's existing `toolbar` slot (`TimelineNotesChip`).
 */
import { forwardRef, useMemo, useState } from 'react'
import { analyzeTrend, trendFrameTakeaway, type TrendOverlayState } from '@/lib/trendStats'
import type { ChartState } from './chartState'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import ReleaseMarkerTable from './ReleaseMarkerTable'
import TimeSeriesChart, { rateTargetSentence, type TimeSeriesChartProps } from './TimeSeriesChart'
import { TREND_OVERLAYS_OFF } from './TimeSeriesChartOverlayStyle'
import TimeSeriesChartTrendTable from './TimeSeriesChartTrendTable'
import {
  EXECUTIONS_AXIS_TITLE,
  RATE_AXIS_TITLE,
  timeSeriesToChartSeries,
  type NoteMarker,
  type TimeSeriesModel,
} from './timeSeriesModel'
import ChartRangeBrush from './zoom/ChartRangeBrush'
import { useFrameZoom } from './zoom/useFrameZoom'
import {
  CALENDAR_WORDS,
  markersOutsideRange,
  sliceTimeSeriesModel,
  sliceTrendAnalysis,
  zoomOptionsOf,
  type ChartZoomOptions,
} from './zoom/zoomModel'

export interface TimeSeriesChartFrameProps
  extends Omit<ChartFrameProps, 'children' | 'series' | 'chartType' | 'axes' | 'tableExtras' | 'zoomNote'>,
    Pick<TimeSeriesChartProps, 'inProgressRuns' | 'timeZone' | 'locale' | 'now' | 'animate' | 'rateTarget'> {
  /**
   * The model to draw. Required for `ready` / `truncated`; ignored (and
   * allowed to be null) for every other state, which the frame owns.
   */
  model: TimeSeriesModel | null
  state: ChartState<unknown>
  /**
   * VIZ-405 trend overlays: `true` offers them (both off), an object can
   * start some on. Leave it out and nothing about the frame changes.
   */
  trendAnalysis?: boolean | { initialShown?: Partial<TrendOverlayState> }
  /**
   * VIZ-407 local zoom: `true` draws the range brush; the object form can open
   * zoomed and can offer "Apply as time filter" (see `ChartZoomOptions`).
   */
  zoom?: boolean | ChartZoomOptions
}

const NO_DAYS: readonly string[] = []

/**
 * The axis caption the drawn chart ALWAYS prints under its plot ("Days are UTC
 * buckets; …"): one 16 px `text-xs` line, 4 px (`gap-1`) below the plot. The
 * frame's body reserves it in every state, so a loading or empty frame is as
 * tall as the drawn one and nothing below moves when the data arrives (Wave
 * 2.6: Summary's trend grew 60 px as it drew, moving the tables under it).
 * The other notes under the plot depend on the data and cannot be reserved.
 */
export const TIME_SERIES_CAPTION_RESERVE = 16 + 4

/** The summary adds its own full stop after the y axis's name. */
const withoutFullStop = (text: string) => (text.endsWith('.') ? text.slice(0, -1) : text)

/** TRND-04: the window's notes as table rows (dates, text, author) — React text, never markup. */
function TimelineNoteTable({ notes }: { notes: readonly NoteMarker[] }) {
  return (
    <div data-chart-note-table="" className="mt-3 rounded border border-[var(--color-border)]">
      <table className="w-full border-collapse text-left text-xs text-[var(--color-text)]">
        <caption className="px-2 py-1 text-left text-sm font-medium text-[var(--color-text)]">Timeline notes</caption>
        <thead>
          <tr>
            <th scope="col" className="border-b border-[var(--color-border)] px-2 py-1 font-semibold">
              Days (UTC)
            </th>
            <th scope="col" className="border-b border-[var(--color-border)] px-2 py-1 font-semibold">
              Note
            </th>
            <th scope="col" className="border-b border-[var(--color-border)] px-2 py-1 font-semibold">
              By
            </th>
          </tr>
        </thead>
        <tbody>
          {notes.map((note) => (
            <tr key={note.id} className="border-b border-[var(--color-border)] last:border-b-0">
              <th scope="row" className="px-2 py-1 font-medium tabular-nums">
                {note.fromDay === note.toDay ? note.fromDay : `${note.fromDay} – ${note.toDay}`}
              </th>
              <td className="px-2 py-1 break-words">{note.label}</td>
              <td className="px-2 py-1">{note.author ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const TimeSeriesChartFrame = forwardRef<HTMLDivElement, TimeSeriesChartFrameProps>(function TimeSeriesChartFrame(
  { model, inProgressRuns, timeZone, locale, now, animate, height = 280, trendAnalysis, zoom, rateTarget, ...frameProps },
  ref,
) {
  const trendRequested = trendAnalysis !== undefined && trendAnalysis !== false
  // ONE analysis, always on the FULL model — never recomputed on a zoomed slice.
  const analysis = useMemo(
    () => (trendRequested && model ? analyzeTrend(model.points) : null),
    [trendRequested, model],
  )
  const [shown, setShown] = useState<TrendOverlayState>(() => ({
    ...TREND_OVERLAYS_OFF,
    ...(typeof trendAnalysis === 'object' ? trendAnalysis.initialShown : undefined),
  }))
  const takeaway = analysis ? trendFrameTakeaway(analysis, shown, frameProps.takeaway) : frameProps.takeaway

  const xs = useMemo(() => (model ? model.points.map((point) => point.x) : NO_DAYS), [model])
  const zoomState = useFrameZoom({
    options: zoomOptionsOf(zoom),
    xs,
    model,
    title: frameProps.title,
    words: CALENDAR_WORDS,
    trendAnalysis: analysis !== null,
    markersOutside: (range) => (model ? markersOutsideRange(model.markers, xs, range).length : 0),
  })
  const { range } = zoomState
  // The strip's context: the pass rate over the WHOLE window, gaps kept.
  const spark = useMemo(() => (model ? [model.points.map((point) => point.rate)] : undefined), [model])

  // What is DRAWN: the full model, or its zoomed slice.
  const view = useMemo(() => (model ? sliceTimeSeriesModel(model, range) : null), [model, range])
  const viewDays = useMemo(() => (view ? view.points.map((point) => point.x) : NO_DAYS), [view])
  const viewAnalysis = useMemo(
    () => (analysis && range ? sliceTrendAnalysis(analysis, viewDays) : analysis),
    [analysis, range, viewDays],
  )
  const series = useMemo(() => (view ? timeSeriesToChartSeries(view) : null), [view])

  const outsideView = useMemo(
    () => (model && range ? markersOutsideRange(model.markers, xs, range).map((marker) => marker.x) : undefined),
    [model, xs, range],
  )
  const releaseTable = model ? (
    // The FULL model's markers: a zoom marks the ones it hides, it never drops them.
    <ReleaseMarkerTable markers={model.markers} outsideWindow={model.markersOutsideWindow} outsideView={outsideView} />
  ) : null
  // VIZ-104 K2: one sentence about the target, for the summary and the table view (about the DRAWN view).
  const targetSentence = rateTarget && view ? rateTargetSentence(view, rateTarget) : null
  const axes = useMemo(
    () => ({
      x: 'Day (UTC)',
      y: `${RATE_AXIS_TITLE} / ${EXECUTIONS_AXIS_TITLE}${targetSentence ? `. ${withoutFullStop(targetSentence)}` : ''}`,
    }),
    [targetSentence],
  )
  const targetTableNote = targetSentence ? (
    <p data-chart-target-table-note="" className="mt-2 px-2 text-xs text-[var(--color-text-secondary)]">
      {targetSentence}
    </p>
  ) : null
  const zoomTableNote = zoomState.note ? (
    <p data-chart-zoom-table-note="" className="mt-2 px-2 text-xs text-[var(--color-text-secondary)]">
      {zoomState.note}
    </p>
  ) : null
  // TRND-04: the timeline notes the plot draws, for the table reader (only when there are any).
  const notesTable = model && model.notes?.length ? <TimelineNoteTable notes={model.notes} /> : null

  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      // Until it draws, the body holds the plot AND its caption line. Drawn,
      // the chart's own content is that tall already (the plot keeps
      // `height`), so the drawn frame's markup is exactly what it was.
      height={frameProps.state.status === 'ready' || frameProps.state.status === 'truncated' ? height : height + TIME_SERIES_CAPTION_RESERVE}
      takeaway={takeaway}
      series={series}
      scopeLabel={zoomState.scopeLabel(frameProps.scopeLabel)}
      changeLabel={zoomState.changeLabel ?? frameProps.changeLabel}
      zoomNote={zoomState.note}
      chartType="Line and bar chart"
      axes={axes}
      tableExtras={
        <>
          {zoomTableNote}
          {targetTableNote}
          {releaseTable}
          {notesTable}
          {viewAnalysis && view ? (
            <TimeSeriesChartTrendTable analysis={viewAnalysis} days={viewDays} zoomNote={range ? zoomState.note : undefined} />
          ) : null}
        </>
      }
    >
      {view ? (
        <>
          <TimeSeriesChart
            model={view}
            // The frame's own title names the keyboard cursor's surface, so the
            // reader hears the chart they can see rather than a generic default.
            title={frameProps.title}
            height={height}
            animate={animate}
            inProgressRuns={inProgressRuns}
            timeZone={timeZone}
            locale={locale}
            now={now}
            trendOverlays={viewAnalysis ? { analysis: viewAnalysis, shown, onShownChange: setShown } : undefined}
            rateTarget={rateTarget}
          />
          {zoomState.brush ? (
            // A BAND scale: the execution bars give every day a slot, so the
            // strip's days are slots too, centred as the bars are.
            <ChartRangeBrush {...zoomState.brush} scale="band" spark={spark} />
          ) : null}
        </>
      ) : null}
    </ChartFrame>
  )
})

export default TimeSeriesChartFrame
