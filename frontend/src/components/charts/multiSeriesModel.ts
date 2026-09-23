/**
 * The pure model behind `MultiSeriesChart` (VIZ-404) — suites, releases or
 * branches overlaid on one chart.
 *
 * Nothing here touches React or a chart engine, so every rule the story sets
 * is table-testable, and the plot, the shared tooltip, the keyboard cursor,
 * the summary and the table view all read the SAME model:
 *
 *   - At most `MAX_DRAWN_SERIES` (8) lines. Past that, the `KEPT_BEFORE_OTHER`
 *     (7) largest BY VOLUME are kept and the rest fold into "Other". Volume is
 *     the sample behind the values (`n`, executions), never the metric's value:
 *     ranking a pass rate by the rate itself puts a 100%-of-one-test series
 *     above one that ran ten thousand tests.
 *   - "Other" for a COUNT is the sum of what it folds. "Other" for a RATE is
 *     the rate of the MERGED counts: the sum of y times n over the sum of n,
 *     where `n` is the rate's own denominator (evaluated executions) — exactly
 *     what `chart-data` computes when it caps the series axis. It is never a
 *     plain average of the folded rates: averaging 100%-of-1 and 0%-of-99 says
 *     50% where the truth is 1%. A server "Other" that has to be folded again
 *     keeps its values: they pool in by their own counts.
 *   - The day `meta.partial_day` names is still filling: its points are marked
 *     and drawn hollow, and a direct label never anchors on it.
 *   - A day a series did not measure is a GAP (`y: null`) carrying its reason,
 *     never a zero, and a day nobody reported is still a day on the axis.
 *     Aligned, a day PAST a release's returned range is not a gap: that
 *     release simply has no such day yet ("R2 has 10 days; days 10–11 are past
 *     its range"). It is drawn the same (nothing), but counted and worded
 *     apart, so a short release is never reported as a missed measurement.
 *   - The shared tooltip lists every shown series for one day, SORTED
 *     DESCENDING; an unmeasured value is "—" with its reason and sorts last.
 *   - Line style varies by DASH as well as colour, so a reader who cannot tell
 *     the hues apart still tells the lines apart.
 *   - Direct labels at the line ends never overlap: they are nudged apart, and
 *     when even nudging cannot fit them the chart drops to the legend alone and
 *     says so (`placeDirectLabels`).
 *   - `comparable: false` is a banner with the reason; the comparison is still
 *     drawn.
 *   - Release-over-release alignment is `seriesAlignment`'s client transform.
 *
 * Free of `@/` value imports (only `import type`), so the Playwright specs can
 * import its constants in plain Node.
 */
import type { EnvelopeMeta, SeriesChart, SeriesPoint } from '@/lib/viz/contracts'
import { niceScale, zeroBasedScale } from './niceScale'
import { SVG_POINT_LIMIT, utcDayRange } from './timeSeriesModel'
import { ALIGNED_X_TITLE, alignByReleaseStart, alignedRowLabel, type AlignmentStartSource } from './seriesAlignment'

// ── Constants ────────────────────────────────────────────────────────────────

/** The most lines one chart draws (the eight categorical series colours). */
export const MAX_DRAWN_SERIES = 8
/** Past `MAX_DRAWN_SERIES`, this many are kept and the rest become "Other". */
export const KEPT_BEFORE_OTHER = MAX_DRAWN_SERIES - 1
/** The key `chart-data` gives its merged remainder (`OTHER_KEY` in the service). */
export const OTHER_KEY = '__other__'
export const OTHER_LABEL = 'Other'

/**
 * One `stroke-dasharray` per series slot (index 7 is "Other"). Every line
 * differs from every other by its dash as well as its hue — the categorical
 * hues are not all distinguishable to every reader.
 */
export const SERIES_DASHES: readonly (string | undefined)[] = [
  undefined,
  '9 4',
  '2 3',
  '10 3 2 3',
  '5 5',
  // Paired dashes. It was dash-dot-dot (`14 4 2 4 2 4`), which read as the
  // dash-dot of slot 3 wherever the two hues were equally light (lab).
  '6 2 6 10',
  '16 6',
  '1 4',
]

/** The x-axis title on a calendar axis. */
export const ABSOLUTE_X_TITLE = 'Day (UTC)'
export { ALIGNED_X_TITLE }

/** Shown under a calendar-axis comparison. */
export const ABSOLUTE_CAPTION = 'Days are UTC buckets.'
const ALIGNED_CAPTION_TAIL = 'the table and the tooltip give the date each day stands for.'
/** Shown under an aligned comparison where every release's start date was given. */
export const ALIGNED_CAPTION_NAMED = `Day 0 is each release's start date; ${ALIGNED_CAPTION_TAIL}`
/**
 * Shown under an aligned comparison with no start dates: day 0 is the first
 * day with runs, which is not necessarily the day the release started.
 */
export const ALIGNED_CAPTION_ACTIVE = `Day 0 is each release's first day with runs, not necessarily its start date; ${ALIGNED_CAPTION_TAIL}`
/** Shown when some releases had a start date given and some did not. */
export const ALIGNED_CAPTION_MIXED = `Day 0 is each release's start date where one was given, and otherwise its first day with runs; ${ALIGNED_CAPTION_TAIL}`

