import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VIZ_STATUSES } from '@/lib/viz/contracts'
import {
  CHART_FONT,
  CHART_VARS,
  FLAKY_MARKER,
  PRESENTATION_TOKEN_ATTRIBUTE,
  RECHARTS_AXIS_TICK,
  RECHARTS_AXIS_TITLE,
  RECHARTS_GRID,
  RECHARTS_TOOLTIP_STYLE,
  SERIES_COUNT,
  STATUS_ENCODING,
  echartsAxisLabel,
  echartsBaseText,
  echartsDecal,
  echartsLegendText,
  echartsSplitLine,
  readChartTokens,
  resetChartTokenCache,
  seriesColor,
  useChartTokens,
} from './tokens'
import { PRESENTATION_ATTRIBUTE } from '@/store/presentationStore'

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
    // W29b UIX-05: the one-measure colour and the page's font, for the canvas.
    expect(tokens.measure).toBe('signal:--chart-measure')
    expect(tokens.fontSans).toBe('signal:--font-sans')
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

describe('presentation mode and the token cache (VIZ-106)', () => {
  // Presentation mode remaps `--chart-axis` (and the muted text) on <html>. The
  // fake answers "<theme>+present:<property>" while the attribute is set, so a
  // stale cache shows up as the old value.
  function fakePresentationStyle() {
    return vi.spyOn(window, 'getComputedStyle').mockImplementation((el: Element) => {
      const theme = el.getAttribute('data-theme') ?? 'none'
      const mode = el.getAttribute(PRESENTATION_TOKEN_ATTRIBUTE) === 'on' ? '+present' : ''
      return { getPropertyValue: (name: string) => ` ${theme}${mode}:${name} ` } as unknown as CSSStyleDeclaration
    })
  }

  beforeEach(() => {
    resetChartTokenCache()
    html().setAttribute('data-theme', 'signal')
  })
  afterEach(() => {
    vi.restoreAllMocks()
    html().removeAttribute('data-theme')
    html().removeAttribute(PRESENTATION_TOKEN_ATTRIBUTE)
    resetChartTokenCache()
  })

  it('uses the attribute the presentation store sets', () => {
    expect(PRESENTATION_TOKEN_ATTRIBUTE).toBe(PRESENTATION_ATTRIBUTE)
  })

  it('re-reads when presentation mode toggles under the same theme, and memoises each mode', () => {
    const spy = fakePresentationStyle()
    const page = readChartTokens()
    expect(page.axis).toBe('signal:--chart-axis')
    html().setAttribute(PRESENTATION_TOKEN_ATTRIBUTE, 'on')
    const presenting = readChartTokens()
    expect(presenting).not.toBe(page)
    expect(presenting.axis).toBe('signal+present:--chart-axis')
    expect(readChartTokens()).toBe(presenting)
    html().removeAttribute(PRESENTATION_TOKEN_ATTRIBUTE)
    expect(readChartTokens().axis).toBe('signal:--chart-axis')
    expect(spy).toHaveBeenCalledTimes(3)
  })

  it('re-renders a canvas chart’s tokens when presentation mode is toggled', async () => {
    fakePresentationStyle()
    const { result } = renderHook(() => useChartTokens())
    expect(result.current.axis).toBe('signal:--chart-axis')
    await act(async () => {
      html().setAttribute(PRESENTATION_TOKEN_ATTRIBUTE, 'on')
      // MutationObserver callbacks run as a microtask.
      await Promise.resolve()
    })
    expect(result.current.axis).toBe('signal+present:--chart-axis')
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
    expect(CHART_VARS.measure).toBe('var(--chart-measure)')
    expect(CHART_VARS.measureTint).toBe('var(--chart-measure-tint)')
  })
})

/** W29b UIX-05: one text style, one grid and one measure colour across the engines. */
describe('the kit’s one text, grid and measure style (UIX-05)', () => {
  it('CHART_FONT is the one font table: 11 for every in-plot text, 12 legend and tooltip, 20 the donut centre', () => {
    expect(CHART_FONT).toEqual({ tick: 11, axisTitle: 11, dataLabel: 11, annotation: 11, legend: 12, tip: 12, centre: 20 })
    // Nothing the kit draws is under the 11 px floor.
    expect(Math.min(...Object.values(CHART_FONT))).toBe(11)
  })

  it('Recharts reads it: ticks and axis titles at 11, the tooltip at 12', () => {
    expect(RECHARTS_AXIS_TICK).toEqual({ fill: 'var(--chart-axis)', fontSize: 11 })
    expect(RECHARTS_AXIS_TITLE).toEqual({ fill: 'var(--chart-axis)', fontSize: 11 })
    expect(RECHARTS_TOOLTIP_STYLE.fontSize).toBe('12px')
  })

  it('RECHARTS_GRID is a solid 1 px hairline in the grid colour (no dash)', () => {
    expect(RECHARTS_GRID).toEqual({ stroke: 'var(--chart-grid)', strokeWidth: 1 })
    expect(RECHARTS_GRID).not.toHaveProperty('strokeDasharray')
  })

  it('the ECharts helpers carry the page font, the kit size and the axis / text colour, scaled up only', () => {
    const tokens = { fontSans: 'TestSans, sans-serif', axis: 'axis', text: 'text', grid: 'grid' }
    expect(echartsBaseText(tokens)).toEqual({ fontFamily: 'TestSans, sans-serif', fontSize: 11, color: 'axis' })
    expect(echartsAxisLabel(tokens)).toEqual({ fontFamily: 'TestSans, sans-serif', fontSize: 11, color: 'axis' })
    expect(echartsLegendText(tokens)).toEqual({ fontFamily: 'TestSans, sans-serif', fontSize: 12, color: 'text' })
    expect(echartsSplitLine(tokens)).toEqual({ lineStyle: { color: 'grid', width: 1, type: 'solid' } })
    // Presentation mode (16/11) reaches 16 px; a scale at or under 1, or not a number, never shrinks the text.
    expect(echartsAxisLabel(tokens, 16 / 11).fontSize).toBe(16)
    expect(echartsLegendText(tokens, 16 / 11).fontSize).toBe(17)
    for (const scale of [1, 0.5, Number.NaN]) expect(echartsBaseText(tokens, scale).fontSize).toBe(11)
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
