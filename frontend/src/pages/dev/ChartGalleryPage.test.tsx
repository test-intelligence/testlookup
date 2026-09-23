import { render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ChartGalleryPage from './ChartGalleryPage'
import { GALLERY_ITEM_IDS, GALLERY_ITEMS, galleryCanvasSize, galleryEngine, galleryFramed } from './chartGalleryFixtures'
import { STATE_ITEM_IDS, STATE_ITEMS } from './chartStatesFixtures'

// Recharts is mocked the way the chart component tests mock it (jsdom has no
// layout, so ResponsiveContainer would render nothing). Every series mark
// echoes `isAnimationActive`, which is how "the gallery passes animate={false}"
// becomes observable end to end: page → component → Recharts prop.
vi.mock('recharts', () => {
  const box = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  const mark = ({ children, isAnimationActive }: { children?: ReactNode; isAnimationActive?: boolean }) => (
    <div data-animate={String(isAnimationActive)}>{children}</div>
  )
  return {
    ResponsiveContainer: box,
    LineChart: box,
    AreaChart: box,
    BarChart: box,
    // Wave 2 (VIZ-403 / VIZ-406): the time series and the p50/p95 band draw a
    // line and an area on one chart.
    ComposedChart: box,
    PieChart: box,
    RadialBarChart: box,
    CartesianGrid: () => <div />,
    XAxis: () => <div />,
    YAxis: () => <div />,
    Tooltip: () => <div />,
    Legend: () => <div />,
    Cell: () => <div />,
    PolarAngleAxis: () => <div />,
    // Wave 2: the donut's centre total, the ranked bar's value labels and the
    // diverging chart's zero baseline.
    Label: () => <div />,
    LabelList: () => <div />,
    ReferenceLine: () => <div />,
    // VIZ-405: the anomaly triangle. A marker on the plot, not a series: it
    // takes no `isAnimationActive`, so it is not counted below.
    ReferenceDot: () => <div />,
    Line: mark,
    Area: mark,
    Bar: mark,
    Pie: mark,
    RadialBar: mark,
    // VIZ-404: the direct labels read the plot area and the y scale from the
    // chart context; with no layout there is none, and they draw nothing.
    usePlotArea: () => undefined,
    useYAxisScale: () => undefined,
    // …and its pinned tooltip reads the chart width and the x scale.
    useChartWidth: () => undefined,
    useXAxisScale: () => undefined,
  }
})

// jsdom has no canvas: the ECharts engine is mocked at the registry, and
// each heatmap's init is recorded so the page's canvas items are observable.
const engine = vi.hoisted(() => ({ inits: [] as HTMLElement[] }))
vi.mock('@/components/charts/engines/registry', () => ({
  loadChartEngine: async () => ({
    init: (el: HTMLElement) => {
      engine.inits.push(el)
      return { setOption: () => {}, resize: () => {}, dispose: () => {} }
    },
  }),
}))

function renderAt(search = '') {
  return render(
    <MemoryRouter initialEntries={[`/__charts${search}`]}>
      <ChartGalleryPage />
    </MemoryRouter>,
  )
}

const html = () => document.documentElement

describe('ChartGalleryPage', () => {
  afterEach(() => {
    html().removeAttribute('data-theme')
  })

  it('renders every gallery item exactly once, with its heading', () => {
    const { container } = renderAt()
    const rendered = [...container.querySelectorAll('[data-gallery-item]')].map((el) =>
      el.getAttribute('data-gallery-item'),
    )
    expect(rendered).toEqual(GALLERY_ITEM_IDS)
    expect(new Set(rendered).size).toBe(GALLERY_ITEM_IDS.length)
    expect(GALLERY_ITEM_IDS.length).toBeGreaterThanOrEqual(8)

    for (const item of GALLERY_ITEMS) {
      const section = container.querySelector(`[data-gallery-item="${item.id}"]`)
      expect(section).not.toBeNull()
      // Exactly ONE level-2 heading per item, and it names the section: a
      // framed item is named by the FRAME's own heading (the gallery adds
      // none above it), an unframed one by the gallery's.
      const headings = screen.getAllByRole('heading', { level: 2, name: item.title })
      expect(headings, `${item.id}: heading drawn ${headings.length} times`).toHaveLength(1)
      if (galleryFramed(item)) {
        expect(section?.getAttribute('aria-label')).toBe(item.title)
        expect(section?.getAttribute('aria-labelledby')).toBeNull()
      } else {
        expect(section?.getAttribute('aria-labelledby')).toBe(headings[0].id)
      }
      // A Wave-2 item draws a whole ChartFrame, so its box is taller.
      const canvas = galleryCanvasSize(item)
      expect(section?.querySelector(`[data-gallery-canvas="${item.id}"]`)).toHaveStyle({
        width: `${canvas.width}px`,
        height: `${canvas.height}px`,
      })
    }
  })

  it('has one explicit empty item per chart component', () => {
    const empties = GALLERY_ITEMS.filter((item) => item.empty)
    expect(empties.map((item) => item.chart).sort()).toEqual([
      'donut',
      // VIZ-406: nothing timed at all — no value can go on a log axis.
      'duration-histogram',
      'gauge',
      'heatmap',
      'status-donut',
      'trend',
    ])
    const { container } = renderAt()
    expect(container.querySelectorAll('[data-gallery-empty="true"]')).toHaveLength(empties.length)
    // The donut's own empty state, not a blank box.
    expect(screen.getByText('No defect data')).toBeInTheDocument()
  })

  it('passes animate={false} through every chart to every Recharts series', () => {
    const { container } = renderAt()
    const marks = [...container.querySelectorAll('[data-animate]')]
    // Wave 1: line 4 + area 2 + bar 4 + pie 1 + radial bar 1, plus the empty
    // trend (4) and the zero gauge (1) = 17. The empty donut draws no Pie.
    // Wave 2: four status donuts (1 Pie each; the all-zero one hands over to
    // the frame and draws nothing), six ranked bars (1 Bar each), three
    // status charts (4 Bars each) and the registry's two (a Pie and a Bar) = 24.
    // VIZ-403 / VIZ-406: three time series (a Bar and a Line each = 6), the
    // duration histogram (1 Bar; the "nothing timed" one has no bucket to draw,
    // so it renders its empty text and no Bar at all) and the p50/p95 band (the
    // band Area + the two percentile Lines = 3) = 10. `slowest-tests` adds
    // nothing: it is a list of <div> bars, not a Recharts chart.
    // VIZ-404: one Line per SHOWN series — three suites (3), twelve folded to
    // 7 + Other (8), the gaps item (3), two branches (2), two releases (2) and
    // three suites with one hidden (2) = 20.
    // VIZ-405: the two trend-overlay time series. Both draw the VIZ-403 Bar and
    // rate Line (2 each = 4); the one with 27 days of runs adds the moving
    // average and the trend line, both started ON, each drawn over a
    // card-coloured halo Line (4), while the sparse one (6 days with runs,
    // under the 7 the overlays need) draws none of them = 8. The anomaly
    // marker is a ReferenceDot, not a series.
    // Wave 2.4 (VIZ-601 hostile names, VIZ-407 zoom): the hostile donut (1 Pie),
    // the hostile stacked bar (a Bar per status it has — passed, failed = 2),
    // the hostile-release time series (Bar + rate Line = 2) and the hostile
    // comparison (2 Lines) = 7, and the ranked bars of formula-like test names
    // and of names a first-character check misses (1 Bar each) = 9; the zoomed trend (Bar + rate Line, and 13 days
    // with runs in view is past the 7 the overlays need, so the moving average
    // and the trend line over their halos = 4, total 6), the Apply-unavailable
    // time series (Bar + Line = 2), the zoomed comparison with cart hidden
    // (2 Lines) and the zoomed p50/p95 band (Area + 2 Lines = 3) = 13. A zoom
    // SLICES a model and draws the same series, so it adds no series of its own.
    expect(marks.length).toBe(17 + 24 + 10 + 20 + 8 + 9 + 13)
    for (const mark of marks) expect(mark).toHaveAttribute('data-animate', 'false')
  })

  it('mounts every heatmap item on the ECharts engine, inside its own canvas', async () => {
    engine.inits.length = 0
    const { container } = renderAt()
    const heatmaps = GALLERY_ITEMS.filter((item) => galleryEngine(item) === 'echarts')
    expect(heatmaps.map((item) => item.id)).toEqual(['heatmap', 'heatmap-hostile-label', 'heatmap-status', 'heatmap-empty'])
    await waitFor(() => expect(engine.inits).toHaveLength(heatmaps.length))
    for (const item of heatmaps) {
      const section = container.querySelector(`[data-gallery-item="${item.id}"]`)
      expect(section).toHaveAttribute('data-gallery-engine', 'echarts')
      const el = section?.querySelector('[data-chart-engine="echarts"]')
      expect(el).not.toBeNull()
      expect(engine.inits).toContain(el)
    }
  })

  it('applies a real theme from ?theme= to <html>, and restores it on unmount', () => {
    html().setAttribute('data-theme', 'signal')
    const view = renderAt('?theme=lab')
    expect(html().getAttribute('data-theme')).toBe('lab')
    expect(screen.getByTestId('chart-gallery')).toHaveAttribute('data-gallery-theme', 'lab')
    view.unmount()
    expect(html().getAttribute('data-theme')).toBe('signal')
  })

  it.each(['neon', 'LAB', '', 'lab%00', '<script>'])(
    'ignores an invalid ?theme (%s) and leaves the active theme alone',
    (bad) => {
      html().setAttribute('data-theme', 'ember')
      renderAt(`?theme=${bad}`)
      expect(html().getAttribute('data-theme')).toBe('ember')
      expect(screen.getByTestId('chart-gallery')).toHaveAttribute('data-gallery-theme', 'default')
    },
  )

  it('never persists the theme it renders in', () => {
    renderAt('?theme=lab')
    expect(localStorage.getItem('testlookup-theme') ?? '').not.toContain('lab')
  })

  it('?view=states renders one ChartFrame per state, each in its expected state', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {}) // the throwing renderer is on purpose
    try {
      const { container } = renderAt('?view=states')
      expect(screen.getByTestId('chart-gallery')).toHaveAttribute('data-gallery-view', 'states')
      // The chart items are not on this view.
      expect(container.querySelectorAll('[data-gallery-item]')).toHaveLength(0)
      const ids = [...container.querySelectorAll('[data-state-item]')].map((el) => el.getAttribute('data-state-item'))
      expect(ids).toEqual(STATE_ITEM_IDS)
      await waitFor(() =>
        expect(container.querySelector('[data-state-item="invalid-payload"] [data-chart-frame]')).toHaveAttribute(
          'data-chart-state',
          'error',
        ),
      )
      for (const item of STATE_ITEMS) {
        const frame = container.querySelector(`[data-state-item="${item.id}"] [data-chart-frame]`)
        expect(frame, item.id).toHaveAttribute('data-chart-state', item.state)
        for (const text of item.expectText) expect(frame, `${item.id}: ${text}`).toHaveTextContent(text)
        expect(frame?.querySelectorAll('[data-chart-engine="echarts"]').length, item.id).toBe(item.draws ? 1 : 0)
      }
      // One page-level announcer; no frame carries a live region or an alert of its own.
      expect(container.querySelectorAll('[role="status"]')).toHaveLength(1)
      expect(container.querySelectorAll('[aria-live="assertive"]')).toHaveLength(1)
      expect(container.querySelectorAll('[role="alert"]')).toHaveLength(0)
      expect(container.querySelectorAll('[data-chart-frame] [role="status"], [data-chart-frame] [role="alert"]')).toHaveLength(0)
    } finally {
      spy.mockRestore()
    }
  })

  // Baseline review B: the hostile-release item rebuilt the headline trend
  // from its points alone, so the still-filling 03-10 lost its partial mark
  // and was drawn as a finished low bar, and two of its releases went missing.
  it('draws the hostile-release trend with the headline trend\'s partial day and every release', () => {
    const { container } = renderAt()
    const item = container.querySelector('[data-gallery-item="timeseries-hostile-release"]')
    expect(item?.querySelector('[data-chart-partial-note]')).toHaveTextContent('2026-03-10 is still filling')
    const headline = container.querySelector('[data-gallery-item="timeseries-trend-releases"]')
    // The same notes as the item it is built from: the one release before the
    // window is stated there too, so no release was dropped on the way.
    const notes = (root: Element | null) =>
      [...(root?.querySelectorAll('[data-chart="time-series"] p') ?? [])].map((p) => p.textContent)
    expect(notes(item)).toEqual(notes(headline))
  })

  // Baseline review A (D3): a frame title is plain text, so Markdown code-span
  // backticks were drawn as literal characters.
  it('names no gallery item with Markdown backticks', () => {
    for (const item of GALLERY_ITEMS) expect(item.title, item.id).not.toMatch(/`/)
  })
})