/** A day that one series did not report at all, while another did. */
export const NOT_REPORTED_REASON = 'this series reported nothing for this day'
/**
 * A folded RATE day whose measured values carry no execution counts to pool
 * them by: never a plain average of the rates.
 */
export const OTHER_RATE_REASON =
  '"Other" pools the folded rates by their execution counts, and none of them has a count for this day; averaging the rates would give a wrong number'
/** A folded day none of the folded series has a value for. */
export const OTHER_EMPTY_REASON = 'none of the series folded into "Other" has a value for this day'
/** `comparable: false` with no reason given. */
export const NOT_COMPARABLE_FALLBACK = 'the API did not say why'

/** How a still-filling day's value is marked in the tooltip, the readout and the announcer. */
export const PARTIAL_MARK = 'still filling'

/** Appended to a hidden series' name in the table view and the summary. */
export const HIDDEN_SUFFIX = ' (hidden)'

/** A direct label's line box, in px. Labels closer than this overlap. */
export const DIRECT_LABEL_HEIGHT = 16
/** Characters a direct label keeps before it is shortened (the legend has the full name). */
export const DIRECT_LABEL_MAX_CHARS = 12
/**
 * A direct label's row in the gutter, left to right (px from the plot's right
 * edge, where every line's last point is at most):
 *
 *   gap · connector (flat run-in, then one angled step) · gap · swatch · gap · name
 *
 * The CONNECTOR is a 1 px dotted line in the axis colour (`LEADER_DASH`) —
 * never a series' colour or dash — and it starts `LEADER_GAP` px clear of the plot, so it can
 * never be read as the line continuing (drawn in the line's own style, a
 * nudged label's leader read as every suite crashing on the last day). The
 * match between line and name is the SWATCH: a short run of the line's own
 * colour AND dash, right before the name — the same cue the legend uses, so
 * it never rests on colour alone.
 */
export const LEADER_GAP = 4
/**
 * The connector's own pattern: a fine even dot that is none of the series'
 * dashes (not even the solid line of slot 0), so it differs from every line
 * in pattern as well as in colour and weight.
 */
export const LEADER_DASH = '2 2'
/** The connector's flat run-in at the height of the line's end, before it turns. */
export const LEADER_RUN_IN = 4
/** The connector's angled step: the horizontal room it has to reach the label's row. */
export const LEADER_STEP = 20
/** Between the connector's end and the swatch, and between the swatch and the name. */
export const LABEL_SWATCH_GAP = 3
/** Long enough that every series dash period (at most 24 px) shows whole. */
export const LABEL_SWATCH_LENGTH = 24
/** Room for a name of `DIRECT_LABEL_MAX_CHARS` at 11 px. */
export const LABEL_TEXT_ROOM = 76
/** Where, from the plot's right edge, each part of the row starts. */
export const LABEL_ROW = {
  leaderStart: LEADER_GAP,
  leaderTurn: LEADER_GAP + LEADER_RUN_IN,
  leaderEnd: LEADER_GAP + LEADER_RUN_IN + LEADER_STEP,
  swatchStart: LEADER_GAP + LEADER_RUN_IN + LEADER_STEP + LABEL_SWATCH_GAP,
  textStart: LEADER_GAP + LEADER_RUN_IN + LEADER_STEP + 2 * LABEL_SWATCH_GAP + LABEL_SWATCH_LENGTH,
} as const
/** The gutter the direct labels take to the right of the plot. */
export const DIRECT_LABEL_GUTTER = LABEL_ROW.textStart + LABEL_TEXT_ROOM

/**
 * The narrowest plot the line-end labels may leave. Their gutter is a fixed
 * width (`DIRECT_LABEL_GUTTER`), so at 320 px it once took 104 of the 160 px
 * the plot had and left it 56 px wide; below this the labels give way to the
 * legend.
 */
export const MIN_PLOT_WIDTH_WITH_LABELS = 200

// ── Types ────────────────────────────────────────────────────────────────────

export type MultiSeriesMetricKind = 'rate' | 'count'
export type MultiSeriesAlignment = 'absolute' | 'release-start'

export interface MultiSeriesInputSeries {
  key: string
  label: string
  points: readonly SeriesPoint[]
}

export interface Comparability {
  comparable: boolean
  reason: string | null
  /** The API's machine-readable reason (`meta.comparability.reason_code`). */
  reasonCode?: string | null
}

export interface MultiSeriesPoint {
  x: string
  /** `null` is a GAP, never 0. */
  y: number | null
  /** The sample behind `y` — the volume a series is ranked by. */
  n: number
  /** Why `y` is null; `null` when it is measured. */
  reason: string | null
  /** The absolute UTC day this point stands for (differs from `x` when aligned). */
  date: string | null
  /** The day `meta.partial_day` names: still filling. */
  partial?: boolean
  /**
   * Aligned only: a relative day past this release's returned range — the
   * release has no such day yet. Unmeasured, but NOT a gap.
   */
  pastRange?: boolean
}

