/**
 * Live Stream Service
 *
 * API calls for creating/managing live execution sessions and fetching
 * active session state. The hot-path batch endpoint is used by the
 * Python client SDK, not the dashboard.
 */
import type {
  ActiveSessionsResponse,
  LiveSessionCreate,
  LiveSessionResponse,
  LiveSessionState,
  SessionDetail,
} from '@/types/live-stream'
import { deleteData, getData, postData } from './http'

const liveStreamService = {
  createSession: (payload: LiveSessionCreate) =>
    postData<LiveSessionResponse, LiveSessionCreate>('/api/v1/stream/sessions', payload),

  getActiveSessions: (projectId?: string) => {
    const params = projectId ? { project_id: projectId } : {}
    return getData<ActiveSessionsResponse>('/api/v1/stream/active', { params, timeout: 15000 })
  },

  getSession: (sessionId: string) =>
    getData<SessionDetail>(`/api/v1/stream/sessions/${sessionId}`),

  closeSession: (sessionId: string) =>
    deleteData(`/api/v1/stream/sessions/${sessionId}`),
}

export default liveStreamService
export type {
  ActiveSessionsResponse,
  LiveSessionCreate,
  LiveSessionResponse,
  LiveSessionState,
  SessionDetail,
}
