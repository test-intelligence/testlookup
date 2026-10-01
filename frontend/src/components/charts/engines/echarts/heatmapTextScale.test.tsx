/**
 * K5: canvas text follows the presentation scale.
 *
 * Full screen (and, with B5's presentation mode, the page-level preference)
 * scales every Recharts drawing through `usePresentationScale()`. The heatmap
 * is a canvas: scaling it as a picture would blur it, so its axis and ramp
 * text are SIZED from the same hook instead. Unscaled, the option must be
 * exactly what it was, because the committed heatmap baselines were drawn
 * from it.
 */
import { render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readChartTokens } from '../../tokens'
import {
  HEATMAP_FONT_SIZE,
  HEATMAP_GRID,
  HEATMAP_NO_DATA_KEY_ROW,
  buildHeatmapOption,
  heatmapFontSize,
  heatmapGrid,
  type NumericMatrix,
} from './heatmapOption'

const scale = vi.hoisted(() => ({ value: 1 }))
vi.mock('../../framePlotHeight', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../framePlotHeight')>()),
  usePresentationScale: () => scale.value,
}))

const engine = vi.hoisted(() => {
  const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), dispatchAction: vi.fn() }
  return { instance, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('../registry', () => ({ loadChartEngine: engine.load }))

import HeatmapChart from '../../HeatmapChart'

const data: NumericMatrix = {
  kind: 'matrix',
  value_type: 'rate',
  x_labels: ['2026-09-01', '2026-09-02'],
  y_labels: ['auth', 'billing'],
  cells: [
    { x: 0, y: 0, value: 0.98, n: 40 },
    { x: 1, y: 0, value: 0.97, n: 41 },
    { x: 0, y: 1, value: 0.6, n: 20 },
    { x: 1, y: 1, value: null, n: 0 },
  ],
}

const tokens = readChartTokens()

interface AxisLabel {
  fontSize?: number
  width: number
}
interface Built {
  xAxis: { axisLabel: AxisLabel }
  yAxis: { axisLabel: AxisLabel }
  visualMap: { id?: string; textStyle?: { fontSize?: number } }[]
  grid: typeof HEATMAP_GRID
}

const build = (textScale?: number) =>
  buildHeatmapOption({ data, tokens, description: 'd', chartWidth: 640, textScale }) as unknown as Built

describe('heatmap canvas text scale (K5)', () => {
  beforeEach(() => {
    scale.value = 1
    engine.load.mockReset()
    engine.init.mockClear()
    engine.instance.setOption.mockClear()
  })

  it('unscaled: the option is exactly the pre-K5 option (no font size, the same insets)', () => {
    const plain = buildHeatmapOption({ data, tokens, description: 'd', chartWidth: 640 })
    const one = buildHeatmapOption({ data, tokens, description: 'd', chartWidth: 640, textScale: 1 })
    expect(JSON.stringify(one)).toBe(JSON.stringify(plain))
    const built = build(1)
    expect(built.xAxis.axisLabel).not.toHaveProperty('fontSize')
    expect(built.yAxis.axisLabel).not.toHaveProperty('fontSize')
    expect(built.visualMap[0].textStyle).not.toHaveProperty('fontSize')
    // This matrix has a hatched cell, so the bottom inset also holds the
    // "No data" key's row (R2-18); every other inset is the pre-K5 one.
    expect(built.grid).toEqual({ ...HEATMAP_GRID, bottom: HEATMAP_GRID.bottom + HEATMAP_NO_DATA_KEY_ROW })
  })

  it('full screen (15/11) draws axis and ramp text at 16 px or more', () => {
    const built = build(15 / 11)
    expect(built.xAxis.axisLabel.fontSize).toBeGreaterThanOrEqual(16)
    expect(built.yAxis.axisLabel.fontSize).toBeGreaterThanOrEqual(16)
    expect(built.visualMap[0].textStyle?.fontSize).toBeGreaterThanOrEqual(16)
  })

  it('presentation mode (16/11) is larger still, and the label insets grow with the text', () => {
    const built = build(16 / 11)
    expect(built.yAxis.axisLabel.fontSize).toBe(Math.round(HEATMAP_FONT_SIZE * (16 / 11)))
    expect(built.grid.left).toBeGreaterThan(HEATMAP_GRID.left)
    expect(built.grid.bottom).toBeGreaterThan(HEATMAP_GRID.bottom)
    // The y label column is cut to the (wider) inset it now has.
    expect(built.yAxis.axisLabel.width).toBeGreaterThan(build(1).yAxis.axisLabel.width)
  })

  it('a scale at or below 1, or not a number, never shrinks the text', () => {
    expect(heatmapFontSize(1)).toBeUndefined()
    expect(heatmapFontSize(0.5)).toBeUndefined()
    expect(heatmapFontSize(Number.NaN)).toBeUndefined()
    expect(heatmapGrid(0.5)).toEqual(HEATMAP_GRID)
  })

  it('HeatmapChart reads usePresentationScale and hands the scaled option to the engine', async () => {
    engine.load.mockResolvedValue({ init: engine.init })
    scale.value = 16 / 11
    render(<HeatmapChart data={data} description="Two suites." />)
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    const option = engine.instance.setOption.mock.calls[0][0] as unknown as Built
    expect(option.yAxis.axisLabel.fontSize).toBeGreaterThanOrEqual(16)
  })

  it('HeatmapChart unscaled hands the engine no font size at all', async () => {
    engine.load.mockResolvedValue({ init: engine.init })
    render(<HeatmapChart data={data} description="Two suites." />)
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    const option = engine.instance.setOption.mock.calls[0][0] as unknown as Built
    expect(option.yAxis.axisLabel).not.toHaveProperty('fontSize')
  })
})
