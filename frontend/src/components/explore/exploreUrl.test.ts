/**
 * VIZ-505 — the Explorer's configuration survives a link and a saved view:
 * every allowed configuration round-trips, unrelated keys are kept, and
 * anything invalid falls back with a notice instead of throwing.
 */
import { describe, expect, it } from 'vitest'
import {
  EXPLORE_DAYS,
  explorePlan,
  FACET_IS_SERIES_REASON,
  FIELD_CANDIDATES,
  type ApiDimension,
  type ApiMetric,
  type ExploreConfig,
  type ExploreContext,
  type ExploreFacet,
  type ExploreX,
} from './exploreModel'
import { DEFAULT_EXPLORE, exploreViewFilters, readExploreConfig, writeExploreConfig } from './exploreUrl'

const PINNED: ExploreContext = { pinnedProject: true }
const ALL: ExploreContext = { pinnedProject: false }

function allowedConfigs(ctx: ExploreContext): ExploreConfig[] {
  const out: ExploreConfig[] = []
  for (const metric of FIELD_CANDIDATES.metric)
    for (const x of ['day', 'week'])
      for (const series of FIELD_CANDIDATES.series)
        for (const facet of FIELD_CANDIDATES.facet) {
          if (!explorePlan({ metric, x, series, facet }, ctx).ok) continue
          for (const yScale of ['shared', 'independent'] as const)
            for (const days of EXPLORE_DAYS)
              out.push({
                metric: metric as ApiMetric,
                x: x as ExploreX,
                series: series as ApiDimension | null,
                facet: facet as ExploreFacet | null,
                yScale,
                days,
              })
        }
  return out
}

describe('readExploreConfig / writeExploreConfig', () => {
  it.each([
    ['inside a project', PINNED, 28 * 16 * 2 * 2 * 4],
    ['in All Projects', ALL, 19 * 16 * 2 * 2 * 4],
  ] as const)('round-trips every allowed configuration %s', (_, ctx, count) => {
    const configs = allowedConfigs(ctx)
    expect(configs).toHaveLength(count)
    for (const config of configs) {
      const url = writeExploreConfig(new URLSearchParams(), config)
      expect(readExploreConfig(new URLSearchParams(url.toString()), ctx)).toEqual({ config, notices: [] })
    }
  })

  it('writes the six keys and keeps every other key', () => {
    const next = writeExploreConfig(new URLSearchParams('release=r1&suites=a&metric=old'), { ...DEFAULT_EXPLORE, series: null, days: 7 })
    expect(Object.fromEntries(next)).toEqual({
      release: 'r1',
      suites: 'a',
      metric: 'failure_rate',
      x: 'week',
      series: 'none',
      facet: 'suite',
      y: 'shared',
      days: '7',
    })
  })

  it('an empty URL is the default, quietly; the window comes from the caller', () => {
    expect(readExploreConfig(new URLSearchParams(), PINNED)).toEqual({ config: DEFAULT_EXPLORE, notices: [] })
    expect(readExploreConfig(null, PINNED, { defaultDays: 14 }).config.days).toBe(14)
  })

  it('invalid values fall back with a notice each; nothing throws', () => {
    const { config, notices } = readExploreConfig(new URLSearchParams('metric=drop%20table&x=fortnight&y=log&days=365'), PINNED)
    expect(config).toEqual(DEFAULT_EXPLORE)
    expect(notices).toEqual([
      'Metric "drop table" is not available (not a metric the API knows); showing Failure rate instead.',
      'X axis "fortnight" is not available (the explorer draws a time axis); showing Week instead.',
      'Y-scale "log" is not shared or independent; showing a shared scale instead.',
      'Window "365" is not one of 7, 14, 30, 90 days; showing 30 days instead.',
    ])
    expect(readExploreConfig(new URLSearchParams('days=7.0'), PINNED).notices).toHaveLength(1)
  })

  it('?series=suite&facet=suite is coerced: one line per panel is not a comparison', () => {
    const { config, notices } = readExploreConfig(new URLSearchParams('series=suite&facet=suite'), PINNED)
    expect(config).toMatchObject({ series: 'suite', facet: null })
    expect(notices).toEqual([`Panels "Suite" is not available (${FACET_IS_SERIES_REASON}); showing None instead.`])
  })

  it('a release facet outside one project is coerced', () => {
    const { config, notices } = readExploreConfig(new URLSearchParams('facet=release'), ALL)
    expect(config.facet).toBeNull()
    expect(notices).toHaveLength(1)
  })
})

describe('saved views: filters.explore goes through the same parser', () => {
  it('stores the six keys and reads them back', () => {
    const config: ExploreConfig = { metric: 'duration_p95', x: 'day', series: 'branch', facet: 'release', yScale: 'independent', days: 90 }
    const stored = exploreViewFilters(config)
    expect(stored).toEqual({ metric: 'duration_p95', x: 'day', series: 'branch', facet: 'release', y: 'independent', days: 90 })
    expect(readExploreConfig(JSON.parse(JSON.stringify(stored)) as Record<string, unknown>, PINNED)).toEqual({ config, notices: [] })
  })

  it('a malformed or missing view is the default, never a throw', () => {
    expect(readExploreConfig(undefined, PINNED).config).toEqual(DEFAULT_EXPLORE)
    const odd = readExploreConfig({ metric: 42, x: null, series: ['suite'], days: 14, facet: 'none' }, ALL)
    expect(odd.config).toEqual({ ...DEFAULT_EXPLORE, facet: null, days: 14 })
    expect(odd.notices).toEqual(['Metric "42" is not available (not a metric the API knows); showing Failure rate instead.'])
  })
})
