/**
 * The failure-groups model (VIZ-504): what `/api/v1/analytics/failure-groups`
 * says, read into the shape the three views (bubbles, related groups, ranked
 * table) and the side panel draw. Pure: no React, no DOM, no d3.
 *
 * The body is a C3 `graph` (validated before it gets here: `nodes`, `edges`)
 * plus ADDITIVE keys the contract does not describe and the validator does not
 * look at: `groups` (the per-group counts, share, categories, trend and top
 * tests, in rank order), `no_message`, `singletons` and `omitted` (roll-ups,
 * never nodes), `trend_grain` and `total_failures` (BE3, decision 5). Those
 * keys are read DEFENSIVELY here — a missing or malformed field is `null`
 * (shown as "—"), never a 0 standing in for it, and never a throw: a body that
 * passed the graph validator always draws.
 *
 * Every string in it is untrusted: a group's label is a raw first line of an
 * error message from an ingested CI file (`<img src=x onerror=…>` included),
 * and a test name is a test name. They are kept as data and reach the DOM as
 * React text only. Ids are used as Map keys, never as object keys, so a group
 * whose signature is `__proto__` or `constructor` is just another group.
 */
import type { DrillLevel, GraphChart, SeriesChart } from '@/lib/viz/contracts'
import { formatNumber, formatPercent } from '@/utils/formatters'
import { NO_VALUE } from '../chartText'
import type { ChartMark } from '../marks'
import type { FailureCategoryKey } from './categoryPatterns'
import { tipContent, type TooltipContent, type TooltipRow } from '../tooltip'

/** BE3's sentinel ids (UPPER case on purpose: a signature is always lower case, so they cannot collide). */
export const NO_MESSAGE_ID = '__NO_MESSAGE__'
export const SINGLETONS_ID = '__SINGLETONS__'

/**
 * The failure categories the server sends (`failure_category`, lower-cased),
 * plus its roll-up `other`, in legend order. Each has its fixed colour and
 * pattern in `categoryPatterns.ts` (`FAILURE_CATEGORY_PATTERNS`).
 */
export const FAILURE_CATEGORY_KEYS: readonly FailureCategoryKey[] = [
  'product_bug',
  'automation_defect',
  'test_data',
  'infrastructure',
  'flaky',
  'unknown',
  'other',
]
export type { FailureCategoryKey }

const CATEGORY_WORDS: Readonly<Record<FailureCategoryKey, string>> = {
  product_bug: 'Product bug',
  automation_defect: 'Automation defect',
  test_data: 'Test data',
  infrastructure: 'Infrastructure',
  flaky: 'Flaky',
  unknown: 'Unknown',
  other: 'Other',
}

const KNOWN_CATEGORIES: ReadonlySet<string> = new Set(FAILURE_CATEGORY_KEYS)

/**
 * The encoding a category is DRAWN with (its colour and pattern): one of the
 * seven. A category the server has never sent before is drawn as `other` —
 * its own words are still shown (`categoryLabel`), so nothing is renamed.
 * No category at all is `unknown`, which is what the server calls a failure
 * nobody classified (`COALESCE(failure_category, 'UNKNOWN')`).
 */
export function categoryKey(raw: string | null | undefined): FailureCategoryKey {
  if (raw === null || raw === undefined) return 'unknown'
  const key = raw.trim().toLowerCase()
  if (key === '') return 'unknown'
  return KNOWN_CATEGORIES.has(key) ? (key as FailureCategoryKey) : 'other'
}

/** A category in words: the vocabulary's own words, or the raw text for one it does not know. */
export function categoryLabel(raw: string | null | undefined): string {
  if (raw === null || raw === undefined || raw.trim() === '') return CATEGORY_WORDS.unknown
  const key = raw.trim().toLowerCase()
  return KNOWN_CATEGORIES.has(key) ? CATEGORY_WORDS[key as FailureCategoryKey] : raw
}

export interface FailureGroupCategory {
  category: string
  count: number
}

export interface FailureGroupTrendPoint {
  /** A UTC day, or the Monday of an ISO week (`trendGrain`). */
  x: string
  y: number
}

export interface FailureGroupTopTest {
  fingerprint: string
  projectId: string | null
  name: string
  count: number
}

