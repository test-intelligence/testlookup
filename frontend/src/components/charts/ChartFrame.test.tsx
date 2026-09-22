import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createRef, type ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ChartSeries, EnvelopeMeta, MatrixChart, SeriesChart } from '@/lib/viz/contracts'
import { useChartData } from '@/hooks/useChartData'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import { ANNOUNCE_DEBOUNCE_MS, ChartAnnouncerProvider } from './ChartAnnouncer'
import { CHART_MESSAGES } from './chartMessages'
import { PAGE_SIZE } from './ChartTable'
import { validateChartResponse, type ChartState } from './chartState'
import { chartTableModel, NO_DATA, NO_VALUE } from './chartText'
import { StaleBuildError } from './engines/lazyChartEngine'

const META: EnvelopeMeta = {
  schema_version: 1,
  scope: {
    projects: [{ id: 'p1', name: 'payments' }],
    releases: [],
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
  x_labels: ['d1', 'd2', 'd3'],
  y_labels: ['auth', 'cart'],
  cells: [
    { x: 0, y: 0, value: 0.5, n: 2 },
    { x: 1, y: 0, value: 1, n: 2 },
    { x: 2, y: 0, value: 0.75, n: 4 },
    { x: 0, y: 1, value: 0.25, n: 4 },
    { x: 1, y: 1, value: null, n: 0 },
    { x: 2, y: 1, value: 0.9, n: 10 },
  ],
}

/** A stand-in renderer that records exactly what it was asked to plot. */
const plotted: ChartSeries[] = []
function FakeRenderer({ series }: { series: ChartSeries }) {
  plotted.push(series)
  return <div data-testid="renderer">plot</div>
}

const ready = (meta: EnvelopeMeta | null = META): ChartState => ({ status: 'ready', data: MATRIX, meta, revalidating: false })

function frame(props: Partial<ChartFrameProps> & { state: ChartState }) {
  return render(
    <ChartFrame title="Pass rate by suite" headingLevel={3} series={MATRIX} chartType="Heatmap" axes={{ x: 'Day', y: 'Suite' }} {...props}>
      {props.children ?? ((series: ChartSeries) => <FakeRenderer series={series} />)}
    </ChartFrame>,
  )
}

/** The error message: static text in the frame (no live role — ChartAnnouncer announces changes). */
const errorMessage = () => {
  const node = document.querySelector('[data-chart-state-message="error"]')
  if (!(node instanceof HTMLElement)) throw new Error('no error message rendered')
  return node
}
const drawError = (scope: ParentNode = document) => {
  const node = scope.querySelector('[data-chart-draw-error]')
  if (!(node instanceof HTMLElement)) throw new Error('no draw error rendered')
  return node
}

const errorState = (overrides: Partial<Extract<ChartState, { status: 'error' }>['error']> = {}, retry?: () => void): ChartState => ({
  status: 'error',
  error: { kind: 'server', message: 'The server could not produce this chart.', requestId: 'req-42', status: 500, ...overrides },
  retry,
})

describe('ChartFrame — shell', () => {
  afterEach(() => {
    plotted.length = 0
    vi.useRealTimers()
  })

  it('renders the title as a heading at the given level, naming the chart group', () => {
    frame({ state: ready(), takeaway: 'Down 3.1 pts in 30 days' })
    const heading = screen.getByRole('heading', { level: 3, name: 'Pass rate by suite' })
    expect(heading).toHaveAttribute('title', 'Pass rate by suite')
    expect(heading.className).toContain('line-clamp-2')
    const group = screen.getByRole('group', { name: 'Pass rate by suite' })
    expect(group).toHaveAttribute('aria-labelledby', heading.id)
    // Described by the takeaway AND the generated summary.
    const ids = (group.getAttribute('aria-describedby') ?? '').split(' ')
    expect(ids).toHaveLength(2)
    expect(document.getElementById(ids[0])).toHaveTextContent('Down 3.1 pts in 30 days')
    expect(document.getElementById(ids[1])).toHaveTextContent('Heatmap. X axis: Day (3 values). Y axis: Suite (2 values).')
    expect(document.getElementById(ids[1])).toHaveTextContent('Min 25.0% (cart, d1), max 100.0% (auth, d2).')
    expect(document.getElementById(ids[1])).toHaveTextContent('Latest (d3): auth 75.0%, cart 90.0%.')
  })

  it('no takeaway → no takeaway row and no placeholder text', () => {
    const { container } = frame({ state: ready() })
    expect(container.querySelector('[data-chart-takeaway]')).toBeNull()
    const group = screen.getByRole('group', { name: 'Pass rate by suite' })
    expect((group.getAttribute('aria-describedby') ?? '').split(' ')).toHaveLength(1)
  })

  it('renders the scope and toolbar slots, and exposes the chart body through its ref', () => {
    const ref = createRef<HTMLDivElement>()
    render(
      <ChartFrame
        ref={ref}
        title="T"
        headingLevel={2}
        state={ready()}
        scope={<span>Scope badge</span>}
        toolbar={<button type="button">Export</button>}
        data-testid="frame"
      >
        <div>chart</div>
      </ChartFrame>,
    )
    expect(screen.getByText('Scope badge')).toBeInTheDocument()
    const toolbar = screen.getByRole('toolbar', { name: 'T actions' })
    expect(within(toolbar).getByRole('button', { name: 'Export' })).toBeInTheDocument()
    // No series → no "View as table" (hidden, never disabled-without-reason).
    expect(screen.queryByRole('button', { name: CHART_MESSAGES.viewTable })).toBeNull()
    expect(ref.current).toBe(screen.getByRole('group', { name: 'T' }))
    expect(screen.getByTestId('frame')).toHaveAttribute('data-chart-state', 'ready')
  })

  it('shows "N of M" from meta.totals in the footer', () => {
    const { container } = frame({ state: ready(), footer: <span>as of today</span> })
    expect(container.querySelector('[data-chart-totals]')).toHaveTextContent('42 of 50 runs · 1,260 of 1,500 executions')
    expect(screen.getByText('as of today')).toBeInTheDocument()
    const noMeta = frame({ state: ready(null) })
    expect(noMeta.container.querySelector('[data-chart-totals]')).toBeNull()
  })

  it('dims the previous chart while revalidating', () => {
    frame({ state: { status: 'ready', data: MATRIX, meta: META, revalidating: true } })
    const group = screen.getByRole('group', { name: 'Pass rate by suite' })
    expect(group).toHaveAttribute('data-revalidating', 'true')
    expect(group).toHaveAttribute('aria-busy', 'true')
    expect(group.className).toContain('opacity-60')
    expect(screen.getByTestId('renderer')).toBeInTheDocument()
  })
})

describe('ChartFrame — owns every non-data state (child renderer not mounted)', () => {
  const retry = vi.fn()
  const cases: [string, ChartState, (string | RegExp)[]][] = [
    ['loading', { status: 'loading' }, [`${CHART_MESSAGES.loading}: Pass rate by suite`]],
    ['never-had-data', { status: 'never-had-data' }, [CHART_MESSAGES.neverHadData, CHART_MESSAGES.ingestAction]],
    ['filtered-empty', { status: 'filtered-empty', meta: META }, [CHART_MESSAGES.filteredEmpty]],
    ['not-measured', { status: 'not-measured', reason: 'No baseline yet.', meta: null }, [NO_VALUE, 'No baseline yet.']],
    ['error', errorState({}, retry), [CHART_MESSAGES.error, 'req-42', CHART_MESSAGES.retry]],
    ['forbidden', { status: 'forbidden', requestId: 'req-403' }, [CHART_MESSAGES.forbidden, 'req-403']],
  ]

  it.each(cases)('%s', (_, state, texts) => {
    const { container } = frame({ state })
    expect(screen.queryByTestId('renderer')).toBeNull()
    expect(plotted).toHaveLength(0)
    for (const text of texts) expect(container).toHaveTextContent(text)
    expect(container.querySelector('[data-chart-frame]')).toHaveAttribute('data-chart-state', state.status)
    // No table offered when nothing is drawn.
    expect(screen.queryByRole('button', { name: CHART_MESSAGES.viewTable })).toBeNull()
    expect(container.querySelector('[data-chart-summary]')).toBeNull()
  })

  it('loading holds the chart geometry with a chart skeleton, and is busy', () => {
    const { container } = frame({ state: { status: 'loading' }, height: 300 })
    const skeleton = container.querySelector('[data-skeleton="chart"]') as HTMLElement
    expect(skeleton).not.toBeNull()
    expect(skeleton.style.height).toBe('300px')
    expect(screen.getByRole('group', { name: 'Pass rate by suite' })).toHaveAttribute('aria-busy', 'true')
  })

  it('never-had-data links to ingestion', () => {
    frame({ state: { status: 'never-had-data' }, ingestHref: '/getting-started' })
    expect(screen.getByRole('link', { name: CHART_MESSAGES.ingestAction })).toHaveAttribute('href', '/getting-started')
  })

  it('filtered-empty offers "Clear filters" only with a callback, and calls it', () => {
    const onClear = vi.fn()
    frame({ state: { status: 'filtered-empty', meta: META }, onClearFilters: onClear })
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.clearFilters }))
    expect(onClear).toHaveBeenCalledTimes(1)
    // Zero-result copy keeps the "N of M" so the reader sees data exists outside the filters.
    expect(screen.getByText(/42 of 50 runs/)).toBeInTheDocument()
  })

  it('not-measured shows "—" and the reason — never 0 or 0%', () => {
    const { container } = frame({
      state: { status: 'not-measured', reason: 'Pass rate is not measured before the first run.', meta: null },
    })
    expect(container.querySelector('[data-chart-value]')).toHaveTextContent(/^—$/)
    expect(container.querySelector('[data-chart-reason]')).toHaveTextContent('Pass rate is not measured before the first run.')
    const body = screen.getByRole('group', { name: 'Pass rate by suite' })
    expect(body.textContent ?? '').not.toMatch(/(^|[^\d.])0(\.0+)?%?([^\d.]|$)/)
  })

  it('error shows the request id and a working Retry', () => {
    const onRetry = vi.fn()
    frame({ state: errorState({}, onRetry) })
    const alert = errorMessage()
    expect(within(alert).getByText('req-42')).toHaveAttribute('data-chart-request-id')
    fireEvent.click(within(alert).getByRole('button', { name: CHART_MESSAGES.retry }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('an error with no retry and no request id offers neither', () => {
    frame({ state: errorState({ requestId: null }) })
    expect(screen.queryByRole('button', { name: CHART_MESSAGES.retry })).toBeNull()
    expect(screen.queryByText(/Request ID/)).toBeNull()
  })

  it('a 422 names the parameter; a 429 is a wait state', () => {
    const { unmount } = frame({ state: errorState({ kind: 'invalid-param', param: 'release_id', message: 'The server did not accept the value of "release_id".' }) })
    expect(errorMessage()).toHaveTextContent('"release_id"')
    unmount()
    frame({ state: errorState({ kind: 'rate-limited', retryAfterSeconds: 30, message: 'This chart will try again in 30 s.' }, vi.fn()) })
    expect(errorMessage()).toHaveTextContent(CHART_MESSAGES.rateLimited)
    expect(errorMessage()).toHaveTextContent('30 s')
  })

  it('a session that expired (401 after the refresh retry) offers "Sign in", not Retry', () => {
    frame({
      state: errorState({ kind: 'session-expired', status: 401, message: 'Your session has expired. Sign in again to see this chart.' }, vi.fn()),
      signInHref: '/login?next=%2Foverview',
    })
    expect(errorMessage()).toHaveTextContent(CHART_MESSAGES.sessionExpired)
    expect(screen.getByRole('link', { name: CHART_MESSAGES.signIn })).toHaveAttribute('href', '/login?next=%2Foverview')
    expect(screen.queryByRole('button', { name: CHART_MESSAGES.retry })).toBeNull()
  })

  it('a stale build reads "A new version is available — Reload" and reloads', () => {
    const reload = vi.fn()
    const location = window.location
    Object.defineProperty(window, 'location', { configurable: true, value: { ...location, reload } })
    try {
      frame({ state: errorState({ kind: 'stale-build', message: 'A new version is available', requestId: null }, vi.fn()) })
      expect(errorMessage()).toHaveTextContent('A new version is available')
      fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
      expect(reload).toHaveBeenCalledTimes(1)
      expect(screen.queryByRole('button', { name: CHART_MESSAGES.retry })).toBeNull()
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: location })
    }
  })

  it('truncated draws the chart plus "Showing top N of M", which opens the table', () => {
    frame({ state: { status: 'truncated', data: MATRIX, meta: { ...META, truncated: true, truncated_total: 9 }, shown: 2, total: 9, revalidating: false } })
    expect(screen.getByTestId('renderer')).toBeInTheDocument()
    expect(screen.getByText(/Showing top 2 of 9/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'View the top 2 as a table' }))
    expect(screen.getByRole('table')).toBeInTheDocument()
  })
})

