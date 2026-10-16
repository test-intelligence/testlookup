/**
 * TRND-11 in the menu: an ECharts chart that registered its export option is
 * REDRAWN for its export (`echartsExportImage`, mocked here) — the SVG is the
 * redrawn vector, Light background is enabled and honoured — while a chart
 * without one keeps today's path (the screen's raster through `echartsImage`,
 * Light background disabled). A refused option shows its error, never throws.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartSeries } from '@/lib/viz/contracts'

const downloadBlob = vi.fn()
vi.mock('@/utils/download', () => ({ downloadBlob: (...args: unknown[]) => downloadBlob(...args) }))

const raster = vi.hoisted(() => ({ calls: [] as unknown[] }))
vi.mock('./engines/echarts/exportImage', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./engines/echarts/exportImage')>()),
  echartsImage: (body: unknown) => {
    raster.calls.push(body)
    return { dataUrl: 'data:image/png;base64,AAAA', width: 640, height: 320 }
  },
}))

const redraw = vi.hoisted(() => ({ fn: vi.fn() }))
vi.mock('./engines/echarts/exportSvg', () => ({ echartsExportImage: (...args: unknown[]) => redraw.fn(...args) }))

const images = vi.hoisted(() => ({ inputs: [] as { source: unknown; light?: boolean }[] }))
vi.mock('@/lib/viz/chartImage', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/viz/chartImage')>()),
  exportChartImage: async (input: { source: unknown; light?: boolean }) => {
    images.inputs.push(input)
    return new Blob(['img'], { type: 'image/png' })
  },
}))

import ChartExportMenu, { EXPORT_LABELS, exportPathOf } from './ChartExportMenu'
import { ECHARTS_INSTANCE_ATTRIBUTE } from './engines/echarts/exportImage'
import { registerExportOption, unregisterExportOption } from './engines/echarts/exportRegistry'

const SERIES: ChartSeries = {
  kind: 'series',
  dimensions: ['day'],
  x_type: 'time',
  series: [{ key: 'a', label: 'Runs', points: [{ x: '2026-09-01', y: 3, n: 1 }] }],
}

const LAZY_TIMEOUT = 5_000

beforeAll(async () => {
  await Promise.all([import('@/lib/viz/chartImage'), import('./engines/echarts/exportImage'), import('./engines/echarts/exportSvg')])
})

function chartBody({ registered, threeD = false }: { registered: boolean; threeD?: boolean }) {
  const body = document.createElement('div')
  if (threeD) {
    const overlay = document.createElement('div')
    overlay.setAttribute('data-chart-engine', 'three')
    body.appendChild(overlay)
  }
  const engine = document.createElement('div')
  engine.setAttribute('data-chart-engine', 'echarts')
  engine.setAttribute(ECHARTS_INSTANCE_ATTRIBUTE, 'ec_1')
  body.appendChild(engine)
  document.body.appendChild(body)
  if (registered) registerExportOption(engine, { type: 'heatmap', build: () => ({}) })
  return { body, engine }
}

function open(body: HTMLElement) {
  render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <ChartExportMenu title="Pass rate by suite" getBody={() => body} series={SERIES} provenance={null} />
    </SWRConfig>,
  )
  fireEvent.click(screen.getByRole('button', { name: EXPORT_LABELS.trigger }))
}

describe('ChartExportMenu — ECharts redrawn exports (TRND-11)', () => {
  beforeEach(() => {
    downloadBlob.mockReset()
    raster.calls = []
    images.inputs = []
    redraw.fn.mockReset()
    document.body.innerHTML = ''
  })

  it('exportPathOf: Recharts svg, a registered ECharts chart redraw, unregistered or 3D raster', () => {
    expect(exportPathOf(null)).toBe('svg')
    expect(exportPathOf(document.createElement('div'))).toBe('svg')
    expect(exportPathOf(chartBody({ registered: true }).body)).toBe('redraw')
    expect(exportPathOf(chartBody({ registered: false }).body)).toBe('raster')
    expect(exportPathOf(chartBody({ registered: true, threeD: true }).body)).toBe('raster')
  })

  it('Light background is ENABLED for a registered ECharts chart and toggles', () => {
    const { body } = chartBody({ registered: true })
    open(body)
    const light = screen.getByRole('menuitemcheckbox', { name: new RegExp(`^.?${EXPORT_LABELS.light}$`) })
    expect(light).not.toHaveAttribute('aria-disabled')
    fireEvent.click(light)
    expect(light).toHaveAttribute('aria-checked', 'true')
  })

  it('an SVG export is the redrawn vector, through echartsExportImage and NOT echartsImage', async () => {
    const { body } = chartBody({ registered: true })
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    redraw.fn.mockResolvedValue({ kind: 'svg', svg, width: 640, height: 320 })
    open(body)
    fireEvent.click(screen.getByRole('menuitem', { name: EXPORT_LABELS.svg }))
    await waitFor(() => expect(downloadBlob).toHaveBeenCalledTimes(1), { timeout: LAZY_TIMEOUT })
    expect(redraw.fn).toHaveBeenCalledWith(body, { format: 'svg', light: false, pixelRatio: 2 })
    expect(raster.calls).toEqual([])
    expect(images.inputs[0].source).toEqual({ kind: 'svg', svg })
  })

  it('Light background + PNG: the redrawn light raster, and the frame drawn light', async () => {
    const { body } = chartBody({ registered: true })
    redraw.fn.mockResolvedValue({ kind: 'raster', dataUrl: 'data:image/png;base64,BBBB', width: 640, height: 320 })
    open(body)
    fireEvent.click(screen.getByRole('menuitemcheckbox'))
    fireEvent.click(screen.getByRole('menuitem', { name: EXPORT_LABELS.png }))
    await waitFor(() => expect(downloadBlob).toHaveBeenCalledTimes(1), { timeout: LAZY_TIMEOUT })
    expect(redraw.fn).toHaveBeenCalledWith(body, { format: 'png', light: true, pixelRatio: 2 })
    expect(images.inputs[0]).toMatchObject({ source: { kind: 'raster', dataUrl: 'data:image/png;base64,BBBB' }, light: true })
    expect(raster.calls).toEqual([])
  })

  it('no registered option: today’s raster path, Light background disabled, the redraw never asked', async () => {
    const { body } = chartBody({ registered: false })
    open(body)
    const light = screen.getByRole('menuitemcheckbox')
    expect(light).toHaveAttribute('aria-disabled', 'true')
    expect(light).toHaveTextContent(EXPORT_LABELS.lightUnavailable)
    fireEvent.click(screen.getByRole('menuitem', { name: EXPORT_LABELS.svg }))
    await waitFor(() => expect(downloadBlob).toHaveBeenCalledTimes(1), { timeout: LAZY_TIMEOUT })
    expect(redraw.fn).not.toHaveBeenCalled()
    expect(raster.calls).toEqual([body])
    expect(images.inputs[0]).toMatchObject({ source: { kind: 'raster' }, light: false })
  })

  it('a refused export option shows the menu’s error text and never throws; the chart is untouched', async () => {
    const { body, engine } = chartBody({ registered: true })
    redraw.fn.mockRejectedValue(new Error('Refused chart option: option.tooltip.formatter is not a domTooltipFormatter (ADR decision 4)'))
    open(body)
    await act(async () => {
      fireEvent.click(screen.getByRole('menuitem', { name: EXPORT_LABELS.svg }))
    })
    expect(await screen.findByText(/the SVG export failed\. Refused chart option/)).toBeInTheDocument()
    expect(downloadBlob).not.toHaveBeenCalled()
    expect(body.contains(engine)).toBe(true)
    unregisterExportOption(engine)
  })
})