export interface FailureGroup {
  /** The signature (`__NO_MESSAGE__` never appears here: that is a roll-up). The rows selector sends it back. */
  id: string
  /** 1-based, the server's order (failures desc, signature asc). */
  rank: number
  /** The most frequent raw first line. Untrusted. */
  label: string
  failureCount: number
  affectedTests: number | null
  affectedRuns: number | null
  /** How many different raw first lines share the signature: 2+ means two causes may have collided. */
  distinctRawLines: number | null
  firstSeen: string | null
  lastSeen: string | null
  /** 0..1 of every failing execution in scope; `null` when the server could not say (no failures at all). */
  share: number | null
  categories: FailureGroupCategory[]
  /** The raw dominant category, as the server sent it (`null` when it sent none). */
  dominantCategory: string | null
  trend: FailureGroupTrendPoint[]
  topTests: FailureGroupTopTest[]
}

export interface FailureGroupRollup {
  failureCount: number
  share: number | null
  /** How many signatures the roll-up holds (singletons, omitted); absent for "no message". */
  groupCount: number | null
}

export interface FailureGroupEdge {
  source: string
  target: string
  /** Jaccard of the two groups' affected-test sets, 0..1. */
  weight: number
}

export type TrendGrain = 'day' | 'week'

export interface FailureGroupsModel {
  /** Rank order. */
  groups: FailureGroup[]
  /** Only between groups in `groups`, each pair once. */
  edges: FailureGroupEdge[]
  /** Every failing execution in scope; `null` when the server did not say. */
  totalFailures: number | null
  noMessage: FailureGroupRollup | null
  singletons: FailureGroupRollup | null
  /** Groups beyond the server's cap of 200. */
  omitted: FailureGroupRollup | null
  trendGrain: TrendGrain | null
}

// ── Defensive readers ────────────────────────────────────────────────────────

type Dict = Record<string, unknown>

const isDict = (value: unknown): value is Dict => typeof value === 'object' && value !== null && !Array.isArray(value)
const count = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
const ratio = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null
const text = (value: unknown): string | null => (typeof value === 'string' ? value : null)
const own = (record: Dict, key: string): unknown => (Object.prototype.hasOwnProperty.call(record, key) ? record[key] : undefined)

function readCategories(value: unknown): FailureGroupCategory[] {
  if (!Array.isArray(value)) return []
  const out: FailureGroupCategory[] = []
  for (const item of value) {
    if (!isDict(item)) continue
    const category = text(own(item, 'category'))
    const n = count(own(item, 'count'))
    if (category !== null && n !== null) out.push({ category, count: n })
  }
  return out
}

function readTrend(value: unknown): FailureGroupTrendPoint[] {
  if (!Array.isArray(value)) return []
  const out: FailureGroupTrendPoint[] = []
  for (const item of value) {
    if (!isDict(item)) continue
    const x = text(own(item, 'x'))
    const y = count(own(item, 'y'))
    if (x !== null && y !== null) out.push({ x, y })
  }
  return out
}

function readTopTests(value: unknown): FailureGroupTopTest[] {
  if (!Array.isArray(value)) return []
  const out: FailureGroupTopTest[] = []
  for (const item of value) {
    if (!isDict(item)) continue
    const fingerprint = text(own(item, 'fingerprint'))
    const n = count(own(item, 'count'))
    if (fingerprint === null || n === null) continue
    out.push({ fingerprint, projectId: text(own(item, 'project_id')), name: text(own(item, 'name')) ?? '', count: n })
  }
  return out
}

function readRollup(value: unknown): FailureGroupRollup | null {
  if (!isDict(value)) return null
  const failures = count(own(value, 'failure_count'))
  if (failures === null) return null
  return { failureCount: failures, share: ratio(own(value, 'share_of_failures')), groupCount: count(own(value, 'group_count')) }
}

/** `a` before `b` by UTF-16 code unit: the server's tie order, never the locale's. */
export const byCodeUnit = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0)

/**
 * The model from a validated graph body. The groups come from `groups` (rank
 * order) when the server sent it; otherwise — an older body, a hand-made
 * fixture — from the nodes (size desc, id), with every figure the nodes do not
 * carry left `null`.
 */