describe('ChartFrame — error boundary per frame', () => {
  it('a renderer that throws takes down only its own frame', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    function Boom(): never {
      throw new Error('renderer exploded')
    }
    try {
      render(
        <div>
          <ChartFrame title="Broken" headingLevel={2} state={ready()}>
            <Boom />
          </ChartFrame>
          <ChartFrame title="Fine" headingLevel={2} state={ready()}>
            <div data-testid="fine-chart">ok</div>
          </ChartFrame>
        </div>,
      )
      expect(drawError(screen.getByRole('group', { name: 'Broken' }))).toHaveTextContent('This chart could not be drawn.')
      expect(screen.getByTestId('fine-chart')).toBeInTheDocument()
    } finally {
      spy.mockRestore()
    }
  })

  it('a stale engine chunk thrown while drawing shows Reload', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    function Stale(): never {
      throw new StaleBuildError(new TypeError('Failed to fetch dynamically imported module'))
    }
    try {
      render(
        <ChartFrame title="Stale" headingLevel={2} state={ready()}>
          <Stale />
        </ChartFrame>,
      )
      expect(drawError()).toHaveTextContent('A new version is available')
      expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument()
    } finally {
      spy.mockRestore()
    }
  })

  it('"Try again" re-renders the child', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    let fail = true
    function Flaky() {
      if (fail) throw new Error('once')
      return <div data-testid="recovered">ok</div>
    }
    try {
      render(
        <ChartFrame title="Flaky" headingLevel={2} state={ready()}>
          <Flaky />
        </ChartFrame>,
      )
      fail = false
      fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
      expect(screen.getByTestId('recovered')).toBeInTheDocument()
    } finally {
      spy.mockRestore()
    }
  })
})

