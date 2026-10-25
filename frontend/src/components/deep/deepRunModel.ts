/**
 * W14 AGT-07: Deep Investigation's run configuration, estimate and history, in words (pure; the page and its
 * four components import one string).
 *
 *  - `selectionFromGroups` / `estimateQuery` / `runRequest` — the pre-scan groups a person ticked and the
 *    tighten-only configuration, as the estimate's QUERY (`GET …/estimate`, a count — never ids, never a POST)
 *    and as the trigger's body (`POST /deep-investigate/{run}`: the union of the groups' result ids);
 *  - `costLine` / `minutesText` / `slowestStages` — the measured estimate. Absence is not a zero: an
 *    unmeasured estimate has no minutes and an unpriced model no dollars; a local model's $0.00 IS measured;
 *  - `historyRow` / `enrichRows` — the Recent runs table filled from the server's investigation history.
 */
import { publicPipelineStatus } from '@/types/agent'
import type {
  DeepCost, DeepEstimate, DeepEstimateQuery, DeepEstimateStage, DeepHistoryRow, DeepRunRequest, PreScanGroup,
} from '@/types/agentOps'
import { formatRunWhen } from '@/utils/formatters'

/** `RUN_CONFIG_FAILURE_IDS_MAX` (`core/agent_ops_vocab.py`). */
export const RUN_CONFIG_FAILURE_IDS_MAX = 500
/** `RUN_CONFIG_TOO_MANY_FAILURES`, mirrored. */
export const RUN_CONFIG_TOO_MANY = 'A run configuration takes at most 500 failures: select fewer groups.'
/** `ESTIMATE_LOCAL_NOTE`, mirrored (the server sends its own note; this is the fallback). */
export const ESTIMATE_LOCAL_NOTE = 'local model — no charge'
export const PRESCAN_EMPTY = 'No failing results to pre-scan.'
export const PRESCAN_LOAD_ERROR = 'Could not load the pre-scan.'
export const HISTORY_LOAD_ERROR = 'Could not load the history.'
export const ESTIMATE_LOAD_ERROR = 'Could not load the estimate.'
export const ESTIMATE_NOT_MEASURED = 'Not measured yet: no completed deep investigation in this project'
export const DEEP_ALREADY_IN_PROGRESS = 'Deep investigation already in progress'

/** A pre-scan group's error as one line of text (React text, never markup); a group with no message says so. */
export const groupError = (group: Pick<PreScanGroup, 'representative_error'>) =>
  group.representative_error?.trim() ? group.representative_error : 'No error message'

/** What the configuration controls hold. `null` = the project's own value (nothing tightened). */
export interface DeepRunConfig {
  /** Ticked pre-scan groups, by `signature_id`. */
  selected: readonly string[]
  modelTier: string | null
  maxFailures: number | null
  evidenceSources: readonly string[] | null
}

export const EMPTY_CONFIG: DeepRunConfig = { selected: [], modelTier: null, maxFailures: null, evidenceSources: null }

/** The ticked groups: how many, how many failures, the ids "Run on selection" sends, and whether it may. */
export interface Selection {
  groups: number
  failures: number
  /** The union of the groups' `member_result_ids`, first-seen order. */
  ids: string[]
  /** A ticked group lists only part of its members (more than 500): its ids cannot be sent whole. */
  truncated: boolean
  /** More than 500 failures: the server takes at most 500. */
  tooMany: boolean
}

export function selectionFromGroups(groups: readonly PreScanGroup[], selected: readonly string[]): Selection {
  const picked = groups.filter((g) => selected.includes(g.signature_id))
  const seen = new Set<string>()
  const ids: string[] = []
  for (const group of picked) {
    for (const id of group.member_result_ids) {
      if (!seen.has(id)) {
        seen.add(id)
        ids.push(id)
      }
    }
  }
  const failures = picked.reduce((sum, g) => sum + g.members, 0)
  return {
    groups: picked.length,
    failures,
    ids,
    truncated: picked.some((g) => g.members_truncated),
    tooMany: Math.max(failures, ids.length) > RUN_CONFIG_FAILURE_IDS_MAX,
  }
}