export function failureGroupsModel(series: GraphChart): FailureGroupsModel {
  const body = series as unknown as Dict
  const nodes = new Map(series.nodes.map((node) => [node.id, node]))
  const groups: FailureGroup[] = []
  const seen = new Set<string>()
  const rawGroups = own(body, 'groups')
  if (Array.isArray(rawGroups)) {
    for (const item of rawGroups) {
      if (!isDict(item)) continue
      const id = text(own(item, 'id'))
      const failures = count(own(item, 'failure_count'))
      if (id === null || failures === null || seen.has(id)) continue
      seen.add(id)
      const node = nodes.get(id)
      const dominant = text(own(item, 'dominant_category')) ?? node?.group ?? null
      groups.push({
        id,
        rank: groups.length + 1,
        label: text(own(item, 'label')) ?? node?.label ?? id,
        failureCount: failures,
        affectedTests: count(own(item, 'affected_tests')),
        affectedRuns: count(own(item, 'affected_runs')),
        distinctRawLines: count(own(item, 'distinct_raw_lines')),
        firstSeen: text(own(item, 'first_seen')),
        lastSeen: text(own(item, 'last_seen')),
        share: ratio(own(item, 'share_of_failures')),
        categories: readCategories(own(item, 'categories')),
        dominantCategory: dominant,
        trend: readTrend(own(item, 'trend')),
        topTests: readTopTests(own(item, 'top_tests')),
      })
    }
  } else {
    const ordered = [...series.nodes].sort((a, b) => b.size - a.size || byCodeUnit(a.id, b.id))
    for (const node of ordered) {
      if (seen.has(node.id)) continue
      seen.add(node.id)
      groups.push({
        id: node.id,
        rank: groups.length + 1,
        label: node.label,
        failureCount: node.size,
        affectedTests: null,
        affectedRuns: null,
        distinctRawLines: null,
        firstSeen: null,
        lastSeen: null,
        share: null,
        categories: [],
        dominantCategory: node.group ?? null,
        trend: [],
        topTests: [],
      })
    }
  }

  // An edge is drawn between two groups the reader can see, once per pair.
  const pairs = new Set<string>()
  const edges: FailureGroupEdge[] = []
  for (const edge of series.edges) {
    if (edge.source === edge.target || !seen.has(edge.source) || !seen.has(edge.target)) continue
    const [a, b] = byCodeUnit(edge.source, edge.target) <= 0 ? [edge.source, edge.target] : [edge.target, edge.source]
    const pair = `${a.length}:${a}${b}`
    if (pairs.has(pair)) continue
    pairs.add(pair)
    edges.push({ source: edge.source, target: edge.target, weight: edge.weight })
  }

  const grain = own(body, 'trend_grain')
  return {
    groups,
    edges,
    totalFailures: count(own(body, 'total_failures')),
    noMessage: readRollup(own(body, 'no_message')),
    singletons: readRollup(own(body, 'singletons')),
    omitted: readRollup(own(body, 'omitted')),
    trendGrain: grain === 'day' || grain === 'week' ? grain : null,
  }
}

// ── Words ────────────────────────────────────────────────────────────────────

/** A share (0..1) as "61.2%", or "—" when there is none. */
export const formatShare = (share: number | null): string => (share === null ? NO_VALUE : formatPercent(share, { from: 'ratio' }))

/** How many of the largest groups the takeaway sums. */
export const TAKEAWAY_TOP = 3

/**
 * The one-line takeaway (EPIC 504, a Pareto sentence): "Top 3 groups = 61% of
 * failures". From the server's shares, whose denominator is EVERY failing
 * execution in scope (no-message and singletons included), so the sentence is
 * about all the failures, not just the grouped ones. `undefined` when there is
 * no group, or any share it would add is unknown: a sum with a hole in it is
 * not a smaller number, it is no number.
 */
export function paretoTakeaway(model: FailureGroupsModel): string | undefined {
  const top = model.groups.slice(0, TAKEAWAY_TOP)
  if (top.length === 0 || top.some((group) => group.share === null)) return undefined
  // The shares AS SHOWN (tenths of a percent, `formatShare`'s precision) are added, so the sentence is the
  // sum a reader makes of the table's column (R2-B F-13: 30.9 + 17.6 + 13.2 read "61.8%" from the raw sum).
  const tenths = top.reduce((sum, group) => sum + Math.round((group.share ?? 0) * 1000), 0)
  const percent = formatPercent(Math.min(1000, tenths) / 1000, { from: 'ratio' })
  if (top.length === 1) return `The largest group = ${percent} of failures`
  return `Top ${top.length} groups = ${percent} of failures`
}

/** A UTC instant as its UTC day ("2026-09-12"), or "—". The server sends `…Z` instants. */
export function utcDay(instant: string | null): string {
  if (instant === null) return NO_VALUE
  const match = /^(\d{4}-\d{2}-\d{2})T/.exec(instant)
  return match ? match[1] : NO_VALUE
}

/** What one trend bucket is, for the sparkline's name. */
export function trendLabel(grain: TrendGrain | null): string {
  return grain === 'week' ? 'Failures per week' : 'Failures per day'
}

export const COLLIDE_NOTE = 'different first lines share this signature'

