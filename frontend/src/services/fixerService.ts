import type {
  FixAttemptDetail,
  FixAttemptListResponse,
  FixerConfig,
  StartFixerRunResponse,
} from '@/types/fixer'
import type { AgentConfigDocument, AgentConfigView } from '@/types/agentConfig'
import { getData, postData, putData } from './http'

const FIXER_AGENT_ID = 'fixer'

/**
 * Fixer agent (AI-2). The pinned FixerConfig is projected from AgentConfig;
 * its former route remains a read-only server alias for one release.
 *
 * Error semantics on `startRun`: 403 = Fixer disabled, 409 = a run is already
 * in progress, 422 = suggest mode without a validation runner configured.
 */
export const fixerService = {
  getConfig: async (projectId: string): Promise<FixerConfig> => {
    const view = await getData<AgentConfigView>(
      `/api/v1/projects/${projectId}/agent-configs/${FIXER_AGENT_ID}`,
    )
    const extension = view.config.extensions?.fixer
    if (!extension) throw new Error('Fixer AgentConfig extension is missing')
    return {
      enabled: view.config.enabled,
      mode: view.config.mode === 'suggest' ? 'suggest' : 'shadow',
      ...extension,
    }
  },

  updateConfig: async (projectId: string, update: FixerConfig): Promise<FixerConfig> => {
    const path = `/api/v1/projects/${projectId}/agent-configs/${FIXER_AGENT_ID}`
    const view = await getData<AgentConfigView>(path)
    const config: AgentConfigDocument = {
      ...view.config,
      enabled: update.enabled,
      mode: update.mode,
      extensions: { ...(view.config.extensions ?? { investigator: null, fixer: null }), fixer: {
        runner: update.runner,
        test_globs: update.test_globs,
        budgets: update.budgets,
        schedule: update.schedule,
      } },
    }
    const saved = await putData<AgentConfigView, AgentConfigDocument>(path, config)
    const extension = saved.config.extensions?.fixer
    if (!extension) throw new Error('Saved Fixer AgentConfig extension is missing')
    return {
      enabled: saved.config.enabled,
      mode: saved.config.mode === 'suggest' ? 'suggest' : 'shadow',
      ...extension,
    }
  },

  startRun: (projectId: string) =>
    postData<StartFixerRunResponse>(`/api/v1/projects/${projectId}/fixer/run`),

  listAttempts: (projectId: string, opts: { limit?: number; offset?: number } = {}) =>
    getData<FixAttemptListResponse>(`/api/v1/projects/${projectId}/fixer/attempts`, {
      params: { limit: opts.limit ?? 25, offset: opts.offset ?? 0 },
    }),

  getAttempt: (attemptId: string) =>
    getData<FixAttemptDetail>(`/api/v1/fixer/attempts/${attemptId}`),
}

export default fixerService
