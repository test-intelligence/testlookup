/**
 * The coverage map's model (VIZ-502): everything the treemap, its section and
 * its tests need to know about `/analytics/coverage-map`, with no component,
 * no DOM and no engine.
 *
 * ONE LEVEL PER REQUEST. The endpoint answers one level of the hierarchy at a
 * time (suites, then the classes or files of one suite, then the tests of one
 * class): the parent as the single root and its children (at most 499, the
 * rest folded into one "Other (n)" node). So a drill is a REQUEST, never a
 * client-side zoom, and no response is ever more than 500 nodes. Which level
 * is shown is the page's drill path (C5 `drill=suite~<key>&drill=class~<key>`).
 *
 * KEYS, NOT LABELS. A child's id is `s:<suite key>`, `c:<suite key>␟<class
 * key>`, `t:<fingerprint>` or `other:<parent id>`. The key the next level asks
 * for is recovered by STRIPPING the prefix this level already knows (BE2 R7,
 * `meta.definitions.node_id`), never by splitting on the separator: a class
 * key may itself contain it, and a suite key may contain anything. A child
 * whose id does not carry the expected prefix is not drillable.
 *
 * COLOUR is one of three measures, each on a FIXED scale (never fitted to the
 * data, so the same colour means the same thing on every level and every
 * project), in five bins drawn from the theme's sequential ramp; the most
 * salient step is the concern (a low pass rate, a long time since the last
 * run, a high flaky share). A node the measure says nothing about is not the
 * lowest bin: it is a GAP, drawn hatched and named in words, because "not run
 * in this window", "last run unknown" (its run was deleted) and "never run"
 * are three different facts and none of them is 0.
 */
import type { DrillLevel, TreeChart, TreeNode, TreeNodeStats, VizDimension } from '@/lib/viz/contracts'
import { writeDrillParams } from '@/hooks/useDrillPath'
import { normalizeScope, type ScopeValue } from '@/lib/scopeParams'
import { formatNumber, formatPercent } from '@/utils/formatters'
import type { CatalogParams } from './chartCatalogSources'
import type { DecalKind } from './tokens'
import type { ChartMark, MarkIntent, PointerModifiers } from './marks'
import type { NavRequest } from './useChartKeyboard'
import { middleTruncate } from './labelTruncate'
import { tipContent, type TooltipContent, type TooltipRow } from './tooltip'

// ── Words ────────────────────────────────────────────────────────────────────

export const COVERAGE_MAP_TITLE = 'Test coverage map'

/** The subtitle, exactly as the EPIC's "Honest labelling" scenario words it (VIZ-502). */
export const COVERAGE_MAP_CAPTION = 'Test execution coverage — not code coverage'

/** The breadcrumb's first item: the level-1 root. */
export const ROOT_CRUMB = 'All suites'

export const COVERAGE_KEYBOARD_HINT = 'Arrow keys move, Enter opens, Backspace goes up a level, Tab leaves'

/** What a child of each level is, plural, for the footer and the description. */
export const LEVEL_NOUN: Record<CoverageDepth, string> = { 1: 'suites', 2: 'classes or files', 3: 'tests' }
const LEVEL_NOUN_ONE: Record<CoverageDepth, string> = { 1: 'suite', 2: 'class or file', 3: 'test' }

export const OTHER_NOTE = 'The smaller items of this level, combined. It does not open.'
export const OTHER_RATE = 'Not computed for a combined group'

// ── Levels ───────────────────────────────────────────────────────────────────

export type CoverageDepth = 1 | 2 | 3

/** Which parent's children are shown: the keys come from the drill path, never from labels. */
export interface CoverageLevel {
  depth: CoverageDepth
  /** The suite KEY (depth 2 and 3). */
  suite: string | null
  /** The class KEY (depth 3). */
  classKey: string | null
}

export const TOP_LEVEL: CoverageLevel = { depth: 1, suite: null, classKey: null }

/** Why a link's drill-down levels were cut (C5: the cut is stated), as the page's notice says it. */
export const COVERAGE_DROP_WORDS = {
  unsupported: 'The coverage map drills by suite, then class or file, so the rest of the drill-down in this link was not applied.',
  outOfScope: 'A suite in this link’s drill-down is not in the page’s suite filter, so the map opens above it.',
} as const

