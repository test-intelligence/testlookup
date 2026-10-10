/**
 * Ask AI: a conversation about one project's test results.
 *
 * Rebuilt 2026-10-09 after an evaluation on the homelab. The page used to wait
 * 7–10 s on a blank "…" for each answer, render tables as raw `|` text, mix
 * every project's conversations into one list, and save provider failures as
 * if they were answers. Now: the question shows at once, the assistant says
 * what it is looking up, the answer streams in, Stop and Retry work, and each
 * answer links the builds and tests it names.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Bot, MessageSquare, Plus, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import DataUnavailable from '@/components/ui/DataUnavailable'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import ChatComposer, { type ChatComposerHandle } from '@/components/chat/ChatComposer'
import ChatWelcome from '@/components/chat/ChatWelcome'
import { starterPrompts } from '@/components/chat/chatContent'
import { PendingAnswer, SavedAnswer, UserMessage } from '@/components/chat/ChatMessageView'
import { pendingQuestionVisible, useChatConversation, useRunSummaries } from '@/hooks/useChat'
import { useAIConfig, isLLMAvailable } from '@/hooks/useAIConfig'
import { useProjectStore } from '@/store/projectStore'
import { splitMessageSources } from '@/types/chat'
import type { ChatSession } from '@/types/chat'

function SessionItem({
  session, active, onSelect, onDelete,
}: {
  session: ChatSession
  active: boolean
  onSelect: () => void
  onDelete: () => void
}) {
  return (
    <div
      className={`group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
        active ? 'bg-[var(--color-bg-hover)] border border-[var(--color-border-light)]' : 'hover:bg-[var(--color-bg-secondary)]'
      }`}
      onClick={onSelect}
      data-testid="chat-session"
    >
      <MessageSquare className="w-3.5 h-3.5 text-[var(--color-text-muted)] shrink-0" />
      <span className="text-sm text-[var(--color-text-secondary)] truncate flex-1" title={session.title ?? undefined}>
        {session.title || 'Conversation'}
      </span>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onDelete() }}
        className="opacity-0 group-hover:opacity-100 text-[var(--color-text-muted)] hover:text-[var(--status-failed)] transition-all"
        title="Delete this conversation"
        aria-label="Delete conversation"
      >
        <Trash2 className="w-3 h-3" />
      </button>
    </div>
  )
}

export default function ChatPage() {
  const { activeProject, projects, setActiveProject } = useProjectStore()
  const { data: aiConfig } = useAIConfig()
  const llmAvailable = isLLMAvailable(aiConfig)
  const projectId = activeProject?.id ?? null
  const chat = useChatConversation(projectId)
  const { data: runSummaries = [], isLoading: summariesLoading } = useRunSummaries(projectId, 5)
  const [searchParams, setSearchParams] = useSearchParams()
  // `/chat?prompt=…` (e.g. "Ask AI about this run" on a run page) fills the
  // question box; the reader sends it.
  const [input, setInput] = useState(() => (searchParams.get('prompt') ?? '').slice(0, 4000))
  const composer = useRef<ChatComposerHandle>(null)
  const endRef = useRef<HTMLDivElement>(null)

  // `&project=<id>` names the project the prompt is about. "Ask AI about this
  // run" on an E-Commerce run, with Checkout Service active, asked Checkout
  // Service and got "I can't find build viz-3043" (homelab, 2026-10-10). The
  // chat switches to that project when it is one of the user's; the prompt
  // is local state, so it survives the switch.
  const linkedProjectId = searchParams.get('project')
  useEffect(() => {
    if (!searchParams.get('prompt') && !linkedProjectId) return
    if (linkedProjectId && projects.length === 0) return // wait for the project list
    if (linkedProjectId) {
      const linked = projects.find((p) => p.id === linkedProjectId)
      if (linked && linked.id !== activeProject?.id) setActiveProject(linked)
    }
    const next = new URLSearchParams(searchParams)
    next.delete('prompt')
    next.delete('project')
    setSearchParams(next, { replace: true })
    composer.current?.focus()
  }, [searchParams, setSearchParams, linkedProjectId, projects, activeProject?.id, setActiveProject])

  const { messages, pending } = chat
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
  }, [messages.length, pending?.answer, pending?.phase, pending?.status])

  const prompts = useMemo(
    () => starterPrompts(activeProject?.name ?? 'this project', runSummaries[0]?.build_number ?? null),
    [activeProject?.name, runSummaries],
  )

  if (!activeProject) {
    return (
      <EmptyState
        icon={<MessageSquare className="h-8 w-8" />}
        title="Select a project"
        description="Choose a project before using AI chat so every answer and model call stays in that project scope."
      />
    )
  }

  const ask = async (text: string) => {
    const sent = await chat.send(text)
    if (sent) setInput('')
  }

  const handleSend = () => {
    const text = input
    if (!text.trim()) return
    setInput('')
    void chat.send(text).then((sent) => { if (!sent) setInput(text) })
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm('Delete this conversation?')) return
    try {
      await chat.removeSession(id)
    } catch {
      toast.error('Could not delete the conversation')
    }
  }

  const lastMessage = messages[messages.length - 1]
  const lastIsStoppedAnswer =
    lastMessage?.role === 'assistant' && splitMessageSources(lastMessage.sources).meta?.status === 'stopped'
  // A question whose turn failed earlier (no answer saved): offer to answer it.
  const unanswered = !pending && lastMessage?.role === 'user'
  const showPendingQuestion = pendingQuestionVisible(pending, messages)
  // After a Stop the saved history may already hold the stopped text.
  const showPendingAnswer = !!pending && !(pending.phase === 'stopped' && lastIsStoppedAnswer)
  const conversationOpen = chat.sessionId !== null || pending !== null

  return (
    <div className="h-[calc(100vh-8rem)] flex gap-4">
      <aside className="w-60 shrink-0 flex flex-col gap-2" aria-label="Conversations">
        <button
          type="button"
          onClick={() => { chat.selectSession(null); setInput(''); composer.current?.focus() }}
          className="btn-primary flex items-center gap-2 text-sm justify-center py-2"
          data-testid="chat-new"
        >
          <Plus className="w-4 h-4" /> New chat
        </button>
        <div className="flex-1 overflow-y-auto space-y-1">
          {chat.sessionsLoading ? (
            <div className="flex justify-center mt-4"><LoadingSpinner size="sm" /></div>
          ) : chat.sessions.length === 0 ? (
            <p className="text-xs text-[var(--color-text-muted)] text-center mt-4">No conversations in this project yet</p>
          ) : (
            chat.sessions.map((s) => (
              <SessionItem
                key={s.id}
                session={s}
                active={chat.sessionId === s.id}
                onSelect={() => chat.selectSession(s.id)}
                onDelete={() => void handleDelete(s.id)}
              />
            ))
          )}
        </div>
        <div className="text-xs text-[var(--color-text-faint)] p-2 border-t border-[var(--color-border)]">
          <p className="font-medium text-[var(--color-text-muted)] mb-0.5">Project</p>
          <p className="truncate">{activeProject.name}</p>
        </div>
      </aside>

      <section className="flex-1 flex flex-col card overflow-hidden min-w-0" aria-label="Ask AI conversation">
        <div className="flex-1 overflow-y-auto p-5" data-testid="chat-transcript">
          {!conversationOpen ? (
            <ChatWelcome
              projectName={activeProject.name}
              prompts={prompts}
              onAsk={(p) => void ask(p)}
              runs={runSummaries}
              runsLoading={summariesLoading}
            />
          ) : (
            <div className="max-w-3xl mx-auto w-full space-y-4">
              {chat.messagesLoading && (
                <div className="flex justify-center py-6"><LoadingSpinner size="sm" /></div>
              )}
              {chat.messagesError != null && (
                <DataUnavailable error={chat.messagesError} onRetry={chat.reloadMessages} testId="chat-messages-unavailable" />
              )}
              {messages.map((m, i) =>
                m.role === 'user' ? (
                  <UserMessage key={m.id} content={m.content} />
                ) : (
                  <SavedAnswer
                    key={m.id}
                    message={m}
                    onRegenerate={i === messages.length - 1 && !chat.busy ? () => void chat.retry() : undefined}
                  />
                ),
              )}
              {unanswered && (
                <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)] pl-10" data-testid="chat-unanswered">
                  <Bot className="w-3.5 h-3.5" /> This question has no answer yet.
                  <button type="button" onClick={() => void chat.retry()} className="text-[var(--color-accent)] hover:underline">
                    Answer it now
                  </button>
                </div>
              )}
              {pending && showPendingQuestion && <UserMessage content={pending.question} />}
              {pending && showPendingAnswer && (
                <PendingAnswer pending={pending} onStop={chat.stop} onRetry={() => void chat.retry()} />
              )}
              <div ref={endRef} />
            </div>
          )}
        </div>

        <div className="border-t border-[var(--color-border)] p-3 shrink-0">
          {!llmAvailable ? (
            <div className="flex items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-4 py-3 text-sm text-[var(--color-text-muted)]">
              <Bot className="w-4 h-4 shrink-0" />
              {aiConfig?.analysis_mode === 'ml'
                ? 'Chat is unavailable in ML mode. Switch to LLM or Auto mode in Settings > AI Configuration.'
                : 'Chat is unavailable in Rules mode. Switch to LLM or Auto mode in Settings > AI Configuration.'}
            </div>
          ) : (
            <div className="max-w-3xl mx-auto w-full">
              <ChatComposer
                ref={composer}
                value={input}
                onChange={setInput}
                onSend={handleSend}
                onStop={chat.stop}
                busy={chat.busy}
              />
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
