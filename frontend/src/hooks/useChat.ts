import { useCallback, useEffect, useRef, useState } from 'react'
import useSWR, { useSWRConfig } from 'swr'
import chatService from '@/services/chatService'
import { ChatStreamError, streamChatTurn } from '@/services/chatStream'
import { splitMessageSources } from '@/types/chat'
import type { ChatMessage, ChatSession, ChatTurnFailure, RunSummary } from '@/types/chat'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

/** The server's limit (SendMessageRequest / StreamMessageRequest). The page
 *  used to allow 5,000, and 4,001+ came back as a silent 422. */
export const MAX_CHAT_MESSAGE_LENGTH = 4000

export function useRunSummaries(projectId?: string | null, days = 5) {
  return useSWR<RunSummary[]>(
    ['run-summaries', projectId, days],
    () => chatService.getRunSummaries(projectId, days),
    { revalidateOnFocus: false, refreshInterval: REFRESH_INTERVALS.BACKGROUND },
  )
}

const sessionsKey = (projectId?: string | null) => (projectId ? ['/chat/sessions', projectId] : null)
const messagesKey = (sessionId: string | null) => (sessionId ? `/chat/sessions/${sessionId}/messages` : null)

/** The caller's conversations in one project. */
export function useChatSessions(projectId?: string | null) {
  return useSWR<ChatSession[]>(
    sessionsKey(projectId),
    () => chatService.listSessions(projectId),
    { revalidateOnFocus: false },
  )
}

export function useChatMessages(sessionId: string | null) {
  return useSWR<ChatMessage[]>(
    messagesKey(sessionId),
    () => chatService.getMessages(sessionId ?? ''),
    { revalidateOnFocus: false },
  )
}

export type TurnPhase = 'sending' | 'working' | 'streaming' | 'error' | 'stopped'

/** The turn in flight (or the last one, when it failed or was stopped). */
export interface PendingTurn {
  question: string
  /** Set by the server's `start` event: the saved question's id. */
  userMessageId: string | null
  /** A retry re-answers a question that is already in the history. */
  retry: boolean
  answer: string
  /** What the assistant is doing ("Checking the history of …"). */
  status: string | null
  phase: TurnPhase
  error: ChatTurnFailure | null
}

const NETWORK_FAILURE: ChatTurnFailure = {
  code: 'network',
  message: 'Could not reach TestLookup. Check your connection and try again.',
  retryable: true,
}
const INCOMPLETE: ChatTurnFailure = {
  code: 'incomplete',
  message: 'The answer stopped before it finished. Try again.',
  retryable: true,
}

export const isTurnActive = (pending: PendingTurn | null) =>
  !!pending && (pending.phase === 'sending' || pending.phase === 'working' || pending.phase === 'streaming')

/** Show the turn's question from `pending` until the saved history has it. */
export const pendingQuestionVisible = (pending: PendingTurn | null, messages: ChatMessage[]) =>
  !!pending && !pending.retry && !(pending.userMessageId && messages.some((m) => m.id === pending.userMessageId))

const isStoppedAnswer = (message: ChatMessage) =>
  message.role === 'assistant' && splitMessageSources(message.sources).meta?.status === 'stopped'

/**
 * One project's Ask-AI conversation: the active session, its messages, and
 * the turn in flight.
 *
 * - `send` shows the question at once (before the session even exists),
 *   creates the session if needed, and streams the answer into `pending`.
 * - A second `send` while a turn runs is ignored (a ref, not state: two
 *   clicks in one render both see the old state).
 * - `stop` aborts the stream; the server stops the model and keeps the text
 *   already shown. `retry` answers the last question again without saving it
 *   twice (a stopped answer is replaced).
 * - Switching conversation, or project, stops the turn in flight.
 */