/** The notice's reason for a drill-down the page could not open (the values are the sentences). */
export const COVERAGE_DRILL_NOTICE_REASON = 'Drill-down in this link'

export interface CoverageLevelRead {
  level: CoverageLevel
  /** How many levels of the path the map applied (the rest were cut). */
  used: number
  /** Why levels were cut, as sentences for the page's notice; `[]` when nothing was. */
  dropped: string[]
}

const foldSuite = (value: string) => value.trim().toLowerCase()

/**
 * The level a drill path asks for, how many of its levels that used, and why
 * the rest were cut. The map reads `suite` then `class` and nothing below
 * (a test opens its rows, never a level); anything else (another host's
 * dimension, a class with no suite above it, a third level) ends the path
 * there, so a hand-edited URL shows the deepest level it validly names
 * instead of an error, and says so.
 *
 * A suite level asks `suite=<key>` INSIDE the page's suite scope, so a suite
 * the page's filter does not allow would draw an empty level that blames the
 * filters: like the Failures ladder (`ladderFromPath`), the path is cut there.
 * The key is the suite's name trimmed and lower-cased (`coverage_map_service`),
 * the filter holds names: both are folded the same way.
 */
export function levelFromPath(path: readonly DrillLevel[], suiteFilter: ScopeValue = null): CoverageLevelRead {
  const allowed = normalizeScope(suiteFilter).map(foldSuite).filter((name) => name !== '')
  const [first, second] = path
  const cut = (level: CoverageLevel, used: number, why: string): CoverageLevelRead => ({ level, used, dropped: [why] })
  if (first === undefined) return { level: TOP_LEVEL, used: 0, dropped: [] }
  if (first.dimension !== 'suite') return cut(TOP_LEVEL, 0, COVERAGE_DROP_WORDS.unsupported)
  if (allowed.length > 0 && !allowed.includes(foldSuite(first.value))) return cut(TOP_LEVEL, 0, COVERAGE_DROP_WORDS.outOfScope)
  const suiteLevel: CoverageLevel = { depth: 2, suite: first.value, classKey: null }
  if (second === undefined) return { level: suiteLevel, used: 1, dropped: [] }
  if (second.dimension !== 'class') return cut(suiteLevel, 1, COVERAGE_DROP_WORDS.unsupported)
  const classLevel: CoverageLevel = { depth: 3, suite: first.value, classKey: second.value }
  return path.length > 2 ? cut(classLevel, 2, COVERAGE_DROP_WORDS.unsupported) : { level: classLevel, used: 2, dropped: [] }
}

/** The drill path that names `level` (the inverse of `levelFromPath`). */
export function levelPath(level: CoverageLevel): DrillLevel[] {
  const out: DrillLevel[] = []
  if (level.depth >= 2 && level.suite !== null) out.push({ dimension: 'suite', value: level.suite })
  if (level.depth === 3 && level.classKey !== null) out.push({ dimension: 'class', value: level.classKey })
  return out
}

/** The level's own request parameters (the scope is added by `catalogueParams`). */
export function levelRequest(level: CoverageLevel): CatalogParams {
  const params: CatalogParams = { depth: level.depth }
  if (level.depth >= 2 && level.suite !== null) params.suite = level.suite
  if (level.depth === 3 && level.classKey !== null) params.class_key = level.classKey
  return params
}

// ── Nodes ────────────────────────────────────────────────────────────────────

/** The id prefixes the endpoint issues (`coverage_map_service.py`). */
export const NODE_PREFIX = { suite: 's:', class: 'c:', test: 't:', other: 'other:' } as const

/** Between the suite key and the class key in a class id: U+241F, printable on purpose. */
export const CLASS_KEY_SEPARATOR = '␟'

/** The level-1 root's id. */
export const ROOT_ID = 'all'

/**
 * The id of the root a response for `level` carries: `all`, `s:<suite>`,
 * `c:<suite>␟<class>`. Built from the keys the level asked with (never parsed
 * out of an id), so it tells whether a response IS this level's: while the
 * next level loads, the chart keeps the previous response on screen, and a
 * level-1 response read as level 2 would offer the wrong keys.
 */
