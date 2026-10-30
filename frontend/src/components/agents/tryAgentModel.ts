/**
 * `/agents` › Try an agent (W23c AGT-11): the panel's and the invocation detail's words and pure helpers.
 *
 * The copy lives here so a test imports ONE string; the server's sentences (`INLINE_ERRORS`,
 * `INLINE_SENTENCES`) and caps are the mirror in `types/agentInvoke.ts`. Nothing here reads the network.
 *
 *  - `invokableAgents(catalog)` / `agentOptionLabel(entry)` — the Agent select ("agent.summary.v1 — Summary ·
 *    accepts inline input").
 *  - `parseInline(text)` / `payloadBytes(text, parsed)` / `byteCounter(bytes)` — the JSON editor: a parse error
 *    line, and the size the SERVER measures (the compact JSON the request carries) against its 256 KB cap.
 *  - `requestBody(projectId, agentId, runId, inline?)` — the POST body: `input.payload.test_run_id` is the run
 *    picked on the page (the D18 anchor), `inline_input` only when the inline radio is chosen.
 *  - `refusalLines(error)` — a 413 / 422 / 404 refusal as its sentence, plus Pydantic's `errors` as `loc: msg`
 *    lines (the server never echoes the input value back).
 *  - `statusWord`, `inputLine`, `reviewLine` — the invocation detail's header.
 */
import { isAxiosError } from 'axios'
import { extractErrorMessage } from '@/services/apiErrors'
import {
  INLINE_ERRORS,
  INLINE_INPUT_MAX_BYTES,
  INLINE_SENTENCES,
  type AgentCatalogEntry,
  type AgentInvocation,
  type AgentInvokeBody,
} from '@/types/agentInvoke'
import { formatCompactDateTime } from '@/utils/formatters'

export const PANEL_TITLE = 'Try an agent'
export const OPEN_LABEL = 'Try an agent'
export const CLOSE_LABEL = 'Close'
export const AGENT_LABEL = 'Agent'
export const ANCHOR_LABEL = 'Anchor run'
export const INPUT_LABEL = 'Input'
export const STORED_RADIO = 'Stored run (the server assembles the input)'
export const INLINE_RADIO = 'Inline input (JSON)'
export const INLINE_UNSUPPORTED = 'This agent does not accept inline input in this release.'
export const EDITOR_LABEL = 'Inline input (JSON)'
export const RUN_LABEL = 'Run'
export const RUNNING_LABEL = 'Running…'
export const NO_RUN =
  'Pick a test suite & build above: an invocation always runs against a stored run of the project.'
export const NO_PROJECT = "Pick the run's project in the project picker: the invocation is sent for that project."
export const CATALOG_LOAD_ERROR = 'Could not load the agent catalog.'
export const AGENT_LOAD_ERROR = 'Could not load the agent.'
export const NO_AGENTS = 'No agent can be invoked on its own.'
export const INVOKE_FAILED = 'The agent could not be invoked.'
export const NOT_AN_OBJECT = 'The inline input must be a JSON object.'
export const INVOCATION_LOAD_ERROR = 'Could not load the invocation.'
export const QUEUE_LINK = 'Open the review queue'
export const OUTPUT_TITLE = 'Output'
export const NO_OUTPUT_YET = 'No output yet: the agent is still running.'
export const NO_OUTPUT = 'No output was stored.'
/** Inbox › Approvals: the first cell of an `invocation` review row (ReviewsPage). */
export const INVOCATION_ROW_LABEL = 'Agent invocation (inline input)'
export { INLINE_ERRORS, INLINE_SENTENCES }

/** The agents a person may invoke on their own (the catalog's `invokable`), in the catalog's order. */
export function invokableAgents(catalog: readonly AgentCatalogEntry[] | null | undefined): AgentCatalogEntry[] {
  return (catalog ?? []).filter((entry) => entry.invokable)
}

