import { render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ChartGalleryPage from './ChartGalleryPage'
import { GALLERY_ITEM_IDS, GALLERY_ITEMS, galleryEngine } from './chartGalleryFixtures'
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
    PieChart: box,
    RadialBarChart: box,
    CartesianGrid: () => <div />,
    XAxis: () => <div />,
    YAxis: () => <div />,
    Tooltip: () => <div />,
    Legend: () => <div />,
    Cell: () => <div />,
    Line: mark,
    Area: mark,
    Bar: mark,
    Pie: mark,
    RadialBar: mark,
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
      const heading = screen.getByRole('heading', { level: 2, name: item.title })
      expect(section?.getAttribute('aria-labelledby')).toBe(heading.id)
      expect(section?.querySelector(`[data-gallery-canvas="${item.id}"]`)).toHaveStyle({
        width: '640px',
        height: '320px',
      })
    }
  })

  it('has one explicit empty item per chart component', () => {
    const empties = GALLERY_ITEMS.filter((item) => item.empty)
    expect(empties.map((item) => item.chart).sort()).toEqual(['donut', 'gauge', 'heatmap', 'trend'])
    const { container } = renderAt()
    expect(container.querySelectorAll('[data-gallery-empty="true"]')).toHaveLength(empties.length)
    // The donut's own empty state, not a blank box.
    expect(screen.getByText('No defect data')).toBeInTheDocument()
  })

  it('passes animate={false} through every chart to every Recharts series', () => {
    const { container } = renderAt()
    const marks = [...container.querySelectorAll('[data-animate]')]
    // line 4 + area 2 + bar 4 + pie 1 + radial bar 1, plus the empty trend (4)
    // and the zero gauge (1). The empty donut draws no Pie at all.
    expect(marks.length).toBe(17)
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
})
