/**
 * The frame's markup in every state, serialised — for the regression test that
 * full screen (VIZ-608) left the frame outside full screen exactly as it was.
 *
 * The committed file snapshot `__snapshots__/ChartFrame.outside-fullscreen.html`
 * was written by running THESE scenarios against the ChartFrame of main
 * 1862daa7 (before full screen, export and the zoom note existed). The test
 * runs them against today's frame, takes out the controls added since (the
 * full-screen toggle, the export menu, and a toolbar that holds nothing else),
 * and must produce the same bytes. `useId` values are renamed in order of
 * appearance, so the comparison is of structure, attributes and text.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import type { EnvelopeMeta, MatrixChart } from '@/lib/viz/contracts'
import type { ChartFrameProps } from '../ChartFrame'
import type { ChartState } from '../chartState'

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
  x_labels: ['d1', 'd2'],
  y_labels: ['auth', 'cart'],
  cells: [
    { x: 0, y: 0, value: 0.5, n: 2 },
    { x: 1, y: 0, value: 1, n: 2 },
    { x: 0, y: 1, value: 0.25, n: 4 },
    { x: 1, y: 1, value: 0.9, n: 10 },
  ],
}

type Frame = ComponentType<ChartFrameProps>

interface Scenario {
  name: string
  props: Omit<ChartFrameProps, 'title' | 'headingLevel' | 'children'> & { state: ChartState }
  /** A click to make before serialising (by accessible name). */
  click?: string
}

const ready: ChartState = { status: 'ready', data: MATRIX, meta: META, revalidating: false }

export const FRAME_SCENARIOS: Scenario[] = [
  {
    name: 'ready, everything',
    props: {
      state: ready,
      takeaway: 'Down 3.1 pts in 7 days',
      series: MATRIX,
      chartType: 'Heatmap',
      axes: { x: 'Day', y: 'Suite' },
      scope: <span>Scope badge</span>,
      toolbar: <button type="button">Caller action</button>,
      footer: <span>as of today</span>,
    },
  },
  { name: 'ready, table open', props: { state: ready, series: MATRIX, chartType: 'Heatmap' }, click: 'View as table' },
  { name: 'ready, no series, no toolbar', props: { state: ready } },
  { name: 'revalidating', props: { state: { ...ready, revalidating: true }, series: MATRIX } },
  {
    name: 'truncated',
    props: {
      state: { status: 'truncated', data: MATRIX, meta: META, shown: 2, total: 9, revalidating: false },
      series: MATRIX,
    },
  },
  { name: 'loading', props: { state: { status: 'loading' }, height: 300 } },
  {
    name: 'error',
    props: {
      state: {
        status: 'error',
        error: { kind: 'server', message: 'The server could not produce this chart.', requestId: 'req-42', status: 500 },
        retry: () => {},
      },
    },
  },
  { name: 'filtered-empty', props: { state: { status: 'filtered-empty', meta: META }, onClearFilters: () => {} } },
  { name: 'never-had-data', props: { state: { status: 'never-had-data' } } },
  { name: 'not-measured', props: { state: { status: 'not-measured', reason: 'No baseline yet.', meta: null } } },
  { name: 'forbidden', props: { state: { status: 'forbidden', requestId: 'req-403' } } },
]

/** Controls added to the frame after the baseline; removed before comparing. */
const ADDED_SINCE_BASELINE = '[data-chart-fullscreen-toggle], [data-chart-export]'

function normalise(root: HTMLElement): string {
  const clone = root.cloneNode(true) as HTMLElement
  for (const node of clone.querySelectorAll(ADDED_SINCE_BASELINE)) node.remove()
  for (const toolbar of clone.querySelectorAll('[data-chart-toolbar]')) {
    if (toolbar.children.length === 0) toolbar.remove()
  }
  let html = clone.innerHTML
  const ids = Array.from(clone.querySelectorAll('[id]'), (el) => el.id)
  ids.forEach((id, i) => {
    html = html.split(id).join(`ID${i + 1}`)
  })
  return html.replace(/></g, '>\n<')
}

/** Every scenario rendered with `Frame`, serialised and joined. */
export function serialiseFrameScenarios(Frame: Frame): string {
  const out: string[] = []
  for (const scenario of FRAME_SCENARIOS) {
    const { container, unmount } = render(
      <Frame title="Pass rate by suite" headingLevel={3} {...scenario.props}>
        <div data-testid="renderer">plot</div>
      </Frame>,
    )
    if (scenario.click) {
      const name = scenario.click
      act(() => {
        fireEvent.click(screen.getByRole('button', { name }))
      })
    }
    out.push(`<!-- ${scenario.name} -->\n${normalise(container)}`)
    unmount()
  }
  return `${out.join('\n\n')}\n`
}