/** The tighten-only fields, as both the estimate query and the trigger body carry them (absent = untouched). */
function tightened(config: DeepRunConfig) {
  return {
    ...(config.maxFailures != null ? { max_failures_analyzed: config.maxFailures } : {}),
    ...(config.modelTier ? { model_tier: config.modelTier } : {}),
    ...(config.evidenceSources ? { evidence_sources: [...config.evidenceSources] } : {}),
  }
}

/** `GET …/estimate`'s query: a COUNT of the selection (no ids), then the tightened fields. Empty on load. */
export function estimateQuery(config: DeepRunConfig, groups: readonly PreScanGroup[]): DeepEstimateQuery {
  const selection = selectionFromGroups(groups, config.selected)
  return {
    ...(selection.groups > 0 ? { selected_failures: selection.failures } : {}),
    ...tightened(config),
  }
}

/** "Run on selection"'s body (beside `mode`): the selected failures' ids and the tightened fields. */
export function runRequest(config: DeepRunConfig, groups: readonly PreScanGroup[]): DeepRunRequest {
  const selection = selectionFromGroups(groups, config.selected)
  return {
    ...(selection.ids.length > 0 ? { failure_ids: selection.ids } : {}),
    ...tightened(config),
  }
}

/** Why "Run on selection" cannot go: nothing ticked, or the 500-failure rule. Null when it can. */
export function selectionBlock(selection: Selection): string | null {
  if (selection.groups === 0) return 'Select at least one group.'
  if (selection.tooMany || selection.truncated) return RUN_CONFIG_TOO_MANY
  return null
}

/** "{g} groups · {n} failures selected". */
export function selectionLine(selection: Selection): string {
  return `${selection.groups} group${selection.groups === 1 ? '' : 's'} · ${selection.failures} failure${selection.failures === 1 ? '' : 's'} selected`
}

// ── The estimate ─────────────────────────────────────────────────────────

/** The cost tile: a measured $ figure, a local model's $0.00, or "—" with the server's reason. */
export interface CostLine {
  value: string
  /** No number: the tile's caption says "cost unknown". */
  unknown: boolean
  /** `local model — no charge` / the unpriced or no-endpoint sentence; null for a priced model. */
  note: string | null
}

export function costLine(cost: DeepCost): CostLine {
  if (cost.local_model) return { value: '$0.00', unknown: false, note: cost.note ?? ESTIMATE_LOCAL_NOTE }
  if (cost.priced && cost.usd != null) return { value: `$${cost.usd.toFixed(2)}`, unknown: false, note: cost.note ?? null }
  return { value: '—', unknown: true, note: cost.note ?? null }
}

/** `~4 min`, only for a measured estimate (never `~0.5 min` from nothing). */
export function minutesText(estimate: Pick<DeepEstimate, 'measured' | 'minutes_p50'>): string | null {
  if (!estimate.measured || estimate.minutes_p50 == null) return null
  const m = estimate.minutes_p50
  const shown = m < 10 ? (Math.round(m * 10) / 10).toString() : Math.round(m).toString()
  return `~${shown} min`
}

/** The caption under the cost: the sample it was measured from, or why there is none. */
export function sampleCaption(estimate: Pick<DeepEstimate, 'measured' | 'sample_pipelines'>): string {
  if (!estimate.measured) return ESTIMATE_NOT_MEASURED
  const n = estimate.sample_pipelines
  return `estimate from ${n} past run${n === 1 ? '' : 's'}`
}

/** The four slowest measured stages (p50 duration, longest first; ties by name). */
export function slowestStages(stages: readonly DeepEstimateStage[], n = 4): DeepEstimateStage[] {
  return stages
    .filter((s) => s.duration_seconds_p50 != null)
    .sort((a, b) => (b.duration_seconds_p50 ?? 0) - (a.duration_seconds_p50 ?? 0) || a.stage_name.localeCompare(b.stage_name))
    .slice(0, n)
}

