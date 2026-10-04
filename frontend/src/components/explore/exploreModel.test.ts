/**
 * VIZ-505 — the Explorer's rules. `apiRefusal` is held to the server through
 * the shared fixture `contracts/viz/chart_data_combinations.json` (generated
 * and checked by `backend/tests/test_chart_data_combinations.py`); the
 * Explorer's own rules are pinned by the golden counts: 28 configurations
 * per metric and x inside one project, 19 in All Projects.
 */
import { describe, expect, it } from 'vitest'
import combinations from '../../../../contracts/viz/chart_data_combinations.json'
import type { EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'
import {
  allowedOptions,
  API_DIMENSION_KEYS,
  API_DIMENSIONS,
  API_METRICS,
  apiRefusal,
  coerceConfig,
  discoveryParams,
  EXPLORE_METRICS,
  explorePlan,
  facetPanels,
  FACET_IS_SERIES_REASON,
  FIELD_CANDIDATES,
  MAX_PANELS,
  ownMaxOf,
  panelParams,
  panelStyles,
  panelYAxis,
  PLAN_FIELDS,
  RELEASE_NEEDS_PROJECT_REASON,
  seriesStyles,
  yScaleText,
  type ExploreAxes,
  type ExploreContext,
} from './exploreModel'

const PINNED: ExploreContext = { pinnedProject: true }
const ALL: ExploreContext = { pinnedProject: false }
const FALLBACK = { metric: 'failure_rate', x: 'week' } as const

const axes = (over: Partial<ExploreAxes> = {}): ExploreAxes => ({ metric: 'failure_rate', x: 'week', series: 'environment', facet: 'suite', ...over })

/** Every combination of the four planned fields a picker could hold. */
function everyConfig(): ExploreAxes[] {
  const out: ExploreAxes[] = []
  for (const metric of API_METRICS)
    for (const x of FIELD_CANDIDATES.x)
      for (const series of FIELD_CANDIDATES.series)
        for (const facet of FIELD_CANDIDATES.facet) out.push({ metric, x, series, facet })
  return out
}

describe('apiRefusal mirrors parse_chart_spec (shared fixture)', () => {
  it('has the server vocabularies', () => {
    expect([...API_METRICS]).toEqual(combinations.metrics)
    expect(Object.keys(API_DIMENSIONS)).toEqual(Object.keys(combinations.dimensions))
    for (const [name, dim] of Object.entries(combinations.dimensions)) {
      expect(API_DIMENSIONS[name as keyof typeof API_DIMENSIONS], name).toEqual({ time: dim.time, high: dim.high_cardinality })
    }
    // Every metric has a label and a kind.
    expect(Object.keys(EXPLORE_METRICS).sort()).toEqual([...API_METRICS].sort())
  })

  it('gives the server outcome for every fixture case', () => {
    expect(combinations.cases.length).toBe(1188)
    const wrong = combinations.cases.filter(
      (c) => (apiRefusal(c.group_by, c.suite_names, c.top_n) ?? 'ok') !== c.outcome,
    )
    expect(wrong).toEqual([])
  })

  it('covers the three refusals the fixture cannot hold, in the server order', () => {
    expect(apiRefusal([], 0, null)).toBe('missing_parameter')
    expect(apiRefusal(['day', 'suite', 'test'], 0, null)).toBe('group_by_cap')
    // The cap is checked before the names.
    expect(apiRefusal(['nope', 'day', 'day'], 0, null)).toBe('group_by_cap')
    expect(apiRefusal(['error_signature'], 0, null)).toBe('dimension_enum')
    expect(apiRefusal(['day', 'nope'], 0, null)).toBe('dimension_enum')
    expect(apiRefusal(['day'], 0, 2.5)).toBe('top_n_range')
    expect(apiRefusal(['suite'], 0, 365)).toBeNull()
    expect(apiRefusal(['suite'], 0, 366)).toBe('top_n_range')
    expect(apiRefusal(['day', 'suite'], 0, 0)).toBe('top_n_range')
  })
})

describe('explorePlan: the golden matrix', () => {
  const okCount = (ctx: ExploreContext, metric: string, x: string) =>
    FIELD_CANDIDATES.series.flatMap((series) => FIELD_CANDIDATES.facet.map((facet) => ({ metric, x, series, facet }))).filter((c) => explorePlan(c, ctx).ok).length

  it.each(API_METRICS)('%s: 28 configurations per x inside a project, 19 in All Projects; none off a time axis', (metric) => {
    for (const x of ['day', 'week']) {
      expect(okCount(PINNED, metric, x)).toBe(28)
      expect(okCount(ALL, metric, x)).toBe(19)
    }
    for (const x of API_DIMENSION_KEYS.filter((d) => !API_DIMENSIONS[d].time)) expect(okCount(PINNED, metric, x)).toBe(0)
  })

  it('896 inside a project and 608 in All Projects over every metric and both x', () => {
    const all = everyConfig()
    expect(all.filter((c) => explorePlan(c, PINNED).ok)).toHaveLength(896)
    expect(all.filter((c) => explorePlan(c, ALL).ok)).toHaveLength(608)
  })

  it('every request an ok plan makes is one the API accepts', () => {
    for (const ctx of [PINNED, ALL]) {
      for (const config of everyConfig()) {
        const plan = explorePlan(config, ctx)
        if (!plan.ok) continue
        expect(apiRefusal(plan.panel.groupBy, plan.panel.suiteCount, plan.panel.topN), JSON.stringify(config)).toBeNull()
        if (plan.discovery) expect(apiRefusal(plan.discovery.groupBy, 0, plan.discovery.topN), JSON.stringify(config)).toBeNull()
      }
    }
  })

  it('the default scenario: discovery suite x environment, panels week x environment filtered to one suite', () => {
    const plan = explorePlan(axes(), PINNED)
    expect(plan).toEqual({
      ok: true,
      panel: { groupBy: ['week', 'environment'], suiteCount: 1, topN: null },
      discovery: { groupBy: ['suite', 'environment'], topN: null, sharedLegend: true },
    })
    if (!plan.ok || !plan.discovery) throw new Error('plan')
    const base = { project_id: 'p1', days: 30 }
    expect(discoveryParams(base, plan.discovery)).toEqual({ project_id: 'p1', days: 30, metric: 'executions', group_by: ['suite', 'environment'] })
    expect(panelParams(base, 'failure_rate', plan.panel, 'suite', 'checkout')).toEqual({
      project_id: 'p1',
      days: 30,
      metric: 'failure_rate',
      group_by: ['week', 'environment'],
      suite_name: 'checkout',
    })
  })

  it('suite x test: the discovery is the suites alone (no shared legend, no top_n); the panel needs none either', () => {
    const plan = explorePlan(axes({ series: 'test' }), PINNED)
    expect(plan).toEqual({
      ok: true,
      panel: { groupBy: ['week', 'test'], suiteCount: 1, topN: null },
      discovery: { groupBy: ['suite'], topN: null, sharedLegend: false },
    })
  })

  it('release x test: top_n 7 on both requests; the panel filters by release', () => {
    const plan = explorePlan(axes({ series: 'test', facet: 'release' }), PINNED)
    expect(plan).toEqual({
      ok: true,
      panel: { groupBy: ['week', 'test'], suiteCount: 0, topN: 7 },
      discovery: { groupBy: ['release', 'test'], topN: 7, sharedLegend: true },
    })
    if (!plan.ok || !plan.discovery) throw new Error('plan')
    expect(discoveryParams({}, plan.discovery)).toMatchObject({ top_n: 7 })
    expect(panelParams({ release_id: 'r0' }, 'executions', plan.panel, 'release', 'unattributed')).toEqual({
      release_id: 'unattributed',
      metric: 'executions',
      group_by: ['week', 'test'],
      top_n: 7,
    })
  })

  it('facet none: one request, no discovery', () => {
    expect(explorePlan(axes({ series: null, facet: null }), ALL)).toEqual({
      ok: true,
      panel: { groupBy: ['week'], suiteCount: 0, topN: null },
      discovery: null,
    })
  })

  it('names the rule a refused configuration breaks', () => {
    expect(explorePlan(axes({ facet: 'environment' }), PINNED)).toMatchObject({ ok: false, field: 'facet' })
    expect(explorePlan(axes({ series: 'suite' }), PINNED)).toEqual({ ok: false, field: 'facet', reason: FACET_IS_SERIES_REASON })
    expect(explorePlan(axes({ facet: 'release' }), ALL)).toEqual({ ok: false, field: 'facet', reason: RELEASE_NEEDS_PROJECT_REASON })
    expect(explorePlan(axes({ x: 'suite' }), PINNED)).toMatchObject({ ok: false, field: 'x' })
    expect(explorePlan(axes({ series: 'day' }), PINNED)).toMatchObject({ ok: false, field: 'series' })
    expect(explorePlan(axes({ metric: 'nope' }), PINNED)).toMatchObject({ ok: false, field: 'metric' })
  })
})

describe('options and coercion', () => {
  it('offers a value exactly when its plan is ok, judging only the fields before it', () => {
    for (const ctx of [PINNED, ALL]) {
      for (const config of [axes(), axes({ series: 'suite', facet: null }), axes({ series: null, facet: 'release' }), axes({ series: 'test' })]) {
        for (const field of PLAN_FIELDS) {
          const offered = allowedOptions(field, config, ctx)
          const later = PLAN_FIELDS.slice(PLAN_FIELDS.indexOf(field) + 1)
          for (const value of FIELD_CANDIDATES[field]) {
            const probe = { ...config, [field]: value, ...Object.fromEntries(later.map((f) => [f, f === 'x' ? 'day' : null])) }
            expect(offered.includes(value), `${field}=${value}`).toBe(explorePlan(probe, ctx).ok)
          }
        }
      }
    }
    expect(allowedOptions('metric', axes(), PINNED)).toEqual([...API_METRICS])
    expect(allowedOptions('x', axes(), PINNED)).toEqual(['day', 'week'])
    expect(allowedOptions('facet', axes({ series: 'suite' }), PINNED)).toEqual([null, 'release'])
    expect(allowedOptions('facet', axes({ series: 'suite' }), ALL)).toEqual([null])
    expect(allowedOptions('series', axes(), ALL)).toEqual([null, ...API_DIMENSION_KEYS.filter((d) => !API_DIMENSIONS[d].time)])
  })

  it('resets a later field the earlier ones have made invalid, and says so', () => {
    const { axes: fixed, notices } = coerceConfig(axes({ series: 'suite' }), PINNED, FALLBACK)
    expect(fixed).toEqual({ metric: 'failure_rate', x: 'week', series: 'suite', facet: null })
    expect(notices).toEqual([`Panels "Suite" is not available (${FACET_IS_SERIES_REASON}); showing None instead.`])

    const outside = coerceConfig(axes({ facet: 'release' }), ALL, FALLBACK)
    expect(outside.axes.facet).toBeNull()
    expect(outside.notices[0]).toContain(RELEASE_NEEDS_PROJECT_REASON)

    const garbage = coerceConfig({ metric: 'x'.repeat(80), x: 'suite', series: 'day', facet: 'test' }, PINNED, FALLBACK)
    expect(garbage.axes).toEqual({ metric: 'failure_rate', x: 'week', series: null, facet: null })
    expect(garbage.notices).toHaveLength(4)
    expect(garbage.notices[0]).toContain(`"${'x'.repeat(40)}…"`)

    expect(coerceConfig(axes(), PINNED, FALLBACK).notices).toEqual([])
  })
})

/** A discovery response: the facet keys on x in rank order, one series per series key. */
function discovery(keys: readonly string[], seriesKeys: readonly string[] = ['value'], meta: Partial<EnvelopeMeta> = {}): { chart: SeriesChart; meta: EnvelopeMeta } {
  return {
    chart: {
      kind: 'series',
      dimensions: ['suite'],
      x_type: 'category',
      series: seriesKeys.map((key) => ({ key, label: key, points: keys.map((x, i) => ({ x, y: 1000 - i, n: 1000 - i })) })),
      x_labels: { checkout: 'Checkout' },
    },
    meta: meta as EnvelopeMeta,
  }
}

describe('facetPanels', () => {
  it('37 suites and (none): the 12 with the most executions, both notices', () => {
    const keys = Array.from({ length: 37 }, (_, i) => `s${String(i).padStart(2, '0')}`)
    const { chart, meta } = discovery([...keys.slice(0, 3), '(none)', ...keys.slice(3)])
    const result = facetPanels(chart, meta, 'suite')
    expect(result.panels.map((p) => p.key)).toEqual(keys.slice(0, MAX_PANELS))
    expect(result.total).toBe(37)
    expect(result.notices).toEqual(['Showing the 12 suites with the most executions of 37.', 'Runs with no suite are not shown.'])
  })

  it('the true count from truncated_axes wins over the keys returned', () => {
    const keys = Array.from({ length: 20 }, (_, i) => `s${i}`)
    const { chart, meta } = discovery(keys, ['value'], { truncated_axes: { x: { dimension: 'suite', kept: 20, total: 400 } } })
    expect(facetPanels(chart, meta, 'suite').notices).toEqual(['Showing the 12 suites with the most executions of 400.'])
  })

  it('a few suites: every one is a panel, labelled with the ingested spelling, no notice', () => {
    const { chart, meta } = discovery(['checkout', 'search'])
    expect(facetPanels(chart, meta, 'suite')).toEqual({
      panels: [
        { key: 'checkout', label: 'Checkout' },
        { key: 'search', label: 'search' },
      ],
      total: 2,
      notices: [],
    })
  })

  it('unattributed is a release and is kept', () => {
    const { chart, meta } = discovery(['r1', 'unattributed'])
    expect(facetPanels(chart, meta, 'release').panels).toEqual([
      { key: 'r1', label: 'r1' },
      { key: 'unattributed', label: 'Unattributed runs' },
    ])
    expect(facetPanels(null, null, 'release')).toEqual({ panels: [], total: 0, notices: [] })
  })
})

describe('the shared legend', () => {
  it('slot i for the i-th key of the discovery response, "Other" and an eighth key left out', () => {
    const { chart } = discovery(['s1'], ['prod', 'staging', 'qa', 'a', 'b', 'c', 'd', 'e', '__other__'])
    const styles = seriesStyles(chart)
    expect(Object.keys(styles)).toEqual(['prod', 'staging', 'qa', 'a', 'b', 'c', 'd'])
    expect(styles.qa).toEqual({ colour: 2, dash: 2 })
  })

  it('a panel keeps the global slots and gives its other keys the free ones, sorted; never a clash', () => {
    const global = { prod: { colour: 0, dash: 0 }, staging: { colour: 1, dash: 1 }, qa: { colour: 2, dash: 2 } }
    expect(panelStyles(global, ['staging', 'zeta', 'alpha'])).toEqual({
      staging: { colour: 1, dash: 1 },
      alpha: { colour: 0, dash: 0 },
      zeta: { colour: 2, dash: 2 },
    })
    // Slot 7 is "Other"'s when the panel has one.
    const eight = ['k1', 'k2', 'k3', 'k4', 'k5', 'k6', 'k7', '__other__']
    const styles = panelStyles({}, eight)
    expect(Object.values(styles).map((s) => s.colour)).toEqual([0, 1, 2, 3, 4, 5, 6])
    expect(new Set(Object.values(panelStyles(global, ['qa', 'prod', 'x', 'y', 'z'])).map((s) => s.colour)).size).toBe(5)
  })
})

describe('panelYAxis and its words', () => {
  const RATE_AXIS = { domain: [0, 100] as [number, number], ticks: [0, 25, 50, 75, 100] }
  const COUNT_AXIS = { domain: [0, 40] as [number, number], ticks: [0, 10, 20, 30, 40] }

  it('rate, shared: the model axis, 0-100', () => {
    const axis = panelYAxis('rate', 'shared', 35, 80, RATE_AXIS)
    expect(axis).toBe(RATE_AXIS)
    expect(yScaleText('rate', 'shared', axis, 5, 12)).toBe('Shared y-scale: 0–100%')
  })

  it('rate, own: zoomed to the panel, never past 100', () => {
    const axis = panelYAxis('rate', 'independent', 35, 80, RATE_AXIS)
    expect(axis).toEqual({ domain: [0, 40], ticks: [0, 10, 20, 30, 40] })
    expect(yScaleText('rate', 'independent', axis, 5, 12)).toBe('Own y-scale: 0–40%')
    expect(panelYAxis('rate', 'independent', 140, null, RATE_AXIS).domain).toEqual([0, 100])
  })

  it('count, shared: the largest panel loaded so far, and the words say it may widen', () => {
    const axis = panelYAxis('count', 'shared', 35, 950, COUNT_AXIS)
    expect(axis).toEqual({ domain: [0, 1000], ticks: [0, 250, 500, 750, 1000] })
    expect(yScaleText('count', 'shared', axis, 5, 12)).toBe('Shared y-scale: 0–1,000 (from 5 of 12 panels; widens if a panel below is larger)')
    expect(yScaleText('count', 'shared', axis, 12, 12)).toBe('Shared y-scale: 0–1,000')
  })

  it('count, own: the model axis', () => {
    expect(panelYAxis('count', 'independent', 35, 1130, COUNT_AXIS)).toBe(COUNT_AXIS)
    expect(yScaleText('count', 'independent', COUNT_AXIS, 1, 1)).toBe('Own y-scale: 0–40')
  })

  it('ownMaxOf reads measured points only', () => {
    const point = (y: number | null) => ({ x: '2026-10-01', y, n: 1, reason: null, date: null })
    expect(ownMaxOf({ lines: [{ points: [point(3), point(null)] }, { points: [point(9)] }] } as never)).toBe(9)
    expect(ownMaxOf(null)).toBeNull()
  })
})
