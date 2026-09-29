/**
 * The model behind `DayStrip` (VIZ-104 package c, kit gap K5): a one-row strip
 * of cells, one per day (or per build), oldest first.
 *
 * Five pages drew this strip by hand, each a little differently (Trends and
 * Coverage run cadence, FailureAnalysis' timeline and run strip, Runs' build
 * velocity). The differences that MEAN something are kept here as the three
 * modes and the cell `tone`; the ones that were accidents are not:
 *
 *   presence   a day had runs or it did not (Trends). A day whose runs include
 *              failures is `mixed`: it keeps the 80/20 pass/fail fill it had
 *              (`MIXED_DAY_FILL`, with a band never under 3 px), and gains the failure cue below, because
 *              the two hues are 1.07-1.38:1 apart and the fill alone was the
 *              only thing saying "this day failed".
 *   intensity  how MUCH ran, in five levels (Coverage). It says nothing about
 *              pass or fail — which is why Coverage never drew the mixed fill
 *              and Trends did. That difference is deliberate, and it lives in
 *              the cells each page builds: a cell is only `mixed` (and only
 *              carries the cue) when its page says so, in any mode.
 *   status     pass / fail / nothing, with an optional `severity` that deepens
 *              a failed cell (FailureAnalysis, Runs).
 *
 * The failure cue is ONE shape everywhere: the kit's `failed` decal (diagonal,
 * `STATUS_ENCODING.failed`), cut out of the fill in the card colour — over the
 * whole cell for `fail`, over the bottom fifth (the failed band of the 80/20
 * fill, `MIXED_BAND_HEIGHT`) for `mixed`. Status is never colour-only (VIZ-105).
 *
 * Every string a cell carries came from ingested CI data, so it is only ever
 * rendered as React text or an attribute value; nothing here builds markup.
 */
import { shiftDayIso } from '@/utils/calendarDay'
import { CHART_VARS, NON_TEXT_EDGE } from './tokens'

export type DayStripMode = 'presence' | 'intensity' | 'status'
export type DayStripTone = 'none' | 'pass' | 'fail' | 'mixed'
export type DayStripLevel = 0 | 1 | 2 | 3 | 4
export type DayStripMarker = 'today' | 'gap-edge'

/** One cell as a page hands it over. */
export interface DayStripCell {
  /** Stable identity (the ISO day, a build id). */
  key: string
  /** Everything a reader is told about this cell, already formatted: title, table row, cursor. */
  label: string
  tone: DayStripTone
  /** `intensity` mode only: which of the five levels (`intensityLevel`). Missing = 0. */
  level?: DayStripLevel
  /** `status` mode, `fail` cells only: the failed share, 0..1. Deepens the fill. */
  severity?: number
  marker?: DayStripMarker
}

/** The words the strip uses for states, markers and the legend. Every one can be replaced. */
export interface DayStripText {
  none: string
  pass: string
  fail: string
  mixed: string
  /** `intensity` legend ends. */
  less: string
  more: string
  today: string
  gapEdge: string
}

const BASE_TEXT = {
  less: 'Less',
  more: 'More',
  today: 'Today',
  gapEdge: 'Last day with runs before a gap',
} as const

export const DEFAULT_TEXT: Record<DayStripMode, DayStripText> = {
  presence: { none: 'No runs', pass: 'Runs', fail: 'Runs, all failed', mixed: 'Runs with failures', ...BASE_TEXT },
  intensity: { none: 'No runs', pass: 'Runs', fail: 'Failures', mixed: 'Runs with failures', ...BASE_TEXT },
  status: { none: 'No runs', pass: 'Pass', fail: 'Fail', mixed: 'Pass and fail', ...BASE_TEXT },
}

/**
 * The `days` UTC days of a window ending on `todayIso`, oldest first: one
 * cell key per day, so a page cannot drop a quiet day from its strip. Four of
 * the five strips walked this loop by hand.
 */
export function dayWindow(days: number, todayIso: string): string[] {
  const count = Number.isFinite(days) ? Math.max(0, Math.floor(days)) : 0
  const isos: string[] = []
  for (let i = count - 1; i >= 0; i--) isos.push(shiftDayIso(todayIso, -i))
  return isos
}

// ── Intensity levels ────────────────────────────────────────────────────────

/**
 * The inclusive upper bound of levels 0-3; anything above the last is level 4.
 * `0 / 1-5 / 6-20 / 21-50 / more than 50` — Coverage's buckets
 * (`CoveragePage.tsx` `CadenceHeatmap`), kept exactly.
 */
export const INTENSITY_THRESHOLDS = [0, 5, 20, 50] as const

