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
}

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
} {
  const plainSources: ChatSource[] = []
  let toolTrace: ToolTraceEntry[] = []
  let suggestedActions: SuggestedAction[] = []
  let provenanceRaw: Record<string, unknown> | null = null
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
    } else {
      plainSources.push(s)
    }
  }
  return { plainSources, toolTrace, suggestedActions, provenanceRaw }
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
