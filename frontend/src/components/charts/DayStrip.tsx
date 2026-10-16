/**
 * `DayStrip` (VIZ-104 package c, kit gap K5): one row of cells, one per day —
 * or per build: the strip is unit-agnostic and `unit` says which — oldest on
 * the left. It replaces five hand-drawn strips: the Trends and Coverage run
 * cadence heatmaps, FailureAnalysis' failure timeline and 14-day run strip,
 * and Runs' build velocity. What each cell means and how it is painted is in
 * `dayStrip.model.ts`; this file only lays it out.
 *
 * Reading it, four ways, none of them colour alone:
 *
 *   sight     the cells (`role="img"`, named by the page's aggregate `label`),
 *             failures carrying the kit's diagonal decal, and a legend in text.
 *   pointer   each cell's `title`, as the old strips had.
 *   keyboard  the kit's chart cursor (`useChartCursor`): the strip is one tab
 *             stop, the arrow keys / Home / End walk the cells, the focused
 *             cell is outlined and its text drawn BELOW the strip, and the
 *             page's one announcer speaks it. The strip has no live region of
 *             its own (VIZ-105).
 *   reader    an sr-only table, one row per cell (the table alternative VIZ-105
 *             asks every chart for).
 *
 * TRND-04: `onSelectRange` (optional) lets a page take a mouse range off the
 * strip — press on a cell, drag, release; a click is one cell — reported as
 * the first and last cell keys, the dragged cells outlined in the accent
 * meanwhile. Without the prop nothing is attached and the markup is unchanged.
 *
 * No engine and no SVG: a strip of a few dozen boxes needs neither, and the
 * ECharts heatmap would pull its ~170 kB chunk onto three pages for nothing.
 * The only text is the legend row, which is HTML that wraps — nothing is sized
 * to one font, so it holds on the Linux runner's wider DejaVu Sans.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import { useChartCursor, type ChartCursorPoint } from './ChartCursor'
import {
  FAILURE_CUE_FILL,
  MIXED_BAND_HEIGHT,
  buildDayStripModel,
  defaultStartLabel,
  inSelection,
  selectionKeys,
  type CellFace,
  type DaySelection,
  type DayStripCell,
  type DayStripMode,
  type DayStripText,
} from './dayStrip.model'
import { CHART_VARS } from './tokens'

export type { DayStripCell, DayStripLevel, DayStripMarker, DayStripMode, DayStripTone, DayStripText } from './dayStrip.model'

export interface DayStripProps {
  cells: readonly DayStripCell[]
  mode: DayStripMode
  /** The aggregate accessible name of the strip ("Run cadence: 3 empty days, 11 days with runs"). */
  label: string
  /** A short name for the keyboard surface and what it announces. Default: `label`. */
  title?: string
  /** What one cell is, singular: "day" (default), "build". */
  unit?: string
  /** `'square'` (default) or a fixed height in px (the FailureAnalysis run strip is 14). */
  cellHeight?: 'square' | number
  /** Space between cells, px. Default 3. */
  gap?: number
  /** Draw the legend row (start label, swatches, end label). Default true. */
  legend?: boolean
  /** Left end of the legend row. Default "`N` days ago". */
  startLabel?: string
  /** Right end of the legend row. Default "Today". */
  endLabel?: string
  /** Replace any of the words for states, markers and the legend. */
  text?: Partial<DayStripText>
  className?: string
  /**
   * TRND-04: select a range with the mouse — press on a cell, drag, release
   * (a click is one cell) — and receive the first and last cell KEYS (ISO
   * days on a cadence strip), oldest first. The cells are outlined while
   * dragging. Absent: no listener is attached and the output is exactly what
   * it was.
   */
  onSelectRange?: (fromKey: string, toKey: string) => void
}

