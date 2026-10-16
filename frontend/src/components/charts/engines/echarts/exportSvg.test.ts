/**
 * TRND-11: the ECharts export redrawn from the chart's own registered option.
 *
 *   1. The heatmap's option through the REAL SVG renderer (server-side mode,
 *      the `canvasText.sweep.test.ts` pattern): a vector file — `<svg`,
 *      `<text` — with no embedded `<image`.
 *   2. Light background: with the document in a dark theme, the SVG's
 *      background and its label text are the LIGHT theme's card and text
 *      tokens (read from a detached `[data-theme]` host, never the page's).
 *   3. An export option carrying a string `formatter` is refused (the CSP / XSS
 *      rule holds for the export), and nothing is drawn.
 *
 * Colours are CSS keywords: this directory holds no colour literal (check:theme).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const live = vi.hoisted(() => ({ chart: null as null | { container: HTMLElement; width: number; height: number } }))
vi.mock('./exportImage', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./exportImage')>()),
  liveEChartsChart: () => live.chart,
}))

import { LIGHT_THEME } from '@/lib/viz/chartImage'
import { readChartTokens, resetChartTokenCache, type ChartTokens } from '../../tokens'
import { echarts } from './core'
import './heatmap'
import { buildHeatmapOption, type NumericMatrix } from './heatmapOption'
import { registerExportOption, unregisterExportOption } from './exportRegistry'
import { echartsExportImage, EXPORT_LIGHT_THEME, parseSvg, renderOptionToSvg } from './exportSvg'

// No canvas in jsdom: zrender measures text from its own table.
echarts.setPlatformAPI({ createCanvas: () => null as unknown as HTMLCanvasElement })

const MATRIX: NumericMatrix = {
  kind: 'matrix',
  value_type: 'rate',
  x_labels: ['staging', 'prod'],
  y_labels: ['checkout', 'payments'],
  cells: [
    { x: 0, y: 0, value: 0.5, n: 2 },
    { x: 1, y: 1, value: 1, n: 1 },
  ],
}

/** The page is dark ("signal"); the light theme ("lab") answers with light keywords. */
const PALETTE: Record<string, Record<string, string>> = {
  [EXPORT_LIGHT_THEME]: { '--color-bg-card': 'ivory', '--color-text': 'navy', '--chart-axis': 'navy', '--color-text-muted': 'navy' },
  signal: { '--color-bg-card': 'black', '--color-text': 'white', '--chart-axis': 'white', '--color-text-muted': 'white' },
}

function fakeThemes() {
  return vi.spyOn(window, 'getComputedStyle').mockImplementation((el: Element) => {
    const theme = el.getAttribute('data-theme') ?? 'none'
    return {
      getPropertyValue: (name: string) => PALETTE[theme]?.[name] ?? (name.startsWith('--chart-seq') ? 'teal' : 'gray'),
    } as unknown as CSSStyleDeclaration
  })
}

const heatmapExport = (tokens: ChartTokens) =>
  buildHeatmapOption({ data: MATRIX, tokens, description: 'Pass rate by suite and environment', chartWidth: 480, animate: false })

describe('the ECharts SVG export (TRND-11)', () => {
  let container: HTMLElement
  beforeEach(() => {
    resetChartTokenCache()
    document.documentElement.setAttribute('data-theme', 'signal')
    container = document.createElement('div')
    document.body.appendChild(container)
    live.chart = { container, width: 480, height: 240 }
  })
  afterEach(() => {
    vi.restoreAllMocks()
    unregisterExportOption(container)
    container.remove()
    document.documentElement.removeAttribute('data-theme')
    resetChartTokenCache()
  })

  it('names the same light theme as the image export', () => {
    expect(EXPORT_LIGHT_THEME).toBe(LIGHT_THEME)
  })

  it('renders the heatmap option as a vector: <svg> with <text>, no embedded <image>', () => {
    fakeThemes()
    const text = renderOptionToSvg(heatmapExport(readChartTokens()), 480, 240)
    expect(text.startsWith('<svg')).toBe(true)
    expect(text).toContain('<text')
    expect(text).not.toContain('<image')
    const svg = parseSvg(text)
    expect(svg.localName).toBe('svg')
    expect([...svg.querySelectorAll('text')].map((t) => t.textContent)).toEqual(expect.arrayContaining(['checkout', 'payments']))
  })

  it('an SVG export goes through the chart’s registered option at the on-screen size', async () => {
    fakeThemes()
    const build = vi.fn(heatmapExport)
    registerExportOption(container, { type: 'heatmap', build })
    const result = await echartsExportImage(document.body, { format: 'svg', light: false, pixelRatio: 2 })
    expect(result?.kind).toBe('svg')
    expect(result).toMatchObject({ width: 480, height: 240 })
    // The page's own (dark) tokens when Light background is off.
    expect(build.mock.calls[0][0].card).toBe('black')
  })

  it('Light background: the background and a label are the LIGHT tokens while the page is dark', async () => {
    fakeThemes()
    const build = vi.fn(heatmapExport)
    registerExportOption(container, { type: 'heatmap', build })
    const result = await echartsExportImage(document.body, { format: 'svg', light: true, pixelRatio: 2 })
    expect(build.mock.calls[0][0]).toMatchObject({ card: 'ivory', text: 'navy' })
    if (result?.kind !== 'svg') throw new Error('expected an SVG')
    const markup = new XMLSerializer().serializeToString(result.svg)
    // The background rectangle is the light card; the row labels the light text.
    expect(markup).toMatch(/<rect[^>]*fill="ivory"/)
    const label = [...result.svg.querySelectorAll('text')].find((t) => t.textContent === 'checkout')
    expect(label?.getAttribute('fill') ?? label?.getAttribute('style') ?? '').toMatch(/navy/)
    expect(markup).not.toContain('black')
    // The detached light host is gone again; the page keeps its theme.
    expect(document.querySelector(`[data-theme="${EXPORT_LIGHT_THEME}"]`)).toBeNull()
    expect(document.documentElement.getAttribute('data-theme')).toBe('signal')
  })

  it('refuses an export option with a string formatter: the export throws, nothing is drawn', async () => {
    fakeThemes()
    const before = document.body.childElementCount
    registerExportOption(container, {
      type: 'heatmap',
      build: (tokens) => ({ ...heatmapExport(tokens), tooltip: { formatter: '{b}: <img src=x onerror=alert(1)>' } }),
    })
    await expect(echartsExportImage(document.body, { format: 'svg', light: false, pixelRatio: 2 })).rejects.toThrow(/Refused chart option/)
    // The light PNG path is refused the same way, before any off-screen host is made.
    await expect(echartsExportImage(document.body, { format: 'png', light: true, pixelRatio: 2 })).rejects.toThrow(/Refused chart option/)
    expect(document.body.childElementCount).toBe(before)
    expect(document.querySelector('img')).toBeNull()
  })

  it('no registered option, no live chart, or a page-theme PNG → null (the caller keeps the raster path)', async () => {
    fakeThemes()
    expect(await echartsExportImage(document.body, { format: 'svg', light: false, pixelRatio: 2 })).toBeNull()
    registerExportOption(container, { type: 'heatmap', build: heatmapExport })
    expect(await echartsExportImage(document.body, { format: 'png', light: false, pixelRatio: 2 })).toBeNull()
    live.chart = null
    expect(await echartsExportImage(document.body, { format: 'svg', light: true, pixelRatio: 2 })).toBeNull()
  })
})
