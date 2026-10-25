/**
 * W14 AGT-05 / AGT-08: the copy and the pure rules of the pipeline views —
 * the Audit tab (the replay document), the Compare tab (two pipelines of one
 * run) and the workflow line above the AI report. Components import their
 * sentences from here, so a test asserts one string.
 *
 * The input types name only the fields read here (structural: the
 * `types/agentOps.ts` shapes fit).
 */
import { publicPipelineStatus, PUBLIC_PIPELINE_STATUS_LABEL } from '@/types/agent'
import { formatCompactDateTime, formatNumber } from '@/utils/formatters'

// ── Audit ─────────────────────────────────────────────────────────

export interface AuditGapsLike {
  missing_start_events?: readonly string[] | null
  missing_terminal_events?: readonly string[] | null
  missing_replay_checksums?: readonly string[] | null
  missing_checkpoints?: readonly string[] | null
  missing_final_state_checksum?: boolean | null
}

export interface ReplayIntegrityLike {
  replayable?: boolean | null
  audit_gaps?: AuditGapsLike | null
}

export const AUDIT_LOAD_ERROR = 'Could not load the audit document.'
export const AUDIT_REPLAYABLE = 'Replayable ✓'
export const AUDIT_NO_FINAL_CHECKSUM = 'No final-state checksum recorded.'
/** The events table shows the newest this many; `Show all N` reveals the rest. */
export const AUDIT_EVENTS_SHOWN = 200

const GAP_KINDS: ReadonlyArray<[keyof AuditGapsLike, string]> = [
  ['missing_start_events', 'Missing start events'],
  ['missing_terminal_events', 'Missing terminal events'],
  ['missing_replay_checksums', 'Missing replay checksums'],
  ['missing_checkpoints', 'Missing checkpoints'],
]

/** One line per NON-EMPTY gap list (+ the missing final checksum): gaps are data, not an error. */
export function gapLines(gaps: AuditGapsLike | null | undefined): string[] {
  if (!gaps) return []
  const lines: string[] = []
  for (const [key, label] of GAP_KINDS) {
    const list = gaps[key]
    if (Array.isArray(list) && list.length > 0) lines.push(`${label}: ${list.join(', ')}`)
  }
  if (gaps.missing_final_state_checksum) lines.push(AUDIT_NO_FINAL_CHECKSUM)
  return lines
}

/** The four lists' lengths plus 1 for a missing final checksum. */
export function gapCount(gaps: AuditGapsLike | null | undefined): number {
  if (!gaps) return 0
  let n = 0
  for (const [key] of GAP_KINDS) {
    const list = gaps[key]
    if (Array.isArray(list)) n += list.length
  }
  return n + (gaps.missing_final_state_checksum ? 1 : 0)
}

function gapsWord(n: number): string {
  return `${n} gap${n === 1 ? '' : 's'}`
}

/** The Audit tab's badge, from the timeline the page already holds: `replayable ✓` / `N gaps`; null when unknown. */
export function auditBadge(integrity: ReplayIntegrityLike | null | undefined): string | null {
  if (!integrity) return null
  if (integrity.replayable) return 'replayable ✓'
  return gapsWord(gapCount(integrity.audit_gaps))
}

/** The Audit view's header line. */
export function auditHeader(replay: { replayable?: boolean | null; audit_gaps?: AuditGapsLike | null }): string {
  if (replay.replayable) return AUDIT_REPLAYABLE
  return `Not fully replayable · ${gapsWord(gapCount(replay.audit_gaps))}`
}

/** `analysis mode: {requested} → {resolved}`, each `—` when absent. */
export function analysisModeLine(requested?: string | null, resolved?: string | null): string {
  return `analysis mode: ${requested || '—'} → ${resolved || '—'}`
}

/** A checksum's first 12 characters (the full value goes in `title`); `—` when absent. */
export function shortChecksum(value: string | null | undefined): string {
  return value ? value.slice(0, 12) : '—'
}

/** A route decision's other scalar keys as `key: value` text; nested values as JSON text. */
export function decisionDetails(decision: Record<string, unknown>): string[] {
  const shown = new Set(['at', 'decision_point', 'chosen'])
  return Object.keys(decision)
    .filter(key => !shown.has(key))
    .sort()
    .map(key => {
      const value = decision[key]
      const text = value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value)
      return `${key}: ${text}`
    })
}

