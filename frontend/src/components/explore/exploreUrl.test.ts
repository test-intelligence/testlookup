/**
 * VIZ-505 — the Explorer's configuration survives a link and a saved view:
 * every allowed configuration round-trips, unrelated keys are kept, and
 * anything invalid falls back with a notice instead of throwing. TRND-10 adds
 * lines per panel (`top`) and a title; TRND-13 the five chart-data facets.
 */
import { describe, expect, it } from 'vitest'
import {
  EXPLORE_DAYS,
  EXPLORE_TOP_N,
  explorePlan,
  FACET_IS_SERIES_REASON,
  FACET_RUN_COUNT_REASON,
  FIELD_CANDIDATES,
  type ApiDimension,
  type ApiMetric,
  type ExploreConfig,
  type ExploreContext,
  type ExploreFacet,
  type ExploreTopN,
  type ExploreX,
} from './exploreModel'
import { DEFAULT_EXPLORE, exploreViewFilters, readExploreConfig, writeExploreConfig } from './exploreUrl'

const PINNED: ExploreContext = { pinnedProject: true }
const ALL: ExploreContext = { pinnedProject: false }

const TOP_N_CYCLE: readonly (ExploreTopN | null)[] = [null, ...EXPLORE_TOP_N]

/** Every allowed configuration, each with one of the four lines-per-panel values (in turn) and every third one titled. */
function allowedConfigs(ctx: ExploreContext): ExploreConfig[] {
  const out: ExploreConfig[] = []
  for (const metric of FIELD_CANDIDATES.metric)
    for (const x of ['day', 'week'])
      for (const series of FIELD_CANDIDATES.series)
        for (const facet of FIELD_CANDIDATES.facet) {
          if (!explorePlan({ metric, x, series, facet }, ctx).ok) continue
          for (const yScale of ['shared', 'independent'] as const)
            for (const days of EXPLORE_DAYS) {
              const config: ExploreConfig = {
                metric: metric as ApiMetric,
                x: x as ExploreX,
                series: series as ApiDimension | null,
                facet: facet as ExploreFacet | null,
                yScale,
                days,
                topN: TOP_N_CYCLE[out.length % TOP_N_CYCLE.length],
              }
              if (out.length % 3 === 0) config.title = `Panel set ${out.length}`
              out.push(config)
            }
        }
  return out
}

