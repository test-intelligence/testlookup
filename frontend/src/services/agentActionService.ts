import { getData, patchData } from './http'
import type {
  AgentAction,
  AgentActionDecision,
  AgentActionList,
  AgentActionListQuery,
} from '@/types/agentActions'

/**
 * The agent-action queue (W15 AGT-01). The list is QA_LEAD+ (a project-bound
 * API key may read its own project); a decision needs an interactive login,
 * is QA_LEAD+, and is refused with `{ code, message }` — the caller renders
 * the message inline, so the shared error toast is suppressed.
 */
export const agentActionService = {
  listAgentActions: (projectId: string, q: AgentActionListQuery = {}) =>
    getData<AgentActionList>(`/api/v1/projects/${projectId}/agent-actions`, { params: q }),

  decideAgentAction: (projectId: string, actionId: string, body: AgentActionDecision) =>
    patchData<AgentAction, AgentActionDecision>(
      `/api/v1/projects/${projectId}/agent-actions/${encodeURIComponent(actionId)}`,
      body,
      { suppressToast: true },
    ),
}

export const { listAgentActions, decideAgentAction } = agentActionService

export default agentActionService