/** The rows every view says about a group: the tooltip, the keyboard readout and the announcement. */
export function groupTipContent(group: FailureGroup): TooltipContent {
  const rows: (TooltipRow | null)[] = [
    { kind: 'dimension', key: 'category', label: 'Category', value: categoryLabel(group.dominantCategory) },
    { kind: 'value', key: 'failures', label: 'Failures', value: formatNumber(group.failureCount) },
    { kind: 'value', key: 'tests', label: 'Tests', value: formatNumber(group.affectedTests) },
    { kind: 'value', key: 'runs', label: 'Runs', value: formatNumber(group.affectedRuns) },
    group.share === null ? null : { kind: 'share', key: 'share', label: 'Share of failures', value: formatShare(group.share) },
    group.lastSeen === null ? null : { kind: 'value', key: 'last', label: 'Last seen', value: utcDay(group.lastSeen) },
    group.distinctRawLines !== null && group.distinctRawLines > 1
      ? { kind: 'note', key: 'collide', label: 'Note', value: `${formatNumber(group.distinctRawLines)} ${COLLIDE_NOTE}` }
      : null,
  ]
  return tipContent(`#${group.rank} ${group.label}`, rows)
}

/** "No error message: 12 failures (4.0%)", for the muted totals beside the plot. */
export function rollupText(name: string, rollup: FailureGroupRollup | null): string | null {
  if (rollup === null || rollup.failureCount === 0) return null
  const share = rollup.share === null ? '' : ` (${formatShare(rollup.share)})`
  const failures = `${formatNumber(rollup.failureCount)} failure${rollup.failureCount === 1 ? '' : 's'}`
  const groups =
    rollup.groupCount !== null ? ` in ${formatNumber(rollup.groupCount)} signature${rollup.groupCount === 1 ? '' : 's'}` : ''
  return `${name}: ${failures}${groups}${share}`
}

export const NO_MESSAGE_NAME = 'No error message'
export const SINGLETONS_NAME = 'Seen once'
export const OMITTED_NAME = 'Smaller groups not shown'

/** The muted roll-up lines, in a fixed order; the empty ones are left out. */
export function rollupLines(model: FailureGroupsModel): { key: string; text: string }[] {
  const lines: { key: string; text: string | null }[] = [
    { key: 'no-message', text: rollupText(NO_MESSAGE_NAME, model.noMessage) },
    { key: 'singletons', text: rollupText(SINGLETONS_NAME, model.singletons) },
    { key: 'omitted', text: rollupText(OMITTED_NAME, model.omitted) },
  ]
  return lines.filter((line): line is { key: string; text: string } => line.text !== null)
}

// ── Marks ────────────────────────────────────────────────────────────────────

/**
 * The mark a group is to its host (`marks.ts`): its signature under the C5
 * dimension `error_signature`, which the rows endpoint selects by (BE4: a group
 * drill sends `metric=failures`), and the count the bubble drew.
 */
export function groupMark(group: FailureGroup): ChartMark {
  return { dimension: 'error_signature', value: group.id, label: group.label, y: group.failureCount, n: null }
}

/** The panel's heading: the rank, not the raw line (which can be 160 characters of anything). */
export const panelTitle = (group: FailureGroup) => `Failure group #${group.rank}`

// ── The frame's views and its table / export series ─────────────────────────

export type FailureGroupsView = 'bubbles' | 'relations'

export const VIEW_LABELS: Readonly<Record<FailureGroupsView, string>> = {
  bubbles: 'Bubbles',
  relations: 'Related groups',
}

/** The failures per group as a category series: what the bubble view's table and CSV list. */
export function groupsSeries(model: FailureGroupsModel): SeriesChart {
  return {
    kind: 'series',
    dimensions: ['error_signature'],
    x_type: 'category',
    series: [
      {
        key: 'failures',
        label: 'Failures',
        points: model.groups.map((g) => ({ x: g.id, y: g.failureCount, n: g.failureCount })),
      },
    ],
    // `fromEntries` defines OWN properties, so a group whose id is `__proto__` is a label, not a prototype.
    x_labels: Object.fromEntries(model.groups.map((g) => [g.id, g.label])),
  }
}

/** The groups and their links as the graph the related view draws (its table lists the links). */
export function relationsSeries(model: FailureGroupsModel): GraphChart {
  return {
    kind: 'graph',
    nodes: model.groups.map((g) => ({ id: g.id, label: g.label, size: g.failureCount })),
    edges: model.edges.map((e) => ({ ...e })),
  }
}

/**
 * The open rows panel's selectors when they are a failure group's (exactly one
 * `error_signature` level), else none. The Failures page has two rows hosts
 * (this one and the drill ladder) reading one `rows=` key: each opens its panel
 * only for its own selectors, so one URL never opens two panels.
 */
export function ownRows(rows: readonly DrillLevel[]): DrillLevel[] {
  return rows.length === 1 && rows[0].dimension === 'error_signature' ? [rows[0]] : []
}