export interface MultiSeriesLine {
  key: string
  label: string
  /** The merged remainder ("Other"), from the server or folded here. */
  other: boolean
  /** Colour and dash slot, 0..7. "Other" is always slot 7. */
  styleIndex: number
  dash: string | undefined
  points: MultiSeriesPoint[]
  /** Σ n: what the fold ranks by. */
  volume: number
  /** Returned days with no measured value — never counting the days past its range. */
  gaps: number
  /** Trailing days past this series' range (aligned only; 0 on a calendar axis). */
  pastRange: number
  /** A measured point with no measured neighbour: draw dots, or it is invisible. */
  isolated: boolean
  /**
   * The last measured COMPLETE point — where the direct label points. A
   * still-filling day is skipped unless it is the only measured one.
   */
  last: { index: number; y: number } | null
}

export interface MultiSeriesFold {
  /** How many series "Other" stands for (0 when the server did not say). */
  folded: number
  /** How many series there were before folding (0 when unknown). */
  total: number
  /** Who folded: the server (`__other__` arrived), this model, or both. */
  source: 'client' | 'server' | 'both'
  /** How many series THIS model folded (into, or on top of, the server's). */
  clientFolded: number
  /** The names tied on executions at the last kept place, when there was a tie. */
  tie: { kept: string[]; folded: string[] } | null
}

export interface MultiSeriesModel {
  /** The x keys, in axis order: UTC days, or `'0' … 'N'` when aligned. */
  xs: string[]
  /** The table's row header for each x (relative day AND absolute dates when aligned). */
  rowHeaders: Record<string, string>
  lines: MultiSeriesLine[]
  metric: { kind: MultiSeriesMetricKind; title: string }
  alignment: MultiSeriesAlignment
  xTitle: string
  yAxis: { domain: [number, number]; ticks: number[] }
  fold: MultiSeriesFold | null
  foldNotice: string | null
  comparability: Comparability
  /** The not-comparable banner, or `null`. */
  banner: string | null
  /** Days dropped to stay within the SVG point budget. */
  capped: { shown: number; total: number } | null
  /** That crop in words: the LATEST days on a calendar axis, the FIRST days when aligned. */
  cappedNote: string | null
  /** `meta.partial_day` when a drawn point falls on it, else `null`. */
  partialDay: string | null
  /** `meta.includes_in_progress`: runs still in progress on the partial day. */
  inProgressCount: number
  /** Unmeasured points across every line, on days each line's range covers. */
  gaps: number
  /** "R2 has 10 days; days 10–11 are past its range.", one sentence per short line; `null` when none is. */
  rangeNote: string | null
  /** Points no alignment could place (their `x` is not a day). */
  unplaced: number
  caption: string
}

export interface BuildMultiSeriesInput {
  series: readonly MultiSeriesInputSeries[]
  metric: { kind: MultiSeriesMetricKind; title: string }
  /** For the fold notice's counts (`truncated_axes.series`) and `comparable`. */
  meta?: EnvelopeMeta | null
  alignment?: MultiSeriesAlignment
  /** Day 0 per series key when aligning (a release's start date). */
  starts?: Readonly<Record<string, string>>
  /** Overrides whatever `meta` says about comparability. */
  comparability?: Comparability | null
  /** What one series is, plural, for the notices: "suites", "releases", "branches". */
  seriesNoun?: string
}

// ── Comparability ────────────────────────────────────────────────────────────

/**
 * What the envelope says about comparing these series: the C2 meta's OPTIONAL
 * `comparability` object — `{ comparable, reason, reason_code }` — which
 * `chart-data` sets on a two-`group_by` release or branch request. Absent
 * means NOT ASSESSED (every other request): no caveat is drawn, and
 * `comparable: true` here means only "nothing to warn about". Read
 * defensively: the object is optional, and a blank reason is no reason.
 */
export function comparabilityFromMeta(meta: EnvelopeMeta | null | undefined): Comparability {
  return readComparability((meta as { comparability?: unknown } | null | undefined)?.comparability)
}

/** `meta.comparability` itself, in its wire shape, read the same defensive way. */
export function readComparability(raw: unknown): Comparability {
  if (!raw || typeof raw !== 'object') return { comparable: true, reason: null }
  const { comparable, reason, reason_code: code } = raw as { comparable?: unknown; reason?: unknown; reason_code?: unknown }
  if (comparable !== false) return { comparable: true, reason: null }
  return {
    comparable: false,
    reason: typeof reason === 'string' && reason.trim() ? reason : null,
    reasonCode: typeof code === 'string' && code.trim() ? code : null,
  }
}

/**
 * The reason as a clause. The API's reasons are whole sentences ending in a
 * full stop ("… 3 in all of them."); the banner and the caveat add their own
 * punctuation after it, so the stop is dropped rather than doubled.
 */
function reasonClause(comparability: Comparability): string {
  return (comparability.reason ?? NOT_COMPARABLE_FALLBACK).trim().replace(/\.+$/, '')
}

export function comparabilityBanner(comparability: Comparability): string | null {
  if (comparability.comparable) return null
  return `Not directly comparable: ${reasonClause(comparability)}. The comparison is still shown.`
}

