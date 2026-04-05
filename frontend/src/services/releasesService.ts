import type { PaginatedResponse } from '@/types/common'
import type { LinkedRun, Release, ReleaseDetail, ReleasePhase } from '@/types/releases'
import { deleteData, getData, postData, putData } from './http'

export const releasesService = {
  list: (projectId: string | null, status?: string) =>
    getData<PaginatedResponse<Release>>('/api/v1/releases', {
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
}
export type { LinkedRun, Release, ReleaseDetail, ReleasePhase }