describe('ChartFrame — "View as table" (VIZ-105)', () => {
  afterEach(() => {
    plotted.length = 0
  })

  it('property: the table cells equal the values the renderer was handed', () => {
    frame({ state: ready() })
    expect(plotted.length).toBeGreaterThan(0)
    const drawn = plotted[plotted.length - 1] as MatrixChart
    // The renderer got the very object the frame summarises and tabulates.
    expect(drawn).toBe(MATRIX)

    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const table = screen.getByRole('table', { name: 'Pass rate by suite — data table' })
    expect(within(table).getAllByRole('columnheader').map((th) => th.textContent)).toEqual(['Suite', ...drawn.x_labels])
    const bodyRows = within(table).getAllByRole('row').slice(1)
    const cells = bodyRows.map((row) => Array.from(row.children, (cell) => cell.textContent))
    // Expected independently of chartTableModel: straight from the plotted cells.
    const expected = drawn.y_labels.map((label, y) => [
      label,
      ...drawn.x_labels.map((_, x) => {
        const cell = drawn.cells.find((c) => c.x === x && c.y === y)
        // A cell sent as null is "No data" (drawn hatched); a cell never sent is "—".
        if (!cell) return NO_VALUE
        return cell.value === null ? NO_DATA : `${((cell.value as number) * 100).toFixed(1)}%`
      }),
    ])
    expect(cells).toEqual(expected)
    // Every plotted measured value is in the table exactly once.
    const measured = drawn.cells.filter((c) => c.value !== null).length
    expect(cells.flat().filter((c) => c !== NO_VALUE && c !== NO_DATA && !drawn.y_labels.includes(c ?? '')).length).toBe(measured)
    expect(within(table).getAllByRole('rowheader')).toHaveLength(drawn.y_labels.length)
  })

  it('moves focus to the caption on open, and toggles closed', () => {
    frame({ state: ready() })
    const toggle = screen.getByRole('button', { name: CHART_MESSAGES.viewTable })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    const caption = screen.getByText('Pass rate by suite — data table')
    expect(caption.tagName).toBe('CAPTION')
    expect(document.activeElement).toBe(caption)
    const hide = screen.getByRole('button', { name: CHART_MESSAGES.hideTable })
    expect(hide).toHaveAttribute('aria-expanded', 'true')
    expect(document.getElementById(hide.getAttribute('aria-controls') ?? '')).toContainElement(caption)
    fireEvent.click(hide)
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('paginates above 500 rows', () => {
    const long: SeriesChart = {
      kind: 'series',
      dimensions: ['day'],
      x_type: 'category',
      series: [{ key: 'v', label: 'Value', points: Array.from({ length: 1200 }, (_, i) => ({ x: `p${i}`, y: i, n: 1 })) }],
    }
    render(
      <ChartFrame title="Long" headingLevel={2} state={{ status: 'ready', data: long, meta: null, revalidating: false }} series={long}>
        {() => <div />}
      </ChartFrame>,
    )
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('row')).toHaveLength(PAGE_SIZE + 1)
    expect(screen.getByText(`Rows 1–${PAGE_SIZE} of 1200`)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous rows' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Next rows' }))
    expect(screen.getByText(`Rows ${PAGE_SIZE + 1}–${PAGE_SIZE * 2} of 1200`)).toBeInTheDocument()
    expect(within(table).getAllByRole('rowheader')[0]).toHaveTextContent(`p${PAGE_SIZE}`)
    for (let i = 0; i < 20; i++) fireEvent.click(screen.getByRole('button', { name: 'Next rows' }))
    expect(screen.getByText('Rows 1101–1200 of 1200')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next rows' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Previous rows' }))
    expect(screen.getByText('Rows 1001–1100 of 1200')).toBeInTheDocument()
  })

  it('does not paginate at 500 rows or fewer', () => {
    expect(chartTableModel(MATRIX).rows).toHaveLength(2)
    const exact: SeriesChart = {
      kind: 'series',
      dimensions: ['x'],
      x_type: 'category',
      series: [{ key: 'v', label: 'V', points: Array.from({ length: 500 }, (_, i) => ({ x: `p${i}`, y: i, n: 1 })) }],
    }
    render(
      <ChartFrame title="Exact" headingLevel={2} state={{ status: 'ready', data: exact, meta: null, revalidating: false }} series={exact}>
        <div />
      </ChartFrame>,
    )
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(501)
    expect(screen.queryByRole('button', { name: 'Next rows' })).toBeNull()
  })
})