/** "Summary" for `summary`, "Root cause analysis" for `root_cause_analysis`. */
function stageTitle(stage: string | null | undefined): string {
  const words = (stage ?? '').replace(/_/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : ''
}

/** "agent.summary.v1 — Summary · accepts inline input" */
export function agentOptionLabel(entry: AgentCatalogEntry): string {
  const title = stageTitle(entry.stage_name)
  const base = title ? `${entry.agent_id} — ${title}` : entry.agent_id
  return entry.inline_input_supported ? `${base} · accepts inline input` : base
}

/** UTF-8 bytes of a string. */
export function byteLength(text: string): number {
  return new TextEncoder().encode(text).length
}

export type ParsedInline = { ok: true; value: Record<string, unknown> } | { ok: false; message: string }

/** The editor's text as a JSON object, or the line that says why not ("Not valid JSON: …."). */
export function parseInline(text: string): ParsedInline {
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch (err) {
    const why = err instanceof Error && err.message ? err.message.replace(/\.$/, '') : 'unreadable'
    return { ok: false, message: `Not valid JSON: ${why}.` }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { ok: false, message: NOT_AN_OBJECT }
  return { ok: true, value: value as Record<string, unknown> }
}

/**
 * The bytes the server measures against its cap: the compact JSON the request carries once the text parses
 * (the server's canonical form has the same length — sorted keys, `,` / `:` separators), else the raw text.
 */
export function payloadBytes(text: string, parsed: ParsedInline): number {
  return byteLength(parsed.ok ? JSON.stringify(parsed.value) : text)
}

/** Over the server's cap (`INLINE_INPUT_MAX_BYTES`): the request would be refused with 413. */
export function overCap(bytes: number): boolean {
  return bytes > INLINE_INPUT_MAX_BYTES
}

/** "12.4 KB of 256 KB" (1 KB = 1 024 bytes, the cap is 262 144 bytes). */
export function byteCounter(bytes: number): string {
  const kb = (n: number) => (n / 1024).toFixed(1).replace(/\.0$/, '')
  return `${kb(bytes)} KB of ${kb(INLINE_INPUT_MAX_BYTES)} KB`
}

/** The POST body: the picked run anchors the call (`input.payload`); `inline_input` only when one was given. */
export function requestBody(
  projectId: string,
  agentId: string,
  runId: string,
  inline?: Record<string, unknown>,
): AgentInvokeBody {
  const body: AgentInvokeBody = {
    project_id: projectId,
    input: { agent_id: agentId, payload: { test_run_id: runId } },
    mode: 'async',
  }
  if (inline !== undefined) body.inline_input = inline
  return body
}

interface ErrorItem {
  loc?: unknown
  msg?: unknown
}

function errorItemLine(item: ErrorItem): string {
  const msg = typeof item?.msg === 'string' ? item.msg : ''
  if (!msg) return ''
  const loc = Array.isArray(item.loc) ? item.loc.filter((p) => p !== 'body' && p !== 'inline_input').join('.') : ''
  return loc ? `${loc}: ${msg}` : msg
}

/**
 * A refused invoke, as lines shown under the editor: the server's sentence (`detail.message` — 413
 * `inline_input_too_large`, 422 `inline_input_invalid` / `inline_input_unsupported` / `inline_input_needs_anchor`,
 * 404 `anchor_run_not_found`), then each Pydantic error as `failures.0.confidence_score: Input should be …`.
 * Never "Request failed with status code 422".
 */
export function refusalLines(error: unknown): string[] {
  if (!isAxiosError(error) || !error.response) return [INVOKE_FAILED]
  const data = error.response.data as { detail?: unknown; errors?: unknown } | undefined
  const detail = data?.detail
  const lines: string[] = []
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const report = detail as { message?: unknown; errors?: unknown }
    lines.push(typeof report.message === 'string' && report.message.trim() ? report.message : INVOKE_FAILED)
    const items = Array.isArray(report.errors) ? report.errors : Array.isArray(data?.errors) ? data.errors : []
    for (const item of items as ErrorItem[]) {
      const line = errorItemLine(item)
      if (line) lines.push(line)
    }
    return lines
  }
  if (Array.isArray(detail)) {
    const items = (detail as ErrorItem[]).map(errorItemLine).filter(Boolean)
    return items.length ? [INVOKE_FAILED, ...items] : [INVOKE_FAILED]
  }
  return [extractErrorMessage(detail, INVOKE_FAILED)]
}

const STATUS_WORDS: Record<string, string> = {
  in_progress: 'In progress',
  completed: 'Completed',
  failed: 'Failed',
  passed: 'Passed',
}

/** "In progress" / "Completed" / "Failed" / "Passed" (an unknown status reads as written). */
export function statusWord(inv: Pick<AgentInvocation, 'status'>): string {
  return STATUS_WORDS[inv.status] ?? String(inv.status)
}

/** "Inline (sha256 1a2b3c4d5e6f)" for an inline invocation, "Stored run" otherwise. */
export function inputLine(inv: Pick<AgentInvocation, 'input_kind' | 'input_sha256'>): string {
  if (inv.input_kind !== 'inline') return 'Stored run'
  return inv.input_sha256 ? `Inline (sha256 ${inv.input_sha256.slice(0, 12)})` : 'Inline'
}

/** The review line: awaiting review / accepted (when, never who) / rejected; `null` when there is nothing to review. */
export function reviewLine(inv: Pick<AgentInvocation, 'requires_human_review' | 'review'>): string | null {
  const review = inv.review
  if (!review) return null
  if (review.state === 'pending_review' && inv.requires_human_review) return INLINE_SENTENCES.awaiting_review
  if (review.state === 'accepted') return `Accepted by a reviewer · ${formatCompactDateTime(review.reviewed_at)}`
  if (review.state === 'rejected') return `Rejected by a reviewer · ${formatCompactDateTime(review.reviewed_at)}`
  return null
}

/** The run cell when the page cannot name the run: "Run 3f2a1b9c". */
export function runFallbackLabel(runId: string): string {
  return `Run ${runId.slice(0, 8)}`
}

/** The page's query with `invocation` set (or removed for `null`); every other key kept (`run`, `tab`, …). */
export function withInvocation(params: URLSearchParams, invocationId: string | null): URLSearchParams {
  const next = new URLSearchParams(params)
  if (invocationId) next.set('invocation', invocationId)
  else next.delete('invocation')
  return next
}

/** The summary's lead paragraph when the output carries one (`executive_summary`), else `null`. */
export function executiveSummary(output: Record<string, unknown> | null | undefined): string | null {
  const text = output?.executive_summary
  return typeof text === 'string' && text.trim() ? text : null
}

/** `/agents?run=…&invocation=…` — where an invocation is read (the review row links here). */
export function invocationHref(runId: string, invocationId: string): string {
  return `/agents?run=${encodeURIComponent(runId)}&invocation=${encodeURIComponent(invocationId)}`
}
