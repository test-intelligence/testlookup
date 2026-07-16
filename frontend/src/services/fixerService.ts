import type {
  FixAttemptDetail,
  FixAttemptListResponse,
  FixerConfig,
  StartFixerRunResponse,
} from '@/types/fixer'
import { getData, postData, putData } from './http'

/**
 * Fixer agent (AI-2) — per-project config, on-demand runs, and the
 * fix-attempts ledger. Pinned Wave-D contract; PUT config is QA_LEAD+.
 *
 * Error semantics on `startRun`: 403 = Fixer disabled, 409 = a run is already
 * in progress, 422 = suggest mode without a validation runner configured.
 */
export const fixerService = {
  getConfig: (projectId: string) =>
    getData<FixerConfig>(`/api/v1/projects/${projectId}/fixer/config`),

  updateConfig: (projectId: string, config: FixerConfig) =>
    putData<FixerConfig, FixerConfig>(`/api/v1/projects/${projectId}/fixer/config`, config),

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