/** The level a count falls in. A negative or non-finite count is level 0. */
export function intensityLevel(count: number): DayStripLevel {
  if (!Number.isFinite(count) || count <= INTENSITY_THRESHOLDS[0]) return 0
  for (let level = 1; level < INTENSITY_THRESHOLDS.length; level++) {
    if (count <= INTENSITY_THRESHOLDS[level]) return level as DayStripLevel
  }
  return 4
}

/** The range a level stands for, as text: `"0"`, `"1-5"`, … `"more than 50"`. */
export function intensityRangeText(level: DayStripLevel): string {
  if (level === 0) return String(INTENSITY_THRESHOLDS[0])
  if (level === 4) return `more than ${INTENSITY_THRESHOLDS[3]}`
  return `${INTENSITY_THRESHOLDS[level - 1] + 1}-${INTENSITY_THRESHOLDS[level]}`
}

// The share of the passed hue per level (Coverage's ramp). A luminance ramp of
// one hue, so the order reads without telling hues apart.
const LEVEL_SHARE: Record<DayStripLevel, number | null> = { 0: null, 1: 18, 2: 40, 3: 70, 4: 100 }

// ── Fills ───────────────────────────────────────────────────────────────────

const EMPTY_FILL = 'var(--color-bg-secondary)'
/**
 * A day with no runs is a quiet fill in a `NON_TEXT_EDGE` outline. Its old
 * `--color-border` edge was 1.2-1.8:1 on the card, so on the dark themes an
 * empty day could not be told from no cell at all (R2 F9, SC 1.4.11).
 */
const EMPTY_BORDER = `1px solid ${NON_TEXT_EDGE}`
const NO_BORDER = '1px solid transparent'
const PASSED = CHART_VARS.status.passed
const FAILED = CHART_VARS.status.failed
const mix = (color: string, percent: number) =>
  percent >= 100 ? color : `color-mix(in srgb, ${color} ${percent}%, transparent)`
/**
 * A failed day in the status strips is edged in FULL-strength `--status-failed`
 * (3.9:1 or more on the card in every theme), whatever its severity shade: a
 * low-severity day's 45 % fill is under 3:1 (1.9:1 on signal), and the
 * failure hatch then cuts it with card-coloured stripes (R2 F9). The edge
 * keeps the cell's extent visible; the shade still says how much failed.
 */
const FAILED_EDGE = `1px solid ${FAILED}`

/**
 * The failed band of a mixed day: a fifth of the cell, never under 3 px. At
 * 90 days a cell is about 4 px tall, and a fifth of it (under a pixel) was the
 * band disappearing (R2 G4); 3 px leaves the band's card-coloured top edge
 * and two pixels of hatched red.
 */
export const MIXED_BAND_HEIGHT = 'max(20%, 3px)'
/** A mixed day's fill, passed over failed, split where `MIXED_BAND_HEIGHT` says (the kit's own `--pattern-mixed-day`). */
export const MIXED_DAY_FILL = `linear-gradient(180deg, ${PASSED} 0 calc(100% - ${MIXED_BAND_HEIGHT}), ${FAILED} calc(100% - ${MIXED_BAND_HEIGHT}) 100%)`

/**
 * The failure cue: the kit's `failed` decal (diagonal stripes cut in the card
 * colour, `patterns.tsx`), as a CSS layer so it needs no SVG and no ids.
 */
export const FAILURE_CUE_FILL = `repeating-linear-gradient(135deg, transparent 0 2px, ${CHART_VARS.card} 2px 3px)`

/** The share of `--status-failed` in a failed cell of the given severity (FailureAnalysis' ramp). */
export function severityShare(severity: number | undefined): number {
  if (severity === undefined || !Number.isFinite(severity)) return 100
  const s = Math.min(1, Math.max(0, severity))
  return Math.round((0.45 + 0.55 * s) * 100)
}

export interface CellFace {
  background: string
  border: string
  /** The non-colour failure cue: over the whole cell, over its failed band, or none. */
  cue: 'fail' | 'mixed' | null
}

/** How one cell is painted. Pure: exported for the legend swatches and the tests. */
export function cellFace(mode: DayStripMode, tone: DayStripTone, level: DayStripLevel = 0, severity?: number): CellFace {
  const cue = tone === 'fail' ? 'fail' : tone === 'mixed' ? 'mixed' : null
  if (mode === 'intensity') {
    const share = LEVEL_SHARE[level]
    return share === null
      ? { background: EMPTY_FILL, border: EMPTY_BORDER, cue }
      : { background: mix(PASSED, share), border: NO_BORDER, cue }
  }
  if (tone === 'none') return { background: EMPTY_FILL, border: EMPTY_BORDER, cue: null }
  if (tone === 'mixed') {
    return {
      background: MIXED_DAY_FILL,
      border: mode === 'presence' ? `1px solid ${mix(PASSED, 50)}` : NO_BORDER,
      cue,
    }
  }
  if (mode === 'presence') {
    return {
      background: tone === 'pass' ? PASSED : FAILED,
      border: `1px solid ${mix(tone === 'pass' ? PASSED : FAILED, 50)}`,
      cue,
    }
  }
  // status: a pass is the softer 70 % the timeline and run strip drew; a fail
  // is full strength unless a severity says how much of the day failed.
  return tone === 'pass'
    ? { background: mix(PASSED, 70), border: NO_BORDER, cue }
    : { background: mix(FAILED, severityShare(severity)), border: FAILED_EDGE, cue }
}