/** A value of a route decision as text (`—` for null / absent). */
export function scalarText(value: unknown): string {
  if (value == null || value === '') return '—'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

// ── Compare ───────────────────────────────────────────────────────

export interface ComparePipelineLike {
  id: string
  test_run_id: string
  workflow_type: string
  status?: string | null
  public_status?: string | null
  created_at: string
  attempt?: number | null
  rerun_of?: string | null
}

export const COMPARE_LOAD_ERROR = 'Could not load the comparison.'
export const COMPARE_NONE_TITLE = 'No other pipeline of this run to compare with'
export const COMPARE_NOT_IN_PIPELINE = 'not in this pipeline'

/** The OTHER pipelines of the selected pipeline's run (hence its project), newest first. */
export function compareCandidates<P extends ComparePipelineLike>(pipelines: readonly P[], selected: ComparePipelineLike): P[] {
  return pipelines
    .filter(p => p.test_run_id === selected.test_run_id && p.id !== selected.id)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : a.id < b.id ? 1 : -1))
}

/** §0.4: the pipeline's `rerun_of` when listed, else the newest OLDER one of the same type, else the newest other one. */
export function defaultCandidate<P extends ComparePipelineLike>(candidates: readonly P[], selected: ComparePipelineLike): P | null {
  if (selected.rerun_of) {
    const origin = candidates.find(p => p.id === selected.rerun_of)
    if (origin) return origin
  }
  const olderSameType = candidates.find(p => p.workflow_type === selected.workflow_type && p.created_at < selected.created_at)
  return olderSameType ?? candidates[0] ?? null
}

/** An option of the picker: `{workflow_type} · {public status} · {compact date}` (+ ` · attempt N` when > 1). */
export function candidateLabel(p: ComparePipelineLike): string {
  const status = PUBLIC_PIPELINE_STATUS_LABEL[publicPipelineStatus(p)].toLowerCase()
  const attempt = p.attempt != null && p.attempt > 1 ? ` · attempt ${p.attempt}` : ''
  return `${p.workflow_type} · ${status} · ${formatCompactDateTime(p.created_at)}${attempt}`
}

export interface CompareCellLike {
  status: string
  duration_seconds?: number | null
  total_tokens?: number | null
  cost_usd?: number | null
  fallback_used?: boolean | null
  confidence_score?: number | null
  output_checksum_sha256?: string | null
}

export interface CompareRowLike {
  stage_name: string
  a?: CompareCellLike | null
  b?: CompareCellLike | null
  changed?: readonly string[] | null
  output_checksum_equal?: boolean | null
}

export type CompareField = 'status' | 'duration_seconds' | 'total_tokens' | 'cost_usd' | 'fallback_used' | 'confidence_score'

/** The table's value columns, in order (Output is its own column). */
export const COMPARE_COLUMNS: ReadonlyArray<{ field: CompareField; label: string }> = [
  { field: 'status', label: 'Status' },
  { field: 'duration_seconds', label: 'Duration' },
  { field: 'total_tokens', label: 'Tokens' },
  { field: 'cost_usd', label: 'Cost' },
  { field: 'fallback_used', label: 'Fallback' },
  { field: 'confidence_score', label: 'Confidence' },
]

function fieldText(cell: CompareCellLike | null | undefined, field: CompareField): string {
  if (!cell) return '—'
  const value = cell[field]
  if (value == null) return '—'
  switch (field) {
    case 'duration_seconds': {
      const s = Math.round(value as number)
      return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
    }
    case 'total_tokens': return formatNumber(value as number)
    case 'cost_usd': {
      const n = value as number
      return `$${n.toFixed(n !== 0 && n < 0.01 ? 4 : 2)}`
    }
    case 'fallback_used': return value ? 'yes' : 'no'
    case 'confidence_score': {
      const n = value as number
      return `${Math.round(n <= 1 ? n * 100 : n)}%`
    }
    default: return String(value)
  }
}

/** One cell: both sides as text, whether they read the same, and whether the server marked the field changed. */
export function cellText(row: CompareRowLike, field: CompareField): { a: string; b: string; equal: boolean; changed: boolean } {
  const a = fieldText(row.a, field)
  const b = fieldText(row.b, field)
  return { a, b, equal: a === b, changed: (row.changed ?? []).includes(field) }
}