/**
 * The same caveat as one clause, for the frame's summary ("Scope: …."): a
 * reader who never reaches the banner still hears that the comparison is
 * qualified.
 */
export function comparabilityCaveat(comparability: Comparability): string | null {
  if (comparability.comparable) return null
  return `not directly comparable: ${reasonClause(comparability)}; the comparison is still shown`
}

// ── Building the axis and the lines ──────────────────────────────────────────

const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/

/** A line before styling: what the fold works on. */
export interface WorkingLine {
  key: string
  label: string
  other: boolean
  points: MultiSeriesPoint[]
}

const pointOf = (x: string, source: SeriesPoint | undefined, date: string | null, partial: boolean): MultiSeriesPoint => {
  if (!source) return { x, y: null, n: 0, reason: NOT_REPORTED_REASON, date, partial }
  const measured = source.y !== null && source.measured !== false && Number.isFinite(source.y)
  return measured
    ? { x, y: source.y as number, n: source.n, reason: null, date, partial }
    : { x, y: null, n: source.n, reason: source.reason ?? NOT_REPORTED_REASON, date, partial }
}

/** The calendar axis: every UTC day from first to last when every x is a day, else the sorted union. */
function absoluteAxis(series: readonly MultiSeriesInputSeries[]): string[] {
  const seen = new Set<string>()
  for (const s of series) for (const p of s.points) seen.add(p.x)
  const xs = [...seen].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
  if (xs.length > 1 && xs.every((x) => DAY_PATTERN.test(x))) return utcDayRange(xs[0], xs[xs.length - 1])
  return xs
}

const volumeOf = (points: readonly { n: number }[]) => points.reduce((sum, p) => sum + (Number.isFinite(p.n) ? p.n : 0), 0)

/** By name, then key: the order a tie on executions is broken in, and the order the notice names it. */
const byName = (a: Pick<WorkingLine, 'label' | 'key'>, b: Pick<WorkingLine, 'label' | 'key'>) =>
  a.label < b.label ? -1 : a.label > b.label ? 1 : a.key < b.key ? -1 : a.key > b.key ? 1 : 0

/**
 * One folded day of a RATE: the rate of the merged counts, the sum of y times
 * n over the sum of n. `n` is the rate's own denominator, so this is exactly
 * the rate `chart-data` computes from the merged counts; a plain mean of the
 * rates would weigh a one-test suite like a ten-thousand-test one.
 */
function pooledRate(at: readonly MultiSeriesPoint[]): { y: number | null; reason: string | null } {
  let weighted = 0
  let total = 0
  let measured = 0
  for (const p of at) {
    if (p.y === null) continue
    measured += 1
    if (!Number.isFinite(p.n) || p.n <= 0) continue
    weighted += p.y * p.n
    total += p.n
  }
  if (total > 0) return { y: weighted / total, reason: null }
  return { y: null, reason: measured > 0 ? OTHER_RATE_REASON : OTHER_EMPTY_REASON }
}

/**
 * The fold: when there are more than `MAX_DRAWN_SERIES` lines, keep the
 * `KEPT_BEFORE_OTHER` largest by VOLUME (input order kept among them, so a
 * series keeps its colour) and merge the rest — a server "Other" included,
 * values and all — into one "Other". A tie on volume at the last kept place
 * goes to the name that sorts first, and is reported so the notice can say so.
 */
export function foldSeries(
  lines: readonly WorkingLine[],
  kind: MultiSeriesMetricKind,
): { lines: WorkingLine[]; folded: number; tie: MultiSeriesFold['tie'] } {
  if (lines.length <= MAX_DRAWN_SERIES) return { lines: [...lines], folded: 0, tie: null }
  const ranked = lines
    .filter((line) => !line.other)
    .map((line) => ({ line, volume: volumeOf(line.points) }))
    .sort((a, b) => b.volume - a.volume || byName(a.line, b.line))
  const keptEntries = ranked.slice(0, KEPT_BEFORE_OTHER)
  const cut = keptEntries[keptEntries.length - 1]?.volume
  const tie =
    cut !== undefined && ranked[KEPT_BEFORE_OTHER]?.volume === cut
      ? {
          kept: keptEntries.filter((entry) => entry.volume === cut).map((entry) => entry.line.label),
          folded: ranked.slice(KEPT_BEFORE_OTHER).filter((entry) => entry.volume === cut).map((entry) => entry.line.label),
        }
      : null
  const keptKeys = new Set(keptEntries.map((entry) => entry.line.key))
  const kept = lines.filter((line) => !line.other && keptKeys.has(line.key))
  const rest = lines.filter((line) => line.other || !keptKeys.has(line.key))
  const width = lines[0]?.points.length ?? 0
  const points: MultiSeriesPoint[] = Array.from({ length: width }, (_, i) => {
    const at = rest.map((line) => line.points[i]).filter((p): p is MultiSeriesPoint => p !== undefined)
    const x = at[0]?.x ?? ''
    const n = volumeOf(at)
    // Aligned, the folded releases' day d stands for different dates: name one only if they agree.
    const dates = new Set(at.map((p) => p.date))
    const date = dates.size === 1 ? (at[0]?.date ?? null) : null
    const partial = at.some((p) => p.partial === true)
    // Past the range of EVERY release it folds: "Other" has no such day either.
    const pastRange = at.length > 0 && at.every((p) => p.pastRange === true)
    const range = pastRange ? { pastRange } : {}
    if (kind === 'rate') return { x, n, date, partial, ...range, ...pooledRate(at) }
    const measured = at.filter((p) => p.y !== null)
    return measured.length === 0
      ? { x, y: null, n, reason: OTHER_EMPTY_REASON, date, partial, ...range }
      : { x, y: measured.reduce((sum, p) => sum + (p.y as number), 0), n, reason: null, date, partial, ...range }
  })
  const foldedCount = rest.filter((line) => !line.other).length
  return { lines: [...kept, { key: OTHER_KEY, label: OTHER_LABEL, other: true, points }], folded: foldedCount, tie }
}

