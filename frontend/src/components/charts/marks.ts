/**
 * Mark activation (Wave 3, VIZ-602 / VIZ-603): what a chart hands its host
 * when a reader activates one of its marks — a bar, a stacked segment, a
 * heatmap cell, a treemap node, a bubble, a scatter point.
 *
 * Until Wave 3 no kit chart could be activated at all (plan F8): `onClick`
 * existed only on the paging, mode and legend buttons, and the heatmap's
 * `onActivate` only highlighted. This module is the ONE vocabulary every chart
 * and every host shares, so a drill ladder, a rows panel and a cross-filter do
 * not each invent their own:
 *
 *   mark     WHAT was activated: a C5 dimension and the chart's KEY for it
 *            (never the display label: two suites can share a label, and a key
 *            is what the next request sends back), plus the value the chart
 *            drew, so the rows panel can say "the chart counted M".
 *   intent   WHAT the reader wants done with it: drill one level down, open the
 *            rows behind it, or filter the whole page by it.
 *
 * Every prop is optional and defaults to ABSENT, which is today's behaviour: a
 * chart given no `onMarkActivate` attaches no handler, draws no button and
 * renders the exact DOM it renders now (the flag-off pages stay byte-identical).
 *
 * Pure: no React, no DOM, no recharts (a recharts import here would break every
 * explicit `vi.mock('recharts')` factory that lists its exports).
 */
import type { KeyboardEvent as ReactKeyboardEvent, ReactElement, ReactNode } from 'react'
import type { DrillLevel, VizDimension } from '@/lib/viz/contracts'

/** What a reader can ask of an activated mark, in the order a host usually offers them. */
export const MARK_INTENTS = ['drill', 'rows', 'filter'] as const
export type MarkIntent = (typeof MARK_INTENTS)[number]

/** One more dimension a mark sits in: a stacked segment's status, a heatmap cell's column. */
export interface MarkSelector {
  dimension: VizDimension
  /** The chart's KEY for the bucket (a C3 `x_keys` / `y_keys` entry, a fingerprint, a day). Untrusted text. */
  value: string
}

/** The mark a reader activated. */
export interface ChartMark {
  /** The dimension the mark stands for: the bar's category, the cell's row, the node's level. */
  dimension: VizDimension
  /** The chart's KEY for it. Echoed back to the server, never parsed, never shown. */
  value: string
  /** The display text the chart drew for it. Untrusted: React text only. */
  label: string
  /** The value the chart drew (a count, a rate), or `null` for a gap. Never a 0 standing in for null. */
  y: number | null
  /** The sample behind `y` when the chart has one, else `null`. */
  n: number | null
  /**
   * The other dimensions the mark sits in, outermost first: a stacked segment
   * of suite `payments` and status `failed` is `{dimension: 'suite', value:
   * 'payments', context: [{dimension: 'status', value: 'failed'}]}`. Absent
   * for a mark of one dimension.
   */
  context?: readonly MarkSelector[]
}

/** The host's handler: a mark and what the reader asked of it. */
export type MarkActivateHandler = (mark: ChartMark, intent: MarkIntent) => void

/** The intents a host offers for one mark, in the order it wants them (the first is Enter's). */
export type MarkIntentsFor = (mark: ChartMark) => readonly MarkIntent[]

/**
 * The two props every activatable chart takes. Both optional; absent = the
 * chart is exactly what it was before Wave 3.
 */
export interface MarkActivationProps {
  onMarkActivate?: MarkActivateHandler
  /** Default: `DEFAULT_MARK_INTENTS` for every mark. */
  markIntents?: MarkIntentsFor
  /**
   * The activation code for a chart that draws its cursor with
   * `useChartCursor` (the SVG bar chart): `MARK_KIT` from `markKit.ts`, handed
   * in by the HOST with its handler. The cursor and `BarChart` import only
   * this module's types, so the default chart path — every page that draws a
   * bar or a day strip — never downloads the intent rules or the buttons
   * (Wave 3 bundle rule: activation is lazy-section code). Without it a
   * cursor chart is not activatable, handler or not. Canvas charts (heatmap,
   * treemap, scatter, failure groups) live in lazy chunks and import the
   * kit's pieces directly.
   */
  markKit?: MarkKit
}

/** What `useChartCursor` hands the kit on each render of an activatable chart. */
export interface CursorActivationContext {
  points: readonly { mark?: ChartMark }[]
  /** The focused stop, `-1` for none. */
  index: number
  onMarkActivate: MarkActivateHandler
  markIntents?: MarkIntentsFor
  /** Move the cursor to a stop (and speak it). */
  move: (at: number) => void
  /** Close the readout and keep the dismissal (Escape). */
  dismiss: () => void
}

