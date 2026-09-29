/**
 * Everything a time-chart FRAME needs to be zoomable (VIZ-407), in one hook,
 * so the single-series and the multi-series frames cannot drift apart:
 *
 *   - the zoom itself (`useChartZoom`: local, cleared by a scope change);
 *   - what the frame's announcement calls the change. `ChartFrame` already
 *     reports a change to its summary through the page's ONE announcer; a zoom
 *     changes the summary, so the frame only needs the right WORDS
 *     (`changeLabel`: "zoomed to Sep 3–12, 2026"). Those words are used only
 *     for the render the zoom produced — tied to the model and the range it
 *     made — so new data arriving later is still announced as an update, not
 *     as a zoom the reader made minutes ago;
 *   - the note for the footer and the export, and the summary's scope;
 *   - "Apply as time filter": whether this range can become the page window,
 *     and the write through the report filter bar's own setter.
 */
import { useCallback, useMemo, useState } from 'react'
import { useChartAnnouncer } from '../ChartAnnouncer'
import type { ChartRangeBrushProps } from './ChartRangeBrush'
import { useChartZoom } from './useChartZoom'
import { windowAppliedAnnouncement } from './windowWords'
import {
  promoteDecision,
  resolveZoom,
  sameRange,
  zoomChangeLabel,
  zoomKeysFor,
  zoomNote,
  zoomResetLabel,
  zoomScopeLabel,
  type AxisWords,
  type ChartZoomOptions,
  type ZoomRange,
} from './zoomModel'

interface ZoomAction {
  label: string
  /** The model the zoom was made on. */
  model: unknown
  /** The range it produced. */
  range: ZoomRange | null
}

export interface FrameZoomInput {
  /** `null` = the frame is not zoomable; nothing below applies. */
  options: ChartZoomOptions | null
  /** The FULL model's x keys. */
  xs: readonly string[]
  /** The full model (identity only): a new model is new data, not a zoom. */
  model: unknown
  title: string
  words: AxisWords
  /** The axis is relative days: never a page window. */
  relative?: boolean
  /** Trend statistics are on (they stay whole-window while zoomed). */
  trendAnalysis?: boolean
  /** Release markers the zoom takes off the plot. */
  markersOutside?: (range: ZoomRange) => number
}

export interface FrameZoom {
  range: ZoomRange | null
  /** Props for `ChartRangeBrush`, or `null` when no brush is drawn. */
  brush: ChartRangeBrushProps | null
  /** The frame's `changeLabel` while the latest change is the reader's zoom. */
  changeLabel: string | undefined
  /** The footer / export note while zoomed. */
  note: string | undefined
  /** The summary's scope, with the zoom added while zoomed. */
  scopeLabel: (base: string | undefined) => string | undefined
}

export function useFrameZoom({
  options,
  xs,
  model,
  title,
  words,
  relative = false,
  trendAnalysis = false,
  markersOutside,
}: FrameZoomInput): FrameZoom {
  const zoom = useChartZoom(xs, options?.initial)
  const range = options ? zoom.range : null
  const announcer = useChartAnnouncer()
  const [action, setAction] = useState<ZoomAction | null>(null)
  const { setRange, setWindowDays, windowDays } = zoom

  const onRangeChange = useCallback(
    (next: ZoomRange | null) => {
      const keys = zoomKeysFor(xs, next)
      setAction({
        label: keys ? zoomChangeLabel(words, keys.from, keys.to) : zoomResetLabel(xs.length),
        model,
        range: keys ? resolveZoom(xs, keys) : null,
      })
      setRange(next)
    },
    [xs, words, model, setRange],
  )

  const promote = useMemo(
    () =>
      options?.applyAsWindow && range
        ? promoteDecision({
            xs,
            range,
            windowOptions: options.applyAsWindow.windowOptions,
            currentWindowDays: windowDays,
            relative,
          })
        : null,
    [options, range, xs, windowDays, relative],
  )

  const onPromote = useCallback(
    (days: number) => {
      // The report filter bar's own setter: the store, and — with
      // `viz_multi_filters` on — `?window=` through the URL sync. The new
      // window changes the scope, and that clears this zoom.
      setWindowDays(days)
      // Keyed on the title, not a `useId`: an id here would renumber every id
      // an unzoomable frame renders, for a report only a zoomable one makes.
      announcer?.report(`zoom-window:${title}`, title, windowAppliedAnnouncement(days), 'summary')
    },
    [setWindowDays, announcer, title],
  )

  const zoomedKeys = range ? { from: xs[range.start], to: xs[range.end] } : null
  const note = zoomedKeys
    ? zoomNote({
        words,
        from: zoomedKeys.from,
        to: zoomedKeys.to,
        shown: (range as ZoomRange).end - (range as ZoomRange).start + 1,
        total: xs.length,
        trendAnalysis,
        markersOutside: markersOutside ? markersOutside(range as ZoomRange) : 0,
      })
    : undefined

  const changeLabel =
    action !== null && action.model === model && sameRange(action.range, range) ? action.label : undefined

  // A string in, a string out: the frame's summary memo sees an equal value.
  const scopeLabel = (base: string | undefined) =>
    zoomedKeys ? zoomScopeLabel(words, zoomedKeys.from, zoomedKeys.to, base) : base

  const brush: ChartRangeBrushProps | null =
    options && xs.length > 1
      ? { xs, range, onRangeChange, title, words, promote, onPromote }
      : null

  return { range, brush, changeLabel, note, scopeLabel }
}
