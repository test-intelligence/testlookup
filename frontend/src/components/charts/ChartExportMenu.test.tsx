/**
 * `ChartExportMenu` (VIZ-606): the menu button's keyboard contract, the CSV
 * file it hands over, the app version it stamps, and a failure that stays a
 * visible message instead of an exception.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartSeries } from '@/lib/viz/contracts'
import type { ChartProvenance } from '@/lib/viz/chartExport'
import ChartExportMenu, { CANVAS_ENGINE_SELECTOR, EXPORT_LABELS, type ChartExportMenuProps } from './ChartExportMenu'
import { ECHARTS_INSTANCE_ATTRIBUTE } from './engines/echarts/exportImage'
import { ChartFrameContext } from './chartFrameContext'

const downloadBlob = vi.fn()
vi.mock('@/utils/download', () => ({ downloadBlob: (...args: unknown[]) => downloadBlob(...args) }))

const SERIES: ChartSeries = {
  kind: 'series',
  dimensions: ['day'],
  x_type: 'time',
  series: [{ key: 'a', label: 'Runs', points: [{ x: '2026-09-01', y: -3, n: 1 }, { x: '2026-09-02', y: null, n: 0 }] }],
}

const PROVENANCE: ChartProvenance = {
  project: 'Payments',
  releases: [],
  suites: ['api'],
  window: '2026-08-25 – 2026-09-23 UTC (30 days)',
  totals: null,
  generatedAt: '2026-09-23T14:05:09Z',
  appVersion: null,
}

function setup(props: Partial<ChartExportMenuProps> = {}, version?: string) {
  const body = document.createElement('div')
  document.body.appendChild(body)
  const ui = (
    <ChartExportMenu title="Runs per day" getBody={() => body} series={SERIES} provenance={PROVENANCE} {...props} />
  )
  const result = render(
    <SWRConfig value={{ provider: () => new Map(), fallback: version ? { 'system-health': { version } } : {} }}>
      {ui}
    </SWRConfig>,
  )
  return { ...result, body, trigger: screen.getByRole('button', { name: EXPORT_LABELS.trigger }) }
}

async function blobText(blob: Blob): Promise<string> {
  // ignoreBOM: keep the BOM in the text, so the test can see it is there.
  return new TextDecoder('utf-8', { ignoreBOM: true }).decode(await blob.arrayBuffer())
}

/**
 * The image path reaches `chartImage.ts` (and, for a canvas chart,
 * `exportImage.ts`) through a dynamic `import()` — on purpose, to keep them
 * out of the frame's chunk. In vitest the FIRST such import is a cold module
 * transform: measured at ~620 ms on an idle machine, which is most of RTL's
 * 1 s `findBy` ceiling, and past it under the full suite's parallel load. The
 * failure test below then timed out waiting for a message that was merely
 * still being compiled — a flake that said nothing about the menu. Loading
 * both modules here makes the component's own `import()` a cache hit, so the
 * tests time the menu's behaviour, not the module loader. (The production
 * lazy load is unaffected; `npm run check:bundle` guards that.)
 */
beforeAll(async () => {
  await Promise.all([import('@/lib/viz/chartImage'), import('./engines/echarts/exportImage')])
})

beforeEach(() => downloadBlob.mockReset())
afterEach(() => {
  document.body.innerHTML = ''
})