export function levelRootId(level: CoverageLevel): string {
  if (level.depth === 1) return ROOT_ID
  if (level.depth === 2) return `${NODE_PREFIX.suite}${level.suite ?? ''}`
  return `${NODE_PREFIX.class}${level.suite ?? ''}${CLASS_KEY_SEPARATOR}${level.classKey ?? ''}`
}

/** Whether `tree` answers `level` (an empty level, with no root, answers any). */
export function answersLevel(tree: TreeChart, level: CoverageLevel): boolean {
  const root = tree.nodes.find((node) => node.parent_id === null)
  return root === undefined || root.id === levelRootId(level)
}

export type CoverageNodeKind = 'suite' | 'class' | 'test' | 'other'

export interface CoverageChild {
  node: TreeNode
  kind: CoverageNodeKind
  /** The key the next request (or the rows panel) sends; `null` when the id is not one this level issues. */
  key: string | null
}

/** The prefix a child id of `level` must start with. */
function childPrefix(level: CoverageLevel): string {
  if (level.depth === 1) return NODE_PREFIX.suite
  if (level.depth === 2) return `${NODE_PREFIX.class}${level.suite ?? ''}${CLASS_KEY_SEPARATOR}`
  return NODE_PREFIX.test
}

const KIND_OF_DEPTH: Record<CoverageDepth, CoverageNodeKind> = { 1: 'suite', 2: 'class', 3: 'test' }

/** What a child of `level` is, from its id. */
export function childKind(level: CoverageLevel, id: string): CoverageNodeKind {
  return id.startsWith(NODE_PREFIX.other) ? 'other' : KIND_OF_DEPTH[level.depth]
}

/**
 * A child's key: its id with the prefix this level knows STRIPPED (BE2 R7).
 * `null` for the Other node, and for an id that does not start with the
 * prefix (never guessed by splitting).
 */
export function childKey(level: CoverageLevel, id: string): string | null {
  if (id.startsWith(NODE_PREFIX.other)) return null
  const prefix = childPrefix(level)
  if (!id.startsWith(prefix)) return null
  const key = id.slice(prefix.length)
  return key === '' ? null : key
}

export interface CoverageLevelView {
  /** The parent: the response's one node with no parent. */
  root: TreeNode | null
  /** Its children, in the server's order (test count, largest first). */
  children: CoverageChild[]
}

/** The response as one level: the root and its children, each with its kind and key. */
export function levelView(tree: TreeChart | null, level: CoverageLevel): CoverageLevelView {
  if (!tree) return { root: null, children: [] }
  const root = tree.nodes.find((node) => node.parent_id === null) ?? null
  if (!root) return { root: null, children: [] }
  const children = tree.nodes
    .filter((node) => node.parent_id === root.id)
    .map((node) => ({ node, kind: childKind(level, node.id), key: childKey(level, node.id) }))
  return { root, children }
}

/** The drill level a child opens, or `null` (a test, the Other node, an unrecognised id). */
export function drillTarget(child: CoverageChild): DrillLevel | null {
  if (child.key === null) return null
  if (child.kind === 'suite') return { dimension: 'suite', value: child.key }
  if (child.kind === 'class') return { dimension: 'class', value: child.key }
  return null
}

/**
 * The rows behind a test leaf: its executions, as chart-data counts them per
 * test (`group_by=test` with the drilled suite as the scope's one suite; the
 * leaf's fingerprint is the `bucket_test` selector).
 */
export const TEST_ROWS_CHART: { metric: string; groupBy: readonly VizDimension[] } = { metric: 'executions', groupBy: ['test'] }

/** What a reader can do with a child: open its level, see a test's executions, or nothing. */
export function childIntents(child: CoverageChild): MarkIntent[] {
  if (child.key === null) return []
  return child.kind === 'test' ? ['rows'] : ['drill']
}

/** The C5 dimension a child of each level stands for (the Other node too: it is made of them). */
const MARK_DIMENSION: Record<CoverageDepth, VizDimension> = { 1: 'suite', 2: 'class', 3: 'test' }

