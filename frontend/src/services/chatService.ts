import type { ChatMessage, ChatSession, RunSummary } from '@/types/chat'
import { deleteData, getData, postData } from './http'

const chatService = {
  listSessions: () => getData<ChatSession[]>('/api/v1/chat/sessions'),

  getRunSummaries: (projectId?: string | null, days = 5) =>
    getData<RunSummary[]>('/api/v1/chat/run-summaries', {
      params: { project_id: projectId ?? undefined, days },
    }),

  createSession: (payload: { project_id?: string; title?: string }) =>
    postData<ChatSession, { project_id?: string; title?: string }>('/api/v1/chat/sessions', payload),

  deleteSession: (sessionId: string) =>
    deleteData(`/api/v1/chat/sessions/${sessionId}`),

  getMessages: (sessionId: string, limit = 50) =>
    getData<ChatMessage[]>(`/api/v1/chat/sessions/${sessionId}/messages`, {
      params: { limit },
    }),

  sendMessage: (
    sessionId: string,
    message: string,
    projectId?: string | null,
  ) =>
    postData<{ session_id: string; reply: string; sources: unknown[] }, { message: string; project_id?: string }>(
      `/api/v1/chat/sessions/${sessionId}/messages`,
      { message, project_id: projectId ?? undefined },
    ),
}

export default chatService
export type { ChatMessage, ChatSession, RunSummary }
