/**
 * Where a tooltip goes (VIZ-601, "Placement"): BESIDE the mark it describes,
 * never over it; flipped to the other side near an edge; and always inside the
 * part of the chart the reader can actually see — the chart's own box, cut by
 * the viewport and by every scrolling ancestor the chart sits in (a table
 * view's scroll box, a dashboard grid cell, the full-screen body).
 *
 * `MultiSeriesChart` worked this out first for its day tooltip (VIZ-404 fix A,
 * M8): a fixed offset from the day's x, at the top of the plot, flipped left
 * at the chart's right edge. This is that rule, generalised to any mark and
 * any side, as PURE functions over rectangles so the geometry is tested with
 * tables rather than with a browser:
 *
 *   `placeTip`       the placement itself.
 *   `visibleBounds`  the chart box ∩ the clip rectangles, in chart coordinates.
 *   `columnMark` /   the mark of a category column (a day, a histogram bucket)
 *   `bandGap`        and how far from it the tooltip may start while staying
 *                    INSIDE that column's band — so a pointer moving straight
 *                    onto the tooltip never crosses into the next day first,
 *                    and the tooltip is hoverable (SC 1.4.13).
 *
 * The one DOM function, `clipRectsOf`, only reads layout; it is the seam the
 * React side (`PinnedTip` in `ChartTooltip.tsx`) and the ECharts `position`
 * callbacks share.
 */

export interface TipRect {
  left: number
  top: number
  width: number
  height: number
}

export interface TipSize {
  width: number
  height: number
}

export type TipSide = 'right' | 'left' | 'below' | 'above'

/** How far a tooltip sits from its mark. Also `MultiSeriesChart`'s `TIP_GAP`. */
export const TIP_GAP = 12

/** Beside a column (a day, a bucket): left or right only — above or below would sit over the axis. */
export const COLUMN_SIDES: readonly TipSide[] = ['right', 'left']
/** Beside a bar or a ring: either side, then below or above it. */
export const ANY_SIDE: readonly TipSide[] = ['right', 'left', 'below', 'above']

export interface PlaceTipInput {
  /** The mark, in the same coordinates as `bounds`. A point is a 0×0 rect. */
  mark: TipRect
  /** The tooltip's measured size. */
  tip: TipSize
  /** Where the tooltip must stay: the visible part of the chart (`visibleBounds`). */
  bounds: TipRect
  /** Space between mark and tooltip. Default `TIP_GAP`. */
  gap?: number
  /** Sides to try, in order. Default `ANY_SIDE`. */
  sides?: readonly TipSide[]
  /**
   * The cross axis: `'start'` lines the tooltip up with the mark's top (or
   * left) edge — a column's tooltip sits at the top of the plot; `'center'`
   * centres it on the mark — a bar's tooltip sits level with the bar.
   */
  align?: 'start' | 'center'
  /**
   * The pointer's line along the category axis (`TipSweep`): a side that runs
   * along that line keeps the tooltip OFF it, or does not fit.
   */
  sweep?: TipSweep
}

/**
 * A pointer reading a chart sweeps ALONG its category axis: across the days
 * of a time series (`axis: 'x'`, the pointer's line is `y = at`), down the
 * rows of a ranked bar chart (`axis: 'y'`, the line `x = at`).
 *
 * A tooltip beside a day covers the next few days. Were it drawn ON the
 * pointer's line, a pointer sweeping on to the next day would run straight
 * into it — and a hoverable tooltip holds its day while the pointer is on
 * it, so the sweep would stick for the tooltip's whole width (the Wave 2.4
 * review's A2/F3). So a side whose main axis runs along the sweep (left or
 * right of a day, below or above a row) must keep the tooltip's cross extent
 * clear of the line by `SWEEP_MARGIN`: at the mark's start, at its end, or
 * just before or after the line — whichever fits first. A side ACROSS the
 * sweep (right of a ranked bar, which a pointer moving down the rows never
 * approaches through its facing edge) needs nothing: see `approachStep`.
 */
export interface TipSweep {
  axis: 'x' | 'y'
  /** The pointer's cross coordinate, in the same coordinates as the mark. */
  at: number
}

/** How far a tooltip on the sweep's path keeps off the pointer's line. */
export const SWEEP_MARGIN = 12