/** The cursor's activation behaviour for one render (`MarkKit.cursor`). */
export interface CursorActivation {
  /**
   * A key on the chart's surface or on one of its readout buttons. `true`:
   * the cursor does nothing more with it (a key on a button is the button's;
   * Enter is activation's whether or not it acted). `false`: the cursor's own
   * keys run as before.
   */
  onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => boolean
  activatePointer: (at: number, modifiers?: PointerModifiers) => void
  activateMark: (mark: ChartMark, modifiers?: PointerModifiers) => boolean
  /** The readout with the focused mark's intents as buttons after its text. */
  readout: (body: ReactNode, at: number) => ReactNode
}

/** The runtime half of mark activation a cursor chart needs (`markKit.tsx` builds the one value). */
export interface MarkKit {
  availableIntents: (mark: ChartMark, props: MarkActivationProps) => MarkIntent[]
  pointerIntent: (available: readonly MarkIntent[], modifiers?: PointerModifiers) => MarkIntent | null
  keyboardIntent: (available: readonly MarkIntent[], modifiers?: { shiftKey?: boolean }) => MarkIntent | null
  /** The readout's buttons (`MarkActions`). */
  Actions: (props: { mark: ChartMark; intents: readonly MarkIntent[]; onActivate: MarkActivateHandler }) => ReactElement | null
  /** The cursor's Enter, pointer and readout behaviour (`useChartCursor` calls it each render). */
  cursor: (context: CursorActivationContext) => CursorActivation
}

/** What a host that passes `onMarkActivate` but no `markIntents` offers. */
export const DEFAULT_MARK_INTENTS: readonly MarkIntent[] = ['drill']

const KNOWN = new Set<string>(MARK_INTENTS)

/**
 * The intents on offer for `mark`: none without a handler (so nothing is
 * drawn and nothing listens), else the host's list with duplicates and unknown
 * values dropped, in the host's order.
 */
export function availableIntents(mark: ChartMark, props: MarkActivationProps): MarkIntent[] {
  if (!props.onMarkActivate) return []
  const asked = props.markIntents ? props.markIntents(mark) : DEFAULT_MARK_INTENTS
  const out: MarkIntent[] = []
  for (const intent of asked) if (KNOWN.has(intent) && !out.includes(intent)) out.push(intent)
  return out
}

export interface PointerModifiers {
  shiftKey?: boolean
  ctrlKey?: boolean
  metaKey?: boolean
  /** `PointerEvent.pointerType` when the chart has it: `'touch'` has no modifier keys. */
  pointerType?: string
}

/**
 * The intent of a pointer activation (OD-2): a plain click is the first intent
 * on offer (a drill, or the rows where there is no level below); Shift, Ctrl or
 * Cmd asks for `filter` when it is on offer, and otherwise is a plain click. A
 * TOUCH tap is `null`: it selects the mark, and the readout's buttons offer
 * the intents, because a finger has no modifier key to choose with.
 */
export function pointerIntent(available: readonly MarkIntent[], modifiers: PointerModifiers = {}): MarkIntent | null {
  if (available.length === 0 || modifiers.pointerType === 'touch') return null
  const modified = Boolean(modifiers.shiftKey || modifiers.ctrlKey || modifiers.metaKey)
  if (modified && available.includes('filter')) return 'filter'
  return available[0]
}

/** The intent of a keyboard activation: Enter is the first on offer; Shift+Enter is `filter` when on offer. */
export function keyboardIntent(available: readonly MarkIntent[], { shiftKey = false }: { shiftKey?: boolean } = {}): MarkIntent | null {
  if (available.length === 0) return null
  if (shiftKey && available.includes('filter')) return 'filter'
  return available[0]
}

/** The words on the readout's button for `intent` (WCAG 2.1.1: nothing is shortcut-only). */
export function intentLabel(intent: MarkIntent, mark: ChartMark): string {
  switch (intent) {
    case 'drill':
      return `Drill into ${mark.label}`
    case 'rows':
      return 'View rows'
    case 'filter':
      return 'Filter page by this'
  }
}

/** The mark as C5 levels, its own dimension first: what a drill pushes and a rows panel selects. */
export function markSelectors(mark: ChartMark): DrillLevel[] {
  return [{ dimension: mark.dimension, value: mark.value }, ...(mark.context ?? []).map(({ dimension, value }) => ({ dimension, value }))]
}