export function useChatConversation(projectId: string | null | undefined) {
  const { mutate: swrMutate } = useSWRConfig()
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  // A turn outlives the page when the reader navigates away mid-answer: its
  // cache writes must not land on an unmounted provider.
  const mutate = useCallback(
    (...args: Parameters<typeof swrMutate>): Promise<unknown> =>
      mounted.current ? swrMutate(...args).catch(() => undefined) : Promise.resolve(undefined),
    [swrMutate],
  )
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingTurn | null>(null)
  // A new project is a new context: start empty (state adjusted while
  // rendering, the React way to reset on a prop change; the effect below
  // stops the turn that belonged to the old project).
  const [scope, setScope] = useState(projectId)
  if (scope !== projectId) {
    setScope(projectId)
    setSessionId(null)
    setPending(null)
  }
  const sessions = useChatSessions(projectId)
  const messages = useChatMessages(sessionId)
  const abortRef = useRef<AbortController | null>(null)
  const busyRef = useRef(false)

  const update = useCallback(
    (patch: (p: PendingTurn) => PendingTurn) => setPending((p) => (p ? patch(p) : p)),
    [],
  )

  const reset = useCallback((next: string | null) => {
    abortRef.current?.abort()
    abortRef.current = null
    busyRef.current = false
    setPending(null)
    setSessionId(next)
  }, [])

  useEffect(() => () => {
    abortRef.current?.abort()
    abortRef.current = null
    busyRef.current = false
  }, [projectId])

  const runTurn = useCallback(
    async (sid: string, body: { message?: string; retry?: boolean }) => {
      const controller = new AbortController()
      abortRef.current = controller
      let finished = false
      let answered = false
      let questionId: string | null = null
      try {
        await streamChatTurn(sid, body, {
          signal: controller.signal,
          onEvent: (ev) => {
            switch (ev.event) {
              case 'start':
                questionId = ev.data.user_message_id
                update((p) => ({
                  ...p,
                  userMessageId: ev.data.user_message_id,
                  phase: p.phase === 'sending' ? 'working' : p.phase,
                }))
                break
              case 'status':
                update((p) => ({ ...p, status: ev.data.label }))
                break
              case 'delta':
                update((p) => ({ ...p, answer: p.answer + ev.data.text, phase: 'streaming' }))
                break
              case 'done': {
                finished = true
                answered = true
                const answer = ev.data.message
                const question: ChatMessage | null = body.retry
                  ? null
                  : {
                      id: questionId ?? `question-of-${answer.id}`,
                      session_id: sid,
                      role: 'user',
                      content: body.message ?? '',
                      sources: null,
                      created_at: answer.created_at,
                    }
                // Show the saved answer at once, then take the server's list
                // (it has the question's real id and timestamp).
                void mutate(
                  messagesKey(sid),
                  (cur?: ChatMessage[]) => {
                    const list = (cur ?? []).filter((m) => m.id !== answer.id)
                    const missing = question && !list.some((m) => m.id === question.id)
                    return [...list, ...(missing && question ? [question] : []), answer]
                  },
                  { revalidate: true },
                )
                setPending(null)
                break
              }
              case 'error':
                finished = true
                update((p) => ({ ...p, phase: 'error', error: ev.data, status: null }))
                break
            }
          },
        })
        if (!finished) update((p) => ({ ...p, phase: 'error', error: INCOMPLETE, status: null }))
      } catch (err) {
        if (controller.signal.aborted) {
          update((p) => ({ ...p, phase: 'stopped', status: null }))
        } else {
          const failure = err instanceof ChatStreamError ? err.failure : NETWORK_FAILURE
          update((p) => ({ ...p, phase: 'error', error: failure, status: null }))
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null
        busyRef.current = false
        if (!answered) {
          // The question is saved even when no answer is (and a stopped
          // answer is saved with what was shown): take the server's history.
          void mutate(messagesKey(sid))
        }
        // Titles and order change after a turn.
        void mutate(sessionsKey(projectId))
      }
    },
    [mutate, projectId, update],
  )

  const send = useCallback(
    async (text: string): Promise<boolean> => {
      const question = text.trim()
      if (!question || !projectId || busyRef.current || question.length > MAX_CHAT_MESSAGE_LENGTH) return false
      busyRef.current = true
      setPending({ question, userMessageId: null, retry: false, answer: '', status: null, phase: 'sending', error: null })
      let sid = sessionId
      if (!sid) {
        try {
          const created = await chatService.createSession({ project_id: projectId })
          sid = created.id
          void mutate(messagesKey(sid), [], { revalidate: false })
          setSessionId(sid)
          void mutate(sessionsKey(projectId))
        } catch {
          busyRef.current = false
          update((p) => ({
            ...p,
            phase: 'error',
            error: { code: 'session', message: 'Could not start a conversation. Try again.', retryable: true },
          }))
          return false
        }
      }
      await runTurn(sid, { message: question })
      return true
    },
    [mutate, projectId, runTurn, sessionId, update],
  )

  const retry = useCallback(async () => {
    if (busyRef.current) return
    const sid = sessionId
    const saved = messages.data ?? []
    const lastQuestion = [...saved].reverse().find((m) => m.role === 'user')?.content
    const question = pending?.question ?? lastQuestion ?? ''
    if (!sid) {
      // The conversation was never created: ask again from the start.
      setPending(null)
      await send(question)
      return
    }
    busyRef.current = true
    setPending({ question, userMessageId: null, retry: true, answer: '', status: null, phase: 'sending', error: null })
    // A stopped answer is replaced by the new one (the server deletes it too).
    void mutate(
      messagesKey(sid),
      (cur?: ChatMessage[]) => {
        const list = cur ?? []
        const last = list[list.length - 1]
        return last && isStoppedAnswer(last) ? list.slice(0, -1) : list
      },
      { revalidate: false },
    )
    await runTurn(sid, { retry: true })
  }, [messages.data, mutate, pending, runTurn, send, sessionId])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const removeSession = useCallback(
    async (id: string) => {
      await chatService.deleteSession(id)
      if (id === sessionId) reset(null)
      await mutate(sessionsKey(projectId))
    },
    [mutate, projectId, reset, sessionId],
  )

  return {
    sessionId,
    selectSession: reset,
    sessions: sessions.data ?? [],
    sessionsLoading: !sessions.data && !sessions.error,
    messages: messages.data ?? [],
    messagesLoading: !!sessionId && !messages.data && !messages.error,
    messagesError: messages.error as unknown,
    reloadMessages: () => { void messages.mutate() },
    pending,
    busy: isTurnActive(pending),
    send,
    stop,
    retry,
    removeSession,
  }
}
