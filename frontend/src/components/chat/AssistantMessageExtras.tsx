/**
 * AI-6 copilot extras rendered under an assistant chat message:
 *
 *  - the shared US-15.1 trust chrome — an "AI-suggested" badge on EVERY
 *    assistant answer, plus routing provenance when the backend supplies it.
 *    Deliberately no confidence: the copilot has none, and the chrome must
 *    not imply one exists (`confidence` is simply not passed).
 *  - "How I looked this up" — a subtle, collapsed-by-default trace of the
 *    read tools the bounded loop consulted ({tool, summary} entries).
 *  - Suggested action buttons — one-click handoffs that OPEN the existing
 *    audited dialogs pre-filled (US-2.4 quarantine proposal, US-6.1 Jira
 *    issue). The human reviews and submits; the agent never does.
 */
import { useState } from 'react'
import { ChevronDown, ChevronRight, ExternalLink, Search, ShieldAlert } from 'lucide-react'
import AISuggestion, { normalizeProvenance } from '@/components/ai/AISuggestion'
import CreateJiraIssueModal from '@/components/defects/CreateJiraIssueModal'
import ProposeQuarantineModal from '@/components/quarantine/ProposeQuarantineModal'
import type { SuggestedAction, ToolTraceEntry } from '@/types/chat'

export default function AssistantMessageExtras({
  toolTrace, suggestedActions, provenanceRaw,
}: {
  toolTrace: ToolTraceEntry[]
  suggestedActions: SuggestedAction[]
  /** Optional routing provenance carrier — null on every message today. */
  provenanceRaw?: Record<string, unknown> | null
}) {
  const [traceOpen, setTraceOpen] = useState(false)
  const [openAction, setOpenAction] = useState<SuggestedAction | null>(null)

  const quarantineOpen =
    openAction?.type === 'propose_quarantine' &&
    !!openAction.prefill.test_fingerprint &&
    !!openAction.prefill.project_id
  const jiraOpen =
    openAction?.type === 'create_jira' &&
    !!(openAction.prefill.fingerprint ?? openAction.prefill.test_fingerprint) &&
    !!openAction.prefill.project_id

  return (
    <div className="mt-2 pt-2 border-t border-[var(--color-border)] space-y-2">
      {/* Trust chrome — badge always, provenance when known, NO confidence. */}
      <AISuggestion
        bare
        data-testid="chat-ai-suggestion"
        badgeTitle="Written by the AI copilot from the data it could read — a suggestion to verify, not a verdict."
        provenance={normalizeProvenance(provenanceRaw)}
      />

      {toolTrace.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setTraceOpen(v => !v)}
            className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
          >
            {traceOpen ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            <Search className="w-3 h-3" />
            How I looked this up · {toolTrace.length} check{toolTrace.length === 1 ? '' : 's'}
          </button>
          {traceOpen && (
            <ul className="mt-1.5 ml-4 space-y-1">
              {toolTrace.map((entry, i) => (
                <li key={i} className="text-xs text-[var(--color-text-muted)]">
                  <span className="font-mono text-[11px] text-[var(--color-text-secondary)]">{entry.tool}</span>
                  {' — '}
                  {entry.summary}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {suggestedActions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {suggestedActions.map((action, i) => (
            <button
              key={i}
              type="button"
              onClick={() => setOpenAction(action)}
              className="flex items-center gap-1.5 text-xs bg-[var(--color-bg-hover)] hover:bg-[var(--color-bg-hover)]/70 text-[var(--color-text-secondary)] border border-[var(--color-border-light)] px-2.5 py-1 rounded transition-colors"
            >
              {action.type === 'propose_quarantine'
                ? <ShieldAlert className="w-3 h-3" />
                : <ExternalLink className="w-3 h-3" />}
              {action.label}
            </button>
          ))}
        </div>
      )}

      {quarantineOpen && openAction && (
        <ProposeQuarantineModal
          prefill={{
            project_id: openAction.prefill.project_id,
            test_fingerprint: openAction.prefill.test_fingerprint as string,
            test_name: openAction.prefill.test_name,
            suite_name: openAction.prefill.suite_name ?? null,
            fail_count: openAction.prefill.fail_count ?? null,
          }}
          source="ask-ai-copilot"
          onClose={() => setOpenAction(null)}
        />
      )}

      {jiraOpen && openAction && (
        <CreateJiraIssueModal
          projectId={openAction.prefill.project_id}
          fingerprint={openAction.prefill.fingerprint ?? openAction.prefill.test_fingerprint}
          testName={openAction.prefill.test_name}
          onClose={() => setOpenAction(null)}
        />
      )}
    </div>
  )
}
