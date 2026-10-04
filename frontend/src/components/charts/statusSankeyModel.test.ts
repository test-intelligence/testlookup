import { describe, expect, it } from 'vitest'
import { clickedFlow, sankeyOption } from './StatusSankey'
import {
  buildStatusSankey,
  classificationFor,
  nodeName,
  sankeyTakeaway,
  transitionText,
  type StatusTransition,
} from './statusSankeyModel'
import type { ChartTokens } from './tokens'

const FLOWS: StatusTransition[] = [
  { before: 'passed', after: 'passed', count: 80 },
  { before: 'passed', after: 'failed', count: 5 },
  { before: 'passed', after: 'absent', count: 2 },
  { before: 'failed', after: 'passed', count: 3 },
  { before: 'failed', after: 'failed', count: 4 },
  { before: 'absent', after: 'passed', count: 6 },
]

const TOKENS = {
  theme: '', series: [], seq: [], div: [], grid: 'tok-grid', axis: 'tok-axis', card: 'tok-card', border: 'tok-border', text: 'tok-text', textMuted: 'tok-muted', flaky: 'tok-flaky',
  status: { passed: 'tok-passed', failed: 'tok-failed', broken: 'tok-broken', skipped: 'tok-skipped', unknown: 'tok-unknown' },
} as ChartTokens

/** The model for FLOWS; a null here is a test failure, not a crash. */
function built() {
  const model = buildStatusSankey(FLOWS)
  if (!model) throw new Error('FLOWS must build a model')
  return model
}

describe('statusSankeyModel (VIZ-507)', () => {
  it('the flows conserve: each column adds up to its run, new and removed included', () => {
    const model = built()
    expect(model.beforeTotal).toBe(80 + 5 + 2 + 3 + 4) // the earlier run's tests
    expect(model.afterTotal).toBe(80 + 5 + 3 + 4 + 6) // the later run's tests
    const before = model.nodes.filter((n) => n.side === 'before').reduce((s, n) => s + n.total, 0)
    const after = model.nodes.filter((n) => n.side === 'after').reduce((s, n) => s + n.total, 0)
    expect(before).toBe(after) // every flow leaves one node and reaches one
    expect(model.nodes.find((n) => n.name === nodeName('before', 'absent'))?.total).toBe(6)
    expect(model.nodes.find((n) => n.name === nodeName('after', 'absent'))?.total).toBe(2)
  })

  it('names new and removed tests, and emphasises passed → failed', () => {
    expect(transitionText('absent', 'passed')).toBe('New → Passed')
    expect(transitionText('passed', 'absent')).toBe('Passed → Removed')
    const model = built()
    expect(model.links.filter((l) => l.regression).map((l) => transitionText(l.before, l.after))).toEqual(['Passed → Failed'])
    expect(model.regressions).toBe(5)
    expect(model.fixes).toBe(3)
    expect(sankeyTakeaway(model)).toBe('5 tests went from passed to failing; 3 tests fixed. Click a flow to list its tests.')
  })

  it('the table fallback is every transition with its count', () => {
    const { matrix } = built()
    expect(matrix.y_labels).toEqual(['Passed', 'Failed', 'New'])
    expect(matrix.x_labels).toEqual(['Passed', 'Failed', 'Removed'])
    const cell = (y: string, x: string) => matrix.cells.find((c) => matrix.y_labels[c.y] === y && matrix.x_labels[c.x] === x)?.value
    expect(cell('Passed', 'Failed')).toBe(5)
    expect(cell('New', 'Passed')).toBe(6)
    expect(cell('Passed', 'Removed')).toBe(2)
    expect(matrix.cells).toHaveLength(FLOWS.length)
  })

  it('nothing to draw is null; zero and malformed flows are dropped', () => {
    expect(buildStatusSankey([])).toBeNull()
    expect(buildStatusSankey(undefined)).toBeNull()
    expect(
      buildStatusSankey([
        { before: 'passed', after: 'passed', count: 0 },
        { before: 'nope' as never, after: 'passed', count: 3 },
        { before: 'passed', after: 'failed', count: 1.5 },
      ]),
    ).toBeNull()
  })

  it('a flow opens the per-test rows it stands for; unchanged statuses have none', () => {
    expect(classificationFor('passed', 'failed')).toBe('new_failure')
    expect(classificationFor('passed', 'broken')).toBe('new_failure')
    expect(classificationFor('failed', 'passed')).toBe('fixed')
    expect(classificationFor('skipped', 'passed')).toBe('fixed')
    expect(classificationFor('failed', 'failed')).toBe('still_failing')
    expect(classificationFor('absent', 'passed')).toBe('new_test')
    expect(classificationFor('absent', 'failed')).toBe('new_failure')
    expect(classificationFor('passed', 'absent')).toBe('removed_test')
    expect(classificationFor('passed', 'skipped')).toBe('regressed')
    expect(classificationFor('passed', 'passed')).toBeNull()
    expect(classificationFor('skipped', 'skipped')).toBeNull()
  })

  it('the option: regressions in the failed colour at full strength, the rest faint in their source colour', () => {
    const model = built()
    const series = (sankeyOption(model, TOKENS) as { series: { links: { value: number; lineStyle: { color: string; opacity: number } }[] }[] }).series[0]
    const regression = series.links[model.links.findIndex((l) => l.regression)]
    expect(regression.lineStyle).toEqual({ color: 'tok-failed', opacity: 0.85 })
    const unchanged = series.links[model.links.findIndex((l) => l.before === 'passed' && l.after === 'passed')]
    expect(unchanged.lineStyle).toEqual({ color: 'tok-passed', opacity: 0.28 })
    // A click on a flow resolves to its pair; a node click does not.
    expect(clickedFlow({ dataType: 'edge', dataIndex: 1 }, model)).toEqual({ before: 'passed', after: 'failed' })
    expect(clickedFlow({ dataType: 'node', dataIndex: 1 }, model)).toBeNull()
  })
})