/** The child as a kit mark: its dimension and KEY, the label it is drawn with, its pass rate and executions. */
export function childMark(child: CoverageChild, level: CoverageLevel): ChartMark {
  const stats = child.node.stats
  const context: { dimension: VizDimension; value: string }[] = []
  if (level.depth >= 2 && level.suite !== null) context.push({ dimension: 'suite', value: level.suite })
  if (level.depth === 3 && level.classKey !== null) context.push({ dimension: 'class', value: level.classKey })
  return {
    dimension: MARK_DIMENSION[level.depth],
    value: child.key ?? child.node.id,
    label: child.node.label,
    y: stats ? stats.pass_rate : child.node.measure,
    n: stats ? stats.executions : null,
    ...(context.length > 0 ? { context } : {}),
  }
}

// ── Labels ───────────────────────────────────────────────────────────────────

/** The longest name drawn inside a rectangle; ECharts then cuts it to the rectangle, measuring the real font. */
export const NODE_LABEL_MAX = 48

/** A Java / .NET qualified class name: the last segment is the class. */
const QUALIFIED_NAME = /^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+$/

/**
 * The text drawn inside a node. A class level is a FILE PATH for pytest,
 * Playwright and Cypress and a qualified name for TestNG, xUnit and NUnit, and
 * a rectangle cut from the END would print "tests/api/pa…" for every file in
 * one folder: the distinguishing part is the LAST segment, so that is drawn.
 * Then a long name is cut in the middle, so its end survives. The full label
 * is in the tooltip, the announcement and the table.
 */
export function nodeDisplayName(label: string, kind: CoverageNodeKind): string {
  let text = label
  if (kind === 'class') {
    const segments = label.split(/[/\\]/).filter((s) => s !== '')
    if (segments.length > 1) text = segments[segments.length - 1]
    else if (QUALIFIED_NAME.test(label)) {
      const last = label.slice(label.lastIndexOf('.') + 1)
      // A class is capitalised; "test_pay.py" is a file, kept whole.
      if (/^[A-Z]/.test(last)) text = last
    }
  }
  return middleTruncate(text, NODE_LABEL_MAX)
}

// ── Colour ───────────────────────────────────────────────────────────────────

export type CoverageColorBy = 'pass_rate' | 'staleness' | 'flaky_share'

export const COLOR_BY_OPTIONS: readonly { value: CoverageColorBy; label: string }[] = [
  { value: 'pass_rate', label: 'Pass rate' },
  { value: 'staleness', label: 'Days since last run' },
  { value: 'flaky_share', label: 'Flaky share' },
]

export const DEFAULT_COLOR_BY: CoverageColorBy = 'pass_rate'

export function isColorBy(value: string): value is CoverageColorBy {
  return COLOR_BY_OPTIONS.some((option) => option.value === value)
}

export function colorByLabel(colorBy: CoverageColorBy): string {
  return COLOR_BY_OPTIONS.find((option) => option.value === colorBy)?.label ?? colorBy
}

/** Why a node has no value for the measure: three different facts, none of them 0. */
export type CoverageGap = 'not_run' | 'unknown' | 'never'

export const GAP_WORDS: Record<CoverageGap, string> = {
  not_run: 'Not run in this window',
  // Its last run was deleted, so the date is lost: we do not know, which is not "never".
  unknown: 'Last run unknown',
  never: 'Never run',
}

/** Each gap's pattern: never colour alone (the legend draws the same three). */
export const GAP_DECAL: Record<CoverageGap, DecalKind> = { not_run: 'diagonal', unknown: 'dots', never: 'crosshatch' }

export interface NodeMeasure {
  value: number | null
  gap: CoverageGap | null
}

function recencyGap(stats: TreeNodeStats): CoverageGap {
  if (stats.recency === 'never') return 'never'
  if (stats.recency === 'unknown') return 'unknown'
  return 'not_run'
}

/**
 * A node's value for the measure, or the gap it has instead. Flaky share is a
 * gap for a node with no executions in the window: "0% flaky" over nothing
 * run would be a 0 standing in for "not measured".
 */
