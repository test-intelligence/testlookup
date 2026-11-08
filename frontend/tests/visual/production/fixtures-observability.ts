/**
 * W39 STRAT-07: the run's stored observability evidence (`GET /runs/{id}/observability-timeline`, contract §1.2) for
 * `RUN_ID`, shared by the hermetic journey (`tests/ci-e2e/run-observability-evidence.spec.ts`), every spec that opens
 * the Timing tab (through `withTiming`, fail-closed) and the visual region `run-observability-evidence`
 * (`prod-run-timing.visual.spec.ts`). Sentences are the server's (`observability_timeline_vocab.LANE_SENTENCES`).
 *
 *  - `OBSERVABILITY_NOT_ANALYSED`: no deep analysis, no per-test analysis, no OpenShift enrichment — every lane its
 *    sentence, no axis (the default `withTiming` answers);
 *  - `OBSERVABILITY_EVIDENCE`: around the run (`fixtures-timing.ts` `TIMED`, 9 s from T0): a Prometheus series that
 *    rose, two Splunk trace steps, an OpenShift first → last bar and an instant, a per-test Splunk line (zone
 *    assumed) plus one whose time was redacted, a per-test OpenShift check (no time stored);
 *  - `OBSERVABILITY_MIXED`: one honest state per lane (unavailable / unavailable / none found / evidence / not
 *    recorded) with offline mode on now.
 */
import { isoAgo, RUN_ID } from './fixtures'
import { TEST_CASE_ID, TEST_CASE_NAME } from './fixtures-pages'

export const OBSERVABILITY_PATH = `/api/v1/runs/${RUN_ID}/observability-timeline`

// The same T0 as `fixtures-timing.ts` (`at`), spelt out here: that module imports this one for `withTiming`.
const T0 = Date.parse(isoAgo(0, 1.5))
const at = (ms: number) => new Date(T0 + ms).toISOString()
const MIN = 60_000
const epoch = (ms: number) => Date.parse(at(ms)) / 1000

const LABELS = {
  prometheus: ['Prometheus metrics', 'analysis'],
  splunk_trace: ['Splunk trace (deep analysis)', 'analysis'],
  ocp_events: ['OpenShift pod events (at ingest)', 'ingest'],
  splunk_per_test: ['Splunk logs (per-test analysis)', 'per_test_analysis'],
  ocp_per_test: ['OpenShift pod checks (per-test analysis)', 'per_test_analysis'],
} as const
type Source = keyof typeof LABELS

export const SENTENCE = {
  prometheus_evidence: 'Application metrics the deep analysis read from Prometheus around the failures.',
  prometheus_unavailable: 'metrics unavailable: Prometheus timed out',
  prometheus_not_analysed: 'No deep analysis of this run has been stored, so there are no Prometheus metrics.',
  splunk_trace_evidence: 'Log events the deep analysis read from Splunk while it rebuilt the request trace.',
  splunk_trace_unavailable: 'Splunk returned no trace events for this run — the stored answer does not say whether Splunk was reachable.',
  splunk_trace_not_analysed: 'No deep analysis of this run has been stored, so there is no Splunk trace.',
  ocp_events_evidence: "Pod events OpenShift reported for this run's pod when the run was ingested.",
  ocp_events_none_found: "OpenShift reported no events for this run's pod when the run was ingested.",
  ocp_events_not_recorded: 'This run was not enriched with OpenShift pod data when it was ingested.',
  splunk_per_test_evidence: 'Splunk log lines the per-test AI analysis fetched, read from the excerpts it stored.',
  splunk_per_test_not_analysed: 'No test in this run has a per-test AI analysis yet.',
  ocp_per_test_evidence: 'OpenShift pod checks the per-test AI analysis ran. Their stored answers carry no event times, so they are listed without a time.',
  ocp_per_test_not_recorded: 'The per-test AI analysis of this run stored no OpenShift check.',
  ocp_per_test_not_analysed: 'No test in this run has a per-test AI analysis yet.',
} as const

function lane(source: Source, state: string, extra: Record<string, unknown> = {}) {
  const [label, fetchedBy] = LABELS[source]
  return {
    source, label, fetched_by: fetchedBy, state,
    sentence: (SENTENCE as Record<string, string>)[`${source}_${state}`] ?? '',
    truncated: false, series: [], events: [], untimed: [], ...extra,
  }
}

