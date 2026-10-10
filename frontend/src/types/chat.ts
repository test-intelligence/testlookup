export interface ChatSession {
  id: string
  project_id: string | null
  title: string | null
  created_at: string
  updated_at: string
}

export interface ChatSource {
  type: string
  id?: string
  label?: string
  /** test_run: the build number; test_case: the run it belongs to + the test name. */
  build?: string
  run_id?: string
  name?: string
}

/** How an answer was produced, persisted with it (the `meta` carrier). */
export interface ChatMeta {
  status: 'complete' | 'stopped'
  provider?: string | null
  model?: string | null
  first_status_ms?: number | null
  first_token_ms?: number | null
  total_ms?: number | null
  llm_ms?: number | null
  tool_ms?: number | null
  context_ms?: number | null
  rounds?: number
  /** The model's own tool calls. */
  tool_calls?: number
  /** Every lookup behind the answer, the up-front ones included. */
  lookups?: number
  tools?: boolean
}

/** Error vocabulary of a failed turn (backend ChatTurnError codes). */
export interface ChatTurnFailure {
  code: string
  message: string
  retryable: boolean
}

/** One server-sent event of a streamed turn. */
export type ChatStreamEvent =
  | { event: 'start'; data: { user_message_id: string; retry: boolean } }
  | { event: 'status'; data: { label: string; tool: string | null } }
  | { event: 'delta'; data: { text: string } }
  | {
      event: 'done'
      data: {
        message: ChatMessage
        sources: ChatSource[]
        tool_trace: ToolTraceEntry[]
        suggested_actions: SuggestedAction[]
        meta: ChatMeta
      }
    }
  | { event: 'error'; data: ChatTurnFailure }

/** AI-6: one entry per read tool the copilot loop consulted. */
export interface ToolTraceEntry {
  tool: string
  summary: string
}

/** AI-6: a human action handoff — the button opens an existing audited
 *  dialog pre-filled; the human reviews and submits, never the agent. */
export interface SuggestedAction {
  type: 'propose_quarantine' | 'create_jira'
  label: string
  prefill: {
    project_id: string
    test_name: string
    test_fingerprint?: string
    fingerprint?: string
    suite_name?: string | null
    fail_count?: number | null
  }
}

export interface ChatMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant'
  content: string
  sources: ChatSource[] | null
  created_at: string
}

/** Unpack the AI-6 extras persisted inside the message's ``sources`` JSON
 *  column (no dedicated columns — see backend ConversationAgent). Returns the
 *  plain source chips with the special carrier entries stripped out. */
export function splitMessageSources(sources: ChatSource[] | null): {
  plainSources: ChatSource[]
  toolTrace: ToolTraceEntry[]
  suggestedActions: SuggestedAction[]
  /** US-15.1: raw routing-provenance carrier, when the backend emits one.
   *  Null today for every message — the chat trust chrome renders the badge
   *  with no provenance line rather than inventing one. Feed it to
   *  `normalizeProvenance`. */
  provenanceRaw: Record<string, unknown> | null
  /** How the answer was produced (model, timings, stopped or complete). */
  meta: ChatMeta | null
} {
  const plainSources: ChatSource[] = []
  let toolTrace: ToolTraceEntry[] = []
  let suggestedActions: SuggestedAction[] = []
  let provenanceRaw: Record<string, unknown> | null = null
  let meta: ChatMeta | null = null
  for (const s of sources ?? []) {
    const entry = s as ChatSource & {
      trace?: ToolTraceEntry[]
      actions?: SuggestedAction[]
      provenance?: Record<string, unknown>
    }
    if (entry.type === 'tool_trace' && Array.isArray(entry.trace)) {
      toolTrace = entry.trace
    } else if (entry.type === 'suggested_actions' && Array.isArray(entry.actions)) {
      suggestedActions = entry.actions
    } else if (entry.type === 'provenance' && entry.provenance && typeof entry.provenance === 'object') {
      provenanceRaw = entry.provenance
    } else if (entry.type === 'meta') {
      meta = entry as unknown as ChatMeta
    } else {
      plainSources.push(s)
    }
  }
  return { plainSources, toolTrace, suggestedActions, provenanceRaw, meta }
}

export interface RunSummary {
  test_run_id: string
  project_id: string
  build_number: string
  executive_summary: string
  markdown_report: string | null
  executive_panel?: Record<string, unknown> | null
  anomaly_count: number
  is_regression: boolean
  analysis_count: number
  generated_at: string
  is_stub?: boolean
}
