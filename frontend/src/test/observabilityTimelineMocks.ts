/**
 * Fixtures for the W39 STRAT-07 vitests ("Observability evidence" on Run › Timing), in the contract §1.2 wire shape
 * (`RunObservabilityTimeline`). Sentences are the server's (`observability_timeline_vocab.LANE_SENTENCES`).
 *
 * - `lane(source, state, overrides)`: one lane, empty lists, the server's sentence for that state;
 * - `timeline(overrides)`: an answer with all five lanes `not_analysed`, no axis, no analysis;
 * - `evidenceTimeline()`: every kind of item — two Prometheus series (one with a `null` gap), a Splunk trace step, an
 *   OCP first → last span, a per-test Splunk line (zone assumed) plus one untimed line, a per-test OCP check (untimed);
 * - `mixedTimeline()`: one lane per honest state (evidence / none_found / unavailable / not_recorded / not_analysed).
 *
 * Sentinel words, never hex: they cannot collide with a uuid. Times are fixed instants on 2026-10-20 (UTC).
 */
import {
  LANE_FETCHED_BY,
  LANE_LABELS,
  LANE_SOURCES,
  type LaneSource,
  type LaneState,
  type ObservabilityLane,
  type RunObservabilityTimeline,
} from '@/types/observabilityTimeline'

export const RUN_ID = 'run-sentinel-1'
export const T = (clock: string) => `2026-10-20T${clock}Z`
export const epoch = (clock: string) => Date.parse(T(clock)) / 1000

export const SENTENCES: Record<string, string> = {
  prometheus_evidence: 'Application metrics the deep analysis read from Prometheus around the failures.',
  prometheus_none_found: 'Prometheus returned no metric data in the failure window.',
  prometheus_not_analysed: 'No deep analysis of this run has been stored, so there are no Prometheus metrics.',
  splunk_trace_evidence: 'Log events the deep analysis read from Splunk while it rebuilt the request trace.',
  splunk_trace_unavailable:
    'Splunk returned no trace events for this run — the stored answer does not say whether Splunk was reachable.',
  splunk_trace_not_analysed: 'No deep analysis of this run has been stored, so there is no Splunk trace.',
  ocp_events_evidence: "Pod events OpenShift reported for this run's pod when the run was ingested.",
  ocp_events_not_recorded: 'This run was not enriched with OpenShift pod data when it was ingested.',
  ocp_events_not_analysed: 'This run was not enriched with OpenShift pod data when it was ingested.',
  splunk_per_test_evidence: 'Splunk log lines the per-test AI analysis fetched, read from the excerpts it stored.',
  splunk_per_test_not_recorded: 'The per-test AI analysis of this run stored no Splunk look-up.',
  splunk_per_test_not_analysed: 'No test in this run has a per-test AI analysis yet.',
  ocp_per_test_evidence:
    'OpenShift pod checks the per-test AI analysis ran. Their stored answers carry no event times, so they are listed without a time.',
  ocp_per_test_not_analysed: 'No test in this run has a per-test AI analysis yet.',
}

export function lane(source: LaneSource, state: LaneState, overrides: Partial<ObservabilityLane> = {}): ObservabilityLane {
  return {
    source,
    label: LANE_LABELS[source],
    fetched_by: LANE_FETCHED_BY[source],
    state,
    sentence: SENTENCES[`${source}_${state}`] ?? `The ${source} lane is ${state}.`,
    truncated: false,
    series: [],
    events: [],
    untimed: [],
    ...overrides,
  }
}

export function timeline(overrides: Partial<RunObservabilityTimeline> = {}): RunObservabilityTimeline {
  return {
    run_id: RUN_ID,
    run_window: { start: T('10:00:00'), end: T('10:09:00') },
    analysis: null,
    offline_now: false,
    configured_now: { splunk: true, ocp: false, prometheus: true },
    axis: null,
    lanes: LANE_SOURCES.map((source) => lane(source, 'not_analysed')),
    ...overrides,
  }
}

const event = (at: string, title: string, extra: Partial<ObservabilityLane['events'][number]> = {}) => ({
  at, end: null, zone_assumed: false, title, detail: null, level: null, cluster_id: null, test_case_id: null,
  test_name: null, count: null, ...extra,
})

export function evidenceTimeline(): RunObservabilityTimeline {
  return timeline({
    analysis: { pipeline_run_id: 'pipeline-sentinel-1', analysed_at: T('10:12:00'), other_analyses: 2 },
    axis: { start: T('09:58:00'), end: T('10:10:00') },
    lanes: [
      lane('prometheus', 'evidence', {
        series: [
          {
            cluster_id: 'cluster-sentinel-a', label: 'p95 latency', unit: 'seconds', rose: true,
            sentence: 'p95 latency for checkout rose 3.1× in the failure window',
            points: [[epoch('09:58:00'), 0.2], [epoch('10:00:00'), 0.3], [epoch('10:02:00'), null], [epoch('10:04:00'), 0.9], [epoch('10:06:00'), 1.1]],
          },
          {
            cluster_id: 'cluster-sentinel-a', label: '5xx error rate', unit: 'per_second', rose: false,
            sentence: '5xx error rate for checkout stayed within 1.1× of its baseline in the failure window',
            points: [[epoch('09:58:00'), 0.01], [epoch('10:06:00'), 0.01]],
          },
        ],
      }),
      lane('splunk_trace', 'evidence', {
        events: [event(T('10:03:05'), 'gateway: upstream timeout', { level: 'ERROR', cluster_id: 'cluster-sentinel-a' })],
      }),
      lane('ocp_events', 'evidence', {
        events: [event(T('10:01:00'), 'BackOff', { end: T('10:07:30'), detail: 'Back-off restarting failed container', count: 6 })],
      }),
      lane('splunk_per_test', 'evidence', {
        events: [event('2026-10-20T10:04:10+00:00', 'Connection refused by payments', { level: 'ERROR', zone_assumed: true, test_name: 'test_pay' })],
        untimed: [{ title: '[REDACTED_PHONE] [ERROR] retry budget spent', detail: null, test_case_id: 'case-sentinel-1', test_name: 'test_pay', reason: 'time_unreadable' }],
      }),
      lane('ocp_per_test', 'evidence', {
        untimed: [{ title: 'Pod: checkout-sentinel | Phase: Running', detail: '[BackOff] Back-off restarting failed container (count: 6)', test_case_id: 'case-sentinel-1', test_name: 'test_pay', reason: 'no_time_stored' }],
      }),
    ],
  })
}

export function mixedTimeline(): RunObservabilityTimeline {
  return timeline({
    analysis: { pipeline_run_id: 'pipeline-sentinel-2', analysed_at: T('10:12:00'), other_analyses: 0 },
    axis: { start: T('10:00:00'), end: T('10:09:00') },
    offline_now: true,
    configured_now: { splunk: false, ocp: false, prometheus: false },
    lanes: [
      lane('prometheus', 'none_found'),
      lane('splunk_trace', 'unavailable'),
      lane('ocp_events', 'not_recorded'),
      lane('splunk_per_test', 'evidence', { events: [event(T('10:04:10'), 'Connection refused by payments', { level: 'ERROR' })] }),
      lane('ocp_per_test', 'not_analysed'),
    ],
  })
}