describe('ChartFrame — one page-level announcer (no per-frame live regions)', () => {
  afterEach(() => {
    vi.useRealTimers()
    plotted.length = 0
  })

  const TITLES = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo']
  const scaled = (v: number): MatrixChart => ({
    ...MATRIX,
    cells: MATRIX.cells.map((c) => ({ ...c, value: c.value === null ? null : v })),
  })
  const readyOf = (series: MatrixChart): ChartState => ({ status: 'ready', data: series, meta: null, revalidating: false })

  /** Every text the element held, in order (a live region is heard on each change). */
  function recordText(node: Element): string[] {
    const seen: string[] = []
    const observer = new MutationObserver(() => {
      const text = node.textContent ?? ''
      if (text && text !== seen[seen.length - 1]) seen.push(text)
    })
    observer.observe(node, { childList: true, characterData: true, subtree: true })
    return seen
  }

  function page(states: Record<string, ChartState>, series: MatrixChart = MATRIX) {
    return (
      <ChartAnnouncerProvider>
        {TITLES.filter((t) => states[t]).map((title) => (
          <ChartFrame key={title} title={title} headingLevel={2} state={states[title]} series={series} chartType="Heatmap">
            <div />
          </ChartFrame>
        ))}
      </ChartAnnouncerProvider>
    )
  }

  it('N frames re-rendering → exactly ONE short announcement, debounced; the summary stays in aria-describedby', async () => {
    vi.useFakeTimers()
    const all = (series: MatrixChart) => Object.fromEntries(TITLES.map((t) => [t, readyOf(series)]))
    const { rerender, container } = render(page(all(MATRIX)))
    // One polite region for the whole page, none inside any frame.
    const polite = container.querySelectorAll('[role="status"], [aria-live="polite"]')
    expect(polite).toHaveLength(1)
    for (const frameNode of container.querySelectorAll('[data-chart-frame]')) {
      expect(frameNode.querySelector('[role="status"], [role="alert"], [aria-live]')).toBeNull()
    }
    const live = polite[0]
    const heard = recordText(live)
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS * 2))
    expect(live).toHaveTextContent('') // the initial load is never announced

    rerender(page(all(scaled(0.1)), scaled(0.1)))
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS / 2))
    rerender(page(all(scaled(0.2)), scaled(0.2)))
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS * 2))
    await act(async () => {})
    expect(heard).toEqual([`${TITLES.length} charts updated`])
    // The long summary is described, never announced.
    expect(live.textContent).not.toContain('Min')
    const group = screen.getByRole('group', { name: 'Alpha' })
    const describedBy = (group.getAttribute('aria-describedby') ?? '').split(' ')
    expect(describedBy.map((id) => document.getElementById(id)?.textContent ?? '').join(' ')).toContain('Min 20.0%')
  })

  it('a single change names the chart and its new state', async () => {
    vi.useFakeTimers()
    const base = Object.fromEntries(TITLES.map((t) => [t, readyOf(MATRIX)]))
    const { rerender, container } = render(page(base))
    const live = container.querySelector('[role="status"]') as HTMLElement
    const heard = recordText(live)
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS * 2))
    rerender(page({ ...base, Bravo: errorState() }))
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS * 2))
    await act(async () => {})
    expect(heard).toEqual(['Bravo chart: error'])
  })

  it('load-time state messages are static text in the frame: no alert, no status role', () => {
    const { container } = render(
      page({
        Alpha: errorState({}, vi.fn()),
        Bravo: { status: 'filtered-empty', meta: META },
        Charlie: { status: 'never-had-data' },
        Delta: { status: 'forbidden', requestId: 'req-1' },
        Echo: { status: 'not-measured', reason: 'r', meta: null },
      }),
    )
    expect(container.querySelectorAll('[data-chart-state-message]')).toHaveLength(5)
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(0)
    expect(container.querySelectorAll('[data-chart-frame] [role="status"], [data-chart-frame] [aria-live]')).toHaveLength(0)
    // …and nothing was announced at load.
    expect(container.querySelector('[role="status"]')).toHaveTextContent('')
  })

  it('only the result of the reader\'s own Retry is announced assertively', async () => {
    vi.useFakeTimers()
    const retry = vi.fn()
    const { rerender, container } = render(page({ Alpha: errorState({}, retry) }))
    const assertive = container.querySelector('[aria-live="assertive"]') as HTMLElement
    expect(assertive).not.toBeNull()
    const heard = recordText(assertive)
    // A change the reader did not ask for is never assertive.
    rerender(page({ Alpha: errorState({ requestId: 'req-43' }, retry) }))
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS * 2))
    expect(heard).toEqual([])

    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.retry }))
    expect(retry).toHaveBeenCalledTimes(1)
    rerender(page({ Alpha: { status: 'loading' } }))
    rerender(page({ Alpha: errorState({ requestId: 'req-44' }, retry) }))
    await act(async () => {})
    expect(heard).toEqual(['Alpha chart: still could not load'])

    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.retry }))
    rerender(page({ Alpha: { status: 'loading' } }))
    rerender(page({ Alpha: readyOf(MATRIX) }))
    await act(async () => {})
    expect(heard).toEqual(['Alpha chart: still could not load', 'Alpha chart: loaded'])
  })

  it('without a provider a frame still renders and announces nothing (no stray live region)', () => {
    const { container } = frame({ state: ready() })
    expect(container.querySelectorAll('[role="status"], [role="alert"], [aria-live]')).toHaveLength(0)
  })
})