describe('readExploreConfig / writeExploreConfig', () => {
  it.each([
    ['inside a project', PINNED, (15 * 73 + 55) * 2 * 2 * 4],
    ['in All Projects', ALL, (15 * 64 + 46) * 2 * 2 * 4],
  ] as const)('round-trips every allowed configuration %s', (_, ctx, count) => {
    const configs = allowedConfigs(ctx)
    expect(configs).toHaveLength(count)
    for (const config of configs) {
      const url = writeExploreConfig(new URLSearchParams(), config)
      expect(readExploreConfig(new URLSearchParams(url.toString()), ctx)).toEqual({ config, notices: [] })
    }
  })

  it('writes the seven keys and keeps every other key', () => {
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
      top: 'none',
    })
  })

  it('a title is written when there is one and removed when it goes', () => {
    const titled = writeExploreConfig(new URLSearchParams(), { ...DEFAULT_EXPLORE, topN: 3, title: 'Branch health' })
    expect(titled.get('top')).toBe('3')
    expect(titled.get('title')).toBe('Branch health')
    const cleared = writeExploreConfig(titled, { ...DEFAULT_EXPLORE })
    expect(cleared.has('title')).toBe(false)
    expect(cleared.get('top')).toBe('none')
  })

  it('an empty URL is the default, quietly; the window comes from the caller', () => {
    expect(readExploreConfig(new URLSearchParams(), PINNED)).toEqual({ config: DEFAULT_EXPLORE, notices: [] })
    expect(readExploreConfig(null, PINNED, { defaultDays: 14 }).config.days).toBe(14)
  })

  it('a link from before lines per panel and titles reads as it always did', () => {
    const { config, notices } = readExploreConfig(new URLSearchParams('metric=pass_rate&x=day&series=branch&facet=release&y=independent&days=14'), PINNED)
    expect(notices).toEqual([])
    expect(config).toEqual({ metric: 'pass_rate', x: 'day', series: 'branch', facet: 'release', yScale: 'independent', days: 14, topN: null })
  })

  it('invalid values fall back with a notice each; nothing throws', () => {
    const { config, notices } = readExploreConfig(
      new URLSearchParams(`metric=drop%20table&x=fortnight&y=log&days=365&top=8&title=${'t'.repeat(81)}`),
      PINNED,
    )
    expect(config).toEqual(DEFAULT_EXPLORE)
    expect(notices).toEqual([
      'Metric "drop table" is not available (not a metric the API knows); showing Failure rate instead.',
      'X axis "fortnight" is not available (the explorer draws a time axis); showing Week instead.',
      'Y-scale "log" is not shared or independent; showing a shared scale instead.',
      'Window "365" is not one of 7, 14, 30, 90 days; showing 30 days instead.',
      'Lines per panel "8" is not 3, 5, 7; showing the default number of lines instead.',
      'The title is longer than 80 characters; showing no title instead.',
    ])
    expect(readExploreConfig(new URLSearchParams('days=7.0'), PINNED).notices).toHaveLength(1)
    expect(readExploreConfig(new URLSearchParams('top=3.0'), PINNED).notices).toHaveLength(1)
    expect(readExploreConfig(new URLSearchParams('top=none'), PINNED)).toEqual({ config: DEFAULT_EXPLORE, notices: [] })
    // A blank title is no title, quietly; a title is kept as typed (a space between two words survives).
    expect(readExploreConfig(new URLSearchParams('title=%20%20'), PINNED)).toEqual({ config: DEFAULT_EXPLORE, notices: [] })
    expect(readExploreConfig(new URLSearchParams('title=Branch%20'), PINNED).config.title).toBe('Branch ')
    const typed = writeExploreConfig(new URLSearchParams(), { ...DEFAULT_EXPLORE, title: 'Branch ' })
    expect(readExploreConfig(typed, PINNED).config.title).toBe('Branch ')
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

  it('TRND-13: the five chart-data facets round-trip; Runs by status from a link is coerced, with the reason', () => {
    for (const facet of ['branch', 'environment', 'status', 'failure_category', 'ingestion_source'] as const) {
      const { config, notices } = readExploreConfig(new URLSearchParams(`series=none&facet=${facet}`), ALL)
      expect(notices, facet).toEqual([])
      expect(config.facet).toBe(facet)
    }
    const runs = readExploreConfig(new URLSearchParams('metric=run_count&series=none&facet=status'), PINNED)
    expect(runs.config.facet).toBeNull()
    expect(runs.notices).toEqual([`Panels "Status" is not available (${FACET_RUN_COUNT_REASON}); showing None instead.`])
  })
})

describe('saved views: filters.explore goes through the same parser', () => {
  it('stores the seven keys (and the title) and reads them back', () => {
    const config: ExploreConfig = {
      metric: 'duration_p95',
      x: 'day',
      series: 'branch',
      facet: 'release',
      yScale: 'independent',
      days: 90,
      topN: 5,
      title: 'Slow branches',
    }
    const stored = exploreViewFilters(config)
    expect(stored).toEqual({
      metric: 'duration_p95',
      x: 'day',
      series: 'branch',
      facet: 'release',
      y: 'independent',
      days: 90,
      top: 5,
      title: 'Slow branches',
    })
    expect(readExploreConfig(JSON.parse(JSON.stringify(stored)) as Record<string, unknown>, PINNED)).toEqual({ config, notices: [] })
    expect(exploreViewFilters({ ...config, topN: null, title: undefined })).not.toHaveProperty('title')
    expect(exploreViewFilters({ ...config, topN: null })).toMatchObject({ top: 'none' })
  })

  it('a malformed or missing view is the default, never a throw', () => {
    expect(readExploreConfig(undefined, PINNED).config).toEqual(DEFAULT_EXPLORE)
    const odd = readExploreConfig({ metric: 42, x: null, series: ['suite'], days: 14, facet: 'none', top: { n: 3 }, title: ['x'] }, ALL)
    expect(odd.config).toEqual({ ...DEFAULT_EXPLORE, facet: null, days: 14 })
    expect(odd.notices).toEqual(['Metric "42" is not available (not a metric the API knows); showing Failure rate instead.'])
    // A stored number is read like its string.
    expect(readExploreConfig({ top: 7 }, PINNED).config.topN).toBe(7)
    expect(readExploreConfig({ top: 4 }, PINNED).notices).toEqual(['Lines per panel "4" is not 3, 5, 7; showing the default number of lines instead.'])
  })
})
