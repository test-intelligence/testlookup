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
import { scopeParam, type ScopeValue } from '@/lib/scopeParams'

const liveStreamService = {
  createSession: (payload: LiveSessionCreate) =>
    postData<LiveSessionResponse, LiveSessionCreate>('/api/v1/stream/sessions', payload),

  getActiveSessions: (projectId?: string, suiteName?: ScopeValue, days?: number) => {
    const params = {
      ...(projectId ? { project_id: projectId } : {}),
      // C1 wire rule: none → absent, one → the scalar it always was, several
      // (VIZ-303, flag on) → a repeated `suite_name` (backend: E3 round A).
      ...scopeParam('suite_name', suiteName),
      // Backend default is 7. Only forward the param when the caller set it
      // explicitly so the request stays clean in the dev tools network panel.
      ...(typeof days === 'number' ? { days } : {}),
    }
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
