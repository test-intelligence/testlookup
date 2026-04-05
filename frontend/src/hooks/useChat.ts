import { useCallback, useState } from 'react'
import useSWR, { mutate as globalMutate } from 'swr'
import chatService from '@/services/chatService'
import type { ChatMessage, ChatSession, RunSummary } from '@/types/chat'

export function useRunSummaries(projectId?: string | null, days = 5) {
  return useSWR<RunSummary[]>(
    ['run-summaries', projectId, days],
    () => chatService.getRunSummaries(projectId, days),
    { revalidateOnFocus: false, refreshInterval: 60_000 },
  )
}

export function useChatSessions() {
  return useSWR<ChatSession[]>(
    '/chat/sessions',
    () => chatService.listSessions(),
    { revalidateOnFocus: false },
  )
}

export function useChatMessages(sessionId: string | null) {
  return useSWR<ChatMessage[]>(
    sessionId ? `/chat/sessions/${sessionId}/messages` : null,
    () => chatService.getMessages(sessionId ?? ''),
    { revalidateOnFocus: false },
  )
}

interface UseChatReturn {
  messages: ChatMessage[]
  isLoading: boolean
  isSending: boolean
  sendMessage: (text: string, overrideSessionId?: string) => Promise<void>
  error: string | null
}

export function useChat(sessionId: string | null, projectId?: string | null): UseChatReturn {
  const { data: fetchedMessages = [] } = useChatMessages(sessionId)
  const [optimisticMessages, setOptimisticMessages] = useState<ChatMessage[]>([])
  const [isSending, setIsSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Merge fetched + optimistic (deduplicate by id)
  const fetchedIds = new Set(fetchedMessages.map((m: ChatMessage) => m.id))
  const merged = [
    ...fetchedMessages,
    ...optimisticMessages.filter((m: ChatMessage) => !fetchedIds.has(m.id)),
  ].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())

  const sendMessage = useCallback(
    // overrideSessionId allows callers to pass a freshly-created session ID
    // before React has re-rendered with the updated state (avoids stale closure).
    async (text: string, overrideSessionId?: string) => {
      const sid = overrideSessionId ?? sessionId
      if (!sid || !text.trim() || isSending) return

      const tempUserMsg: ChatMessage = {
        id: `temp-user-${Date.now()}`,
        session_id: sid,
        role: 'user',
        content: text,
        sources: null,
        created_at: new Date().toISOString(),
      }
      const tempAssistantMsg: ChatMessage = {
        id: `temp-assistant-${Date.now()}`,
        session_id: sid,
        role: 'assistant',
        content: '…',
        sources: null,
        created_at: new Date().toISOString(),
      }

      setOptimisticMessages((prev) => [...prev, tempUserMsg, tempAssistantMsg])
      setIsSending(true)
      setError(null)

      try {
        await chatService.sendMessage(sid, text, projectId)
        setOptimisticMessages((prev) =>
          prev.filter((m) => m.id !== tempAssistantMsg.id && m.id !== tempUserMsg.id),
        )
        // Use globalMutate with the actual session key — avoids the stale-closure
        // problem where mutate() from useChatMessages(null) is a no-op when the
        // session was just auto-created before this hook re-rendered with the new ID.
        await globalMutate(`/chat/sessions/${sid}/messages`)
      } catch (err: unknown) {
        setOptimisticMessages((prev) =>
          prev.filter((m) => m.id !== tempAssistantMsg.id),
        )
        const axiosErr = err as { response?: { data?: { detail?: string } } }
        setError(axiosErr?.response?.data?.detail ?? 'Failed to send message')
      } finally {
        setIsSending(false)
      }
    },
    [sessionId, projectId, isSending],
  )

  return {
    messages: merged,
    isLoading: !fetchedMessages && !error,
    isSending,
    sendMessage,
    error,
  }
}
