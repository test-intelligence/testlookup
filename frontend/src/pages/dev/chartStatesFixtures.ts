/**
 * Fixed data for the chart-STATES view of the DEV gallery (`/__charts?view=states`,
 * VIZ-107 / VIZ-105): one `ChartFrame` per state, so every state's copy,
 * action and geometry is on one page for the e2e spec, axe and a screenshot.
 *
 * Same rules as `chartGalleryFixtures.ts`: literals only (no clock, no random,
 * no fetch), and no runtime app imports — the Playwright spec imports this file
 * in plain Node. `import type` is erased, so it is allowed.
 */
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import { GALLERY_HEATMAP_DATA, type GalleryMatrix } from './chartGalleryFixtures'

export const STATES_VIEW_PARAM = 'states'

/** A complete C2 envelope; the frames' "N of M" footers read `totals`. */
export const STATES_META: EnvelopeMeta = {
  schema_version: 1,
  scope: {
    projects: [{ id: 'p-gallery', name: 'gallery' }],
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

export const NOT_MEASURED_REASON = 'Pass rate is not measured for runs ingested before 2026-09-01.'

/** The top 4 suites of 6: a truncated heatmap. */
export const TRUNCATED_HEATMAP_DATA: GalleryMatrix = {
  ...GALLERY_HEATMAP_DATA,
  y_labels: GALLERY_HEATMAP_DATA.y_labels.slice(0, 4),
  cells: GALLERY_HEATMAP_DATA.cells.filter((cell) => cell.y < 4),
}
export const TRUNCATED_TOTAL = 6

export const REQUEST_IDS = {
  error: 'req-gallery-500-7f3a',
  invalidPayload: 'req-gallery-invalid-9c21',
  forbidden: 'req-gallery-403-11de',
  invalidParam: 'req-gallery-422-5b0e',
  rateLimited: 'req-gallery-429-e4d2',
} as const

/** The parameter the 422 frame names. */
export const INVALID_PARAM = 'release_id'
export const RETRY_AFTER_SECONDS = 30

/**
 * A payload that FAILS the contract: a cell points past the end of `x_labels`
 * (`cell_index_range`). The frame driven by it must show an error with its
 * request id and must NOT draw a heatmap.
 */
export const INVALID_PAYLOAD = {
  meta: null,
  series: {
    kind: 'matrix',
    value_type: 'rate',
    x_labels: ['09-01'],
    y_labels: ['auth'],
    cells: [{ x: 3, y: 0, value: 0.9, n: 10 }],
  },
}

export type StateItemId =
  | 'loading'
  | 'never-had-data'
  | 'filtered-empty'
  | 'not-measured'
  | 'error'
  | 'invalid-payload'
  | 'invalid-param'
  | 'rate-limited'
  | 'stale-build'
  | 'forbidden'
  | 'truncated'
  | 'ready'
  | 'draw-failure'

export interface StateItem {
  id: StateItemId
  title: string
  /** The `data-chart-state` the frame must carry. */
  state: 'loading' | 'never-had-data' | 'filtered-empty' | 'not-measured' | 'error' | 'forbidden' | 'truncated' | 'ready'
  /** Text the frame must show (asserted by the e2e spec). */
  expectText: string[]
  /** Whether a chart must actually be drawn in this frame. */
  draws: boolean
}

export const STATE_ITEMS: StateItem[] = [
  { id: 'loading', title: 'Loading', state: 'loading', expectText: [], draws: false },
  {
    id: 'never-had-data',
    title: 'Never had data',
    state: 'never-had-data',
    expectText: ['No runs have been ingested for this project yet', 'Ingest test results'],
    draws: false,
  },
  {
    id: 'filtered-empty',
    title: 'Filtered empty',
    state: 'filtered-empty',
    expectText: ['No data matches the current filters', 'Clear filters'],
    draws: false,
  },
  {
    id: 'not-measured',
    title: 'Not measured',
    state: 'not-measured',
    expectText: ['—', NOT_MEASURED_REASON],
    draws: false,
  },
  {
    id: 'error',
    title: 'Server error',
    state: 'error',
    expectText: ['Could not load this chart', REQUEST_IDS.error, 'Retry'],
    draws: false,
  },
  {
    id: 'invalid-payload',
    title: 'Payload fails the contract',
    state: 'error',
    expectText: ['Could not load this chart', REQUEST_IDS.invalidPayload],
    draws: false,
  },
  {
    id: 'invalid-param',
    title: 'Rejected filter value',
    state: 'error',
    expectText: ['Could not load this chart', INVALID_PARAM, REQUEST_IDS.invalidParam],
    draws: false,
  },
  {
    id: 'rate-limited',
    title: 'Rate limited',
    state: 'error',
    expectText: ['Waiting to retry', `${RETRY_AFTER_SECONDS} s`],
    draws: false,
  },
  {
    id: 'stale-build',
    title: 'Stale build',
    state: 'error',
    expectText: ['A new version is available', 'Reload'],
    draws: false,
  },
  {
    id: 'forbidden',
    title: 'Forbidden',
    state: 'forbidden',
    expectText: ['You do not have access to this project'],
    draws: false,
  },
  {
    id: 'truncated',
    title: 'Truncated',
    state: 'truncated',
    expectText: [`Showing top ${TRUNCATED_HEATMAP_DATA.y_labels.length} of ${TRUNCATED_TOTAL}`],
    draws: true,
  },
  { id: 'ready', title: 'Ready', state: 'ready', expectText: ['42 of 50 runs'], draws: true },
  {
    id: 'draw-failure',
    title: 'Renderer throws',
    state: 'ready',
    expectText: ['This chart could not be drawn.'],
    draws: false,
  },
]

export const STATE_ITEM_IDS: StateItemId[] = STATE_ITEMS.map((item) => item.id)