/** Whether `side` runs along the sweep: the pointer, sweeping on, would meet a tooltip there. */
export function alongSweep(side: TipSide, sweep: TipSweep | undefined): boolean {
  if (!sweep) return false
  return sweep.axis === 'x' ? side === 'left' || side === 'right' : side === 'below' || side === 'above'
}

export interface TipPlacement {
  left: number
  top: number
  side: TipSide
  /**
   * False only when NO side had room and the tooltip was clamped into the
   * bounds anyway (a tooltip larger than the chart can hold). Visible beats
   * clear of the mark: a tooltip pushed out of view helps nobody. `PinnedTip`
   * first tries to avoid this by wrapping the box narrower (`tipMaxWidth`).
   */
  fits: boolean
}

const right = (r: TipRect) => r.left + r.width
const bottom = (r: TipRect) => r.top + r.height

/** `value` kept in `[min, max]`; a range that cannot hold it pins it to `min`. */
export function clamp(value: number, min: number, max: number): number {
  return max < min ? min : Math.min(Math.max(value, min), max)
}

function cross(markStart: number, markSize: number, tipSize: number, align: 'start' | 'center'): number {
  return align === 'start' ? markStart : markStart + markSize / 2 - tipSize / 2
}

type CandidateInput = Required<Omit<PlaceTipInput, 'sides' | 'sweep'>> & Pick<PlaceTipInput, 'sweep'>

/**
 * The cross-axis position on one side: aligned as asked, clamped into the
 * bounds — or, on a side along the sweep, the first of (aligned, the mark's
 * start, the mark's end, just before the pointer's line, just after it) that
 * keeps the whole tooltip off the line. `null`: nothing does.
 */
function crossPosition(
  markStart: number,
  markSize: number,
  size: number,
  boundsStart: number,
  boundsSize: number,
  align: 'start' | 'center',
  line: number | null,
): number | null {
  const fit = (value: number) => clamp(value, boundsStart, boundsStart + boundsSize - size)
  const aligned = fit(cross(markStart, markSize, size, align))
  if (line === null) return aligned
  const clear = (value: number) => value + size <= line - SWEEP_MARGIN || value >= line + SWEEP_MARGIN
  const options = [aligned, fit(markStart), fit(markStart + markSize - size), fit(line - SWEEP_MARGIN - size), fit(line + SWEEP_MARGIN)]
  return options.find(clear) ?? null
}

/** The tooltip's position on `side`, with its main axis unclamped and its cross axis clamped. */
function candidate(side: TipSide, { mark, tip, bounds, gap, align, sweep }: CandidateInput) {
  const line = sweep && alongSweep(side, sweep) ? sweep.at : null
  if (side === 'right' || side === 'left') {
    const left = side === 'right' ? right(mark) + gap : mark.left - gap - tip.width
    const top = crossPosition(mark.top, mark.height, tip.height, bounds.top, bounds.height, align, line)
    const room = side === 'right' ? right(bounds) - (right(mark) + gap) : mark.left - gap - bounds.left
    // Off the pointer's line nowhere: this side does not fit, however wide it is.
    if (top === null) return { left, top: clamp(mark.top, bounds.top, bottom(bounds) - tip.height), room: -Infinity, need: tip.width }
    return { left, top, room, need: tip.width }
  }
  const top = side === 'below' ? bottom(mark) + gap : mark.top - gap - tip.height
  const left = crossPosition(mark.left, mark.width, tip.width, bounds.left, bounds.width, align, line)
  const room = side === 'below' ? bottom(bounds) - (bottom(mark) + gap) : mark.top - gap - bounds.top
  if (left === null) return { left: clamp(mark.left, bounds.left, right(bounds) - tip.width), top, room: -Infinity, need: tip.height }
  return { left, top, room, need: tip.height }
}

/**
 * The first side, in `sides` order, where the whole tooltip fits inside
 * `bounds` without touching the mark. On a side that fits the tooltip is
 * separated from the mark along that side's axis by at least `gap`, so it
 * cannot cover it. When no side fits, the roomiest one is used and the
 * tooltip is clamped into the bounds (`fits: false`).
 */