const seconds = (s: number) => (s < 60 ? `${Math.round(s)} s` : `${(Math.round((s / 60) * 10) / 10).toString()} min`)
const tokenText = (t: number) => (t >= 1000 ? `${(Math.round(t / 100) / 10).toString()}k tok` : `${t} tok`)

/** One stage row: `{stage} · {p50} · {tokens}` (the server's stage name, underscores spaced). */
export function stageLine(stage: DeepEstimateStage): { name: string; detail: string } {
  const hasTokens = stage.input_tokens_p50 != null || stage.output_tokens_p50 != null
  const total = (stage.input_tokens_p50 ?? 0) + (stage.output_tokens_p50 ?? 0)
  return {
    name: stage.stage_name.replace(/_/g, ' '),
    detail: [
      stage.duration_seconds_p50 != null ? seconds(stage.duration_seconds_p50) : '—',
      hasTokens ? tokenText(total) : null,
      // The server scaled this stage's p50 by the failures analysed (PER_FAILURE_STAGES); the row's title says so
      // in full (one short word keeps the row inside the 343 px column on CI's DejaVu).
      stage.per_failure ? 'scaled' : null,
    ].filter(Boolean).join(' · '),
  }
}

// ── The Recent runs table ────────────────────────────────────────────────

/** One row of the Recent runs / Investigated runs table. An unknown investigation fact is null ("—"). */
export interface DeepRunRow {
  runId: string
  runIdLabel: string
  /** Per-(project, suite) run number, as /runs shows it; null for a legacy run. */
  runSeq: number | null
  suiteLabel: string | null
  suiteNames: string[] | null
  whenRel: string
  whenAbs: string
  failures: number
  clusters: number | null
  findings: number | null
  avgConf: number | null
  cost: number | null
  status: 'complete' | 'running' | 'failed' | null
}

/** "3h ago" / "Yesterday" / "4d ago" (the table's relative age). */
export function ageLabel(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return '—'
  const hours = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 3_600_000))
  if (hours < 1) return 'just now'
  if (hours < 24) return `${hours}h ago`
  if (hours < 48) return 'Yesterday'
  return `${Math.floor(hours / 24)}d ago`
}

/** A history row's pipeline as the table's three states. */
export function historyStatus(row: Pick<DeepHistoryRow, 'status' | 'public_status'>): DeepRunRow['status'] {
  const status = publicPipelineStatus(row)
  if (status === 'in_progress') return 'running'
  if (status === 'failed') return 'failed'
  return 'complete'
}

/** A server history row as a table row (the "Investigated runs" mode). */
export function historyRow(row: DeepHistoryRow, now: number = Date.now()): DeepRunRow {
  return {
    runId: row.run_id,
    runIdLabel: row.run_id.slice(0, 8),
    runSeq: row.run_seq ?? null,
    suiteLabel: row.suite_name ?? null,
    suiteNames: row.suite_name ? [row.suite_name] : null,
    whenRel: ageLabel(row.run_created_at, now),
    whenAbs: row.run_created_at ? formatRunWhen(row.run_created_at) : '—',
    failures: row.failures,
    clusters: row.clusters ?? null,
    findings: row.findings,
    avgConf: row.avg_confidence ?? null,
    cost: row.cost_usd ?? null,
    status: historyStatus(row),
  }
}

/**
 * The recent runs, filled from the history where that run was investigated. What the page knows live for the
 * FOCUSED run (its clusters, findings, decision-trail cost, pipeline status) stays; the history fills only what
 * is unknown; a run the history does not list keeps "—".
 */
export function enrichRows(rows: readonly DeepRunRow[], history: readonly DeepHistoryRow[] | undefined): DeepRunRow[] {
  if (!history || history.length === 0) return [...rows]
  const byRun = new Map(history.map((h) => [h.run_id, h]))
  return rows.map((row) => {
    const h = byRun.get(row.runId)
    if (!h) return row
    return {
      ...row,
      clusters: row.clusters ?? h.clusters ?? null,
      findings: row.findings ?? h.findings,
      avgConf: row.avgConf ?? h.avg_confidence ?? null,
      cost: row.cost ?? h.cost_usd ?? null,
      status: row.status ?? historyStatus(h),
    }
  })
}
