/**
 * VIZ-507 — the run-compare status flows, as a Sankey: how many tests went
 * from each status in the earlier run to each status in the later one,
 * unchanged tests included, with "New" (not in the earlier run) and
 * "Removed" (not in the later one). Pure: no React, no engine.
 *
 * The server counts every test exactly once (`compare_runs`' transitions),
 * so the flows conserve: what leaves the "before" column adds up to the
 * earlier run's test count and what reaches the "after" column to the later
 * one's. "Passed → failed" (and → broken), the regressions, is emphasised.
 *
 * The same numbers as a matrix (C3 `matrix`, `value_type: 'count'`) are the
 * frame's "View as table" and export: the table fallback the story asks for.
 */
import type { MatrixChart } from '@/lib/viz/contracts'
import type { RunCompareClassification } from '@/services/runCompareService'

export type FlowStatus = 'passed' | 'failed' | 'broken' | 'skipped' | 'unknown' | 'absent'

export interface StatusTransition {
  before: FlowStatus
  after: FlowStatus
  count: number
}

export const FLOW_ORDER: readonly FlowStatus[] = ['passed', 'failed', 'broken', 'skipped', 'unknown', 'absent']

const STATUS_WORD: Record<Exclude<FlowStatus, 'absent'>, string> = {
  passed: 'Passed',
  failed: 'Failed',
  broken: 'Broken',
  skipped: 'Skipped',
  unknown: 'Unknown',
}

/** A status's name on one side: absent before is "New", absent after is "Removed". */
export function statusWord(status: FlowStatus, side: 'before' | 'after'): string {
  if (status === 'absent') return side === 'before' ? 'New' : 'Removed'
  return STATUS_WORD[status]
}

/** "Passed → Failed", "New → Passed", "Passed → Removed". */
export function transitionText(before: FlowStatus, after: FlowStatus): string {
  return `${statusWord(before, 'before')} → ${statusWord(after, 'after')}`
}

/** A node's unique name (ECharts keys nodes by name): the side, then the status. */
export const nodeName = (side: 'before' | 'after', status: FlowStatus) =>
  `${side === 'before' ? 'Earlier run' : 'Later run'}: ${statusWord(status, side)}`

/** A regression: passed before, failed or broken after. */
export const isRegression = (before: FlowStatus, after: FlowStatus) =>
  before === 'passed' && (after === 'failed' || after === 'broken')

/**
 * The delta-table filter a flow opens (its rows), or `null` for a flow the
 * per-test diff does not list (an unchanged status: those tests did not change).
 */
export function classificationFor(before: FlowStatus, after: FlowStatus): RunCompareClassification | null {
  if (after === 'absent') return before === 'absent' ? null : 'removed_test'
  if (before === 'absent') return after === 'passed' ? 'new_test' : after === 'failed' || after === 'broken' ? 'new_failure' : null
  if (isRegression(before, after)) return 'new_failure'
  if (after === 'passed' && before !== 'passed') return 'fixed'
  if (before === after) return before === 'failed' || before === 'broken' ? 'still_failing' : null
  return 'regressed'
}

export interface SankeyNode {
  name: string
  side: 'before' | 'after'
  status: FlowStatus
  total: number
}

export interface SankeyLink extends StatusTransition {
  source: string
  target: string
  regression: boolean
}

export interface StatusSankeyModel {
  nodes: SankeyNode[]
  links: SankeyLink[]
  /** Tests in the earlier / later run (absent excluded). */
  beforeTotal: number
  afterTotal: number
  regressions: number
  fixes: number
  /** The table fallback and export. */
  matrix: MatrixChart
}

/** The model, or `null` when there is no flow to draw. Zero and malformed flows are dropped. */
export function buildStatusSankey(transitions: readonly StatusTransition[] | null | undefined): StatusSankeyModel | null {
  const flows = (transitions ?? []).filter(
    (t) => FLOW_ORDER.includes(t.before) && FLOW_ORDER.includes(t.after) && Number.isInteger(t.count) && t.count > 0,
  )
  if (flows.length === 0) return null
  const totals = new Map<string, number>()
  for (const t of flows) {
    totals.set(nodeName('before', t.before), (totals.get(nodeName('before', t.before)) ?? 0) + t.count)
    totals.set(nodeName('after', t.after), (totals.get(nodeName('after', t.after)) ?? 0) + t.count)
  }
  const nodes: SankeyNode[] = []
  for (const side of ['before', 'after'] as const) {
    for (const status of FLOW_ORDER) {
      const name = nodeName(side, status)
      const total = totals.get(name)
      if (total) nodes.push({ name, side, status, total })
    }
  }
  const links: SankeyLink[] = flows.map((t) => ({
    ...t,
    source: nodeName('before', t.before),
    target: nodeName('after', t.after),
    regression: isRegression(t.before, t.after),
  }))
  const sum = (pick: (t: StatusTransition) => boolean) => flows.filter(pick).reduce((n, t) => n + t.count, 0)
  const befores = FLOW_ORDER.filter((s) => flows.some((t) => t.before === s))
  const afters = FLOW_ORDER.filter((s) => flows.some((t) => t.after === s))
  return {
    nodes,
    links,
    beforeTotal: sum((t) => t.before !== 'absent'),
    afterTotal: sum((t) => t.after !== 'absent'),
    regressions: sum((t) => isRegression(t.before, t.after)),
    fixes: sum((t) => t.after === 'passed' && t.before !== 'passed' && t.before !== 'absent'),
    matrix: {
      kind: 'matrix',
      value_type: 'count',
      x_labels: afters.map((s) => statusWord(s, 'after')),
      y_labels: befores.map((s) => statusWord(s, 'before')),
      x_keys: afters,
      y_keys: befores,
      cells: flows.map((t) => ({ x: afters.indexOf(t.after), y: befores.indexOf(t.before), value: t.count, n: t.count })),
    },
  }
}

/** The frame's takeaway: the regressions first, because they are what a reader came for. */
export function sankeyTakeaway(model: StatusSankeyModel): string {
  const tests = (n: number) => `${n.toLocaleString('en-US')} ${n === 1 ? 'test' : 'tests'}`
  const parts = [`${tests(model.regressions)} went from passed to failing`, `${tests(model.fixes)} fixed`]
  return `${parts.join('; ')}. Click a flow to list its tests.`
}