export function placeTip({ mark, tip, bounds, gap = TIP_GAP, sides = ANY_SIDE, align = 'center', sweep }: PlaceTipInput): TipPlacement {
  const input = { mark, tip, bounds, gap, align, sweep }
  const tried = (sides.length ? sides : ANY_SIDE).map((side) => ({ side, ...candidate(side, input) }))
  const fit = tried.find((c) => c.room >= c.need)
  if (fit) return { left: fit.left, top: fit.top, side: fit.side, fits: true }
  const roomiest = tried.reduce((best, c) => (c.room > best.room ? c : best))
  return {
    left: clamp(roomiest.left, bounds.left, right(bounds) - tip.width),
    top: clamp(roomiest.top, bounds.top, bottom(bounds) - tip.height),
    side: roomiest.side,
    fits: false,
  }
}

/**
 * The narrowest a tooltip is squeezed to so it can sit BESIDE its mark. Under
 * this its lines wrap into a ribbon a word or two wide, which reads worse than
 * a tooltip clamped over the edge of the mark.
 */
export const TIP_MIN_WIDTH = 160

/**
 * How wide a tooltip may be drawn so that it still fits BESIDE its mark.
 *
 * `placeTip` falls back to clamping over the mark when no side has room for
 * the tooltip at its natural size — which on a phone, or on the right-most
 * day of a narrow chart, is every side: a 340 px tooltip cannot sit left or
 * right of anything in a 420 px window. Most of that width is one long line
 * (a release name, a local-time range) that can WRAP. So when the natural
 * size fits nowhere, this returns the room on the roomiest left/right side in
 * `sides` — the width to wrap to, after which the taller, narrower box is
 * placed again — as long as that room is at least `TIP_MIN_WIDTH`. Otherwise
 * (a side already fits, the sides are vertical only, or every side is too
 * cramped to be worth it) it returns `bounds.width`, which only ever stops
 * the box being wider than what the reader can see.
 */
export function tipMaxWidth({
  mark,
  tip,
  bounds,
  gap = TIP_GAP,
  sides = ANY_SIDE,
  align = 'center',
  sweep,
}: PlaceTipInput): number {
  const full = bounds.width
  if (placeTip({ mark, tip, bounds, gap, sides, align, sweep }).fits) return full
  // The WIDTH available on each side — a side the pointer's line blocks is
  // judged after wrapping, by `placeTip` again, so it is measured unblocked here.
  const rooms = (sides.length ? sides : ANY_SIDE)
    .filter((side) => side === 'left' || side === 'right')
    .map((side) => candidate(side, { mark, tip, bounds, gap, align }).room)
  if (rooms.length === 0) return full
  const best = Math.floor(Math.max(...rooms))
  return best >= TIP_MIN_WIDTH && best < tip.width ? best : full
}

/** The overlap of two rectangles, or `null` when they do not overlap. */
export function intersect(a: TipRect, b: TipRect): TipRect | null {
  const left = Math.max(a.left, b.left)
  const top = Math.max(a.top, b.top)
  const r = Math.min(right(a), right(b))
  const btm = Math.min(bottom(a), bottom(b))
  return r > left && btm > top ? { left, top, width: r - left, height: btm - top } : null
}

/**
 * The part of the chart the reader can see, in CHART coordinates (0,0 = the
 * chart box's top-left). `chart` and `clips` are in viewport coordinates
 * (`getBoundingClientRect`). A chart scrolled wholly out of view has nothing
 * visible; the chart box itself is returned then, since there is no better
 * place and no reader looking.
 */
export function visibleBounds(chart: TipRect, clips: readonly TipRect[]): TipRect {
  let seen: TipRect | null = chart
  for (const clip of clips) {
    seen = seen && intersect(seen, clip)
  }
  const box = seen ?? chart
  return { left: box.left - chart.left, top: box.top - chart.top, width: box.width, height: box.height }
}

/** A category column's mark: `halfWidth` either side of its centre `x`, over the plot's height. */
export function columnMark(x: number, halfWidth: number, plot: { top: number; height: number }): TipRect {
  const half = Math.max(0, halfWidth)
  return { left: x - half, top: plot.top, width: half * 2, height: Math.max(0, plot.height) }
}

