/**
 * `/__charts?view=states` — every `ChartFrame` state on one page (VIZ-107,
 * VIZ-105). DEVELOPMENT BUILDS ONLY (rendered by `ChartGalleryPage`).
 *
 * Error states are produced by the REAL classifier from response-shaped
 * objects (status, headers, body), and the "payload fails the contract" frame
 * runs the REAL `useChartData` hook over an in-memory loader, so the page
 * exercises the same code a chart page does — with no backend and no network.
 */
import { useState } from 'react'
import ChartFrame from '@/components/charts/ChartFrame'
import HeatmapChart from '@/components/charts/HeatmapChart'
import type { NumericMatrix } from '@/components/charts/engines/echarts/heatmapOption'
import { StaleBuildError } from '@/components/charts/engines/lazyChartEngine'
import {
  classifyChartError,
  shownCount,
  validateChartResponse,
  type ChartState,
} from '@/components/charts/chartState'
import type { ChartSeries } from '@/lib/viz/contracts'
import { useChartData } from '@/hooks/useChartData'
import { GALLERY_HEATMAP_DATA } from './chartGalleryFixtures'
import {
  INVALID_PARAM,
  INVALID_PAYLOAD,
  NOT_MEASURED_REASON,
  REQUEST_IDS,
  RETRY_AFTER_SECONDS,
  STATE_ITEMS,
  STATES_META,
  TRUNCATED_HEATMAP_DATA,
  TRUNCATED_TOTAL,
  type StateItemId,
} from './chartStatesFixtures'

const FRAME_HEIGHT = 280
const noop = () => {}

function stateFromError(error: unknown): ChartState {
  const classified = classifyChartError(error)
  if (classified.type === 'forbidden') return { status: 'forbidden', requestId: classified.requestId }
  if (classified.type === 'error') return { status: 'error', error: classified.error, retry: noop }
  return { status: 'loading' }
}

const response = (status: number, requestId: string, data: unknown = {}, headers: Record<string, string> = {}) => ({
  response: { status, headers: { 'x-request-id': requestId, ...headers }, data },
})

const heatmapSeries = GALLERY_HEATMAP_DATA as ChartSeries
const truncatedSeries = TRUNCATED_HEATMAP_DATA as ChartSeries

const FIXED_STATES: Record<Exclude<StateItemId, 'invalid-payload'>, ChartState> = {
  loading: { status: 'loading' },
  'never-had-data': { status: 'never-had-data' },
  'filtered-empty': { status: 'filtered-empty', meta: STATES_META },
  'not-measured': {
    status: 'not-measured',
    reason: NOT_MEASURED_REASON,
    meta: { ...STATES_META, measured: false, reason: NOT_MEASURED_REASON },
  },
  error: stateFromError(response(500, REQUEST_IDS.error, { code: 'internal', message: 'boom', request_id: REQUEST_IDS.error })),
  'invalid-param': stateFromError(
    response(422, REQUEST_IDS.invalidParam, {
      code: 'invalid_parameter',
      message: 'Unknown release',
      request_id: REQUEST_IDS.invalidParam,
      detail: { param: INVALID_PARAM },
    }),
  ),
  'rate-limited': stateFromError(response(429, REQUEST_IDS.rateLimited, {}, { 'retry-after': String(RETRY_AFTER_SECONDS) })),
  'stale-build': stateFromError(new StaleBuildError(new TypeError('Failed to fetch dynamically imported module'))),
  forbidden: stateFromError(response(403, REQUEST_IDS.forbidden)),
  truncated: {
    status: 'truncated',
    data: truncatedSeries,
    meta: { ...STATES_META, truncated: true, truncated_total: TRUNCATED_TOTAL },
    shown: shownCount(truncatedSeries),
    total: TRUNCATED_TOTAL,
    revalidating: false,
  },
  ready: { status: 'ready', data: heatmapSeries, meta: STATES_META, revalidating: false },
  'draw-failure': { status: 'ready', data: heatmapSeries, meta: null, revalidating: false },
}

/** A renderer that throws while rendering: its frame's boundary must contain it. */
function ThrowingRenderer(): never {
  throw new Error('Gallery: this renderer throws on purpose')
}

function renderHeatmap(series: ChartSeries, description: string) {
  if (series.kind !== 'matrix' || series.value_type === 'status') return null
  return <HeatmapChart data={series as NumericMatrix} description={description} height={FRAME_HEIGHT} animate={false} />
}

/** The one frame driven by the real hook: its payload fails validation. */
function InvalidPayloadFrame({ title }: { title: string }) {
  const state = useChartData(
    ['__charts', 'invalid-payload'],
    async () => ({ data: INVALID_PAYLOAD, requestId: REQUEST_IDS.invalidPayload }),
    { validate: validateChartResponse, everHadData: true },
  )
  return (
    <ChartFrame title={title} headingLevel={2} state={state} chartType="Heatmap" height={FRAME_HEIGHT}>
      {(series) => renderHeatmap(series, 'This must never be drawn.')}
    </ChartFrame>
  )
}

export default function ChartStatesGallery() {
  const [clearedFilters, setClearedFilters] = useState(0)

  return (
    <div className="grid max-w-full gap-6 lg:grid-cols-2" data-states-cleared={clearedFilters}>
      {STATE_ITEMS.map((item) => {
        let frame
        if (item.id === 'invalid-payload') {
          frame = <InvalidPayloadFrame title={item.title} />
        } else {
          const state = FIXED_STATES[item.id]
          const series = item.id === 'truncated' ? truncatedSeries : heatmapSeries
          frame = (
            <ChartFrame
              title={item.title}
              takeaway={item.id === 'ready' ? 'admin is the lowest suite every day.' : undefined}
              headingLevel={2}
              state={state}
              series={series}
              chartType="Heatmap"
              axes={{ x: 'Day', y: 'Suite' }}
              scopeLabel="project gallery, 2026-09-01 to 2026-09-07"
              height={FRAME_HEIGHT}
              onClearFilters={() => setClearedFilters((n) => n + 1)}
            >
              {item.id === 'draw-failure'
                ? () => <ThrowingRenderer />
                : (s) => renderHeatmap(s, 'Pass rate by suite and day.')}
            </ChartFrame>
          )
        }
        return (
          <div key={item.id} data-state-item={item.id} className="min-w-0">
            {frame}
          </div>
        )
      })}
    </div>
  )
}