describe('ChartExportMenu — the menu button', () => {
  it('a visible "Export" label that announces a menu, collapsed', () => {
    const { trigger } = setup()
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu')
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('menu')).toBeNull()
  })

  // Baseline review A: nothing on screen said "Export" opens a menu rather
  // than downloading at once. A chevron does; it is decoration, so the name
  // stays exactly "Export".
  it('shows a menu chevron that is hidden from the accessible name', () => {
    const { trigger } = setup()
    const chevron = trigger.querySelector('[data-chart-export-chevron]')
    expect(chevron?.tagName.toLowerCase()).toBe('svg')
    expect(chevron).toHaveAttribute('aria-hidden', 'true')
    expect(trigger).toHaveAccessibleName(EXPORT_LABELS.trigger)
    expect(trigger.querySelector('[data-chart-export-busy]')).toBeNull()
  })

  it('opens INLINE (inside its own wrapper, not portalled) with focus on the first item', () => {
    const { trigger, container } = setup()
    fireEvent.click(trigger)
    const menu = screen.getByRole('menu')
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(trigger).toHaveAttribute('aria-controls', menu.id)
    expect(container.contains(menu)).toBe(true)
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: EXPORT_LABELS.png }))
    expect(screen.getAllByRole('menuitem').map((i) => i.textContent)).toEqual([
      EXPORT_LABELS.png,
      EXPORT_LABELS.svg,
      EXPORT_LABELS.csv,
    ])
  })

  it('arrow keys move through the items and wrap; Home / End jump', () => {
    const { trigger } = setup()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    const menu = screen.getByRole('menu')
    const names = () => document.activeElement?.textContent
    expect(names()).toBe(EXPORT_LABELS.png)
    fireEvent.keyDown(menu, { key: 'ArrowDown' })
    expect(names()).toBe(EXPORT_LABELS.svg)
    fireEvent.keyDown(menu, { key: 'End' })
    expect(names()).toContain(EXPORT_LABELS.light)
    fireEvent.keyDown(menu, { key: 'ArrowDown' })
    expect(names()).toBe(EXPORT_LABELS.png)
    fireEvent.keyDown(menu, { key: 'ArrowUp' })
    expect(names()).toContain(EXPORT_LABELS.light)
    fireEvent.keyDown(menu, { key: 'Home' })
    expect(names()).toBe(EXPORT_LABELS.png)
  })

  it('ArrowUp on the trigger opens on the LAST item', () => {
    const { trigger } = setup()
    fireEvent.keyDown(trigger, { key: 'ArrowUp' })
    expect(document.activeElement?.textContent).toContain(EXPORT_LABELS.light)
  })

  it('Escape closes and returns focus to the trigger', () => {
    const { trigger } = setup()
    fireEvent.click(trigger)
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(document.activeElement).toBe(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('Tab closes the menu', () => {
    const { trigger } = setup()
    fireEvent.click(trigger)
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Tab' })
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('a press outside closes it', () => {
    const { trigger } = setup()
    fireEvent.click(trigger)
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('"Light background" is a checkbox item that toggles and keeps the menu open', () => {
    const { trigger } = setup()
    fireEvent.click(trigger)
    const light = screen.getByRole('menuitemcheckbox', { name: new RegExp(EXPORT_LABELS.light) })
    expect(light).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(light)
    expect(light).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('menu')).toBeInTheDocument()
  })

  it('for a canvas-engine chart, "Light background" is disabled and says why', () => {
    const { trigger, body } = setup()
    const engine = document.createElement('div')
    engine.setAttribute(ECHARTS_INSTANCE_ATTRIBUTE, 'ec_1')
    body.appendChild(engine)
    expect(body.querySelector(CANVAS_ENGINE_SELECTOR)).toBe(engine)
    fireEvent.click(trigger)
    const light = screen.getByRole('menuitemcheckbox')
    expect(light).toHaveAttribute('aria-disabled', 'true')
    expect(light).toHaveTextContent(EXPORT_LABELS.lightUnavailable)
    fireEvent.click(light)
    expect(light).toHaveAttribute('aria-checked', 'false')
  })
})

describe('ChartExportMenu — full screen (review A7)', () => {
  // Under the Fullscreen API the browser takes Escape for itself and leaves:
  // the menu's own Escape never runs, so the frame's change must close it.
  it('leaving (or entering) full screen closes an open menu', () => {
    const body = document.createElement('div')
    document.body.appendChild(body)
    const ui = (fullscreen: boolean) => (
      <SWRConfig value={{ provider: () => new Map() }}>
        <ChartFrameContext.Provider value={{ fullscreen, bodyHeight: fullscreen ? 600 : null, portalContainer: null }}>
          <ChartExportMenu title="Runs per day" getBody={() => body} series={SERIES} provenance={PROVENANCE} />
        </ChartFrameContext.Provider>
      </SWRConfig>
    )
    const { rerender } = render(ui(true))
    fireEvent.click(screen.getByRole('button', { name: EXPORT_LABELS.trigger }))
    expect(screen.getByRole('menu')).toBeInTheDocument()
    rerender(ui(false))
    expect(screen.queryByRole('menu')).toBeNull()
    expect(screen.getByRole('button', { name: EXPORT_LABELS.trigger })).toHaveAttribute('aria-expanded', 'false')
    // It opens again as usual afterwards.
    fireEvent.click(screen.getByRole('button', { name: EXPORT_LABELS.trigger }))
    expect(screen.getByRole('menu')).toBeInTheDocument()
    rerender(ui(true))
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('a re-render that does not change full screen leaves the menu open', () => {
    const body = document.createElement('div')
    document.body.appendChild(body)
    const ui = (title: string) => (
      <SWRConfig value={{ provider: () => new Map() }}>
        <ChartFrameContext.Provider value={{ fullscreen: true, bodyHeight: 600, portalContainer: null }}>
          <ChartExportMenu title={title} getBody={() => body} series={SERIES} provenance={PROVENANCE} />
        </ChartFrameContext.Provider>
      </SWRConfig>
    )
    const { rerender } = render(ui('Runs per day'))
    fireEvent.click(screen.getByRole('button', { name: EXPORT_LABELS.trigger }))
    rerender(ui('Runs per day (updated)'))
    expect(screen.getByRole('menu')).toBeInTheDocument()
  })
})

describe('ChartExportMenu — CSV', () => {
  it('downloads the chart CSV, named by the story, with the app version from the cached health poll', async () => {
    const { trigger } = setup({}, '1.4.2')
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('menuitem', { name: EXPORT_LABELS.csv }))
    await waitFor(() => expect(downloadBlob).toHaveBeenCalledTimes(1))
    const [blob, filename] = downloadBlob.mock.calls[0] as [Blob, string]
    expect(filename).toBe('testlookup_runs-per-day_payments_20260923-1405Z.csv')
    const text = await blobText(blob)
    expect(text.charCodeAt(0)).toBe(0xfeff)
    expect(text).toContain('# App version,1.4.2')
    expect(text).toContain('# Test Suite,api')
    expect(text).toContain('2026-09-01,-3\r\n2026-09-02,\r\n')
    // Focus is back on the trigger after choosing.
    expect(document.activeElement).toBe(trigger)
  })

  it('with no scope it still exports, and says "Scope unavailable"', async () => {
    const { trigger } = setup({ provenance: null })
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('menuitem', { name: EXPORT_LABELS.csv }))
    await waitFor(() => expect(downloadBlob).toHaveBeenCalledTimes(1))
    const [blob, filename] = downloadBlob.mock.calls[0] as [Blob, string]
    expect(filename).toMatch(/^testlookup_runs-per-day_all-projects_\d{8}-\d{4}Z\.csv$/)
    expect(await blobText(blob)).toContain('# Scope,Scope unavailable')
  })

  it('a chartSlug names the file instead of the title', async () => {
    const { trigger } = setup({ chartSlug: 'pass-rate' })
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('menuitem', { name: EXPORT_LABELS.csv }))
    await waitFor(() => expect(downloadBlob).toHaveBeenCalled())
    expect(downloadBlob.mock.calls[0][1]).toMatch(/^testlookup_pass-rate_payments_/)
  })
})

describe('ChartExportMenu — failure', () => {
  it('an image export that fails shows a visible message tied to the trigger, and never throws', async () => {
    // jsdom has no canvas and the body holds no chart: the PNG export must fail — gracefully.
    const { trigger } = setup()
    fireEvent.click(trigger)
    await act(async () => {
      fireEvent.click(screen.getByRole('menuitem', { name: EXPORT_LABELS.png }))
    })
    const message = await screen.findByText(/the PNG export failed/)
    expect(message.closest('[data-chart-export-error]')).not.toBeNull()
    expect(trigger.getAttribute('aria-describedby')).toBe(message.closest('[data-chart-export-error]')?.id)
    expect(downloadBlob).not.toHaveBeenCalled()
    expect(trigger).toHaveTextContent(EXPORT_LABELS.trigger)
    fireEvent.click(screen.getByRole('button', { name: EXPORT_LABELS.dismiss }))
    expect(screen.queryByText(/the PNG export failed/)).toBeNull()
  })

  it('a body that is gone is a message, not a crash', async () => {
    setup({ getBody: () => null })
    fireEvent.click(screen.getByRole('button', { name: EXPORT_LABELS.trigger }))
    await act(async () => {
      fireEvent.click(screen.getByRole('menuitem', { name: EXPORT_LABELS.svg }))
    })
    expect(await screen.findByText(/not on screen/)).toBeInTheDocument()
  })
})