/** The Output column: `same` / `changed` / `unknown` (a missing checksum is unknown, not changed). */
export function outputCell(row: CompareRowLike): 'same' | 'changed' | 'unknown' {
  if (row.output_checksum_equal === true) return 'same'
  if (row.output_checksum_equal === false) return 'changed'
  return 'unknown'
}

/** The line above the table. */
export function outputLine(compare: { output: string; changed_stages: number; stages: readonly CompareRowLike[] }): string {
  if (compare.output === 'identical') return 'Output identical'
  if (compare.output === 'changed') {
    const counted = compare.stages.filter(r => r.output_checksum_equal === false || (r.changed ?? []).includes('presence')).length
    const n = counted > 0 ? counted : compare.changed_stages
    return `Output changed in ${n} stage${n === 1 ? '' : 's'}`
  }
  return 'Output unknown: no stage recorded a checksum'
}

// ── The workflow picker (AGT-08) ──────────────────────────────────
//
// Kept here, beside the other copy, rather than in WorkflowPicker.tsx (the
// contract's §4.3 place): a component file that also exports functions trips
// `react-refresh/only-export-components`.

export interface PickerChoicesLike {
  forks: ReadonlyArray<{ workflow_id: string; version: number; name: string; base: string }>
  defaults: ReadonlyArray<{ base: string; source: string; state: string; resolved_ref?: string | null }>
}

export interface PickerOption {
  /** `''` is the project default (the trigger names no workflow). */
  value: string
  label: string
  /** What `triggerPipeline(runId, workflowId?, workflowVersion?)` is called with. */
  workflowId?: string
  workflowVersion?: number
}

export const PICKER_TITLE = 'Trigger a pipeline'
export const WORKFLOWS_LOAD_ERROR = 'Could not load the workflows.'
export const TRIGGER_DEFAULT_TITLE = "Queue the project's default pipeline for the run picked here"
export const CHOOSE_WORKFLOW_TITLE = 'Choose the workflow to queue'

/**
 * The picker's options: the project default first (what **Trigger pipeline**
 * queues), the built-ins (deep only while Settings › AI allows it), then each
 * published fork's newest published version (drafts never execute). A fork on
 * the deep base is offered only when deep is (the server would refuse it).
 */
export function pickerOptions(
  choices: PickerChoicesLike | undefined,
  { deepAvailable }: { deepAvailable: boolean },
): PickerOption[] {
  const offlineDefault = choices?.defaults.find(d => d.base === 'offline')
  const usable = offlineDefault?.source === 'project_default' && offlineDefault.state === 'ok' && offlineDefault.resolved_ref
  const options: PickerOption[] = [
    { value: '', label: usable ? `Project default — ${offlineDefault.resolved_ref}` : 'Project default — built-in offline' },
    { value: 'builtin:offline', label: 'Built-in offline', workflowId: 'offline' },
  ]
  if (deepAvailable) options.push({ value: 'builtin:deep', label: 'Built-in deep', workflowId: 'deep' })
  for (const fork of choices?.forks ?? []) {
    if (fork.base === 'deep' && !deepAvailable) continue
    const ref = `${fork.workflow_id}@${fork.version}`
    options.push({ value: `fork:${ref}`, label: `${fork.name} · ${ref}`, workflowId: fork.workflow_id, workflowVersion: fork.version })
  }
  return options
}

// ── The workflow line (AGT-08) ────────────────────────────────────

const SOURCE_LABEL: Record<string, string> = {
  project_default: 'project default',
  builtin: 'built-in',
  named: 'chosen for this run',
  default_unavailable: 'built-in — the default was unavailable',
}

/** How a pipeline's workflow was chosen, in words (`workflow_default_source`); null when unknown. */
export function sourceLabel(source: string | null | undefined): string | null {
  if (!source) return null
  return SOURCE_LABEL[source] ?? null
}

/** `Workflow {ref} · {how}` from the timeline's two fields; null for a legacy row (no line). */
export function workflowLine(
  timeline: { workflow_ref?: string | null; workflow_source?: string | null } | null | undefined,
): string | null {
  const ref = timeline?.workflow_ref
  if (!ref) return null
  const how = sourceLabel(timeline?.workflow_source)
  return how ? `Workflow ${ref} · ${how}` : `Workflow ${ref}`
}