function finishLine(line: WorkingLine, styleIndex: number): MultiSeriesLine {
  let gaps = 0
  let pastRange = 0
  let isolated = false
  let last: MultiSeriesLine['last'] = null
  let lastPartial: MultiSeriesLine['last'] = null
  const { points } = line
  for (let i = 0; i < points.length; i++) {
    const y = points[i].y
    if (y === null) {
      if (points[i].pastRange) pastRange += 1
      else gaps += 1
      continue
    }
    // A still-filling day is no place to name a line: its value is still moving.
    if (points[i].partial) lastPartial = { index: i, y }
    else last = { index: i, y }
    const before = i > 0 ? points[i - 1].y : null
    const after = i < points.length - 1 ? points[i + 1].y : null
    if (before === null && after === null) isolated = true
  }
  return {
    key: line.key,
    label: line.label,
    other: line.other,
    styleIndex,
    dash: SERIES_DASHES[styleIndex],
    points,
    volume: volumeOf(points),
    gaps,
    pastRange,
    isolated,
    last: last ?? lastPartial,
  }
}

function yAxisFor(kind: MultiSeriesMetricKind, lines: readonly MultiSeriesLine[]): MultiSeriesModel['yAxis'] {
  if (kind === 'rate') {
    const { domain, ticks } = niceScale(0, 100, { intervals: 4 })
    return { domain, ticks }
  }
  let max = 0
  for (const line of lines) for (const p of line.points) if (p.y !== null && p.y > max) max = p.y
  const { domain, ticks } = zeroBasedScale(max, { intervals: 4, integer: true })
  return { domain, ticks }
}

/** "suites" → "suite", "branches" → "branch", "series" → "series". */
function singularOf(noun: string): string {
  if (noun === 'series') return noun
  if (/(ch|sh|x)es$/.test(noun)) return noun.slice(0, -2)
  return noun.endsWith('s') ? noun.slice(0, -1) : noun
}