const TODAY_RING = `0 0 0 1px ${CHART_VARS.accent}`
const GAP_EDGE_OUTLINE = `1px dashed ${CHART_VARS.status.broken}`
const CURSOR_OUTLINE = `2px solid ${CHART_VARS.text}`
/** The cells of a range being dragged: the accent, solid, so it is not the gap edge's dash. */
const SELECTION_OUTLINE = `2px solid ${CHART_VARS.accent}`

/** The failure cue drawn over a face: the whole cell, or its bottom (failed) fifth. */
function FailureCue({ cue }: { cue: CellFace['cue'] }) {
  if (!cue) return null
  const style: CSSProperties =
    cue === 'fail'
      ? { position: 'absolute', inset: 0, background: FAILURE_CUE_FILL }
      : {
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: 0,
          height: MIXED_BAND_HEIGHT,
          background: FAILURE_CUE_FILL,
          // A card-coloured edge on the band: the 80/20 split is a shape, not
          // only a change of hue.
          borderTop: `1px solid ${CHART_VARS.card}`,
        }
  return <span aria-hidden="true" data-day-cue={cue} style={style} />
}

function Swatch({ face }: { face: CellFace }) {
  return (
    <i
      aria-hidden="true"
      data-day-swatch=""
      className="relative inline-block h-2 w-2 shrink-0 overflow-hidden rounded-sm"
      style={{ background: face.background, border: face.border }}
    >
      <FailureCue cue={face.cue} />
    </i>
  )
}

const capitalise = (word: string) => word.charAt(0).toUpperCase() + word.slice(1)

