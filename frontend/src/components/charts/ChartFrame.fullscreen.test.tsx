/**
 * ChartFrame's full screen (VIZ-608), its export mount (VIZ-606) and its zoom
 * note (VIZ-407) — and the regression guard that none of it changed the frame
 * OUTSIDE full screen. The shell's own behaviour stays in ChartFrame.test.tsx.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EnvelopeMeta, MatrixChart } from '@/lib/viz/contracts'
import type { ChartProvenance } from '@/lib/viz/chartExport'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import type { ChartExportMenuProps } from './ChartExportMenu'
import { CHART_MESSAGES } from './chartMessages'
import { ANNOUNCE_DEBOUNCE_MS, ChartAnnouncerProvider, useChartAnnouncer } from './ChartAnnouncer'
import type { ChartState } from './chartState'
import { useChartFrameHeight, useChartFullscreen, useChartPortalContainer } from './chartFrameContext'
import { serialiseFrameScenarios } from './__fixtures__/chartFrameScenarios'

/** The export menu is builder "export"'s; here only what the frame hands it matters. */
const exportProps: ChartExportMenuProps[] = []
vi.mock('./ChartExportMenu', () => ({
  default: (props: ChartExportMenuProps) => {
    exportProps.push(props)
    return (
      <button type="button" data-chart-export="">
        Export
      </button>
    )
  },
}))

const META: EnvelopeMeta = {
  schema_version: 1,
  scope: {
    projects: [{ id: 'p1', name: 'payments' }],
    releases: [{ id: 'r1', name: '2026.09', status: 'released' }],
    suites: [],
    window: { from: '2026-09-01', to: '2026-09-07', days: 7, timezone: 'UTC' },
  },
  totals: { matched_runs: 42, total_runs: 50, matched_executions: 1260, total_executions: 1500 },
  pass_rate_basis: 'executions',
  ignored_filters: [],
  truncated: false,
  truncated_total: null,
  measured: true,
  reason: null,
  includes_in_progress: 0,
  partial_day: null,
  generated_at: '2026-09-08T00:00:00Z',
  as_of: '2026-09-08T00:00:00Z',
}

const MATRIX: MatrixChart = {
  kind: 'matrix',
  value_type: 'rate',
  x_labels: ['d1', 'd2'],
  y_labels: ['auth', 'cart'],
  cells: [
    { x: 0, y: 0, value: 0.5, n: 2 },
    { x: 1, y: 0, value: 1, n: 2 },
    { x: 0, y: 1, value: 0.25, n: 4 },
    { x: 1, y: 1, value: 0.9, n: 10 },
  ],
}

const ready: ChartState = { status: 'ready', data: MATRIX, meta: META, revalidating: false }

/** A chart that reports what the frame's context tells it. */
function Probe({ height = 240 }: { height?: number }) {
  const drawnAt = useChartFrameHeight(height)
  const container = useChartPortalContainer()
  const fullscreen = useChartFullscreen()
  return (
    <div data-testid="probe" data-height={drawnAt} data-fullscreen={String(fullscreen)} data-portal={container ? container.getAttribute('data-chart-frame') ?? 'element' : 'none'}>
      plot
    </div>
  )
}

function frame(props: Partial<ChartFrameProps> = {}) {
  return render(
    <div>
      <button type="button">page before</button>
      <ChartFrame title="Pass rate by suite" headingLevel={3} state={ready} series={MATRIX} chartType="Heatmap" {...props}>
        {props.children ?? <Probe />}
      </ChartFrame>
      <button type="button">page after</button>
    </div>,
  )
}

const root = () => document.querySelector('[data-chart-frame]') as HTMLElement
const probe = () => screen.getByTestId('probe')
const enterButton = () => screen.getByRole('button', { name: CHART_MESSAGES.fullScreen })
const exitButton = () => screen.getByRole('button', { name: CHART_MESSAGES.exitFullScreen })