describe('ChartFrame + useChartData — the boundary', () => {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
  )

  function Chart({ payload, requestId }: { payload: unknown; requestId: string }) {
    const state = useChartData(['frame', requestId], async () => ({ data: payload, requestId }), {
      validate: validateChartResponse,
      everHadData: true,
    })
    return (
      <ChartFrame title="Bound" headingLevel={2} state={state} series={state.status === 'ready' ? state.data.series : null}>
        {(series) => <FakeRenderer series={series} />}
      </ChartFrame>
    )
  }

  afterEach(() => {
    plotted.length = 0
  })

  it('a payload that fails validation renders the error with its request id and never the chart', async () => {
    render(<Chart payload={{ meta: null, series: { ...MATRIX, cells: [{ x: 9, y: 0, value: 0.5, n: 1 }] } }} requestId="req-bad" />, {
      wrapper,
    })
    const alert = await waitFor(errorMessage)
    expect(alert).toHaveTextContent(CHART_MESSAGES.error)
    expect(within(alert).getByText('req-bad')).toBeInTheDocument()
    expect(screen.queryByTestId('renderer')).toBeNull()
    expect(plotted).toHaveLength(0)
  })

  it('a valid payload is drawn', async () => {
    render(<Chart payload={{ meta: META, series: MATRIX }} requestId="req-good" />, { wrapper })
    await waitFor(() => expect(screen.getByTestId('renderer')).toBeInTheDocument())
    expect(plotted[plotted.length - 1]).toEqual(MATRIX)
  })
})
