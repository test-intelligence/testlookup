/**
 * Ask-AI chat messages: the user's questions, saved answers (with the runs
 * and tests they name, linked), and the answer being written.
 */
import { memo, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Bot, FlaskConical, GitBranch, RotateCcw, Square, User } from 'lucide-react'
import ChatMarkdown from '@/components/chat/ChatMarkdown'
import AssistantMessageExtras from '@/components/chat/AssistantMessageExtras'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { splitMessageSources } from '@/types/chat'
import type { ChatMessage } from '@/types/chat'
import { metaLine, sourceLinks } from '@/components/chat/chatContent'
import type { PendingTurn } from '@/hooks/useChat'

function Avatar({ role }: { role: 'user' | 'assistant' }) {
  return (
    <div
      className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 ${
        role === 'user' ? 'bg-[var(--color-btn-primary-bg)]' : 'bg-[var(--color-bg-hover)]'
      }`}
    >
      {role === 'user'
        ? <User className="w-3.5 h-3.5 text-[var(--color-btn-primary-text,var(--color-text))]" />
        : <Bot className="w-3.5 h-3.5 text-[var(--color-text)]" />}
    </div>
  )
}

export const UserMessage = memo(function UserMessage({ content }: { content: string }) {
  return (
    <div className="flex gap-3 flex-row-reverse" data-testid="chat-user-message">
      <Avatar role="user" />
      <div className="max-w-[80%] rounded-xl px-4 py-2.5 text-[13.5px] leading-relaxed bg-[var(--color-bg-hover)] text-[var(--color-text)]">
        <p className="whitespace-pre-wrap break-words">{content}</p>
      </div>
    </div>
  )
})

function AssistantShell({ children, testId }: { children: React.ReactNode; testId: string }) {
  return (
    <div className="flex gap-3" data-testid={testId}>
      <Avatar role="assistant" />
      <div className="min-w-0 max-w-[88%] flex-1 rounded-xl px-4 py-3 bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
        {children}
      </div>
    </div>
  )
}

export const SavedAnswer = memo(function SavedAnswer({
  message, onRegenerate,
}: {
  message: ChatMessage
  /** Offered on a stopped answer that is the conversation's last message. */
  onRegenerate?: () => void
}) {
  const { plainSources, toolTrace, suggestedActions, provenanceRaw, meta } = splitMessageSources(message.sources)
  const links = sourceLinks(plainSources)
  const stopped = meta?.status === 'stopped'
  const line = metaLine(meta)
  return (
    <AssistantShell testId="chat-assistant-message">
      <ChatMarkdown text={message.content} />
      {stopped && (
        <div className="mt-2 flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <Square className="w-3 h-3" /> Stopped before the answer was finished.
          {onRegenerate && (
            <button type="button" onClick={onRegenerate} className="ml-1 inline-flex items-center gap-1 text-[var(--color-accent)] hover:underline">
              <RotateCcw className="w-3 h-3" /> Regenerate
            </button>
          )}
        </div>
      )}
      {links.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-1.5" data-testid="chat-sources">
          {links.map((l) => (
            <Link
              key={l.key}
              to={l.to}
              className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full border border-[var(--color-border)] bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] hover:text-[var(--color-text)] hover:border-[var(--color-border-light)] transition-colors"
            >
              {l.kind === 'run' ? <GitBranch className="w-3 h-3" /> : <FlaskConical className="w-3 h-3" />}
              {l.label}
            </Link>
          ))}
        </div>
      )}
      <AssistantMessageExtras toolTrace={toolTrace} suggestedActions={suggestedActions} provenanceRaw={provenanceRaw} />
      {line && <p className="mt-1.5 text-[11px] text-[var(--color-text-faint)]" data-testid="chat-answer-meta">{line}</p>}
    </AssistantShell>
  )
})

function useElapsed(running: boolean): number {
  const [started] = useState(() => Date.now())
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return undefined
    const id = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(id)
  }, [running])
  return Math.max(0, Math.floor((now - started) / 1000))
}

/** The answer being written: what the assistant is doing, then the text. */
export function PendingAnswer({
  pending, onStop, onRetry,
}: {
  pending: PendingTurn
  onStop: () => void
  onRetry: () => void
}) {
  const working = pending.phase === 'sending' || pending.phase === 'working'
  const streaming = pending.phase === 'streaming'
  const elapsed = useElapsed(working || streaming)
  return (
    <AssistantShell testId="chat-pending-answer">
      {pending.answer && (
        <div className={streaming ? 'chat-streaming' : undefined}>
          <ChatMarkdown text={pending.answer} />
          {streaming && <span className="inline-block w-2 h-4 align-middle bg-[var(--color-text-muted)] animate-pulse" aria-hidden />}
        </div>
      )}
      {working && (
        <div className="flex items-center gap-2 text-[13px] text-[var(--color-text-muted)]" role="status" data-testid="chat-status">
          <LoadingSpinner size="sm" />
          <span>{pending.status ?? (pending.phase === 'sending' ? 'Sending…' : 'Thinking…')}</span>
          {elapsed >= 2 && <span className="text-[var(--color-text-faint)]">· {elapsed} s</span>}
          <button type="button" onClick={onStop} className="ml-auto text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
            Stop
          </button>
        </div>
      )}
      {pending.phase === 'error' && pending.error && (
        <div
          className="mt-1 flex items-start gap-2 rounded-lg border px-3 py-2 text-[13px]"
          style={{ borderColor: 'var(--status-failed-bd)', background: 'color-mix(in srgb, var(--status-failed-bg) 35%, transparent)' }}
          role="alert"
          data-testid="chat-error"
        >
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-[var(--status-failed)]" />
          <div className="flex-1">
            <p className="text-[var(--color-text)]">{pending.error.message}</p>
            {pending.answer && <p className="text-xs text-[var(--color-text-muted)] mt-0.5">The text above is incomplete and was not saved.</p>}
          </div>
          {pending.error.retryable && (
            <button type="button" onClick={onRetry} className="btn-secondary text-xs px-2.5 py-1 inline-flex items-center gap-1 shrink-0">
              <RotateCcw className="w-3 h-3" /> Retry
            </button>
          )}
        </div>
      )}
      {pending.phase === 'stopped' && (
        <div className="mt-1 flex items-center gap-2 text-xs text-[var(--color-text-muted)]" data-testid="chat-stopped">
          <Square className="w-3 h-3" /> {pending.answer ? 'Stopped.' : 'Stopped before any answer.'}
          <button type="button" onClick={onRetry} className="ml-1 inline-flex items-center gap-1 text-[var(--color-accent)] hover:underline">
            <RotateCcw className="w-3 h-3" /> Regenerate
          </button>
        </div>
      )}
    </AssistantShell>
  )
}
