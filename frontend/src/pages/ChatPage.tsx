import { memo, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  Bot, ChevronDown, ChevronUp,
  CheckCircle2, MessageSquare, Plus, Send, Trash2, User,
  Zap,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import ExecutiveSummaryPanel from '@/components/ai/ExecutiveSummaryPanel'
import type { ExecutivePanel } from '@/services/runIntelligenceService'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import AppLogo from '@/components/ui/AppLogo'
import { useChat, useChatSessions, useRunSummaries } from '@/hooks/useChat'
import { useAIConfig, isLLMAvailable } from '@/hooks/useAIConfig'
import chatService from '@/services/chatService'
import { useProjectStore } from '@/store/projectStore'
import type { ChatSession, RunSummary } from '@/types/chat'

// Upper bound on a single chat message. Enforced client-side to avoid sending
// unbounded prompts that would blow up LLM context windows and token cost.
// Backend should enforce a matching limit as defense-in-depth.
const MAX_CHAT_MESSAGE_LENGTH = 5000

// ── Helpers ────────────────────────────────────────────────────────────────

function fromNow(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const h = Math.floor(diff / 3_600_000)
  if (h < 1) return 'just now'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

// ── Session sidebar item ───────────────────────────────────────────────────

function SessionItem({
  session, active, onSelect, onDelete,
}: {
  session: ChatSession; active: boolean; onSelect: () => void; onDelete: () => void
}) {
  return (
    <div
      className={`group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
        active ? 'bg-white/10 border border-[var(--color-border-light)]' : 'hover:bg-[var(--color-bg-secondary)]/80'
      }`}
      onClick={onSelect}
    >
      <MessageSquare className="w-3.5 h-3.5 text-[var(--color-text-muted)] shrink-0" />
      <span className="text-sm text-[var(--color-text-secondary)] truncate flex-1">
        {session.title || 'Conversation'}
      </span>
      <button
        onClick={e => { e.stopPropagation(); onDelete() }}
        className="opacity-0 group-hover:opacity-100 text-[var(--color-text-muted)] hover:text-red-400 transition-all"
      >
        <Trash2 className="w-3 h-3" />
      </button>
    </div>
  )
}

// ── Message bubble ─────────────────────────────────────────────────────────

const MessageBubble = memo(function MessageBubble({ role, content, sources }: {
  role: 'user' | 'assistant'
  content: string
  sources?: Array<{ type: string; id?: string }> | null
}) {
  const isUser = role === 'user'
  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 ${
        isUser ? 'bg-[var(--color-btn-primary-bg)]' : 'bg-[var(--color-bg-hover)]'
      }`}>
        {isUser
          ? <User className="w-3.5 h-3.5 text-[var(--color-text)]" />
          : <Bot className="w-3.5 h-3.5 text-[var(--color-text)]" />}
      </div>
      <div className={`max-w-[80%] rounded-xl px-4 py-3 text-sm leading-relaxed ${
        isUser ? 'bg-[var(--color-bg-hover)] text-[var(--color-text)]' : 'bg-[var(--color-bg-secondary)] text-[var(--color-text)]'
      }`}>
        {content === '…' ? (
          <span className="inline-flex gap-1 items-center text-[var(--color-text-muted)]">
            <span className="w-1.5 h-1.5 rounded-full bg-neutral-600 animate-bounce [animation-delay:0ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-neutral-600 animate-bounce [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-neutral-600 animate-bounce [animation-delay:300ms]" />
          </span>
        ) : isUser ? (
          <p className="whitespace-pre-wrap">{content}</p>
        ) : (
          <div className="prose prose-invert prose-sm max-w-none">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
        )}
        {!isUser && sources && sources.length > 0 && (
          <div className="mt-2 pt-2 border-t border-[var(--color-border)] flex flex-wrap gap-1">
            {sources.map((s, i) => (
              <span key={i} className="text-xs bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] px-2 py-0.5 rounded-full">
                {s.type}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
})

// ── Run summary card ───────────────────────────────────────────────────────

function RunSummaryCard({
  summary,
  onAskAbout,
}: {
  summary: RunSummary
  onAskAbout: (runId: string, build: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const hasFullReport = !summary.is_stub && !!summary.markdown_report

  return (
    <div className={`rounded-lg border transition-colors ${
      summary.is_regression
        ? 'border-red-700/40 bg-red-900/10'
        : summary.is_stub
          ? 'border-[var(--color-border)] bg-[var(--color-bg-secondary)]/40'
          : 'border-[var(--color-border)] bg-[var(--color-bg-secondary)]/80'
    }`}>
      {/* Card header */}
      <div className="flex items-start gap-3 px-4 py-3">
        <div className={`mt-0.5 w-8 h-8 rounded-md flex items-center justify-center shrink-0 ${
          summary.is_regression ? 'bg-red-900/50' : summary.is_stub ? 'bg-[var(--color-bg-hover)]/50' : 'bg-[var(--color-bg-hover)]'
        }`}>
          {summary.is_regression
            ? <AlertTriangle className="w-4 h-4 text-red-400" />
            : summary.is_stub
              ? <Bot className="w-4 h-4 text-[var(--color-text-muted)] animate-pulse" />
              : <CheckCircle2 className="w-4 h-4 text-emerald-400" />}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-sm font-semibold text-[var(--color-text)]">
              {summary.build_number || summary.test_run_id.slice(0, 8)}
            </span>
            {summary.is_stub && (
              <span className="text-[10px] bg-[var(--color-bg-hover)]/60 text-[var(--color-text-muted)] border border-[var(--color-border-light)]/40 px-1.5 py-0.5 rounded font-medium">
                AI PENDING
              </span>
            )}
            {!summary.is_stub && summary.is_regression && (
              <span className="text-[10px] bg-red-900/60 text-red-300 border border-red-700/40 px-1.5 py-0.5 rounded font-medium">
                REGRESSION
              </span>
            )}
            {!summary.is_stub && summary.anomaly_count > 0 && (
              <span className="text-[10px] bg-yellow-900/40 text-yellow-400 border border-yellow-700/30 px-1.5 py-0.5 rounded">
                {summary.anomaly_count} anomal{summary.anomaly_count === 1 ? 'y' : 'ies'}
              </span>
            )}
            <span className="text-xs text-[var(--color-text-muted)] ml-auto shrink-0">
              {fromNow(summary.generated_at)}
            </span>
          </div>

          {/* Executive summary */}
          {summary.executive_panel ? (
            <div className="mt-2">
              <ExecutiveSummaryPanel panel={summary.executive_panel as unknown as ExecutivePanel} compact />
            </div>
          ) : (
            <div className="text-sm text-[var(--color-text-secondary)] mt-1.5 leading-relaxed prose prose-invert prose-sm max-w-none">
              <ReactMarkdown>{summary.executive_summary}</ReactMarkdown>
            </div>
          )}

          <div className="flex items-center gap-2 mt-2.5">
            {!summary.is_stub && (
              <span className="text-xs text-[var(--color-text-muted)]">
                {summary.analysis_count} test{summary.analysis_count !== 1 ? 's' : ''} analysed
              </span>
            )}
            {hasFullReport && (
              <button
                onClick={() => setExpanded(v => !v)}
                className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors ml-1"
              >
                {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                {expanded ? 'Hide report' : 'Full report'}
              </button>
            )}
            <button
              onClick={() => onAskAbout(summary.test_run_id, summary.build_number)}
              className="ml-auto flex items-center gap-1 text-xs bg-neutral-200/30 hover:bg-neutral-200/50 text-[var(--color-text-secondary)] border border-[var(--color-border-light)] px-2.5 py-1 rounded transition-colors"
            >
              <MessageSquare className="w-3 h-3" />
              Ask AI
            </button>
          </div>
        </div>
      </div>

      {/* Expandable full markdown report (AI summaries only) */}
      {expanded && hasFullReport && (
        <div className="border-t border-[var(--color-border)] px-4 py-3 max-h-80 overflow-y-auto">
          <div className="prose prose-invert prose-sm max-w-none text-[var(--color-text-secondary)]">
            <ReactMarkdown>{summary.markdown_report ?? ''}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Starter prompts ────────────────────────────────────────────────────────

const STARTER_PROMPTS = [
  'What are the most common failure patterns in recent runs?',
  'Show me tests that have been consistently failing this week',
  'What was the pass rate trend for the last 7 days?',
  'Are there any critical regressions I should be aware of?',
  'Which tests are showing flaky behaviour?',
]

// ── Main page ──────────────────────────────────────────────────────────────

export default function ChatPage() {
  const { activeProject } = useProjectStore()
  const { data: aiConfig } = useAIConfig()
  const llmAvailable = isLLMAvailable(aiConfig)
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const { data: sessions = [], mutate: reloadSessions } = useChatSessions()
  const { messages, isSending, sendMessage, error } = useChat(
    activeSessionId,
    activeProject?.id,
  )
  const { data: runSummaries = [], isLoading: summariesLoading } = useRunSummaries(
    activeProject?.id,
    5,
  )

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleNewSession = async () => {
    try {
      const session = await chatService.createSession({
        project_id: activeProject?.id,
        title: 'New conversation',
      })
      await reloadSessions()
      setActiveSessionId(session.id)
    } catch {
      toast.error('Failed to create session')
    }
  }

  const handleDeleteSession = async (sessionId: string) => {
    if (!confirm('Delete this conversation?')) return
    try {
      await chatService.deleteSession(sessionId)
      if (activeSessionId === sessionId) setActiveSessionId(null)
      await reloadSessions()
    } catch {
      toast.error('Failed to delete session')
    }
  }

  // Create a session (if needed) and send the message
  const handleSend = async (text?: string) => {
    const msg = text ?? input
    if (!msg.trim() || isSending) return
    // Hard cap to prevent runaway LLM context cost / DoS on the backend.
    if (msg.length > MAX_CHAT_MESSAGE_LENGTH) {
      toast.error(`Message too long (${msg.length}/${MAX_CHAT_MESSAGE_LENGTH} characters).`)
      return
    }
    if (!text) setInput('')

    let sid = activeSessionId
    if (!sid) {
      try {
        const session = await chatService.createSession({ project_id: activeProject?.id })
        sid = session.id
        setActiveSessionId(sid)
        await reloadSessions()
      } catch {
        if (!text) setInput(msg)
        toast.error('Could not create chat session')
        return
      }
    }

    await sendMessage(msg, sid)
    await reloadSessions()
  }

  // "Ask AI" button on a summary card — auto-sends a focused question
  const handleAskAboutRun = async (runId: string, build: string) => {
    const prompt = `Give me a detailed analysis of test run ${build || runId.slice(0, 8)}. What were the key failures, root causes, and recommended actions?`
    await handleSend(prompt)
  }

  // Starter prompts auto-send immediately (no extra click needed)
  const handleStarterPrompt = async (prompt: string) => {
    await handleSend(prompt)
  }

  return (
    <div className="h-[calc(100vh-8rem)] flex gap-4">
      {/* ── Sidebar ── */}
      <aside className="w-56 shrink-0 flex flex-col gap-2">
        <button
          onClick={handleNewSession}
          className="btn-primary flex items-center gap-2 text-sm justify-center py-2"
        >
          <Plus className="w-4 h-4" />
          New Chat
        </button>

        <div className="flex-1 overflow-y-auto space-y-1">
          {sessions.length === 0 ? (
            <p className="text-xs text-[var(--color-text-muted)] text-center mt-4">No conversations yet</p>
          ) : (
            sessions.map((s: ChatSession) => (
              <SessionItem
                key={s.id}
                session={s}
                active={activeSessionId === s.id}
                onSelect={() => setActiveSessionId(s.id)}
                onDelete={() => handleDeleteSession(s.id)}
              />
            ))
          )}
        </div>

        <div className="text-xs text-[var(--color-text-faint)] p-2 border-t border-[var(--color-border)]">
          <p className="font-medium text-[var(--color-text-muted)] mb-1">Context</p>
          <p>{activeProject?.name || 'All projects'}</p>
        </div>
      </aside>

      {/* ── Chat area ── */}
      <div className="flex-1 flex flex-col card overflow-hidden min-w-0">
        {!activeSessionId ? (
          /* ── Empty state: summaries + starter prompts ── */
          <div className="flex-1 overflow-y-auto p-5 space-y-5">
            {/* Hero */}
            <div className="flex items-center gap-3">
              <div className="shrink-0">
                <AppLogo glyph className="text-[28px]" />
              </div>
              <div>
                <h3 className="font-semibold text-[var(--color-text)] leading-tight">TestLookup Chat</h3>
                <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                  Ask questions about test results, failures, trends, and AI analysis findings.
                </p>
              </div>
            </div>

            {/* Pre-computed run summaries */}
            <div>
              <div className="flex items-center gap-2 mb-3">
                <Zap className="w-3.5 h-3.5 text-yellow-400" />
                <h4 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                  Recent Run Summaries — last 5 days
                </h4>
                <span className="text-xs text-[var(--color-text-faint)] ml-auto">pre-computed · instant</span>
              </div>

              {summariesLoading ? (
                <div className="flex items-center justify-center py-8">
                  <LoadingSpinner size="sm" />
                </div>
              ) : runSummaries.length === 0 ? (
                <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)]/60 px-4 py-6 text-center">
                  <p className="text-sm text-[var(--color-text-muted)]">
                    No summaries yet. Summaries are generated automatically after each test run completes.
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  {runSummaries.map(s => (
                    <RunSummaryCard
                      key={s.test_run_id}
                      summary={s}
                      onAskAbout={handleAskAboutRun}
                    />
                  ))}
                </div>
              )}
            </div>

            {/* Starter prompts */}
            <div>
              <h4 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
                Or ask a question
              </h4>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {STARTER_PROMPTS.map(prompt => (
                  <button
                    key={prompt}
                    onClick={() => handleStarterPrompt(prompt)}
                    className="text-left text-sm text-[var(--color-text-secondary)] bg-[var(--color-bg-secondary)]/80 hover:bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 transition-colors"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          /* ── Active session: message list ── */
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.length === 0 && !isSending && (
              <div className="text-center text-sm text-[var(--color-text-muted)] mt-8">
                Send your first message to start the conversation.
              </div>
            )}
            {messages.map(msg => (
              <MessageBubble
                key={msg.id}
                role={msg.role}
                content={msg.content}
                sources={msg.sources}
              />
            ))}
            {error && (
              <div className="text-xs text-red-400 text-center">{error}</div>
            )}
            <div ref={messagesEndRef} />
          </div>
        )}

        {/* ── Input bar ── */}
        <div className="border-t border-[var(--color-border)] p-3 shrink-0">
          {!llmAvailable ? (
            <div className="flex items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)]/60 px-4 py-3 text-sm text-[var(--color-text-muted)]">
              <Bot className="w-4 h-4 shrink-0" />
              Chat is unavailable in Rules mode. Switch to LLM or Auto mode in Settings &gt; AI Configuration.
            </div>
          ) : (
            <>
              <div className="flex gap-2 items-end">
                <textarea
                  value={input}
                  onChange={e => setInput(e.target.value.slice(0, MAX_CHAT_MESSAGE_LENGTH))}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      handleSend()
                    }
                  }}
                  placeholder="Ask about test results, failures, trends…"
                  rows={1}
                  maxLength={MAX_CHAT_MESSAGE_LENGTH}
                  className="input flex-1 resize-none text-sm py-2 leading-relaxed"
                  style={{ maxHeight: '120px', overflow: 'auto' }}
                />
                <button
                  onClick={() => handleSend()}
                  disabled={!input.trim() || isSending}
                  className="btn-primary p-2.5 shrink-0 disabled:opacity-40"
                >
                  {isSending ? <LoadingSpinner size="sm" /> : <Send className="w-4 h-4" />}
                </button>
              </div>
              <p className="text-xs text-[var(--color-text-faint)] mt-1">Press Enter to send · Shift+Enter for new line</p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