/**
 * The gap that keeps a column's tooltip starting INSIDE the column's band:
 * never more than `max`, never so much that the pointer would cross into the
 * neighbouring band on its way over. `band` is the width one category
 * occupies; `markHalf` is half the drawn mark (a bar, a dot).
 */
export function bandGap(band: number, markHalf: number, max = TIP_GAP): number {
  return clamp(band / 2 - Math.max(0, markHalf), 0, max)
}

/// ── The approach: when the tooltip may hold the pointer ──────────────────────
//
// SC 1.4.13 (Hoverable) asks that a reader can move the pointer ONTO a
// tooltip without it going. It does not ask the tooltip to block every mark
// beneath it. A tooltip that takes every move made over it (as VIZ-601 first
// did) turns a sweep across the days into a stuck day: the box beside day 1
// covers days 2-4, and the chart saw no move until the pointer left the box.
//
// So the tooltip holds the pointer only when the pointer came to it FROM ITS
// MARK: a move that starts in the corridor between the mark and the tooltip
// and heads for the tooltip. Everything else — a sweep down the rows running
// into it, a pointer arriving from anywhere else — passes through to the
// chart, which picks the mark under the pointer, and the tooltip moves beside
// that mark instead. `approachStep` is that rule, one pointer move at a time;
// `PinnedTip` feeds it the chart's own `mousemove`s.

export interface TipPoint {
  x: number
  y: number
}

/** How far INTO the mark the corridor starts: the pointer begins its approach on the mark itself. */
export const CORRIDOR_INSET = 12
/** How far past the ends of the tooltip's facing edge a pointer may aim and still count as heading for it. */
export const AIM_TOLERANCE = 4

/**
 * The corridor: from `CORRIDOR_INSET` inside the mark's side that faces the
 * tooltip (never more than the mark is thick) to the tooltip's facing edge,
 * across the span the mark and the tooltip cover together.
 */
export function tipCorridor(mark: TipRect, tip: TipRect, side: TipSide, inset = CORRIDOR_INSET): TipRect {
  if (side === 'right' || side === 'left') {
    const into = Math.min(inset, mark.width)
    const from = side === 'right' ? right(mark) - into : right(tip)
    const to = side === 'right' ? tip.left : mark.left + into
    const top = Math.min(mark.top, tip.top)
    return { left: from, top, width: Math.max(0, to - from), height: Math.max(bottom(mark), bottom(tip)) - top }
  }
  const into = Math.min(inset, mark.height)
  const from = side === 'below' ? bottom(mark) - into : bottom(tip)
  const to = side === 'below' ? tip.top : mark.top + into
  const left = Math.min(mark.left, tip.left)
  return { left, top: from, width: Math.max(right(mark), right(tip)) - left, height: Math.max(0, to - from) }
}

/** `p` inside `r` (edges included). */
export function inRect(p: TipPoint, r: TipRect): boolean {
  return p.x >= r.left && p.x <= right(r) && p.y >= r.top && p.y <= bottom(r)
}

/**
 * Whether a pointer that moved from `from` to `to` is heading for `target`:
 * the ray from `from` through `to` meets the rectangle, and `to` is not
 * already past it. Equivalently, `to` lies in the convex hull of `from` and
 * the rectangle — the "safe triangle" a menu uses for a pointer on its way to
 * a submenu. A pointer standing still is heading nowhere.
 */
export function headsFor(from: TipPoint, to: TipPoint, target: TipRect): boolean {
  const dx = to.x - from.x
  const dy = to.y - from.y
  if (dx === 0 && dy === 0) return false
  let enter = -Infinity
  let leave = Infinity
  for (const [origin, delta, low, high] of [
    [from.x, dx, target.left, right(target)],
    [from.y, dy, target.top, bottom(target)],
  ] as const) {
    if (delta === 0) {
      if (origin < low || origin > high) return false
      continue
    }
    const a = (low - origin) / delta
    const b = (high - origin) / delta
    enter = Math.max(enter, Math.min(a, b))
    leave = Math.min(leave, Math.max(a, b))
  }
  // Meets it ahead (`leave >= enter`, `leave >= 0`), and `to` (at 1) is not beyond it.
  return leave >= Math.max(enter, 0) && leave >= 1
}