export function measureOf(stats: TreeNodeStats | undefined, colorBy: CoverageColorBy): NodeMeasure {
  if (!stats) return { value: null, gap: 'unknown' }
  switch (colorBy) {
    case 'pass_rate':
      return stats.pass_rate === null ? { value: null, gap: recencyGap(stats) } : { value: stats.pass_rate, gap: null }
    case 'staleness':
      return stats.staleness_days === null
        ? { value: null, gap: stats.recency === 'never' ? 'never' : 'unknown' }
        : { value: stats.staleness_days, gap: null }
    case 'flaky_share':
      return stats.executions === 0 || stats.flaky_share === null
        ? { value: null, gap: recencyGap(stats) }
        : { value: stats.flaky_share, gap: null }
  }
}

export interface ColorBin {
  /** The value's upper (`below`) or lower (`atLeast`) edge; see `binOf`. */
  edge: number
  label: string
}

/**
 * The five bins of each measure, least salient first. Fixed edges, chosen
 * where a reader draws a line, not fitted to the data:
 *   - pass rate (%, the bin holds values AT LEAST its edge): 90, 75, 50, 25, 0;
 *   - days since the last run (the bin holds values AT MOST its edge): 1, 7, 30, 90, more;
 *   - flaky share (0..1, AT MOST its edge): none, 5%, 15%, 30%, more.
 */
export const COLOR_BINS: Record<CoverageColorBy, readonly ColorBin[]> = {
  pass_rate: [
    { edge: 90, label: '90–100%' },
    { edge: 75, label: '75–90%' },
    { edge: 50, label: '50–75%' },
    { edge: 25, label: '25–50%' },
    { edge: 0, label: 'Under 25%' },
  ],
  staleness: [
    { edge: 1, label: '0–1 days' },
    { edge: 7, label: '2–7 days' },
    { edge: 30, label: '8–30 days' },
    { edge: 90, label: '31–90 days' },
    { edge: Number.POSITIVE_INFINITY, label: 'Over 90 days' },
  ],
  flaky_share: [
    { edge: 0, label: 'None' },
    { edge: 0.05, label: 'Up to 5%' },
    { edge: 0.15, label: '5–15%' },
    { edge: 0.3, label: '15–30%' },
    { edge: Number.POSITIVE_INFINITY, label: 'Over 30%' },
  ],
}

/** The ramp step (of the theme's seven) each bin is drawn in: evenly spread, the last is the salient end. */
export const BIN_RAMP_STEPS = [0, 2, 3, 5, 6] as const

/** Which bin (0 = least salient) a measured value falls in. */
export function binOf(value: number, colorBy: CoverageColorBy): number {
  const bins = COLOR_BINS[colorBy]
  if (colorBy === 'pass_rate') {
    const index = bins.findIndex((bin) => value >= bin.edge)
    return index === -1 ? bins.length - 1 : index
  }
  const index = bins.findIndex((bin) => value <= bin.edge)
  return index === -1 ? bins.length - 1 : index
}

/**
 * A measured value as a tile prints it under the name (F-03: the bin is never
 * carried by colour alone): the table's and the tooltip's number, short.
 */
export function measureText(value: number, colorBy: CoverageColorBy): string {
  switch (colorBy) {
    case 'pass_rate':
      return formatPercent(value)
    case 'staleness':
      return value === 1 ? '1 day' : `${formatNumber(value)} days`
    case 'flaky_share':
      return `${formatPercent(value, { from: 'ratio' })} flaky`
  }
}

/**
 * The text a tile draws: its shortened name, then, when the measure has a
 * value for it, that value on a second line. Built into the DATA (no ECharts
 * formatter, plan F6); ECharts drops a line the tile has no height for and
 * cuts one it has no width for, so a small tile keeps its name. A gap draws
 * no second line: its pattern says it, the legend names the pattern.
 */
export function nodeCanvasText(child: CoverageChild, colorBy: CoverageColorBy): string {
  const name = nodeDisplayName(child.node.label, child.kind)
  // Exactly the value the fill is binned from (`nodeFill`), so text and colour never disagree.
  const { value } = measureOf(child.node.stats, colorBy)
  return value === null ? name : `${name}\n${measureText(value, colorBy)}`
}

/** The legend's sentence about the tiles' second line (F-03). */
export function tileValueNote(colorBy: CoverageColorBy): string {
  return `Tiles with room show their ${colorByLabel(colorBy).toLowerCase()} under the name.`
}

