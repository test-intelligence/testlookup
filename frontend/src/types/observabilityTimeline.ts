/**
 * W39 STRAT-07 (first slice): a run's observability evidence — the Splunk / OpenShift / Prometheus evidence the AI
 * analysis ALREADY fetched, read from what is stored (`GET /api/v1/runs/{run_id}/observability-timeline`). Nothing
 * is fetched when someone opens it: no fetcher, tool or LLM runs, nothing is written.
 *
 * The constants mirror `backend/app/services/observability_timeline_vocab.py` value for value (pinned by
 * `backend/tests/test_observability_timeline_vocab_parity.py`, which reads this file as TEXT — keep every constant a
 * plain literal on its own `export const` line, strings in one piece, records as one `key: 'value',` line per entry
 * closed by a column-0 `} as const`, no comment inside a record).
 *
 * Absence is not health: every lane says in words why it holds nothing (`state` + the server's `sentence`), and a
 * time that could not be read is never invented — the item goes to the lane's `untimed` list. Every string is
 * rendered as React text (titles and details come from stored tool output).
 */

/** The five lanes, in the order the card draws them (contract D-2). */
export const LANE_SOURCES = ['prometheus', 'splunk_trace', 'ocp_events', 'splunk_per_test', 'ocp_per_test'] as const
export type LaneSource = (typeof LANE_SOURCES)[number]

/** A lane's honest state (contract D-4). */
export const LANE_STATES = ['evidence', 'none_found', 'not_recorded', 'not_analysed', 'unavailable'] as const
export type LaneState = (typeof LANE_STATES)[number]

/** Which step fetched a lane's evidence, long before the card was opened. */
export const FETCHED_BY = ['analysis', 'ingest', 'per_test_analysis'] as const
export type FetchedBy = (typeof FETCHED_BY)[number]

/** Why an item is listed "Without a time" instead of being placed on the axis. */
export const UNTIMED_REASONS = ['no_time_stored', 'time_unreadable'] as const
export type UntimedReason = (typeof UNTIMED_REASONS)[number]

export const LANE_LABELS = {
  prometheus: 'Prometheus metrics',
  splunk_trace: 'Splunk trace (deep analysis)',
  ocp_events: 'OpenShift pod events (at ingest)',
  splunk_per_test: 'Splunk logs (per-test analysis)',
  ocp_per_test: 'OpenShift pod checks (per-test analysis)',
} as const

export const LANE_FETCHED_BY = {
  prometheus: 'analysis',
  splunk_trace: 'analysis',
  ocp_events: 'ingest',
  splunk_per_test: 'per_test_analysis',
  ocp_per_test: 'per_test_analysis',
} as const

/** The lane's chip. */
export const FETCHED_BY_LABELS = {
  analysis: 'Fetched by the deep analysis',
  ingest: 'Fetched at ingest',
  per_test_analysis: 'Fetched by the per-test analysis',
} as const

export const UNTIMED_REASON_LABELS = {
  no_time_stored: 'no time was stored',
  time_unreadable: 'the stored time could not be read',
} as const

/** The card's region name (never "Timeline" or "Evidence" alone: those name a region / a tab already). */
export const CARD_TITLE = 'Observability evidence'
export const HEADER_SENTENCE = 'Evidence the AI analysis already fetched — nothing is fetched when you open this card.'
/** `offline_now` — labelled "now", never presented as the state at analysis time. */
export const OFFLINE_NOW_SENTENCE = 'AI offline mode is on, so analyses fetch no observability evidence.'
/** The heading of a lane's untimed list. */
export const UNTIMED_HEADING = 'Without a time'
/** A lane whose items were cut to the stored caps (contract D-7). */
export const TRUNCATED_SENTENCE = 'Some items were left out to keep this lane readable.'
/** An item whose time carried no zone: the fetchers build their windows from the UTC wall clock. */
export const ZONE_ASSUMED_SENTENCE = 'No time zone was stored; read as UTC.'
/** The sentences the server writes for the states D-4 names verbatim (rendered from `lane.sentence`). */
export const SPLUNK_TRACE_EMPTY_SENTENCE = 'Splunk returned no trace events for this run — the stored answer does not say whether Splunk was reachable.'
export const OCP_NOT_ENRICHED_SENTENCE = 'This run was not enriched with OpenShift pod data when it was ingested.'
export const STORE_UNREADABLE_SENTENCE = 'The analysis store could not be read.'

/** `configured_now`'s keys and how the "now" note names them. */
export const CONFIGURED_SOURCES = ['splunk', 'ocp', 'prometheus'] as const
export type ConfiguredSource = (typeof CONFIGURED_SOURCES)[number]
export const CONFIGURED_LABELS = {
  splunk: 'Splunk',
  ocp: 'OpenShift',
  prometheus: 'Prometheus',
} as const

// ── The answer (contract §1.2, `schemas.RunObservabilityTimeline`) ──────────

/** Two UTC ISO instants. */
export interface ObservabilityWindow {
  start: string
  end: string
}

/** The analysis whose snapshot fed the Prometheus and Splunk trace lanes. */
export interface ObservabilityAnalysis {
  pipeline_run_id: string
  analysed_at: string | null
  /** Other stored analyses of this run that the card does not show. */
  other_analyses: number
}

/** Prometheus only: one series' samples, `[epoch_seconds, value | null]`. */
export interface ObservabilitySeries {
  cluster_id: string | null
  label: string
  unit: string
  points: [number, number | null][]
  sentence: string
  rose: boolean
}

/** A timed item: an instant (ISO UTC), or a first → last span (OCP events only). */
export interface ObservabilityEvent {
  at: string
  end: string | null
  zone_assumed: boolean
  title: string
  detail: string | null
  level: string | null
  cluster_id: string | null
  test_case_id: string | null
  test_name: string | null
  count: number | null
}

export interface ObservabilityUntimed {
  title: string
  detail: string | null
  test_case_id: string | null
  test_name: string | null
  reason: UntimedReason
}

export interface ObservabilityLane {
  source: LaneSource
  label: string
  fetched_by: FetchedBy
  state: LaneState
  sentence: string
  truncated: boolean
  series: ObservabilitySeries[]
  events: ObservabilityEvent[]
  untimed: ObservabilityUntimed[]
}

export interface RunObservabilityTimeline {
  run_id: string
  /** The run's own start / end; null when unknown. */
  run_window: ObservabilityWindow | null
  /** The snapshot used for `prometheus` / `splunk_trace`; null when the run was never analysed. */
  analysis: ObservabilityAnalysis | null
  offline_now: boolean
  configured_now: Record<ConfiguredSource, boolean>
  /** Min / max over every timed item and the run window; null when nothing is timed. */
  axis: ObservabilityWindow | null
  /** Always the five sources, in `LANE_SOURCES` order. */
  lanes: ObservabilityLane[]
}