/**
 * What a pointer may aim at: the tooltip, widened by `AIM_TOLERANCE` ALONG
 * its facing edge only (a pointer aiming just past a corner is still aiming at
 * it) — never pushed out towards the mark, so a pointer moving parallel to the
 * facing edge, just in front of it, is not taken to be heading onto it.
 */
function aimTarget(tip: TipRect, side: TipSide, by = AIM_TOLERANCE): TipRect {
  return side === 'right' || side === 'left'
    ? { left: tip.left, top: tip.top - by, width: tip.width, height: tip.height + by * 2 }
    : { left: tip.left - by, top: tip.top, width: tip.width + by * 2, height: tip.height }
}

/** Where the tooltip is and what it describes, as the approach needs them. */
export interface ApproachGeometry {
  mark: TipRect
  tip: TipRect
  side: TipSide
}

export interface ApproachStep {
  /**
   * Where the approach began (a point in the corridor), while the pointer is
   * on its way to the tooltip or on it; `null` when it is not approaching.
   */
  armed: TipPoint | null
  /** The pointer is ON the tooltip, having come from its mark: the tooltip holds it. */
  hold: boolean
  /**
   * The pointer is between the mark and the tooltip, heading for it: the
   * chart must not re-pick its mark from this move. A pointer aiming for a
   * tooltip that sits above the next days crosses their columns on the way.
   */
  suppress: boolean
}

/**
 * One pointer move, from `prev` to `cur` (chart coordinates), against the
 * tooltip `g`. The approach ARMS when a move starts in the corridor and heads
 * for the tooltip (or lands on it); it stays armed while the pointer keeps
 * heading for the tooltip from where it armed (inside the hull of that point
 * and the tooltip), and while it is on the tooltip. Anything else disarms it.
 *
 * Why a sweep never arms it: a tooltip beside a day is kept off the pointer's
 * line (`TipSweep`), so a pointer moving along that line is heading beside
 * the tooltip, not for it; and a sweep down the rows moves ACROSS a tooltip
 * beside a bar, never from the bar towards it.
 */
export function approachStep(armed: TipPoint | null, prev: TipPoint | null, cur: TipPoint, g: ApproachGeometry): ApproachStep {
  const target = aimTarget(g.tip, g.side)
  const onTip = inRect(cur, g.tip)
  if (armed) {
    if (onTip) return { armed, hold: true, suppress: false }
    if (headsFor(armed, cur, target)) return { armed, hold: false, suppress: true }
  }
  if (prev && inRect(prev, tipCorridor(g.mark, g.tip, g.side)) && (onTip || headsFor(prev, cur, target))) {
    return { armed: prev, hold: onTip, suppress: !onTip }
  }
  return { armed: null, hold: false, suppress: false }
}

/**
 * A tooltip placed along the sweep (`alongSweep`) that the pointer has come
 * onto the line of WITHOUT approaching it — a pointer that moved up or down
 * its day into the tooltip's height. The tooltip is re-placed off the new
 * line, so a sweep from there does not run into it. (Half the margin: a
 * re-placed tooltip is a full `SWEEP_MARGIN` off, so a pointer wobbling on
 * the line does not make it jump back and forth.)
 */
export function onSweepLine(cur: TipPoint, tip: TipRect, side: TipSide, sweep: TipSweep | undefined): boolean {
  if (!sweep || !alongSweep(side, sweep)) return false
  const margin = SWEEP_MARGIN / 2
  return sweep.axis === 'x'
    ? cur.y >= tip.top - margin && cur.y <= bottom(tip) + margin
    : cur.x >= tip.left - margin && cur.x <= right(tip) + margin
}

const OVERFLOWS = new Set(['auto', 'scroll', 'hidden', 'clip', 'overlay'])

function toRect(r: { left: number; top: number; width: number; height: number }): TipRect {
  return { left: r.left, top: r.top, width: r.width, height: r.height }
}

/**
 * Every rectangle that clips `el` on screen, in viewport coordinates: the
 * viewport itself, then each ancestor that scrolls or hides overflow (its
 * padding box, where its content is visible). Reads layout only.
 */
