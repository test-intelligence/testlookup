import type { LinkedRun, Release, ReleaseDetail, ReleaseOutcome, ReleasePhase } from '@/types/releases'
import { deleteData, getData, postData, putData } from './http'

/**
 * `GET /api/v1/releases`: every matching release, unpaged — `{items, total}`,
 * no `page`/`size`/`pages` (`release_service.list_releases`). The router's
 * no-membership path answers the same shape (HYG-10).
 */
export interface ReleaseList {
  items: Release[]
  total: number
}

export const releasesService = {
  list: (projectId: string | null, status?: string) =>
    getData<ReleaseList>('/api/v1/releases', {
      params: { ...(projectId ? { project_id: projectId } : {}), ...(status ? { status } : {}) },
    }),

  get: (id: string) =>
    getData<ReleaseDetail>(`/api/v1/releases/${id}`),

  create: (body: {
    project_id: string
    name: string
    version?: string
    description?: string
    status?: string
    planned_date?: string
    phases?: Partial<ReleasePhase>[]
  }) => postData<Release, {
    project_id: string
    name: string
    version?: string
    description?: string
    status?: string
    planned_date?: string
    phases?: Partial<ReleasePhase>[]
  }>('/api/v1/releases', body),

  update: (id: string, body: Partial<Release>) =>
    putData<Release, Partial<Release>>(`/api/v1/releases/${id}`, body),

  delete: (id: string) => deleteData(`/api/v1/releases/${id}`),

  addPhase: (releaseId: string, body: Partial<ReleasePhase>) =>
    postData<ReleasePhase, Partial<ReleasePhase>>(`/api/v1/releases/${releaseId}/phases`, body),

  updatePhase: (releaseId: string, phaseId: string, body: Partial<ReleasePhase>) =>
    putData<ReleasePhase, Partial<ReleasePhase>>(`/api/v1/releases/${releaseId}/phases/${phaseId}`, body),

  deletePhase: (releaseId: string, phaseId: string) =>
    deleteData(`/api/v1/releases/${releaseId}/phases/${phaseId}`),

  linkRun: (releaseId: string, testRunId: string, phaseId?: string) =>
    postData(`/api/v1/releases/${releaseId}/test-runs`, { test_run_id: testRunId, phase_id: phaseId }),

  unlinkRun: (releaseId: string, runId: string) =>
    deleteData(`/api/v1/releases/${releaseId}/test-runs/${runId}`),

  markOutcome: (releaseId: string, outcome: 'incident' | 'rollback', reason: string) =>
    postData<ReleaseOutcome, { outcome: 'incident' | 'rollback'; reason: string }>(
      `/api/v1/releases/${releaseId}/outcomes`,
      { outcome, reason },
    ),
}
export type { LinkedRun, Release, ReleaseDetail, ReleaseOutcome, ReleasePhase }