// ── Text ─────────────────────────────────────────────────────────────────────

const KIND_NOUN: Record<CoverageNodeKind, string> = { suite: 'Suite', class: 'Class or file', test: 'Test', other: 'Combined' }

/** The last run, as the table says it: a UTC day, "Never run", or "Last run unknown". */
export function lastRunText(stats: TreeNodeStats): string {
  if (stats.recency === 'seen' && stats.last_executed_at) return stats.last_executed_at.slice(0, 10)
  return stats.recency === 'never' ? GAP_WORDS.never : GAP_WORDS.unknown
}

/**
 * A node's tooltip, in the shared model (VIZ-601): its full label as the title,
 * then what it is, its tests, pass rate (or the reason it has none),
 * executions, flaky tests and its last run. The mouse tooltip and the keyboard
 * announcement are both this, so they cannot differ.
 */
export function nodeTooltip(child: CoverageChild): TooltipContent {
  const stats = child.node.stats
  const rows: (TooltipRow | null)[] = [{ key: 'kind', kind: 'dimension', label: 'Level', value: KIND_NOUN[child.kind] }]
  if (!stats) {
    rows.push({ key: 'tests', label: 'Tests', value: formatNumber(child.node.value) })
    return tipContent(child.node.label, rows)
  }
  const rate =
    child.kind === 'other'
      ? OTHER_RATE
      : stats.pass_rate === null
        ? GAP_WORDS[recencyGap(stats)]
        : formatPercent(stats.pass_rate)
  rows.push(
    { key: 'tests', label: 'Tests', value: formatNumber(stats.test_count) },
    { key: 'pass-rate', label: 'Pass rate', value: rate },
    { key: 'executions', kind: 'sample', label: 'Executions', value: formatNumber(stats.executions) },
    {
      key: 'flaky',
      label: 'Flaky tests',
      value: formatNumber(stats.flaky_count),
      ...(stats.flaky_share !== null && stats.executions > 0 ? { detail: formatPercent(stats.flaky_share, { from: 'ratio' }) } : {}),
    },
    { key: 'last-run', label: 'Last run', value: lastRunText(stats) },
    stats.staleness_days === null
      ? null
      : { key: 'staleness', label: 'Days since last run', value: formatNumber(stats.staleness_days) },
    child.kind === 'other' ? { key: 'other', kind: 'note', label: '', value: OTHER_NOTE } : null,
  )
  return tipContent(child.node.label, rows)
}

/** The chart's one-sentence accessible name. */
export function coverageDescription(view: CoverageLevelView, level: CoverageLevel, colorBy: CoverageColorBy): string {
  const parent = view.root?.label ?? ROOT_CRUMB
  const count = view.children.length
  const noun = count === 1 ? LEVEL_NOUN_ONE[level.depth] : LEVEL_NOUN[level.depth]
  return `${COVERAGE_MAP_TITLE}: ${formatNumber(count)} ${noun} in ${parent}, sized by test count, coloured by ${colorByLabel(colorBy).toLowerCase()}.`
}

/**
 * An empty level's frame body, px (F-14): its one sentence, not a 360 px band
 * of nothing over a footer that counts the scope's executions. The section and
 * the gallery item draw the same.
 */
export const COVERAGE_MAP_EMPTY_HEIGHT = 120

/** Where a level's children sit, after "No <children> to map". */
const EMPTY_PLACE: Record<CoverageDepth, string> = { 1: '', 2: ' in this suite', 3: ' in this class or file' }

/**
 * An empty level's one sentence (the frame's `emptyMessage`). Not "No data
 * matches the current filters": the footer under it counts the scope's
 * executions, and a level with no children is a fact about the level.
 */
export function coverageEmptyText(level: CoverageLevel): string {
  return `No ${LEVEL_NOUN[level.depth]} to map${EMPTY_PLACE[level.depth]} in this window.`
}

/** The footer's truncation sentence, or `''` when the level holds every child. */
export function otherNote(view: CoverageLevelView, total: number | null, depth: CoverageDepth): string {
  const folded = view.children.find((child) => child.kind === 'other')
  if (!folded) return ''
  const kept = view.children.length - 1
  const of = total !== null && total > kept ? ` of ${formatNumber(total)}` : ''
  return `Showing the ${formatNumber(kept)} largest${of} ${LEVEL_NOUN[depth]}; the rest are combined in ${folded.node.label}.`
}