export default function DayStrip({
  cells,
  mode,
  label,
  title,
  unit = 'day',
  cellHeight = 'square',
  gap = 3,
  legend = true,
  startLabel,
  endLabel,
  text,
  className,
  onSelectRange,
}: DayStripProps) {
  const model = useMemo(() => buildDayStripModel(cells, mode, text), [cells, mode, text])
  const points = useMemo<ChartCursorPoint[]>(
    () => model.cells.map((cell, index) => ({ key: `${index}:${cell.key}`, text: cell.text })),
    [model],
  )
  const cursor = useChartCursor({ title: title ?? label, chartType: 'day strip', points, noun: unit })

  // TRND-04: the mouse range selection, only when the page asked for it.
  const selectable = typeof onSelectRange === 'function'
  const [drag, setDrag] = useState<DaySelection | null>(null)
  const dragRef = useRef<DaySelection | null>(null)
  const onSelectRef = useRef(onSelectRange)
  const keysRef = useRef(model.cells)
  useEffect(() => {
    onSelectRef.current = onSelectRange
    keysRef.current = model.cells
  })
  const finish = useCallback(() => {
    const current = dragRef.current
    dragRef.current = null
    setDrag(null)
    const keys = selectionKeys(keysRef.current, current)
    if (keys) onSelectRef.current?.(keys.from, keys.to)
  }, [])
  const dragging = drag !== null
  useEffect(() => {
    // A release outside the strip still ends the drag (on the last cell the pointer crossed).
    if (!dragging) return
    window.addEventListener('mouseup', finish)
    return () => window.removeEventListener('mouseup', finish)
  }, [dragging, finish])
  const update = (next: DaySelection) => {
    dragRef.current = next
    setDrag(next)
  }

  if (model.cells.length === 0) return null

  const count = model.cells.length

  return (
    // `relative`: the sr-only table below is `position: absolute`, and without a
    // positioned ancestor its containing block was the page, so a strip inside a
    // sideways-scrolling box (a narrow window) widened the whole page instead
    // (the gallery's phone-width check caught 661 px in a 375 px window).
    <div data-day-strip={mode} className={['relative', className].filter(Boolean).join(' ')}>
      <div className="rounded-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]" {...cursor.surfaceProps}>
        <div
          role="img"
          aria-label={label}
          className="grid"
          style={{ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))`, gap }}
        >
          {model.cells.map((cell, index) => {
            const active = index === cursor.index
            const selected = selectable && inSelection(drag, index)
            const selection = selectable
              ? {
                  onMouseDown: (event: ReactMouseEvent) => {
                    if (event.button !== 0) return
                    // No text selection while dragging across the cells.
                    event.preventDefault()
                    update({ anchor: index, current: index })
                  },
                  onMouseEnter: () => {
                    const current = dragRef.current
                    if (current) update({ anchor: current.anchor, current: index })
                  },
                  // Released on a cell: that cell ends the range (the window listener covers a release elsewhere).
                  onMouseUp: () => {
                    const current = dragRef.current
                    if (current) dragRef.current = { anchor: current.anchor, current: index }
                    finish()
                  },
                }
              : {}
            return (
              <div
                key={`${index}:${cell.key}`}
                title={cell.label}
                data-day-cell=""
                data-tone={cell.tone}
                data-level={mode === 'intensity' ? cell.level : undefined}
                data-marker={cell.marker ?? undefined}
                data-cursor-active={active ? '' : undefined}
                data-day-selected={selected ? '' : undefined}
                className="relative overflow-hidden rounded-sm"
                {...selection}
                style={{
                  ...(cellHeight === 'square' ? { aspectRatio: '1' } : { height: cellHeight }),
                  background: cell.background,
                  border: cell.border,
                  boxShadow: cell.marker === 'today' ? TODAY_RING : 'none',
                  // The gap edge is dashed and today is solid, so the two
                  // markers differ in shape, not only in hue.
                  outline: active
                    ? CURSOR_OUTLINE
                    : selected
                      ? SELECTION_OUTLINE
                      : cell.marker === 'gap-edge'
                        ? GAP_EDGE_OUTLINE
                        : undefined,
                  outlineOffset: 1,
                  ...(selectable ? { cursor: 'crosshair' } : {}),
                }}
              >
                <FailureCue cue={cell.cue} />
              </div>
            )
          })}
        </div>
      </div>
      {cursor.readout}
      {legend && (
        // `--color-text-secondary`, the kit's token for small text: `--color-text-muted`
        // is under 4.5:1 on the light theme (4.15), which the gallery's axe gate caught.
        <div
          data-day-strip-legend=""
          className="mt-2.5 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[10px] text-[var(--color-text-secondary)]"
        >
          <span>{startLabel ?? defaultStartLabel(count, unit, model.text.today)}</span>
          {mode === 'intensity' ? (
            <span className="inline-flex flex-wrap items-center gap-1">
              {model.text.less}
              {model.legend
                .filter((item) => item.key.startsWith('level-'))
                .map((item) => (
                  <Swatch key={item.key} face={item.face} />
                ))}
              {model.text.more}
              {model.legend
                .filter((item) => !item.key.startsWith('level-'))
                .map((item) => (
                  <span key={item.key} className="ml-1.5 inline-flex items-center gap-1">
                    <Swatch face={item.face} />
                    {item.label}
                  </span>
                ))}
            </span>
          ) : (
            <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
              {model.legend.map((item) => (
                <span key={item.key} data-legend-tone={item.key} className="inline-flex items-center gap-1">
                  <Swatch face={item.face} />
                  {item.label}
                </span>
              ))}
            </span>
          )}
          <span>{endLabel ?? model.text.today}</span>
        </div>
      )}
      {/* The sr-only box is a DIV around the table, not the table itself: a table
          ignores `width: 1px` and sizes to its content, so an sr-only TABLE still
          added its full width and height to the scrollable overflow of whatever
          box held the strip (708 px of scroll under a 160 px strip). */}
      <div className="sr-only" data-day-strip-sr="">
      <table data-day-strip-table="">
        <caption>{label}</caption>
        <thead>
          <tr>
            <th scope="col">{capitalise(unit)}</th>
            <th scope="col">State</th>
            <th scope="col">Marker</th>
          </tr>
        </thead>
        <tbody>
          {model.cells.map((cell, index) => (
            <tr key={`${index}:${cell.key}`}>
              <th scope="row">{cell.label}</th>
              <td>{cell.state}</td>
              <td>{cell.markerText ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  )
}
