/**
 * The circle views' shared geometry and items (VIZ-504): where the plot sits
 * beside its readout, how a label is fitted into a circle, the keyboard's walk,
 * and the plot items a list of failure groups becomes. Pure: no React, no d3.
 */
import { formatNumber } from '@/utils/formatters'
import { middleTruncate } from '../labelTruncate'
import type { ChartMark } from '../marks'
import type { TooltipContent } from '../tooltip'
import type { NavRequest } from '../useChartKeyboard'
import type { FailureCategoryKey } from './categoryPatterns'
import { categoryKey, groupMark, groupTipContent, type FailureGroup } from './failureGroups.model'

export interface PlotNode {
  id: string
  x: number
  y: number
  r: number
}

export interface PlotLink {
  source: string
  target: string
  weight: number
}

export interface PlotItem {
  id: string
  /** 1-based, the keyboard order. */
  rank: number
  /** Drawn on the circle when it is among the first `labelled` and fits. Untrusted text. */
  label: string
  /** The pattern and colour; `null` draws the neutral fill (a view whose colour means nothing). */
  category: FailureCategoryKey | null
  tip: TooltipContent
  /** What an activation hands the host; `null` = this item cannot be activated. */
  mark: ChartMark | null
}

export interface PlotLayout {
  nodes: readonly PlotNode[]
  links?: readonly PlotLink[]
  /** A sentence about how the layout drew the data (the enlarged smallest circles), shown with the readout. */
  note?: string | null
}

export interface PlotBox {
  width: number
  height: number
}

/**
 * The body height a DRAWN empty answer holds, px — the clusters' "no cluster"
 * sentence, the groups' "no two failures share a first line": one sentence, not
 * the plot's height (R2-B F-14: a ~250 px empty band under it). The loading
 * skeleton still holds the plot's height; only a settled empty answer is short.
 */
export const EMPTY_ANSWER_HEIGHT = 120

/** The readout column's width beside the plot, px; below it, the readout wraps under the plot. */
export const READOUT_WIDTH = 240
export const PLOT_GAP = 16

/** The plot box for a container `width` px wide: the readout beside it when it fits, else under it. */
export function plotBox(width: number, height: number, shape: 'square' | 'wide'): PlotBox & { beside: boolean } {
  const beside = width >= height + READOUT_WIDTH + PLOT_GAP || (shape === 'wide' && width >= 2 * READOUT_WIDTH + PLOT_GAP)
  const room = beside ? width - READOUT_WIDTH - PLOT_GAP : width
  if (shape === 'square') {
    const side = Math.max(0, Math.min(room, height))
    return { width: side, height: side, beside }
  }
  return { width: Math.max(0, room), height, beside }
}

/**
 * The fewest characters OF THE LABEL a cut keeps (the "…" not counted);
 * shorter than that, no label. "co…or", "<i…>" and "Err…3" (R2-B F-01) named
 * nothing a reader could match to the table; the readout and the table name
 * every circle in full.
 */
export const MIN_VISIBLE_CHARS = 6
/** A cut must save at least this many characters, or it is not made (R2-B F-12: "const…ctor" for "constructor"). */
export const MIN_SAVED_CHARS = 3

/**
 * The longest text that fits `room` px — the whole label, or its MIDDLE cut out
 * (two error lines often share a long head and differ at the end) — measured,
 * never counted, so DejaVu Sans on Linux is not cut where Segoe UI fits.
 *
 * A cut that would save fewer than `MIN_SAVED_CHARS` characters is not made:
 * the whole label is drawn instead when it fits `wholeRoom` (the circle's own
 * width, looser than the label band `room`), else the next real cut is.
 * `null` when no cut keeping `MIN_VISIBLE_CHARS` characters fits.
 */