export function clipRectsOf(el: Element): TipRect[] {
  const doc = el.ownerDocument
  const view = doc.defaultView
  const root = doc.documentElement
  const clips: TipRect[] = [
    { left: 0, top: 0, width: root.clientWidth || view?.innerWidth || 0, height: root.clientHeight || view?.innerHeight || 0 },
  ]
  for (let node = el.parentElement; node && node !== root && node !== doc.body; node = node.parentElement) {
    const style = view?.getComputedStyle(node)
    if (!style) continue
    if (!OVERFLOWS.has(style.overflowX) && !OVERFLOWS.has(style.overflowY) && !OVERFLOWS.has(style.overflow)) continue
    const box = node.getBoundingClientRect()
    clips.push({
      left: box.left + node.clientLeft,
      top: box.top + node.clientTop,
      width: node.clientWidth,
      height: node.clientHeight,
    })
  }
  return clips.filter((clip) => clip.width > 0 && clip.height > 0)
}

/** `visibleBounds` for a chart box element, read from the DOM. */
export function visibleBoundsOf(chart: Element): TipRect {
  return visibleBounds(toRect(chart.getBoundingClientRect()), clipRectsOf(chart))
}

/**
 * The factor a chart element is drawn at on screen: its box on screen over
 * its layout width — `PRESENTATION_SCALE` for a Recharts drawing in full
 * screen (`ChartResponsive`), else 1 (also when it cannot be measured). What
 * is measured with `getBoundingClientRect` is divided by it to get back to
 * the chart's own coordinates, which is what Recharts' geometry is in.
 */
export function chartScaleOf(chart: HTMLElement | null): number {
  if (!chart || chart.offsetWidth <= 0) return 1
  const shown = chart.getBoundingClientRect().width
  return shown > 0 ? shown / chart.offsetWidth : 1
}

/** A rectangle measured on screen, in the coordinates of a chart drawn at `scale`. */
export function unscaled(rect: TipRect, scale: number): TipRect {
  if (scale === 1 || !(scale > 0)) return rect
  return { left: rect.left / scale, top: rect.top / scale, width: rect.width / scale, height: rect.height / scale }
}

// ── ECharts ──────────────────────────────────────────────────────────────────

/** ECharts' `tooltip.position` size argument, as far as placement needs it. */
export interface EchartsTipSizes {
  contentSize: number[]
  viewSize: number[]
}

/** The item rectangle ECharts passes an item-triggered tooltip (a heatmap cell). */
export interface EchartsItemRect {
  x: number
  y: number
  width: number
  height: number
}

/** The mark an ECharts tooltip describes: from the item's `rect` (item trigger) or the pointer (axis trigger). */
export type EchartsMarkOf = (point: readonly number[], rect: EchartsItemRect | undefined, view: TipSize) => TipRect | null

/**
 * An ECharts `tooltip.position` callback with the SAME placement rule as the
 * React tooltips: beside the mark, flipped at an edge, inside the visible part
 * of the chart. It returns a position only — never content — so it is not a
 * formatter and carries none of a formatter's risk. `dom` is the tooltip's
 * element; its parent is the chart container ECharts appends it to.
 */
export function echartsTipPosition(
  markOf: EchartsMarkOf,
  { sides = ANY_SIDE, align = 'center', gap = TIP_GAP }: Pick<PlaceTipInput, 'sides' | 'align' | 'gap'> = {},
) {
  return (
    point: number[],
    _params: unknown,
    dom: unknown,
    rect: EchartsItemRect | null | undefined,
    size: EchartsTipSizes,
  ): [number, number] => {
    const view = { width: size.viewSize[0] ?? 0, height: size.viewSize[1] ?? 0 }
    const container = typeof Element !== 'undefined' && dom instanceof Element ? dom.parentElement : null
    const measured = container?.getBoundingClientRect()
    const bounds =
      container && measured && measured.width > 0 && measured.height > 0
        ? visibleBoundsOf(container)
        : { left: 0, top: 0, width: view.width, height: view.height }
    const mark = markOf(point, rect ?? undefined, view) ?? { left: point[0] ?? 0, top: point[1] ?? 0, width: 0, height: 0 }
    const placed = placeTip({
      mark,
      tip: { width: size.contentSize[0] ?? 0, height: size.contentSize[1] ?? 0 },
      bounds,
      sides,
      align,
      gap,
    })
    return [placed.left, placed.top]
  }
}