// ── Breadcrumb ───────────────────────────────────────────────────────────────

/** A drill level's place in the label memory. */
export const crumbKey = (level: DrillLevel): string => `${level.dimension}~${level.value}`

/** The class key of tests that carry no class (`coverage_map_service.NO_CLASS_KEY`). */
export const NO_CLASS_KEY = '__none__'
export const NO_CLASS_LABEL = '(ungrouped)'

/**
 * The labels this response teaches: its children's (so a drill shows the
 * label the reader activated, and a test's rows panel its name) and its
 * root's (so a shared URL opened at a deep level names the level it is on).
 * Keys only, never parsed.
 */
export function learnLabels(view: CoverageLevelView, level: CoverageLevel): Record<string, string> {
  const out: Record<string, string> = {}
  for (const child of view.children) {
    const target = drillTarget(child) ?? (child.kind === 'test' && child.key !== null ? { dimension: 'test' as const, value: child.key } : null)
    if (target) out[crumbKey(target)] = child.node.label
  }
  const path = levelPath(level)
  const here = path[path.length - 1]
  if (here && view.root) out[crumbKey(here)] = view.root.label
  return out
}

/** A level's breadcrumb label: what the map last called it, else the key itself (a class's NULL bucket by name). */
export function crumbLabel(labels: Readonly<Record<string, string>>, level: DrillLevel): string {
  const known = Object.prototype.hasOwnProperty.call(labels, crumbKey(level)) ? labels[crumbKey(level)] : undefined
  if (known !== undefined) return known
  if (level.dimension === 'class' && level.value === NO_CLASS_KEY) return NO_CLASS_LABEL
  return level.value
}

/**
 * The address of the map at `levels` (C5): the current query string with its
 * `drill` levels replaced and any open rows panel closed. Every other key is
 * kept as it is, in its order; `URLSearchParams` does all the encoding.
 */
export function drillSearch(search: string, levels: readonly DrillLevel[]): string {
  // The kit's one encoder (`useDrillPath`), so a breadcrumb link and a drill step write the same URL.
  const text = writeDrillParams(search, { path: levels, rows: [] }).toString()
  return text === '' ? '?' : `?${text}`
}

// ── Keyboard and pointer ─────────────────────────────────────────────────────

/** The keys, in words, for this level (Backspace only where there is a level above). */
export function treemapKeyboardHint(level: CoverageLevel): string {
  const open = level.depth === 3 ? 'Enter shows its runs' : 'Enter opens it'
  const up = level.depth > 1 ? ', Backspace goes up a level' : ''
  return `Arrow keys move, ${open}${up}, Escape clears, Tab leaves`
}

/** Next index in the drawn (size) order: a list, not a grid. */
export function moveInOrder(count: number, current: number | null, { key }: NavRequest): number | null {
  if (count === 0) return null
  if (current === null || current < 0 || current >= count) return key === 'End' ? count - 1 : 0
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

/**
 * The child a canvas click landed on, and the keys held: ECharts hands the
 * node's `dataIndex` (child k is k + 1; the virtual root, 0, is no child) and
 * the DOM event under `event.event`. Read without trusting the shape.
 */
export function treemapClick(params: unknown): { index: number; modifiers: PointerModifiers } | null {
  const p = params as { dataIndex?: unknown; event?: { event?: unknown } } | null | undefined
  const dataIndex = p?.dataIndex
  if (typeof dataIndex !== 'number' || !Number.isInteger(dataIndex) || dataIndex < 1) return null
  const native = (p?.event?.event ?? {}) as { shiftKey?: unknown; ctrlKey?: unknown; metaKey?: unknown; pointerType?: unknown }
  return {
    index: dataIndex - 1,
    modifiers: {
      shiftKey: native.shiftKey === true,
      ctrlKey: native.ctrlKey === true,
      metaKey: native.metaKey === true,
      ...(typeof native.pointerType === 'string' ? { pointerType: native.pointerType } : {}),
    },
  }
}