const event = (when: string, title: string, extra: Record<string, unknown> = {}) => ({
  at: when, end: null, zone_assumed: false, title, detail: null, level: null, cluster_id: null, test_case_id: null,
  test_name: null, count: null, ...extra,
})

const answer = (extra: Record<string, unknown>) => ({
  run_id: RUN_ID,
  run_window: { start: at(0), end: at(9000) },
  analysis: null,
  offline_now: false,
  configured_now: { splunk: true, ocp: true, prometheus: true },
  axis: null,
  ...extra,
})

export const OBSERVABILITY_NOT_ANALYSED = answer({
  lanes: [
    lane('prometheus', 'not_analysed'),
    lane('splunk_trace', 'not_analysed'),
    lane('ocp_events', 'not_recorded'),
    lane('splunk_per_test', 'not_analysed'),
    lane('ocp_per_test', 'not_analysed'),
  ],
})

/** p95 latency, one sample a minute from T0 − 6 min to T0 + 2 min, rising after the run started. */
const P95 = [0.21, 0.2, 0.22, 0.21, 0.24, 0.25, 0.62, 0.81, 0.77].map((value, i) => [epoch((i - 6) * MIN), value])

export const OBSERVABILITY_EVIDENCE = answer({
  analysis: { pipeline_run_id: 'pipeline-evidence-sentinel', analysed_at: at(5 * MIN), other_analyses: 1 },
  axis: { start: at(-6 * MIN), end: at(2 * MIN) },
  lanes: [
    lane('prometheus', 'evidence', {
      series: [{
        cluster_id: 'cluster-sentinel-gateway', label: 'p95 latency', unit: 'seconds', points: P95, rose: true,
        sentence: 'p95 latency for checkout rose 3.4× in the failure window',
      }],
    }),
    lane('splunk_trace', 'evidence', {
      events: [
        event(at(4000), 'gateway: upstream timeout after 5000 ms', { level: 'ERROR', cluster_id: 'cluster-sentinel-gateway' }),
        event(at(-1 * MIN), 'payments: retrying the charge call', { level: 'WARN', cluster_id: 'cluster-sentinel-gateway' }),
      ],
    }),
    lane('ocp_events', 'evidence', {
      events: [
        event(at(-3 * MIN), 'BackOff', { end: at(90_000), detail: 'Back-off restarting failed container', count: 4 }),
        event(at(-5 * MIN), 'Pulled', { detail: 'Container image already present on machine', count: 1 }),
      ],
    }),
    lane('splunk_per_test', 'evidence', {
      events: [event(at(5000).replace('Z', '+00:00'), 'Connection refused by payments', {
        level: 'ERROR', zone_assumed: true, test_case_id: TEST_CASE_ID, test_name: TEST_CASE_NAME,
      })],
      untimed: [{ title: '[REDACTED_PHONE] [ERROR] retry budget spent', detail: null, test_case_id: TEST_CASE_ID, test_name: TEST_CASE_NAME, reason: 'time_unreadable' }],
    }),
    lane('ocp_per_test', 'evidence', {
      untimed: [{
        title: 'Pod: checkout-sentinel | Namespace: shop | Phase: Running', detail: '[BackOff] Back-off restarting failed container (count: 4)',
        test_case_id: TEST_CASE_ID, test_name: TEST_CASE_NAME, reason: 'no_time_stored',
      }],
    }),
  ],
})

export const OBSERVABILITY_MIXED = answer({
  analysis: { pipeline_run_id: 'pipeline-mixed-sentinel', analysed_at: at(5 * MIN), other_analyses: 0 },
  axis: { start: at(0), end: at(9000) },
  offline_now: true,
  configured_now: { splunk: true, ocp: false, prometheus: true },
  lanes: [
    lane('prometheus', 'unavailable'),
    lane('splunk_trace', 'unavailable'),
    lane('ocp_events', 'none_found'),
    lane('splunk_per_test', 'evidence', {
      events: [event(at(5000), 'Connection refused by payments', { level: 'ERROR', test_case_id: TEST_CASE_ID, test_name: TEST_CASE_NAME })],
    }),
    lane('ocp_per_test', 'not_recorded'),
  ],
})