export function fitLabel(
  label: string,
  room: number,
  measure: (text: string) => number,
  wholeRoom = room,
): string | null {
  const width = measure(label)
  if (width <= room) return label
  const length = [...label].length
  if (width <= wholeRoom) return label
  for (let chars = length - MIN_SAVED_CHARS; chars - 1 >= MIN_VISIBLE_CHARS; chars--) {
    const cut = middleTruncate(label, chars)
    if (measure(cut) <= room) return cut
  }
  return null
}

/**
 * `text` in at most `max` characters, its MIDDLE cut out on WORD boundaries:
 * whole words are taken from the head and the tail in turn while they fit, so
 * no word is split at the "…" (R2-B F-23: "Drill into TimeoutError: waiting
 * fo… / imeout 30000ms exceeded"). A text without a usable word boundary (one
 * long token, a hostile one-word line) falls back to the character cut.
 */
export function wordTruncate(text: string, max: number): string {
  if ([...text].length <= max) return text
  const words = text.split(/\s+/).filter(Boolean)
  const size = (part: readonly string[]) =>
    part.reduce((sum, word) => sum + [...word].length, 0) + Math.max(0, part.length - 1)
  let head: string[] = []
  let tail: string[] = []
  let first = 0
  let last = words.length - 1
  let fromHead = true
  let misses = 0
  while (first <= last && misses < 2) {
    const nextHead = fromHead ? [...head, words[first]] : head
    const nextTail = fromHead ? tail : [words[last], ...tail]
    if (size(nextHead) + 1 + size(nextTail) <= max) {
      if (fromHead) {
        head = nextHead
        first++
      } else {
        tail = nextTail
        last--
      }
      misses = 0
    } else {
      misses++
    }
    fromHead = !fromHead
  }
  // Every word fitted once the runs of white space were one space each: nothing to cut.
  if (first > last) return [...head, ...tail].join(' ')
  if (head.length === 0 || tail.length === 0) return middleTruncate(text, max)
  return `${head.join(' ')}…${tail.join(' ')}`
}

/**
 * The keyboard's walk over `count` circles in RANK order: Right/Down to the
 * next, Left/Up to the previous (stopping at the ends), Home/End to the first
 * and last. Nothing highlighted yet: forward keys start at the first, backward
 * keys at the last.
 */
export const rankWalk =
  (count: number) =>
  (current: number | null, { key }: NavRequest): number | null => {
    if (count === 0) return null
    if (current === null) return key === 'End' || key === 'ArrowLeft' || key === 'ArrowUp' ? count - 1 : 0
    switch (key) {
      case 'ArrowRight':
      case 'ArrowDown':
        return Math.min(count - 1, current + 1)
      case 'ArrowLeft':
      case 'ArrowUp':
        return Math.max(0, current - 1)
      case 'Home':
        return 0
      case 'End':
        return count - 1
    }
  }

/** The plot items of `groups`, in rank order: what the bubble and relation views walk. */
export function groupPlotItems(groups: readonly FailureGroup[]): PlotItem[] {
  return groups.map((group) => ({
    id: group.id,
    rank: group.rank,
    label: group.label,
    category: categoryKey(group.dominantCategory),
    tip: groupTipContent(group),
    mark: groupMark(group),
  }))
}

/**
 * The sentence for enlarged circles (`packLayout`'s floor), or `null` when
 * every circle is drawn at its own size. Area follows failures everywhere else,
 * so a reader comparing two small circles is told where that stops being true.
 */
export function floorNote(floorValue: number): string | null {
  if (floorValue <= 0) return null
  return `Groups with fewer than ${formatNumber(floorValue)} failures are drawn at the smallest size, so they can be pointed at.`
}

/**
 * A raw error line can be one 160-character token: `anywhere` lets it wrap at
 * any character AND shrinks a table cell's minimum width, which `break-words`
 * does not. An inline style, because a Tailwind class no other file uses grows
 * the ONE eager stylesheet (Tailwind emits a rule per distinct class, whatever
 * chunk uses it).
 */
export const WRAP_ANYWHERE = { overflowWrap: 'anywhere' } as const
