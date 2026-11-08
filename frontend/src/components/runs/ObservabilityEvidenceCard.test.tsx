/**
 * W39 STRAT-07 — the "Observability evidence" card (contract §2): one read of the run route (no global toast),
 * loading / error as words, the header + the analysis it shows + the "now" notes, one row per lane in vocabulary
 * order with its state and the server's sentence, plots only for lanes with timed evidence (the run window shaded,
 * Prometheus lines with their sentence, Splunk dots, an OCP bar, the item on hover / focus), the "Without a time"
 * list, and no axis when nothing is timed. The real hook + service run; only the HTTP client is mocked.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LANE_SOURCES, OFFLINE_NOW_SENTENCE, type RunObservabilityTimeline } from '@/types/observabilityTimeline'
import { evidenceTimeline, lane, mixedTimeline, RUN_ID, SENTENCES, timeline } from '@/test/observabilityTimelineMocks'
import ObservabilityEvidenceCard from './ObservabilityEvidenceCard'
import { LOAD_ERROR, NO_ANALYSIS_SENTENCE, RUN_WINDOW_CAPTION } from './observabilityEvidenceText'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

let body: unknown

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

async function renderCard(answer: RunObservabilityTimeline | Error | null = null) {
  if (answer !== null) body = answer
  const wrapper = ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, revalidateOnFocus: false, shouldRetryOnError: false }}>
      {children}
    </SWRConfig>
  )
  const view = render(<ObservabilityEvidenceCard runId={RUN_ID} />, { wrapper })
  await settle()
  return view
}

const card = () => screen.getByRole('region', { name: 'Observability evidence' })
const laneEl = (source: string) => card().querySelector(`[data-lane="${source}"]`) as HTMLElement

beforeEach(() => {
  body = evidenceTimeline()
  get.mockReset()
  get.mockImplementation(() => (body instanceof Error ? Promise.reject(body) : Promise.resolve({ data: body })))
})

describe('ObservabilityEvidenceCard', () => {
  it('asks the run route once, without a global toast; a spinner while it loads', async () => {
    get.mockImplementation(() => new Promise(() => {}))
    await renderCard()
    expect(card()).toHaveAttribute('data-observability-evidence', '')
    expect(card().querySelector('[data-observability-state="loading"]')).not.toBeNull()
    expect(card()).not.toHaveAttribute('data-axis-state')
    expect(get).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledWith(`/api/v1/runs/${RUN_ID}/observability-timeline`, { suppressToast: true })
  })

  it('a failed read is one sentence, not a crash', async () => {
    await renderCard(new Error('boom'))
    expect(within(card()).getByText(LOAD_ERROR)).toHaveAttribute('data-observability-state', 'error')
    expect(card().querySelector('[data-lane]')).toBeNull()
  })

  it('evidence: the header, the analysis and its earlier ones, the "now" note, five lanes in order on one axis', async () => {
    await renderCard()
    const region = card()
    expect(region).toHaveAttribute('data-axis-state', 'timed')
    expect(within(region).getByText('Evidence the AI analysis already fetched — nothing is fetched when you open this card.')).toBeInTheDocument()
    expect(region.querySelector('[data-observability-analysis]')).toHaveAttribute('data-observability-analysis', 'pipeline-sentinel-1')
    expect(region.querySelector('[data-observability-analysed]')?.textContent).toMatch(/^From the analysis of .+\.$/)
    expect(region.querySelector('[data-observability-others]')).toHaveTextContent('2 earlier analyses not shown.')
    expect(region.querySelector('[data-now="offline"]')).toBeNull()
    expect(region.querySelector('[data-now="configured"]')).toHaveTextContent(
      'Now Splunk and Prometheus are configured for this project; OpenShift is not.',
    )
    expect([...region.querySelectorAll('[data-lane]')].map((el) => el.getAttribute('data-lane'))).toEqual([...LANE_SOURCES])
    expect([...region.querySelectorAll('[data-lane]')].every((el) => el.getAttribute('data-lane-state') === 'evidence')).toBe(true)
    // ONE axis for every lane; the run window shaded behind each plot.
    expect(region.querySelectorAll('[data-evidence-axis]')).toHaveLength(1)
    expect(region.querySelectorAll('[data-evidence-tick]').length).toBeGreaterThan(1)
    expect(region.querySelectorAll('[data-evidence-run-window]').length).toBeGreaterThan(0)
    expect(region.querySelector('[data-evidence-caption]')).toHaveTextContent(RUN_WINDOW_CAPTION)
    expect(within(laneEl('ocp_events')).getByText('Fetched at ingest')).toHaveAttribute('data-lane-fetched-by', 'ingest')
  })

  it('Prometheus: one small line per series with its sentence; a null sample is a gap (two line pieces)', async () => {
    await renderCard()
    const prom = laneEl('prometheus')
    const series = [...prom.querySelectorAll('[data-evidence-series]')] as HTMLElement[]
    expect(series.map((s) => s.getAttribute('data-evidence-series'))).toEqual(['p95 latency', '5xx error rate'])
    expect(series[0]).toHaveAttribute('data-series-rose', 'true')
    expect(series[0]).toHaveTextContent('p95 latency (seconds) — p95 latency for checkout rose 3.1× in the failure window')
    expect(within(series[0]).getByRole('img', { name: 'p95 latency: 4 points' })).toBeInTheDocument()
    expect(series[0].querySelectorAll('[data-evidence-line]')).toHaveLength(2)
    expect(prom.querySelector('[data-untimed]')).toBeNull()
  })

  it('Splunk items are dots, an OCP first → last is a bar; hover or focus names the item', async () => {
    await renderCard()
    const trace = laneEl('splunk_trace')
    const dot = trace.querySelector('[data-evidence-mark="instant"]') as HTMLElement
    expect(dot.querySelector('title')?.textContent).toMatch(/\[ERROR\] — gateway: upstream timeout$/)
    const bar = laneEl('ocp_events').querySelector('[data-evidence-mark="span"]') as HTMLElement
    expect(bar.querySelector('rect')).not.toBeNull()
    expect(bar.querySelector('title')?.textContent).toMatch(/ → .* — BackOff — Back-off restarting failed container — Seen 6×$/)
    const tip = () => card().querySelector('[data-evidence-tip]') as HTMLElement
    // Idle: a no-break space only (it keeps the line's height).
    expect(tip().textContent).toBe(' ')
    fireEvent.mouseEnter(dot)
    expect(tip()).toHaveTextContent('gateway: upstream timeout')
    fireEvent.mouseLeave(dot)
    expect(tip().textContent).toBe(' ')
    fireEvent.focus(bar)
    expect(tip()).toHaveTextContent('BackOff')
    const assumed = laneEl('splunk_per_test').querySelector('[data-evidence-mark="instant"] title')
    expect(assumed?.textContent).toMatch(/Test: test_pay — No time zone was stored; read as UTC\.$/)
  })

  it('the "Without a time" lists: the server\'s untimed items with their reason, never placed on the axis', async () => {
    await renderCard()
    const perTest = laneEl('splunk_per_test')
    const list = perTest.querySelector('[data-untimed]') as HTMLElement
    expect(within(list).getByRole('heading', { name: 'Without a time' })).toBeInTheDocument()
    const item = list.querySelector('[data-untimed-item]') as HTMLElement
    expect(item).toHaveAttribute('data-untimed-item', 'time_unreadable')
    expect(item).toHaveTextContent('[REDACTED_PHONE] [ERROR] retry budget spent · test_pay — the stored time could not be read')
    expect(perTest.querySelectorAll('[data-evidence-mark]')).toHaveLength(1)
    const ocp = laneEl('ocp_per_test')
    expect(ocp.querySelector('[data-evidence-plot]')).toBeNull()
    expect(ocp.querySelector('[data-untimed-item]')).toHaveAttribute('data-untimed-item', 'no_time_stored')
    expect(ocp.querySelector('[data-untimed-detail]')).toHaveTextContent('[BackOff] Back-off restarting failed container (count: 6)')
  })

  it('mixed honest states: each lane its state and sentence; only the lane with evidence draws; "now" offline', async () => {
    await renderCard(mixedTimeline())
    const states = Object.fromEntries([...card().querySelectorAll('[data-lane]')].map((el) => [el.getAttribute('data-lane'), el.getAttribute('data-lane-state')]))
    expect(states).toEqual({
      prometheus: 'none_found', splunk_trace: 'unavailable', ocp_events: 'not_recorded', splunk_per_test: 'evidence', ocp_per_test: 'not_analysed',
    })
    expect(laneEl('prometheus').querySelector('[data-lane-sentence]')).toHaveTextContent(SENTENCES.prometheus_none_found)
    expect(laneEl('splunk_trace').querySelector('[data-lane-sentence]')).toHaveTextContent(SENTENCES.splunk_trace_unavailable)
    expect(laneEl('ocp_events').querySelector('[data-lane-sentence]')).toHaveTextContent(SENTENCES.ocp_events_not_recorded)
    for (const source of ['prometheus', 'splunk_trace', 'ocp_events', 'ocp_per_test']) {
      expect(laneEl(source).querySelector('svg'), source).toBeNull()
    }
    expect(laneEl('splunk_per_test').querySelector('[data-evidence-plot="splunk_per_test"]')).not.toBeNull()
    expect(card().querySelector('[data-now="offline"]')).toHaveTextContent(`Now ${OFFLINE_NOW_SENTENCE}`)
    expect(card().querySelector('[data-now="configured"]')).toHaveTextContent('Splunk, OpenShift and Prometheus are not configured for this project.')
    expect(card().querySelector('[data-observability-others]')).toBeNull()
  })

  it('a lane that is not "evidence" draws nothing, even if the answer carried items for it', async () => {
    const answer = mixedTimeline()
    answer.lanes[1] = lane('splunk_trace', 'unavailable', { events: [{ ...answer.lanes[3].events[0], title: 'stray item' }] })
    await renderCard(answer)
    expect(laneEl('splunk_trace').querySelector('svg')).toBeNull()
    expect(laneEl('splunk_trace')).not.toHaveTextContent('stray item')
    expect(laneEl('splunk_per_test').querySelector('[data-evidence-plot]')).not.toBeNull()
  })

  it('not analysed: the sentence for the missing analysis, every lane its sentence only, no axis', async () => {
    await renderCard(timeline())
    expect(card()).toHaveAttribute('data-axis-state', 'none')
    expect(card().querySelector('[data-observability-analysis]')).toHaveTextContent(NO_ANALYSIS_SENTENCE)
    expect(card().querySelector('[data-evidence-axis]')).toBeNull()
    expect(card().querySelector('[data-lanes] svg')).toBeNull()
    expect(card().querySelector('[data-evidence-tip]')).toBeNull()
    expect([...card().querySelectorAll('[data-lane-state]')].map((el) => el.getAttribute('data-lane-state'))).toEqual(Array(5).fill('not_analysed'))
    expect(laneEl('splunk_per_test')).toHaveTextContent(SENTENCES.splunk_per_test_not_analysed)
  })

  it('only untimed evidence: no axis, no plot (data-axis-state "untimed"), the list still shown', async () => {
    const untimed = timeline({
      axis: null,
      lanes: LANE_SOURCES.map((s) => (s === 'ocp_per_test'
        ? lane(s, 'evidence', { untimed: [{ title: 'Pod: checkout-sentinel', detail: null, test_case_id: null, test_name: null, reason: 'no_time_stored' }] })
        : lane(s, 'not_recorded'))),
    })
    await renderCard(untimed)
    expect(card()).toHaveAttribute('data-axis-state', 'untimed')
    expect(card().querySelector('[data-evidence-axis]')).toBeNull()
    expect(card().querySelector('[data-lanes] svg')).toBeNull()
    expect(laneEl('ocp_per_test').querySelector('[data-untimed-item]')).toHaveTextContent('Pod: checkout-sentinel — no time was stored')
  })

  it('a truncated lane says so; a long untimed list shows 25 and counts the rest', async () => {
    const many = Array.from({ length: 30 }, (_, i) => ({ title: `check ${i}`, detail: null, test_case_id: null, test_name: null, reason: 'no_time_stored' as const }))
    await renderCard(timeline({ lanes: LANE_SOURCES.map((s) => (s === 'ocp_per_test' ? lane(s, 'evidence', { truncated: true, untimed: many }) : lane(s, 'not_recorded'))) }))
    const ocp = laneEl('ocp_per_test')
    expect(ocp.querySelectorAll('[data-untimed-item]')).toHaveLength(25)
    expect(ocp.querySelector('[data-untimed-more]')).toHaveTextContent('5 more without a time are not listed here.')
    expect(ocp.querySelector('[data-lane-truncated]')).toHaveTextContent('Some items were left out to keep this lane readable.')
  })
})
