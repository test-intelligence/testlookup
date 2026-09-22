import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VIZ_STATUSES } from '@/lib/viz/contracts'
import {
  CHART_VARS,
  FLAKY_MARKER,
  SERIES_COUNT,
  STATUS_ENCODING,
  echartsDecal,
  readChartTokens,
  resetChartTokenCache,
  seriesColor,
  useChartTokens,
} from './tokens'

// No colour literals in this directory (check:theme scans tests too): the fake
// computed style answers with "<theme>:<property>" so every value is traceable.
function fakeComputedStyle() {
  return vi.spyOn(window, 'getComputedStyle').mockImplementation((el: Element) => {
    const theme = el.getAttribute('data-theme') ?? 'none'
    return { getPropertyValue: (name: string) => ` ${theme}:${name} ` } as unknown as CSSStyleDeclaration
  })
}

const html = () => document.documentElement

describe('readChartTokens', () => {
  beforeEach(() => {
    resetChartTokenCache()
    html().setAttribute('data-theme', 'signal')
  })
  afterEach(() => {
    vi.restoreAllMocks()
    html().removeAttribute('data-theme')
    resetChartTokenCache()
  })

  it('reads every token from the computed style of <html>, trimmed', () => {
    fakeComputedStyle()
    const tokens = readChartTokens()
    expect(tokens.theme).toBe('signal')
    expect(tokens.series).toHaveLength(SERIES_COUNT)
    expect(tokens.series[0]).toBe('signal:--chart-series-1')
    expect(tokens.seq[6]).toBe('signal:--chart-seq-7')
    expect(tokens.div[3]).toBe('signal:--chart-div-4')
    expect(tokens.status.unknown).toBe('signal:--status-unknown')
    expect(tokens.flaky).toBe('signal:--status-flaky')
    expect(tokens.grid).toBe('signal:--chart-grid')
    expect(tokens.axis).toBe('signal:--chart-axis')
    expect(tokens.card).toBe('signal:--color-bg-card')
  })

  it('is memoised per theme: a second read does not call getComputedStyle again', () => {
    const spy = fakeComputedStyle()
    const first = readChartTokens()
    const second = readChartTokens()
    expect(second).toBe(first)
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('re-reads when the theme attribute changes, and memoises the new theme', () => {
    const spy = fakeComputedStyle()
    const signal = readChartTokens()
    html().setAttribute('data-theme', 'lab')
    const lab = readChartTokens()
    expect(lab).not.toBe(signal)
    expect(lab.theme).toBe('lab')
    expect(lab.series[0]).toBe('lab:--chart-series-1')
    readChartTokens()
    expect(spy).toHaveBeenCalledTimes(2)
  })
})

describe('useChartTokens', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    html().removeAttribute('data-theme')
    resetChartTokenCache()
  })

  it('re-renders with the new theme when <html data-theme> changes', async () => {
    resetChartTokenCache()
    html().setAttribute('data-theme', 'signal')
    fakeComputedStyle()
    const { result } = renderHook(() => useChartTokens())
    expect(result.current.theme).toBe('signal')
    await act(async () => {
      html().setAttribute('data-theme', 'ember')
      // MutationObserver callbacks run as a microtask.
      await Promise.resolve()
    })
    expect(result.current.theme).toBe('ember')
    expect(result.current.axis).toBe('ember:--chart-axis')
  })
})

describe('CHART_VARS (Recharts / SVG)', () => {
  it('are var() references, so SVG follows a theme switch with no re-render', () => {
    expect(CHART_VARS.series).toEqual(Array.from({ length: 8 }, (_, i) => `var(--chart-series-${i + 1})`))
    expect(CHART_VARS.status).toEqual({
      passed: 'var(--status-passed)',
      failed: 'var(--status-failed)',
      broken: 'var(--status-broken)',
      skipped: 'var(--status-skipped)',
      unknown: 'var(--status-unknown)',
    })
    expect(CHART_VARS.grid).toBe('var(--chart-grid)')
    expect(CHART_VARS.axis).toBe('var(--chart-axis)')
  })
})

describe('seriesColor', () => {
  it('never recycles a hue: series past the eighth get the "Other" colour', () => {
    const tokens = { series: Array.from({ length: 8 }, (_, i) => `s${i + 1}`) }
    expect(seriesColor(tokens, 0)).toBe('s1')
    expect(seriesColor(tokens, 6)).toBe('s7')
    expect(seriesColor(tokens, 7)).toBe('s8')
    expect(seriesColor(tokens, 12)).toBe('s8')
    expect(seriesColor(tokens, -1)).toBe('s1')
  })
})

describe('status encodings', () => {
  it('cover exactly the five statuses — flaky is an attribute, not a sixth status', () => {
    expect(Object.keys(STATUS_ENCODING).sort()).toEqual([...VIZ_STATUSES].sort())
    expect(Object.keys(STATUS_ENCODING)).not.toContain('flaky')
    expect(FLAKY_MARKER.token).toBe('--status-flaky')
  })

  it('give every status its own icon, pattern, decal and label (never colour-only)', () => {
    const values = Object.values(STATUS_ENCODING)
    for (const key of ['icon', 'patternId', 'decal', 'label'] as const) {
      expect(new Set(values.map((v) => v[key])).size, key).toBe(VIZ_STATUSES.length)
    }
    expect(values.map((v) => v.icon)).not.toContain(FLAKY_MARKER.icon)
  })

  it('build a distinct ECharts decal per status, drawn in the card colour', () => {
    const tokens = { card: 'card-colour' }
    expect(echartsDecal('passed', tokens)).toBeNull()
    const decals = (['failed', 'broken', 'skipped', 'unknown'] as const).map((s) => echartsDecal(s, tokens))
    for (const decal of decals) expect(decal?.color).toBe('card-colour')
    expect(new Set(decals.map((d) => JSON.stringify(d))).size).toBe(4)
  })
})