/**
 * The left end of the legend: how far back the OLDEST cell is. The newest cell
 * is "today" (0 ago), so of `count` cells the oldest is `count - 1` ago — a
 * 14-day window starts 13 days ago, not 14 (R1 F7). One cell is only today.
 */
export function defaultStartLabel(count: number, unit: string, today: string): string {
  const back = count - 1
  if (back <= 0) return today
  return `${back} ${back === 1 ? unit : `${unit}s`} ago`
}

// ── The model ───────────────────────────────────────────────────────────────

export interface DayStripCellView extends CellFace {
  key: string
  label: string
  tone: DayStripTone
  level: DayStripLevel
  marker: DayStripMarker | null
  /** The cell's state in words (the table's State column). */
  state: string
  /** The marker in words, or null. */
  markerText: string | null
  /** What the cursor says and shows for this cell. */
  text: string
}

export interface DayStripLegendItem {
  key: string
  label: string
  face: CellFace
}

export interface DayStripModel {
  cells: DayStripCellView[]
  counts: Record<DayStripTone, number>
  /** Swatches between the start and end labels. `intensity` puts `less` / `more` around them. */
  legend: DayStripLegendItem[]
  text: DayStripText
}

/** How many cells carry each tone — for a page's aggregate label. */
export function countTones(cells: readonly Pick<DayStripCell, 'tone'>[]): Record<DayStripTone, number> {
  const counts: Record<DayStripTone, number> = { none: 0, pass: 0, fail: 0, mixed: 0 }
  for (const cell of cells) counts[cell.tone]++
  return counts
}

function stateText(mode: DayStripMode, tone: DayStripTone, level: DayStripLevel, text: DayStripText): string {
  if (mode !== 'intensity') return text[tone]
  const range = `Activity level ${level} of 4 (${intensityRangeText(level)})`
  // Intensity says nothing about failures unless the page marked the day.
  return tone === 'mixed' || tone === 'fail' ? `${range}, ${text[tone]}` : range
}

export function buildDayStripModel(
  cells: readonly DayStripCell[],
  mode: DayStripMode,
  overrides: Partial<DayStripText> = {},
): DayStripModel {
  const text: DayStripText = { ...DEFAULT_TEXT[mode], ...overrides }
  const views = cells.map<DayStripCellView>((cell) => {
    const level = cell.level ?? 0
    const marker = cell.marker ?? null
    const state = stateText(mode, cell.tone, level, text)
    const markerText = marker === 'today' ? text.today : marker === 'gap-edge' ? text.gapEdge : null
    return {
      key: cell.key,
      label: cell.label,
      tone: cell.tone,
      level,
      marker,
      state,
      markerText,
      text: [cell.label, state, markerText].filter(Boolean).join('. '),
      ...cellFace(mode, cell.tone, level, cell.severity),
    }
  })
  const counts = countTones(cells)

  let legend: DayStripLegendItem[]
  if (mode === 'intensity') {
    // The three swatches Coverage showed between "Less" and "More".
    legend = ([0, 2, 4] as const).map((level) => ({
      key: `level-${level}`,
      label: `Activity level ${level} of 4 (${intensityRangeText(level)})`,
      face: cellFace('intensity', level === 0 ? 'none' : 'pass', level),
    }))
  } else {
    const tones: DayStripTone[] = mode === 'presence' ? ['none', 'pass'] : ['pass', 'fail', 'none']
    // A failure tone only earns a swatch when the strip draws one: the
    // presence legend of a clean window stays the two entries it always was.
    if (mode === 'presence' && counts.fail > 0) tones.push('fail')
    if (counts.mixed > 0) tones.push('mixed')
    legend = tones.map((tone) => ({ key: tone, label: text[tone], face: cellFace(mode, tone) }))
  }
  // Every mode can carry a page-marked failure; say what the cue means.
  if (mode === 'intensity') {
    for (const tone of ['fail', 'mixed'] as const) {
      if (counts[tone] > 0) legend.push({ key: tone, label: text[tone], face: cellFace('status', tone) })
    }
  }

  return { cells: views, counts, legend, text }
}