// ── A browser-like Fullscreen API (jsdom has none — that is the fallback case) ──
function stubFullscreenApi({ reject = false }: { reject?: boolean } = {}) {
  let current: Element | null = null
  const grant = (element: Element) => {
    current = element
  }
  const change = () => setTimeout(() => document.dispatchEvent(new Event('fullscreenchange')), 0)
  Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, get: () => true })
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => current })
  const request = vi.fn(function (this: Element) {
    if (reject) return Promise.reject(new TypeError('Permissions check failed'))
    grant(this)
    change()
    return Promise.resolve()
  })
  const exit = vi.fn(() => {
    current = null
    change()
    return Promise.resolve()
  })
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, writable: true, value: request })
  Object.defineProperty(document, 'exitFullscreen', { configurable: true, writable: true, value: exit })
  return { request, exit }
}
function removeFullscreenApi() {
  for (const key of ['fullscreenEnabled', 'fullscreenElement', 'exitFullscreen'] as const) {
    delete (document as unknown as Record<string, unknown>)[key]
  }
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).requestFullscreen
}
const settle = () => act(() => new Promise<void>((resolve) => setTimeout(resolve, 5)))

/** A ResizeObserver that reports `height` for whatever it observes, as a browser would on observe(). */
function measureBodyAs(height: number) {
  const Original = globalThis.ResizeObserver
  class Reporting {
    constructor(private readonly callback: ResizeObserverCallback) {}
    observe(target: Element) {
      this.callback([{ target, contentRect: { height, width: 1200 } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver)
    }
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = Reporting as unknown as typeof ResizeObserver
  return () => {
    globalThis.ResizeObserver = Original
  }
}

beforeEach(() => {
  exportProps.length = 0
})
afterEach(() => {
  removeFullscreenApi()
  document.documentElement.style.overflow = ''
})

describe('ChartFrame outside full screen — unchanged apart from the new buttons', () => {
  it('every state serialises to the markup of the frame before full screen existed', async () => {
    await expect(serialiseFrameScenarios(ChartFrame)).toMatchFileSnapshot('./__snapshots__/ChartFrame.outside-fullscreen.html')
  })

  it('the new buttons come AFTER the existing toolbar content, full screen last', () => {
    frame({ toolbar: <button type="button">Caller action</button> })
    const toolbar = screen.getByRole('toolbar', { name: 'Pass rate by suite actions' })
    expect(within(toolbar).getAllByRole('button').map((b) => b.textContent || b.getAttribute('aria-label'))).toEqual([
      'Caller action',
      CHART_MESSAGES.viewTable,
      'Export',
      CHART_MESSAGES.fullScreen,
    ])
  })

  it('outside full screen: context is the pass-through (height as given, no portal container)', () => {
    frame({ children: <Probe height={321} /> })
    expect(probe()).toHaveAttribute('data-height', '321')
    expect(probe()).toHaveAttribute('data-fullscreen', 'false')
    expect(probe()).toHaveAttribute('data-portal', 'none')
    expect(root()).not.toHaveAttribute('role')
    expect(root()).not.toHaveAttribute('data-chart-fullscreen')
  })

  it.each<[string, ChartState]>([
    ['loading', { status: 'loading' }],
    ['error', { status: 'error', error: { kind: 'server', message: 'x', requestId: null, status: 500 } }],
    ['filtered-empty', { status: 'filtered-empty', meta: META }],
    ['never-had-data', { status: 'never-had-data' }],
  ])('%s: no Full screen and no Export — nothing is drawn to act on', (_, state) => {
    frame({ state })
    expect(screen.queryByRole('button', { name: CHART_MESSAGES.fullScreen })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Export' })).toBeNull()
    expect(exportProps).toHaveLength(0)
  })

  it('the full-screen button is at least 24 × 24 (an icon with a name, not an unlabelled glyph)', () => {
    frame()
    const button = enterButton()
    expect(button).toHaveAttribute('title', CHART_MESSAGES.fullScreen)
    // `p-1` around a 16 px icon, plus the 1 px border: 26 px, the height of the frame's worded buttons.
    expect(button.className).toContain('p-1')
    expect(button.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    expect(button.querySelector('svg')?.getAttribute('class')).toContain('h-4 w-4')
  })
})

describe('ChartFrame full screen — the Fullscreen API', () => {
  it('enters on the frame (not the body), becomes a labelled modal, and hands the chart the screen', async () => {
    const api = stubFullscreenApi()
    const restore = measureBodyAs(812)
    try {
      frame()
      fireEvent.click(enterButton())
      expect(api.request).toHaveBeenCalledTimes(1)
      expect(api.request.mock.contexts[0]).toBe(root())
      await settle()
      expect(root()).toHaveAttribute('data-chart-fullscreen', 'api')
      const dialog = screen.getByRole('dialog', { name: 'Pass rate by suite' })
      expect(dialog).toBe(root())
      expect(dialog).toHaveAttribute('aria-modal', 'true')
      // The chart draws at the measured body height, and portals into the frame.
      expect(probe()).toHaveAttribute('data-height', '812')
      expect(probe()).toHaveAttribute('data-fullscreen', 'true')
      expect(probe()).toHaveAttribute('data-portal', '')
      // The body's inline min-height is gone: the full-screen CSS sizes it.
      expect(screen.getByRole('group', { name: 'Pass rate by suite' }).style.minHeight).toBe('')
      // The way out is worded, not just an icon.
      expect(exitButton()).toHaveTextContent(CHART_MESSAGES.exitFullScreen)
    } finally {
      restore()
    }
  })

  it('before the body is measured, the chart gets most of the viewport, never less than its own height', async () => {
    stubFullscreenApi()
    frame({ children: <Probe height={200} /> })
    fireEvent.click(enterButton())
    await settle()
    expect(probe()).toHaveAttribute('data-height', String(Math.max(200, Math.round(window.innerHeight * 0.6))))
  })

  it('"Exit full screen" leaves, returns focus to the Full screen button and restores the scroll', async () => {
    const api = stubFullscreenApi()
    const { container } = frame()
    const scroller = container.parentElement as HTMLElement
    let top = 640
    Object.defineProperty(scroller, 'scrollTop', { configurable: true, get: () => top, set: (v: number) => (top = v) })
    try {
      fireEvent.click(enterButton())
      await settle()
      top = 0
      fireEvent.click(exitButton())
      expect(api.exit).toHaveBeenCalledTimes(1)
      await settle()
      expect(root()).not.toHaveAttribute('data-chart-fullscreen')
      expect(root()).not.toHaveAttribute('role')
      expect(document.activeElement).toBe(enterButton())
      expect(top).toBe(640)
      expect(probe()).toHaveAttribute('data-height', '240')
    } finally {
      delete (scroller as unknown as Record<string, unknown>).scrollTop
    }
  })

  it('the table view and the export menu still work in full screen', async () => {
    stubFullscreenApi()
    frame()
    fireEvent.click(enterButton())
    await settle()
    fireEvent.click(within(root()).getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(within(root()).getByRole('table', { name: 'Pass rate by suite — data table' })).toBeInTheDocument()
    expect(within(root()).getByRole('button', { name: 'Export' })).toBeInTheDocument()
  })

  it('a frame that stops being drawn while full screen keeps its way out', async () => {
    stubFullscreenApi()
    const { rerender } = frame()
    fireEvent.click(enterButton())
    await settle()
    rerender(
      <div>
        <button type="button">page before</button>
        <ChartFrame title="Pass rate by suite" headingLevel={3} state={{ status: 'loading' }} series={MATRIX}>
          <Probe />
        </ChartFrame>
        <button type="button">page after</button>
      </div>,
    )
    expect(root()).toHaveAttribute('data-chart-state', 'loading')
    expect(root()).toHaveAttribute('data-chart-fullscreen', 'api')
    expect(exitButton()).toBeInTheDocument()
  })
})

describe('ChartFrame full screen — the overlay fallback', () => {
  it.each([
    ['the API is absent', () => {}],
    ['the request is rejected', () => void stubFullscreenApi({ reject: true })],
  ])('%s → a maximised dialog with a focus trap; Escape exits and focus returns', async (_, arrange) => {
    arrange()
    frame()
    fireEvent.click(enterButton())
    await settle()
    const dialog = screen.getByRole('dialog', { name: 'Pass rate by suite' })
    expect(dialog).toHaveAttribute('data-chart-fullscreen', 'overlay')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    // Tab from the last control wraps to the first; Shift+Tab from the first to the last.
    const buttons = within(dialog).getAllByRole('button')
    const first = buttons[0]
    const last = buttons[buttons.length - 1]
    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement).toBe(first)
    fireEvent.keyDown(first, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
    // The page's own controls are never reached.
    expect(document.activeElement).not.toBe(screen.getByRole('button', { name: 'page after' }))

    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' })
    await settle()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(enterButton())
  })
})

describe('ChartFrame full screen — Escape belongs to the innermost control', () => {
  it('a menu inside the overlay that handles Escape itself closes alone; the next Escape leaves', async () => {
    const onMenuEscape = vi.fn()
    frame({
      toolbar: (
        <button
          type="button"
          onKeyDown={(event) => {
            // What ChartExportMenu does with an open menu (VIZ-606).
            if (event.key === 'Escape') {
              event.stopPropagation()
              onMenuEscape()
            }
          }}
        >
          Open menu
        </button>
      ),
    })
    fireEvent.click(enterButton())
    await settle()
    const menu = screen.getByRole('button', { name: 'Open menu' })
    fireEvent.keyDown(menu, { key: 'Escape' })
    await settle()
    expect(onMenuEscape).toHaveBeenCalledTimes(1)
    expect(root()).toHaveAttribute('data-chart-fullscreen', 'overlay')
    fireEvent.keyDown(exitButton(), { key: 'Escape' })
    await settle()
    expect(root()).not.toHaveAttribute('data-chart-fullscreen')
  })
})

describe('ChartFrame full screen — the page announcer speaks from inside (review A1)', () => {
  /** A chart that makes announcements through the page's one announcer. */
  function Speaker() {
    const announcer = useChartAnnouncer()
    return (
      <div data-testid="probe">
        <button type="button" onClick={() => announcer?.assertive('Day 3: 12 runs')}>
          say now
        </button>
        <button type="button" onClick={() => announcer?.report('other-frame', 'Durations', 'updated')}>
          report
        </button>
      </div>
    )
  }
  const inFrame = (which: 'polite' | 'assertive') => root().querySelector(`[data-chart-announcer-outlet="${which}"]`)
  const onPage = (which: 'polite' | 'assertive') => document.querySelector(`[data-chart-announcer="${which}"]`)
  const withAnnouncer = () =>
    render(
      <ChartAnnouncerProvider>
        <button type="button">page before</button>
        <ChartFrame title="Pass rate by suite" headingLevel={3} state={ready} series={MATRIX} chartType="Heatmap">
          <Speaker />
        </ChartFrame>
        <button type="button">page after</button>
      </ChartAnnouncerProvider>,
    )

  it('outside full screen: no region in the frame, the page speaks (unchanged)', () => {
    withAnnouncer()
    expect(root().querySelector('[aria-live], [role="status"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'say now' }))
    expect(onPage('assertive')).toHaveTextContent('Day 3: 12 runs')
  })

  it.each([
    ['the Fullscreen API', () => void stubFullscreenApi()],
    ['the overlay', () => {}],
  ])('%s: a polite/assertive pair INSIDE the frame says it, and the page regions say nothing — never both', async (_, arrange) => {
    arrange()
    withAnnouncer()
    fireEvent.click(enterButton())
    await settle()
    expect(root()).toHaveAttribute('data-chart-fullscreen')
    // A status region and an assertive one, inside the full-screen element.
    expect(inFrame('polite')).toHaveAttribute('role', 'status')
    expect(inFrame('assertive')).toHaveAttribute('aria-live', 'assertive')
    expect(inFrame('assertive')).toHaveAttribute('aria-atomic', 'true')
    // Neither starts with old text.
    expect(inFrame('polite')).toHaveTextContent('')
    expect(inFrame('assertive')).toHaveTextContent('')

    fireEvent.click(within(root()).getByRole('button', { name: 'say now' }))
    expect(inFrame('assertive')).toHaveTextContent('Day 3: 12 runs')
    expect(onPage('assertive')?.textContent).toBe('')

    vi.useFakeTimers()
    try {
      fireEvent.click(within(root()).getByRole('button', { name: 'report' }))
      act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS))
    } finally {
      vi.useRealTimers()
    }
    expect(inFrame('polite')).toHaveTextContent('Durations chart: updated')
    expect(onPage('polite')?.textContent).toBe('')
    // Still ONE announcer: exactly one polite and one assertive region are speaking text.
    const speaking = Array.from(document.querySelectorAll('[aria-live], [role="status"]')).filter((el) => el.textContent)
    expect(speaking).toHaveLength(2)
  })

  it('on the way out the outlet goes, and the page regions speak again (starting empty)', async () => {
    stubFullscreenApi()
    withAnnouncer()
    fireEvent.click(enterButton())
    await settle()
    fireEvent.click(within(root()).getByRole('button', { name: 'say now' }))
    fireEvent.click(exitButton())
    await settle()
    expect(root().querySelector('[data-chart-announcer-outlet]')).toBeNull()
    // Not re-read on the page after the fact.
    expect(onPage('assertive')?.textContent).toBe('')
    fireEvent.click(screen.getByRole('button', { name: 'say now' }))
    expect(onPage('assertive')).toHaveTextContent('Day 3: 12 runs')
  })
})

describe('ChartFrame full screen — the page behind is inert (review A10)', () => {
  it('overlay: the page around the frame is inert while it is on, and live again after', async () => {
    frame()
    fireEvent.click(enterButton())
    await settle()
    expect(screen.getByRole('button', { name: 'page after', hidden: true })).toHaveAttribute('inert')
    expect(root()).not.toHaveAttribute('inert')
    fireEvent.keyDown(exitButton(), { key: 'Escape' })
    await settle()
    expect(screen.getByRole('button', { name: 'page after' })).not.toHaveAttribute('inert')
  })
})

describe('ChartFrame full screen — Escape, innermost first (review A7)', () => {
  /**
   * A pointer tooltip as the chart cursor makes one: a `[data-chart-tooltip]`
   * box that a DOCUMENT keydown listener (registered at mount, before full
   * screen) removes on Escape, synchronously.
   */
  function PointerTip({ canvas = false }: { canvas?: boolean }) {
    useEffect(() => {
      const onKey = (event: KeyboardEvent) => {
        if (event.key === 'Escape') document.querySelector('[data-testid="tip"]')?.remove()
      }
      document.addEventListener('keydown', onKey)
      return () => document.removeEventListener('keydown', onKey)
    }, [])
    const tip = (
      <div data-chart-tooltip="" data-testid="tip">
        2026-03-03
      </div>
    )
    // The attribute ECharts puts on its container (`CANVAS_ENGINE_SELECTOR`).
    const engine = (node: HTMLDivElement | null) => node?.setAttribute('_echarts_instance_', 'ec_1')
    return <div data-testid="probe">{canvas ? <div ref={engine}>{tip}</div> : tip}</div>
  }

  it('a pointer tooltip up, focus on "Exit full screen": Escape dismisses the tooltip only; the next Escape leaves', async () => {
    frame({ children: <PointerTip /> })
    fireEvent.click(enterButton())
    await settle()
    expect(screen.getByTestId('tip')).toBeInTheDocument()
    exitButton().focus()
    fireEvent.keyDown(exitButton(), { key: 'Escape' })
    await settle()
    expect(screen.queryByTestId('tip')).toBeNull()
    expect(root()).toHaveAttribute('data-chart-fullscreen', 'overlay')
    fireEvent.keyDown(exitButton(), { key: 'Escape' })
    await settle()
    expect(root()).not.toHaveAttribute('data-chart-fullscreen')
  })

  it('an ECharts tooltip (which Escape does not dismiss) does not hold full screen: one Escape leaves', async () => {
    frame({ children: <PointerTip canvas /> })
    fireEvent.click(enterButton())
    await settle()
    fireEvent.keyDown(exitButton(), { key: 'Escape' })
    await settle()
    expect(root()).not.toHaveAttribute('data-chart-fullscreen')
  })

  it('a hidden tooltip box (visibility: hidden, as Recharts leaves it) is not in the way', async () => {
    frame({
      children: (
        <div data-testid="probe">
          <div style={{ visibility: 'hidden' }}>
            <div data-chart-tooltip="">old</div>
          </div>
        </div>
      ),
    })
    fireEvent.click(enterButton())
    await settle()
    fireEvent.keyDown(exitButton(), { key: 'Escape' })
    await settle()
    expect(root()).not.toHaveAttribute('data-chart-fullscreen')
  })
})

describe('ChartFrame full screen — the measured height is forgotten on exit (review N5)', () => {
  it('re-entering draws at the unmeasured default, not the last full screen\'s height', async () => {
    stubFullscreenApi()
    const restore = measureBodyAs(812)
    try {
      frame({ children: <Probe height={200} /> })
      fireEvent.click(enterButton())
      await settle()
      expect(probe()).toHaveAttribute('data-height', '812')
      fireEvent.click(exitButton())
      await settle()
    } finally {
      restore()
    }
    // No measurement this time (a browser that has not laid the new size out yet).
    const unmeasured = measureBodyAs(0)
    try {
      fireEvent.click(enterButton())
      await settle()
      expect(probe()).toHaveAttribute('data-height', String(Math.max(200, Math.round(window.innerHeight * 0.6))))
    } finally {
      unmeasured()
    }
  })
})

describe('ChartFrame — zoom note and export provenance', () => {
  it('a zoom note is visible in the footer and stamped on the export', () => {
    frame({ zoomNote: 'Zoomed to 3 Sep – 5 Sep (not the page window)' })
    const note = document.querySelector('[data-chart-zoom-note]')
    expect(note).toHaveTextContent('Zoomed to 3 Sep – 5 Sep (not the page window)')
    expect(document.querySelector('[data-chart-footer]')).toContainElement(note as HTMLElement)
    const last = exportProps[exportProps.length - 1]
    expect(last.provenance?.note).toBe('Zoomed to 3 Sep – 5 Sep (not the page window)')
  })

  it('no zoom → no note in the footer and none on the export', () => {
    frame()
    expect(document.querySelector('[data-chart-zoom-note]')).toBeNull()
    expect(exportProps[exportProps.length - 1].provenance?.note).toBeUndefined()
  })

  it('a zoom note alone still opens the footer (a frame with no totals)', () => {
    frame({ state: { ...ready, meta: null } as ChartState, zoomNote: 'Zoomed to 3 Sep – 5 Sep' })
    expect(document.querySelector('[data-chart-footer]')).toHaveTextContent('Zoomed to 3 Sep – 5 Sep')
  })

  it('hands the menu the title, the slug, the drawn series, the body element and the scope from meta', () => {
    const format = (v: number | null) => String(v)
    frame({ exportSlug: 'pass-rate', format, axes: { x: 'Day', y: 'Suite' } })
    const last = exportProps[exportProps.length - 1]
    expect(last.title).toBe('Pass rate by suite')
    expect(last.chartSlug).toBe('pass-rate')
    expect(last.series).toBe(MATRIX)
    expect(last.format).toBe(format)
    expect(last.axes).toEqual({ x: 'Day', y: 'Suite' })
    expect(last.getBody()).toBe(screen.getByRole('group', { name: 'Pass rate by suite' }))
    expect(last.provenance?.project).toContain('payments')
    expect(last.provenance?.releases).toEqual(['2026.09'])
    expect(last.provenance?.generatedAt).toBe('2026-09-08T00:00:00Z')
  })

  it('an explicit provenance wins over the one built from meta (null included)', () => {
    const own: ChartProvenance = {
      project: 'Custom',
      releases: [],
      suites: [],
      window: 'w',
      totals: null,
      generatedAt: '2026-01-01T00:00:00Z',
      appVersion: null,
    }
    const { unmount } = frame({ provenance: own })
    expect(exportProps[exportProps.length - 1].provenance).toBe(own)
    unmount()
    frame({ provenance: null })
    expect(exportProps[exportProps.length - 1].provenance).toBeNull()
  })

  it('drawn but without a series → Full screen yes, Export no (there is nothing to export)', () => {
    frame({ series: null })
    expect(enterButton()).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Export' })).toBeNull()
  })

  it('truncated is drawn: Export and Full screen are offered', () => {
    frame({ state: { status: 'truncated', data: MATRIX, meta: META, shown: 2, total: 9, revalidating: false } })
    expect(screen.getByRole('button', { name: 'Export' })).toBeInTheDocument()
    expect(enterButton()).toBeInTheDocument()
  })
})
