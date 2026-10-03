import { describe, expect, it } from 'vitest'
import type { GraphChart } from '@/lib/viz/contracts'
import { validateAnyChartResponse } from '../chartState'
import { tooltipText } from '../tooltip'
import { failureGroupsResponse, failureGroupsSeries, HOSTILE_GROUP_LABELS } from './failureGroups.fixtures'
import {
  byCodeUnit,
  categoryKey,
  categoryLabel,
  failureGroupsModel,
  formatShare,
  groupTipContent,
  paretoTakeaway,
  rollupLines,
  trendLabel,
  utcDay,
} from './failureGroups.model'

const graph = (over: Partial<GraphChart> & Record<string, unknown> = {}): GraphChart =>
  ({ kind: 'graph', nodes: [], edges: [], ...over }) as GraphChart

describe('failureGroups fixtures', () => {
  it.each([{}, { hostile: true }, { giant: true }, { edges: false }, { groups: 200 }, { groups: 0 }])(
    'validate as a C3 graph response (%o)',
    (options) => {
      const checked = validateAnyChartResponse(failureGroupsResponse(options))
      expect(checked.ok, checked.ok ? '' : checked.errors.join('\n')).toBe(true)
    },
  )
})

describe('failureGroupsModel', () => {
  it('reads the groups in the server rank order, with every figure', () => {
    const model = failureGroupsModel(failureGroupsSeries({ groups: 3 }))
    expect(model.groups.map((g) => g.rank)).toEqual([1, 2, 3])
    const [first] = model.groups
    expect(first).toMatchObject({
      id: 'error #0: assertion',
      label: 'Error 0: AssertionError at step 0',
      failureCount: 400,
      affectedTests: 57,
      affectedRuns: 133,
      distinctRawLines: 2,
      dominantCategory: 'product_bug',
      firstSeen: '2026-09-01T08:00:00Z',
    })
    expect(first.trend).toHaveLength(7)
    expect(first.topTests[0]).toEqual({ fingerprint: 'fp-0-a', projectId: 'proj-1', name: 'test_checkout_0_a', count: 200 })
    expect(model.trendGrain).toBe('day')
    expect(model.noMessage).toEqual({ failureCount: 12, share: expect.any(Number), groupCount: null })
    expect(model.singletons?.groupCount).toBe(30)
    expect(model.totalFailures).toBe(400 + 250 + 181 + 42)
  })

  it('a missing or malformed figure is null, never 0, and never throws', () => {
    const model = failureGroupsModel(
      graph({
        nodes: [{ id: 'a', label: 'A', size: 3 }],
        groups: [
          {
            id: 'a',
            failure_count: 3,
            affected_tests: -1,
            affected_runs: 'x',
            share_of_failures: 1.5,
            first_seen: 7,
            trend: 'no',
            top_tests: [null, { fingerprint: 'f' }],
          },
          'junk',
          { id: 'b' },
        ],
        no_message: { failure_count: 'many' },
        singletons: null,
        trend_grain: 'month',
      }),
    )
    expect(model.groups).toHaveLength(1)
    expect(model.groups[0]).toMatchObject({
      affectedTests: null,
      affectedRuns: null,
      share: null,
      firstSeen: null,
      trend: [],
      topTests: [],
    })
    expect(model.groups[0].label).toBe('A') // the node's label when the group has none
    expect(model.noMessage).toBeNull()
    expect(model.singletons).toBeNull()
    expect(model.totalFailures).toBeNull()
    expect(model.trendGrain).toBeNull()
  })

  it('without `groups`, ranks the nodes by size then id (code unit) and leaves the rest null', () => {
    const model = failureGroupsModel(
      graph({
        nodes: [
          { id: 'b', label: 'B', size: 5 },
          { id: 'a', label: 'A', size: 5 },
          { id: 'C', label: 'C', size: 9, group: 'flaky' },
        ],
      }),
    )
    expect(model.groups.map((g) => g.id)).toEqual(['C', 'a', 'b'])
    expect(model.groups[0]).toMatchObject({
      rank: 1,
      failureCount: 9,
      dominantCategory: 'flaky',
      share: null,
      affectedTests: null,
    })
  })

  it('hostile ids are just ids (Map keys, never object keys)', () => {
    const model = failureGroupsModel(
      graph({
        nodes: [
          { id: '__proto__', label: 'p', size: 4 },
          { id: 'constructor', label: 'c', size: 3 },
        ],
        edges: [{ source: '__proto__', target: 'constructor', weight: 0.5 }],
      }),
    )
    expect(model.groups.map((g) => g.id)).toEqual(['__proto__', 'constructor'])
    expect(model.edges).toEqual([{ source: '__proto__', target: 'constructor', weight: 0.5 }])
  })

  it('keeps an edge only between two shown groups, once per pair, never a loop', () => {
    const model = failureGroupsModel(
      graph({
        nodes: [
          { id: 'a', label: 'A', size: 4 },
          { id: 'b', label: 'B', size: 3 },
        ],
        edges: [
          { source: 'a', target: 'b', weight: 0.5 },
          { source: 'b', target: 'a', weight: 0.5 },
          { source: 'a', target: 'a', weight: 1 },
          { source: 'a', target: 'zz', weight: 0.3 },
        ],
      }),
    )
    expect(model.edges).toEqual([{ source: 'a', target: 'b', weight: 0.5 }])
  })

  it('a duplicate group id is read once', () => {
    const model = failureGroupsModel(
      graph({
        nodes: [{ id: 'a', label: 'A', size: 2 }],
        groups: [
          { id: 'a', failure_count: 2 },
          { id: 'a', failure_count: 9 },
        ],
      }),
    )
    expect(model.groups).toHaveLength(1)
    expect(model.groups[0].failureCount).toBe(2)
  })
})

