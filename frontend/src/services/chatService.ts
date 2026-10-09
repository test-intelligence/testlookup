import type { ChatMessage, ChatSession, RunSummary } from '@/types/chat'
import { deleteData, getData, postData } from './http'

/**
 * Ask-AI chat sessions and history. Asking a question is streamed — see
 * `services/chatStream.ts`.
 */
const chatService = {
  /** The caller's conversations; `projectId` narrows them to one project. */
  listSessions: (projectId?: string | null) =>
    getData<ChatSession[]>('/api/v1/chat/sessions', {
      params: { project_id: projectId ?? undefined },
    }),

  getRunSummaries: (projectId?: string | null, days = 5) =>
    getData<RunSummary[]>('/api/v1/chat/run-summaries', {
      params: { project_id: projectId ?? undefined, days },
    }),

  createSession: (payload: { project_id?: string; title?: string }) =>
    postData<ChatSession, { project_id?: string; title?: string }>('/api/v1/chat/sessions', payload),

  deleteSession: (sessionId: string) =>
    deleteData(`/api/v1/chat/sessions/${sessionId}`),

  /** The latest `limit` messages, oldest first. */
  getMessages: (sessionId: string, limit = 100) =>
    getData<ChatMessage[]>(`/api/v1/chat/sessions/${sessionId}/messages`, {
      params: { limit },
    }),
}

export default chatService
export type { ChatMessage, ChatSession, RunSummary }
