/**
 * What Ask AI is for, before the first question: starter questions that map
 * to the tasks it answers well, and the project's recent runs to ask about.
 */
import { AlertTriangle, Bot, CheckCircle2, MessageSquare } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import type { RunSummary } from '@/types/chat'
import { buildLabel, type StarterPrompt } from '@/components/chat/chatContent'

function fromNow(iso: string): string {
  const h = Math.floor((Date.now() - new Date(iso).getTime()) / 3_600_000)
  if (h < 1) return 'just now'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export default function ChatWelcome({
  projectName, prompts, onAsk, runs, runsLoading,
}: {
  projectName: string
  prompts: StarterPrompt[]
  onAsk: (prompt: string) => void
  runs: RunSummary[]
  runsLoading: boolean
}) {
  return (
    <div className="max-w-3xl mx-auto w-full space-y-6 py-2" data-testid="chat-welcome">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-lg flex items-center justify-center bg-[var(--color-bg-hover)] shrink-0">
          <Bot className="w-5 h-5 text-[var(--color-text)]" />
        </div>
        <div>
          <h2 className="font-semibold text-[var(--color-text)]">Ask about {projectName}</h2>
          <p className="text-sm text-[var(--color-text-muted)] mt-0.5 leading-relaxed">
            Answers come from this project&apos;s runs, failures, test history, flaky tests, quarantine and
            release-gate data. Each answer shows how it was looked up and links the builds and tests it names.
            Follow-up questions keep the conversation&apos;s context.
          </p>
        </div>
      </div>

      <div>
        <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Try asking</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {prompts.map((p) => (
            <button
              key={p.label}
              type="button"
              onClick={() => onAsk(p.prompt)}
              className="text-left rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] px-3 py-2.5 transition-colors"
              data-testid="chat-starter"
            >
              <span className="flex items-center gap-2 text-xs font-medium text-[var(--color-text-muted)]">
                {p.icon} {p.label}
              </span>
              <span className="block text-sm text-[var(--color-text)] mt-1">{p.prompt}</span>
            </button>
          ))}
        </div>
      </div>

      <div>
        <h3 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Recent runs</h3>
        {runsLoading ? (
          <div className="flex justify-center py-6"><LoadingSpinner size="sm" /></div>
        ) : runs.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)]">No runs in the last 5 days.</p>
        ) : (
          <ul className="divide-y divide-[var(--color-border)] rounded-lg border border-[var(--color-border)]">
            {runs.slice(0, 5).map((run) => {
              const build = run.build_number || run.test_run_id.slice(0, 8)
              const failed = run.is_regression || /failed/i.test(run.executive_summary)
              return (
                <li key={run.test_run_id} className="flex items-center gap-3 px-3 py-2">
                  {failed
                    ? <AlertTriangle className="w-4 h-4 shrink-0 text-[var(--status-failed)]" />
                    : <CheckCircle2 className="w-4 h-4 shrink-0 text-[var(--status-passed)]" />}
                  <span className="font-mono text-sm text-[var(--color-text)]">{build}</span>
                  <span className="text-xs text-[var(--color-text-faint)]">{fromNow(run.generated_at)}</span>
                  <button
                    type="button"
                    onClick={() => onAsk(`What happened in ${buildLabel(build)}? Which tests failed and why?`)}
                    className="ml-auto inline-flex items-center gap-1 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text)] border border-[var(--color-border-light)] rounded px-2 py-1"
                  >
                    <MessageSquare className="w-3 h-3" /> Ask about this run
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