/** "a", "a and b", "a, b and c". */
function listOf(names: readonly string[]): string {
  if (names.length <= 1) return names.join('')
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

function foldNoticeOf(fold: MultiSeriesFold | null, kept: number, noun: string): string | null {
  if (!fold) return null
  const parts: string[] = []
  if (fold.folded > 0 && fold.total > 0) {
    parts.push(
      `${fold.total} ${noun}: the ${kept} with the most executions ${kept === 1 ? 'is' : 'are'} drawn, and the other ${fold.folded} ${fold.folded === 1 ? 'is' : 'are'} folded into "${OTHER_LABEL}".`,
    )
  } else {
    parts.push(`The server folded the smaller ${noun} into "${OTHER_LABEL}"; its values are recomputed from their merged counts.`)
    if (fold.clientFolded > 0) {
      parts.push(
        `To stay within ${MAX_DRAWN_SERIES} lines, ${fold.clientFolded} more ${fold.clientFolded === 1 ? `${singularOf(noun)} is` : `${noun} are`} folded in here, pooled by execution counts.`,
      )
    }
  }
  if (fold.tie) {
    const names = [...fold.tie.kept, ...fold.tie.folded].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
    parts.push(`${listOf(names)} tie on executions for the last place drawn; the tie goes to the name that sorts first.`)
  }
  return parts.join(' ')
}

function alignedCaptionOf(sources: readonly AlignmentStartSource[]): string {
  const named = sources.filter((source) => source === 'named').length
  if (sources.length > 0 && named === sources.length) return ALIGNED_CAPTION_NAMED
  return named > 0 ? ALIGNED_CAPTION_MIXED : ALIGNED_CAPTION_ACTIVE
}

const plain = (n: number) => n.toLocaleString('en-US')

/**
 * One sentence per line that runs out before the axis does: "R2 has 10 days;
 * days 10–11 are past its range." The days are the axis' own relative days.
 */
export function rangeNoteOf(lines: readonly Pick<MultiSeriesLine, 'label' | 'points' | 'pastRange'>[], xs: readonly string[]): string | null {
  const sentences = lines
    .filter((line) => line.pastRange > 0)
    .map((line) => {
      const covered = line.points.length - line.pastRange
      const first = xs[covered] ?? String(covered)
      const last = xs[line.points.length - 1] ?? String(line.points.length - 1)
      const days = `${plain(covered)} ${covered === 1 ? 'day' : 'days'}`
      const past = line.pastRange === 1 ? `day ${first} is` : `days ${first}–${last} are`
      return `${line.label} has ${days}; ${past} past its range.`
    })
  return sentences.length > 0 ? sentences.join(' ') : null
}

export function buildMultiSeriesModel({
  series,
  metric,
  meta = null,
  alignment = 'absolute',
  starts,
  comparability: comparabilityInput = null,
  seriesNoun = 'series',
}: BuildMultiSeriesInput): MultiSeriesModel {
  let xs: string[]
  let working: WorkingLine[]
  let rowHeaders: Record<string, string> = {}
  let capped: MultiSeriesModel['capped'] = null
  let unplaced = 0
  let caption = ABSOLUTE_CAPTION
  // The still-filling UTC day. Matched against each point's ABSOLUTE date, so
  // an aligned axis marks the release day that falls on it.
  const partialOn = meta?.partial_day && DAY_PATTERN.test(meta.partial_day) ? meta.partial_day : null

  if (alignment === 'release-start') {
    const aligned = alignByReleaseStart(series, { starts, maxDays: SVG_POINT_LIMIT })
    const full = alignByReleaseStart(series, { starts })
    if (full.xs.length > aligned.xs.length) capped = { shown: aligned.xs.length, total: full.xs.length }
    xs = aligned.xs
    unplaced = aligned.unplaced
    working = aligned.series.map((s) => ({
      key: s.key,
      label: s.key === OTHER_KEY ? OTHER_LABEL : s.label,
      other: s.key === OTHER_KEY,
      points: s.points.map((p, i) => {
        const point = pointOf(p.x, p, s.dates[i] || null, partialOn !== null && s.dates[i] === partialOn)
        return i >= s.rangeDays ? { ...point, pastRange: true } : point
      }),
    }))
    rowHeaders = Object.fromEntries(xs.map((x, day) => [x, alignedRowLabel(day, aligned.series)]))
    caption = alignedCaptionOf(aligned.series.map((s) => s.startSource))
  } else {
    const axis = absoluteAxis(series)
    xs = axis.length > SVG_POINT_LIMIT ? axis.slice(axis.length - SVG_POINT_LIMIT) : axis
    if (axis.length > SVG_POINT_LIMIT) capped = { shown: xs.length, total: axis.length }
    working = series.map((s) => {
      const byX = new Map(s.points.map((p) => [p.x, p]))
      return {
        key: s.key,
        label: s.key === OTHER_KEY ? OTHER_LABEL : s.label,
        other: s.key === OTHER_KEY,
        points: xs.map((x) => pointOf(x, byX.get(x), DAY_PATTERN.test(x) ? x : null, partialOn !== null && x === partialOn)),
      }
    })
  }

  // "Other" from the server always goes last, whatever order it arrived in.
  working = [...working.filter((line) => !line.other), ...working.filter((line) => line.other)]

  const folding = foldSeries(working, metric.kind)
  const serverOther = working.some((line) => line.other)
  const keptCount = folding.lines.filter((line) => !line.other).length
  const axis = meta?.truncated_axes?.series
  let fold: MultiSeriesFold | null = null
  if (folding.folded > 0 || serverOther) {
    const source: MultiSeriesFold['source'] = folding.folded > 0 ? (serverOther ? 'both' : 'client') : 'server'
    // What is known about the whole: every series there was, less the ones drawn.
    const total = serverOther ? (axis?.total ?? 0) : working.length
    fold = {
      folded: total > 0 ? Math.max(0, total - keptCount) : 0,
      total,
      source,
      clientFolded: folding.folded,
      tie: folding.tie,
    }
  }

  let slot = 0
  const lines = folding.lines.map((line) => finishLine(line, line.other ? MAX_DRAWN_SERIES - 1 : slot++))
  const comparability = comparabilityInput ?? comparabilityFromMeta(meta)
  const partialDay = partialOn !== null && lines.some((line) => line.points.some((p) => p.partial)) ? partialOn : null

  return {
    xs,
    rowHeaders,
    lines,
    metric,
    alignment,
    xTitle: alignment === 'release-start' ? ALIGNED_X_TITLE : ABSOLUTE_X_TITLE,
    yAxis: yAxisFor(metric.kind, lines),
    fold,
    foldNotice: foldNoticeOf(fold, keptCount, seriesNoun),
    comparability,
    banner: comparabilityBanner(comparability),
    capped,
    // The aligned crop keeps the FIRST days since release start; the calendar one the latest.
    cappedNote: capped
      ? alignment === 'release-start'
        ? `Showing the first ${plain(capped.shown)} of ${plain(capped.total)} days since release start.`
        : `Showing the latest ${plain(capped.shown)} of ${plain(capped.total)} days.`
      : null,
    partialDay,
    inProgressCount: partialDay ? Math.max(0, meta?.includes_in_progress ?? 0) : 0,
    gaps: lines.reduce((sum, line) => sum + line.gaps, 0),
    rangeNote: rangeNoteOf(lines, xs),
    unplaced,
    caption,
  }
}

/** `chart-data` (two `group_by`: day × series) → model input. The C3 shape IS the input. */
export function multiSeriesInputFromChartData(chart: SeriesChart | null | undefined): MultiSeriesInputSeries[] {
  return (chart?.series ?? []).map((s) => ({ key: s.key, label: s.key === OTHER_KEY ? OTHER_LABEL : s.label, points: s.points }))
}

// ── The shared tooltip ───────────────────────────────────────────────────────

export type ValueFormat = (value: number) => string

export interface TipRow {
  key: string
  label: string
  /** Formatted, or "—" when unmeasured. */
  value: string
  y: number | null
  reason: string | null
  styleIndex: number
  dash: string | undefined
  /** The absolute day this value is for — shown when the axis is aligned. */
  date: string | null
  /** The value is for the still-filling day (`meta.partial_day`). */
  partial: boolean
}

export interface TipContent {
  /** The day: "2026-03-04", or "Day 3" when aligned. */
  title: string
  rows: TipRow[]
  /** Shown series that are not listed because the reader hid them. */
  hiddenCount: number
}

const NO_VALUE_MARK = '—'

const byLabel = (a: { label: string }, b: { label: string }) => (a.label < b.label ? -1 : a.label > b.label ? 1 : 0)

/** "Day 3" or the day itself. */
export function xTitleOf(model: Pick<MultiSeriesModel, 'alignment'>, x: string): string {
  return model.alignment === 'release-start' ? `Day ${x}` : x
}

/**
 * Every shown series' value on one day, SORTED DESCENDING; an unmeasured value
 * is "—" with its reason and sorts after every measured one. Ties keep a
 * stable, alphabetical order.
 */
export function tipContentAt(
  model: MultiSeriesModel,
  index: number,
  { hidden = new Set<string>(), format }: { hidden?: ReadonlySet<string>; format: ValueFormat },
): TipContent {
  const x = model.xs[index] ?? ''
  const shown = model.lines.filter((line) => !hidden.has(line.key))
  const rows: TipRow[] = shown.map((line) => {
    const point = line.points[index]
    const y = point?.y ?? null
    return {
      key: line.key,
      label: line.label,
      value: y === null ? NO_VALUE_MARK : format(y),
      y,
      reason: y === null ? (point?.reason ?? NOT_REPORTED_REASON) : null,
      styleIndex: line.styleIndex,
      dash: line.dash,
      date: point?.date ?? null,
      partial: point?.partial === true,
    }
  })
  const measured = rows.filter((row) => row.y !== null).sort((a, b) => (b.y as number) - (a.y as number) || byLabel(a, b))
  const unmeasured = rows.filter((row) => row.y === null).sort(byLabel)
  return { title: xTitleOf(model, x), rows: [...measured, ...unmeasured], hiddenCount: model.lines.length - shown.length }
}

/** A row's name in the tooltip: with its absolute day when the axis is aligned. */
export function tipRowName(model: Pick<MultiSeriesModel, 'alignment'>, row: Pick<TipRow, 'label' | 'date'>): string {
  return model.alignment === 'release-start' && row.date ? `${row.label} (${row.date})` : row.label
}

/** The same content as one sentence, for the keyboard cursor and the page announcer. */
export function tipText(model: Pick<MultiSeriesModel, 'alignment'>, content: TipContent): string {
  const rows = content.rows.map((row) =>
    row.y === null
      ? `${tipRowName(model, row)} ${NO_VALUE_MARK} (${row.reason})`
      : `${tipRowName(model, row)} ${row.value}${row.partial ? ` (${PARTIAL_MARK})` : ''}`,
  )
  const hidden = content.hiddenCount > 0 ? `; ${content.hiddenCount} hidden` : ''
  return `${content.title}: ${rows.join(', ') || 'no series shown'}${hidden}`
}

// ── Direct labels ────────────────────────────────────────────────────────────

export interface LabelRequest {
  key: string
  /** Where the label would like to sit: the pixel y of its line's end. */
  y: number
}

export interface PlacedLabel {
  key: string
  /** Where it sits after nudging (the centre of its line box). */
  y: number
  /** Where its line ends, for the leader. */
  target: number
}

export interface LabelPlacement {
  /** False: they cannot all fit without overlapping — draw none, and say so. */
  fits: boolean
  labels: PlacedLabel[]
}

/**
 * Place direct labels so no two overlap, each as close as it can be to where
 * its line ends. Sorted by that height, labels that would overlap are merged
 * into a CLUSTER stacked `height` apart and centred on its members' mean end —
 * so a crowd of lines spreads its names both up and down, and no one label
 * travels far (a label pushed only downwards travelled up to seven rows on the
 * twelve-suite chart, and its leader read as a crash). A cluster that would
 * leave the plot is slid back inside; merging repeats until nothing overlaps.
 * If the column cannot hold them at all, `fits` is false and none is placed: a
 * stack of overlapping names is worse than the legend alone.
 *
 * `plotWidth` is the width the plot has WITH the labels' gutter. The gutter
 * is fixed, so on a narrow chart it would eat the plot: below `minPlotWidth`
 * the labels do not fit either, whatever the vertical room.
 */
export function placeDirectLabels(
  requests: readonly LabelRequest[],
  {
    top,
    bottom,
    height = DIRECT_LABEL_HEIGHT,
    plotWidth,
    minPlotWidth = MIN_PLOT_WIDTH_WITH_LABELS,
  }: { top: number; bottom: number; height?: number; plotWidth?: number; minPlotWidth?: number },
): LabelPlacement {
  const usable = bottom - top
  if (plotWidth !== undefined && !(plotWidth >= minPlotWidth)) return { fits: false, labels: [] }
  if (requests.length === 0) return { fits: true, labels: [] }
  if (!(usable >= requests.length * height)) return { fits: false, labels: [] }
  const half = height / 2
  const sorted = [...requests]
    .filter((request) => Number.isFinite(request.y))
    .sort((a, b) => a.y - b.y || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
  const wanted = sorted.map((request) => Math.min(Math.max(request.y, top + half), bottom - half))
  // A cluster: `count` labels stacked `height` apart, the top one's centre at `start`.
  const clusters: { count: number; sum: number; start: number }[] = []
  const settle = (count: number, sum: number) =>
    Math.min(Math.max(sum / count - ((count - 1) * height) / 2, top + half), bottom - half - (count - 1) * height)
  for (const y of wanted) {
    clusters.push({ count: 1, sum: y, start: y })
    // Merge upwards while the newest cluster overlaps the one above it.
    while (clusters.length > 1) {
      const below = clusters[clusters.length - 1]
      const above = clusters[clusters.length - 2]
      if (above.start + above.count * height <= below.start + 1e-9) break
      const count = above.count + below.count
      const sum = above.sum + below.sum
      clusters.splice(clusters.length - 2, 2, { count, sum, start: settle(count, sum) })
    }
  }
  const ys = clusters.flatMap((cluster) => Array.from({ length: cluster.count }, (_, i) => cluster.start + i * height))
  if (ys.length > 0 && (ys[0] < top + half - 1e-6 || ys[ys.length - 1] > bottom - half + 1e-6)) return { fits: false, labels: [] }
  return { fits: true, labels: sorted.map((request, i) => ({ key: request.key, y: ys[i], target: request.y })) }
}

/** A direct label's text: short enough for the gutter; the legend carries the full name. */
export function directLabelText(label: string, max = DIRECT_LABEL_MAX_CHARS): string {
  const chars = [...label]
  return chars.length <= max ? label : `${chars.slice(0, max - 1).join('')}…`
}

// ── Showing and hiding series ────────────────────────────────────────────────

/** Hide `key` if shown, show it if hidden. */
export function toggleHidden(hidden: ReadonlySet<string>, key: string): Set<string> {
  const next = new Set(hidden)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  return next
}

/**
 * Show `key` alone. If it is ALREADY the only one shown, show every series
 * again — the same gesture undoes itself.
 */
export function isolateHidden(hidden: ReadonlySet<string>, key: string, keys: readonly string[]): Set<string> {
  const alone = keys.every((k) => (k === key ? !hidden.has(k) : hidden.has(k)))
  return alone ? new Set() : new Set(keys.filter((k) => k !== key))
}

export type VisibilityChange =
  | { kind: 'hidden' | 'shown' | 'isolated'; key: string }
  | { kind: 'all-shown' }

/**
 * What the page announcer says after a legend action, e.g. "cart hidden, 2 of
 * 3 series shown". It is the frame's own change label, so it is said once.
 */
export function visibilityAnnouncement(
  model: Pick<MultiSeriesModel, 'lines'>,
  hidden: ReadonlySet<string>,
  change: VisibilityChange,
): string {
  const total = model.lines.length
  const shown = model.lines.filter((line) => !hidden.has(line.key)).length
  const tail = `${shown} of ${total} series shown`
  if (change.kind === 'all-shown') return `all ${total} series shown`
  const label = model.lines.find((line) => line.key === change.key)?.label ?? change.key
  if (change.kind === 'isolated') return shown === total ? `all ${total} series shown` : `only ${label} shown, ${tail}`
  return `${label} ${change.kind}, ${tail}`
}

// ── For ChartFrame: the summary and the table view ───────────────────────────

/**
 * The model as a C3 series for the frame's summary and table view. A hidden
 * series is KEPT and marked "(hidden)" — the table reader must learn it is
 * hidden, not find it silently gone. When aligned, each row's header names the
 * relative day AND the absolute date for every series.
 */
export function multiSeriesToChartSeries(model: MultiSeriesModel, hidden: ReadonlySet<string> = new Set()): SeriesChart {
  const chart: SeriesChart = {
    kind: 'series',
    dimensions: [model.xTitle, 'series'],
    x_type: model.alignment === 'release-start' ? 'category' : 'time',
    series: model.lines.map((line) => ({
      key: line.key,
      label: hidden.has(line.key) ? `${line.label}${HIDDEN_SUFFIX}` : line.label,
      points: line.points.map((p) =>
        p.y === null ? { x: p.x, y: null, n: p.n, measured: false, reason: p.reason ?? NOT_REPORTED_REASON } : { x: p.x, y: p.y, n: p.n },
      ),
    })),
  }
  if (model.alignment === 'release-start') chart.x_labels = model.rowHeaders
  return chart
}
