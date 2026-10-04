/**
 * VIZ-508: with the 3D scatter showing, the frame's body holds TWO canvases —
 * the three.js view on top and the 2D ECharts scatter under it (never
 * unmounted). Export must take the 2D chart: `ChartExportMenu` looks for the
 * ECharts instance attribute, which a three canvas does not carry, and draws
 * from that instance — whatever canvas comes first in the body.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { PointsChart } from '@/lib/viz/contracts'

const downloadBlob = vi.fn()
vi.mock('@/utils/download', () => ({ downloadBlob: (...args: unknown[]) => downloadBlob(...args) }))

const drawn = vi.hoisted(() => ({ bodies: [] as unknown[] }))
vi.mock('./engines/echarts/exportImage', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./engines/echarts/exportImage')>()),
  echartsImage: (body: unknown) => {
    drawn.bodies.push(body)
    return { dataUrl: 'data:image/png;base64,AAAA', width: 640, height: 320 }
  },
}))

const images = vi.hoisted(() => ({ sources: [] as unknown[] }))
vi.mock('@/lib/viz/chartImage', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/viz/chartImage')>()),
  exportChartImage: async (input: { source: unknown }) => {
    images.sources.push(input.source)
    return new Blob(['png'], { type: 'image/png' })
  },
}))

import ChartExportMenu, { EXPORT_LABELS } from './ChartExportMenu'
import { ECHARTS_INSTANCE_ATTRIBUTE } from './engines/echarts/exportImage'

const CHART: PointsChart = {
  kind: 'points',
  x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
  y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
  size: { key: 'executions', label: 'Executions' },
  points: [{ id: 'fp-a', label: 'a', x: 12, y: 0, size: 10, n: 9 }],
}

/** The body as the scatter section lays it out in 3D: the overlay's canvas FIRST, the 2D chart under it. */
function bodyIn3D(): { body: HTMLElement; echarts: HTMLElement } {
  const body = document.createElement('div')
  const overlay = document.createElement('div')
  overlay.setAttribute('data-chart-engine', 'three')
  overlay.appendChild(document.createElement('canvas'))
  const echarts = document.createElement('div')
  echarts.setAttribute('data-chart-engine', 'echarts')
  echarts.setAttribute(ECHARTS_INSTANCE_ATTRIBUTE, 'ec_1')
  echarts.appendChild(document.createElement('canvas'))
  body.append(overlay, echarts)
  document.body.appendChild(body)
  return { body, echarts }
}

// The export path loads its drawing code lazily; under the full suite's load the
// first import can take longer than waitFor's 1 s default (the catalogue tests' 5 s).
const LAZY_TIMEOUT = 5_000

beforeEach(() => {
  downloadBlob.mockReset()
  drawn.bodies = []
  images.sources = []
  document.body.innerHTML = ''
})

describe('ChartExportMenu with the 3D scatter showing (VIZ-508)', () => {
  it('PNG is drawn from the 2D ECharts instance, not the three canvas', async () => {
    const { body } = bodyIn3D()
    render(
      <SWRConfig value={{ provider: () => new Map() }}>
        <ChartExportMenu title="Test duration vs failure rate" getBody={() => body} series={CHART} provenance={null} />
      </SWRConfig>,
    )
    fireEvent.click(screen.getByRole('button', { name: EXPORT_LABELS.trigger }))
    fireEvent.click(screen.getByRole('menuitem', { name: EXPORT_LABELS.png }))
    await waitFor(() => expect(downloadBlob).toHaveBeenCalledTimes(1), { timeout: LAZY_TIMEOUT })
    expect(drawn.bodies).toEqual([body])
    expect(images.sources).toEqual([{ kind: 'raster', dataUrl: 'data:image/png;base64,AAAA', width: 640, height: 320 }])
    expect(String(downloadBlob.mock.calls[0][1])).toMatch(/\.png$/)
  })
})
