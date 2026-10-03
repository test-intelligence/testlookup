import { render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { RUN_AXIS_TITLE } from '@/components/charts/heatmapFromMatrix'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ChartGalleryPage from './ChartGalleryPage'
import {
  GALLERY_ITEM_IDS,
  GALLERY_ITEMS,
  galleryCanvasSize,
  galleryEngine,
  galleryFramed,
  HOSTILE_LABEL,
} from './chartGalleryFixtures'
import { protoKeyNamesSeries } from '@/components/charts/__fixtures__/protoKeyNames'
import { HEATMAP_FRAME_HOSTILE_NAME } from '@/components/charts/__fixtures__/heatmapFrame'
import {
  COVERAGE_HOSTILE_NAME,
  coverageEmpty,
  coverageHostile,
  coverageOneTest,
  coveragePaymentsClasses,
  coverageSuites,
} from '@/components/charts/__fixtures__/coverageMap'
import {
  SCATTER_DENSE_COUNT,
  SCATTER_HOSTILE_NAME,
  scatterAllExcluded,
  scatterDefault,
  scatterDense,
  scatterHostile,
} from '@/components/charts/__fixtures__/testScatter'
import { failureGroupsResponse } from '@/components/charts/failureGroups/failureGroups.fixtures'
import { validateAnyChartResponse } from '@/components/charts/chartState'
import { SCATTER_DENSE_POINTS } from '@/components/charts/engines/echarts/scatterOption'
import { COVERAGE_MAP_HEIGHT } from '@/components/reports/catalogue/CoverageMapSection'
import { SCATTER_HEIGHT } from '@/components/reports/catalogue/ScatterSection.model'
import {
  GALLERY_COVERAGE_MAP_PLOT_HEIGHT,
  GALLERY_FAILURE_GROUPS_PLOT_HEIGHT,
  GALLERY_CLUSTERS_PLOT_HEIGHT,
  GALLERY_SCATTER_PLOT_HEIGHT,
  type GalleryItem,
} from './chartGalleryFixtures'
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

    // Every level-2 heading on the page, collected ONCE. Asking the role query
    // per item walked the whole accessibility tree 84 times, and at 84 items
    // that alone ran past the 15 s timeout on the CI runner (PR #166). The
    // name of these headings is their text (no aria-label on any of them), so
    // matching the text is the same check, made once.
    const levelTwo = screen.getAllByRole('heading', { level: 2 })
    const named = (title: string) =>
      levelTwo.filter((heading) => heading.textContent?.replace(/\s+/g, ' ').trim() === title)

    for (const item of GALLERY_ITEMS) {
      const section = container.querySelector(`[data-gallery-item="${item.id}"]`)
      expect(section).not.toBeNull()
      // Exactly ONE level-2 heading per item, and it names the section: a
      // framed item is named by the FRAME's own heading (the gallery adds
      // none above it), an unframed one by the gallery's.
      const headings = named(item.title)
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
      // Wave 3: a level with no tests (the frame's empty state, not an empty canvas).
      'coverage-map',
      'donut',
      // VIZ-406: nothing timed at all — no value can go on a log axis.
      'duration-histogram',
      'gauge',
      // Wave 2.5 (VIZ-104): an unmeasured bar and ring (an empty track, never
      // 0), and a sparkline with one point, which draws nothing.
      'gauge-bar',
      'heatmap',
      'ring-gauge',
      // Wave 3: every test left out, so nothing to place (the counts are the answer).
      'scatter',
      'sparkline',
      'status-donut',
      // Wave 3 (R6): no cluster this week is an ANSWER (the server's sentence), drawn without a plot.
      'systemic-clusters',
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
    // Wave 2.5 (VIZ-104): one Bar per series of each stacked-column item —
    // four statuses per day (4), three model legs per month (3), the hostile
    // item's two series (2), one day of four statuses (4), 60 days of four
    // (4) and 16 suites of four, drawn as bars (4) = 21 (a series is one Bar
    // however many columns or rows it has); the two
    // rate-target time series (Bar + rate Line each = 4; the target is a
    // ReferenceLine, not a series); and the three rings (1 RadialBar each,
    // the unmeasured one included: it draws its 0-length arc over the track)
    // = 3. Sparkline, GaugeBar and DayStrip draw no Recharts at all.
    // Wave 2.6: the ranked bars of prototype-member names (1 Bar) = 1; the
    // three heatmap frames are ECharts canvases and draw no Recharts series.
    // Wave 3 adds none: the heatmap frames, the treemaps and the scatters are
    // ECharts canvases, and the failure groups are React SVG (their table's
    // trend is the hand-drawn Sparkline).
    expect(marks.length).toBe(17 + 24 + 10 + 20 + 8 + 9 + 13 + 21 + 4 + 3 + 1)
    for (const mark of marks) expect(mark).toHaveAttribute('data-animate', 'false')
  })

  it('mounts every heatmap item on the ECharts engine, inside its own canvas', async () => {
    engine.inits.length = 0
    const { container } = renderAt()
    const heatmaps = GALLERY_ITEMS.filter((item) => galleryEngine(item) === 'echarts')
    expect(heatmaps.map((item) => item.id)).toEqual([
      'heatmap',
      'heatmap-hostile-label',
      'heatmap-status',
      'heatmap-empty',
      // Wave 2.6 (K5): the heatmap inside a ChartFrame, as the Trends catalogue draws it.
      'heatmap-frame',
      'heatmap-frame-90d',
      'heatmap-frame-hostile',
      // Wave 3: FK1's three heatmap frames, FK4's scatters and FK2's treemaps.
      // The all-excluded scatter and the empty level mount no engine (`dom`).
      'heatmap-frame-status',
      'heatmap-frame-edges',
      'heatmap-frame-fit',
      'scatter',
      'scatter-dense',
      'scatter-hostile',
      'coverage-map-pass-rate',
      'coverage-map-staleness',
      'coverage-map-flaky-share',
      'coverage-map-hostile',
      'coverage-map-one-test',
    ])
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

  // Wave 2.6: the gallery spells two kit fixtures out (it cannot import app
  // code); these hold the copies equal, so the baselines draw what the kit's
  // own tests test.
  it('spells out the kit’s prototype-name bars and hostile suite name exactly', () => {
    const item = GALLERY_ITEMS.find((candidate) => candidate.id === 'bar-ranked-prototype-names')
    expect(item?.chart === 'bars' ? item.data : null).toEqual(protoKeyNamesSeries)
    expect(HEATMAP_FRAME_HOSTILE_NAME).toBe(HOSTILE_LABEL)
  })

  it('draws each heatmap frame in its own frame, worst row first, and states the truncation by rows', async () => {
    const { container } = renderAt()
    const frame = container.querySelector('[data-gallery-item="heatmap-frame"] [data-chart-frame]')
    expect(frame).toHaveAttribute('data-chart-state', 'ready')
    expect(frame?.querySelector('[data-heatmap-rows]')).toHaveTextContent('Rows: lowest pass rate first. Top 7 of 12 suites')
    for (const id of ['heatmap-frame-90d', 'heatmap-frame-hostile']) {
      expect(container.querySelector(`[data-gallery-item="${id}"] [data-chart-frame]`), id).toHaveAttribute('data-chart-state', 'ready')
    }
  })

  // Baseline review A (D3): a frame title is plain text, so Markdown code-span
  // backticks were drawn as literal characters.
  it('names no gallery item with Markdown backticks', () => {
    for (const item of GALLERY_ITEMS) expect(item.title, item.id).not.toMatch(/`/)
  })
})

// ── Wave 3 (PR-B) ────────────────────────────────────────────────────────────

const section = (container: HTMLElement, id: string) => {
  const found = container.querySelector<HTMLElement>(`[data-gallery-item="${id}"]`)
  if (!found) throw new Error(`no gallery item ${id}`)
  return found
}
const frameOf = (container: HTMLElement, id: string) => section(container, id).querySelector('[data-chart-frame]')

describe('ChartGalleryPage - Wave 3 items', () => {
  // Every pixel of a Wave 3 item is drawn from one of these: each must be a
  // response the section's own validator would let through, or the baseline
  // records a chart the product could never draw.
  it('draws only fixtures that pass the C2/C3 contract', () => {
    const responses: [string, unknown][] = [
      ['scatterDefault', scatterDefault],
      ['scatterDense', scatterDense],
      ['scatterHostile', scatterHostile],
      ['scatterAllExcluded', scatterAllExcluded],
      ['coverageSuites', coverageSuites.response],
      ['coveragePaymentsClasses', coveragePaymentsClasses.response],
      ['coverageHostile', coverageHostile.response],
      ['coverageOneTest', coverageOneTest.response],
      ['coverageEmpty', coverageEmpty.response],
      ...GALLERY_ITEMS.flatMap((item): [string, unknown][] =>
        item.chart === 'failure-groups' ? [[item.id, failureGroupsResponse(item.groups)]] : [],
      ),
    ]
    for (const [name, response] of responses) {
      const checked = validateAnyChartResponse(response)
      expect(checked.ok ? [] : checked.errors, name).toEqual([])
    }
  })

  // The gallery's fixtures module may not import app code, so the section
  // heights and the hostile name are spelt out; these hold the copies equal.
  it('draws each plot at its section’s height, and spells the hostile name exactly', () => {
    expect(GALLERY_COVERAGE_MAP_PLOT_HEIGHT).toBe(COVERAGE_MAP_HEIGHT)
    expect(GALLERY_SCATTER_PLOT_HEIGHT).toBe(SCATTER_HEIGHT)
    // `FailureGroupsFrame`'s default height (not exported): the section passes none.
    expect(GALLERY_FAILURE_GROUPS_PLOT_HEIGHT).toBe(360)
    // `SystemicClusters`' default height (not exported): the groups section passes none.
    expect(GALLERY_CLUSTERS_PLOT_HEIGHT).toBe(300)
    expect(COVERAGE_HOSTILE_NAME).toBe(HOSTILE_LABEL)
    expect(SCATTER_HOSTILE_NAME).toBe(HOSTILE_LABEL)
    expect(scatterHostile.series.points.map((p) => p.label)).toContain(HOSTILE_LABEL)
  })

  // A generated fixture is a pixel source: if it drifted between machines (a
  // seed, a clock, a random draw), the baselines would flap. Pinned literals.
  it('generates the scatter fixtures deterministically, the dense one past the threshold', () => {
    expect(scatterDefault.series.points).toHaveLength(300)
    expect(scatterDefault.series.points.slice(0, 2)).toEqual([
      { id: 'fp-7-0', label: 'tests/api/test_case_0.py::test_0', x: 12, y: 0, size: 249, n: 249 },
      { id: 'fp-7-1', label: 'tests/api/test_case_1.py::test_1', x: 14839, y: 51.3, size: 149, n: 149 },
    ])
    expect(scatterDefault.series.medians).toEqual({ x: 95.5, y: 0 })
    expect(scatterDense.series.points).toHaveLength(SCATTER_DENSE_COUNT)
    expect(SCATTER_DENSE_COUNT).toBeGreaterThan(SCATTER_DENSE_POINTS)
    expect(scatterDense.series.medians).toEqual({ x: 180, y: 0 })
    // Unique ids (a rows request sends one back), none written with a leading '#'.
    for (const response of [scatterDefault, scatterDense, scatterHostile]) {
      const ids = response.series.points.map((p) => p.id)
      expect(new Set(ids).size).toBe(ids.length)
      expect(ids.filter((id) => id.startsWith('#'))).toEqual([])
    }
  })

  it('passes fit, nouns and the axis titles through to the heatmap frame', () => {
    const { container } = renderAt()
    const rows = (id: string) => section(container, id).querySelector('[data-heatmap-rows]')?.textContent ?? ''
    expect(rows('heatmap-frame-fit')).toContain('Colour scale fitted to the data')
    // The same data without `fit`: the fixed scale, and no such note.
    expect(rows('heatmap-frame')).not.toContain('Colour scale fitted')
    const status = section(container, 'heatmap-frame-status')
    expect(status.querySelector('[aria-label*="4 tests over 8 runs"]')).not.toBeNull()
    // FK1's axis title (X1: builds, not runs), spelt out in the fixtures module and held equal here.
    expect(status.textContent).toContain(RUN_AXIS_TITLE)
    expect(RUN_AXIS_TITLE).toBe('Build (oldest to newest)')
    // The status matrix is shown whole: nothing was cut.
    expect(rows('heatmap-frame-status')).not.toMatch(/Top \d/)
  })

  it('draws a coverage level as the section does: caption, fold, suite rule, grain, and the gaps named', () => {
    const { container } = renderAt()
    const frame = frameOf(container, 'coverage-map-pass-rate')
    expect(frame).toHaveAttribute('data-chart-state', 'ready')
    expect(frame).toHaveTextContent('Test execution coverage — not code coverage')
    expect(frame?.querySelector('[data-coverage-other-note]')).toHaveTextContent(
      'Showing the 10 largest of 16 suites; the rest are combined in Other (6).',
    )
    expect(frame?.querySelector('[data-coverage-suite-rule]')).not.toBeNull()
    expect(frame?.querySelector('[data-catalogue-grain]')).toHaveTextContent('Counted per test execution.')
    const gaps = (id: string) =>
      [...section(container, id).querySelectorAll('[data-coverage-gap]')].map((li) => li.getAttribute('data-coverage-gap'))
    // Pass rate: three different facts, none of them 0, and the combined grey.
    expect(gaps('coverage-map-pass-rate')).toEqual(['not_run', 'unknown', 'never'])
    expect(section(container, 'coverage-map-pass-rate').querySelector('[data-coverage-other]')).not.toBeNull()
    // Staleness: a suite not run in the window still has a last run, so it is binned, not a gap.
    expect(gaps('coverage-map-staleness')).toEqual(['unknown', 'never'])
    expect(section(container, 'coverage-map-staleness').querySelector('select')).toHaveValue('staleness')
    // Flaky share over the classes of one suite: only the file not run in the window is a gap.
    expect(gaps('coverage-map-flaky-share')).toEqual(['not_run'])
    expect(section(container, 'coverage-map-flaky-share').querySelector('select')).toHaveValue('flaky_share')
  })

  it('an empty level is the frame’s empty state, and a scatter with every test left out says why', () => {
    const { container } = renderAt()
    const empty = frameOf(container, 'coverage-map-empty')
    expect(empty).toHaveAttribute('data-chart-state', 'filtered-empty')
    expect(empty?.querySelector('[data-chart-engine]')).toBeNull()

    const excluded = frameOf(container, 'scatter-all-excluded')
    // Not "empty": the counts are the answer.
    expect(excluded).toHaveAttribute('data-chart-state', 'ready')
    expect(excluded?.querySelector('[data-scatter-nothing]')).toHaveTextContent(
      'No test can be placed on this chart: 3 tests with fewer than 5 executions, 1 test with no duration, 2 tests with only skipped or unknown results.',
    )
    // Said once, in the body; the shared footer does not repeat it (F-14, X4).
    expect(excluded?.querySelector('[data-scatter-excluded]')).toBeNull()
    expect(excluded?.querySelector('[data-chart-engine]')).toBeNull()
  })

  it('states the dense scatter and the 1 ms floor in the footer', () => {
    const { container } = renderAt()
    expect(frameOf(container, 'scatter-dense')?.querySelector('[data-scatter-dense]')).toHaveTextContent('Dense: 2,400 tests')
    expect(frameOf(container, 'scatter')?.querySelector('[data-scatter-dense]')).toBeNull()
    expect(frameOf(container, 'scatter')?.querySelector('[data-scatter-floor]')).not.toBeNull()
  })

  it('opens each failure-groups item on its view, and offers the switch only when groups are linked', () => {
    const { container } = renderAt()
    const view = (id: string) => section(container, id).querySelector('[data-group-view]')?.getAttribute('data-group-view')
    expect(view('failure-groups')).toBe('bubbles')
    expect(view('failure-groups-related')).toBe('relations')
    expect(section(container, 'failure-groups').querySelector('[data-group-view-switch]')).not.toBeNull()
    expect(section(container, 'failure-groups-no-edges').querySelector('[data-group-view-switch]')).toBeNull()
    for (const item of GALLERY_ITEMS.filter((candidate): candidate is Extract<GalleryItem, { chart: 'failure-groups' }> => candidate.chart === 'failure-groups')) {
      expect(frameOf(container, item.id), item.id).toHaveAttribute('data-chart-state', 'ready')
    }
  })
})