describe('words', () => {
  it('the Pareto takeaway sums the top three shares of ALL failures', () => {
    const model = failureGroupsModel(failureGroupsSeries({ groups: 5 }))
    const expected = model.groups.slice(0, 3).reduce((s, g) => s + (g.share ?? 0), 0)
    expect(paretoTakeaway(model)).toBe(`Top 3 groups = ${formatShare(expected)} of failures`)
  })

  // R2-B F-13: "Top 3 groups = 61.8%" over a table whose three shares read 30.9 + 17.6 + 13.2 = 61.7.
  it('the Pareto takeaway adds the shares AS SHOWN, so the sentence agrees with the table', () => {
    const series = failureGroupsSeries({ groups: 4 }) as unknown as { groups: { share_of_failures: number }[] }
    const shares = [0.30876, 0.17642, 0.13236, 0.1]
    series.groups.forEach((group, i) => (group.share_of_failures = shares[i]))
    const model = failureGroupsModel(series as unknown as Parameters<typeof failureGroupsModel>[0])
    expect(model.groups.slice(0, 3).map((g) => formatShare(g.share))).toEqual(['30.9%', '17.6%', '13.2%'])
    expect(formatShare(shares[0] + shares[1] + shares[2])).toBe('61.8%')
    expect(paretoTakeaway(model)).toBe('Top 3 groups = 61.7% of failures')
  })

  it('one group, and a group with an unknown share', () => {
    const one = failureGroupsModel(failureGroupsSeries({ groups: 1 }))
    expect(paretoTakeaway(one)).toMatch(/^The largest group = \d+(\.\d)?% of failures$/)
    const unknown = failureGroupsModel(graph({ nodes: [{ id: 'a', label: 'A', size: 2 }] }))
    expect(paretoTakeaway(unknown)).toBeUndefined()
    expect(paretoTakeaway(failureGroupsModel(graph()))).toBeUndefined()
  })

  it('never says "AI"', () => {
    const model = failureGroupsModel(failureGroupsSeries({ groups: 5 }))
    const words = [
      paretoTakeaway(model),
      ...rollupLines(model).map((l) => l.text),
      ...model.groups.map((g) => tooltipText(groupTipContent(g))),
    ]
    for (const sentence of words) expect(sentence).not.toMatch(/\bAI\b/)
  })

  it('categories: the vocabulary in words, anything else drawn as other but shown as sent', () => {
    expect(categoryKey('PRODUCT_BUG')).toBe('product_bug')
    expect(categoryKey(' flaky ')).toBe('flaky')
    expect(categoryKey(null)).toBe('unknown')
    expect(categoryKey('')).toBe('unknown')
    expect(categoryKey('<b>x</b>')).toBe('other')
    expect(categoryKey('__proto__')).toBe('other')
    expect(categoryLabel('test_data')).toBe('Test data')
    expect(categoryLabel(undefined)).toBe('Unknown')
    expect(categoryLabel('<b>x</b>')).toBe('<b>x</b>')
  })

  it('the tooltip names the group, its category, counts and a collision note', () => {
    const model = failureGroupsModel(failureGroupsSeries({ groups: 2, hostile: true }))
    const content = groupTipContent(model.groups[0])
    expect(content.title).toBe(`#1 ${HOSTILE_GROUP_LABELS[0]}`)
    const text = tooltipText(content)
    expect(text).toContain('Category: Product bug')
    expect(text).toContain('Failures: 400')
    expect(text).toContain('2 different first lines share this signature')
    expect(tooltipText(groupTipContent(model.groups[1]))).not.toContain('different first lines')
  })

  it('a share with no denominator is "—", and a roll-up of 0 is not listed', () => {
    expect(formatShare(null)).toBe('—')
    const model = failureGroupsModel(failureGroupsSeries({ groups: 2 }))
    expect(rollupLines(model).map((l) => l.key)).toEqual(['no-message', 'singletons'])
    expect(rollupLines(model)[1].text).toMatch(/^Seen once: 30 failures in 30 signatures \(\d/)
  })

  it('utcDay and trendLabel', () => {
    expect(utcDay('2026-09-12T08:00:00Z')).toBe('2026-09-12')
    expect(utcDay('soon')).toBe('—')
    expect(utcDay(null)).toBe('—')
    expect(trendLabel('week')).toBe('Failures per week')
    expect(trendLabel(null)).toBe('Failures per day')
  })

  it('byCodeUnit orders by code unit, not locale', () => {
    expect(['b', 'B', 'a', 'é'].sort(byCodeUnit)).toEqual(['B', 'a', 'b', 'é'])
  })
})
