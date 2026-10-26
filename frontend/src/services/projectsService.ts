import type { Project, ProjectUpdate } from '@/types/projects'
import { deleteData, getData, postData, putData } from './http'

export type ProjectResetMode = 'runs' | 'full'

export interface ProjectResetRequest {
  mode: ProjectResetMode
  confirmation_name: string
}

export interface ProjectResetResponse {
  mode: ProjectResetMode
  deleted: Record<string, number>
}

export interface DefaultQaLeadResetResponse {
  user_id: string
  email: string
  username: string
  password: string
  project_id: string
  actor_id: string
}

export interface ProjectRestoreResult {
  restored: boolean
  project_id: string
  api_keys_still_revoked: number
  default_qa_lead_reactivated: boolean
}

export const projectsService = {
  list: (): Promise<Project[]> => getData('/api/v1/projects'),
  get: (id: string): Promise<Project> => getData(`/api/v1/projects/${id}`),
  create: (data: Partial<Project>) => postData<Project, Partial<Project>>('/api/v1/projects', data),
  /** `opts.suppressToast`: the caller renders the failure (W10 INT-03: a field-level 422 shows under its field). */
  update: (id: string, data: ProjectUpdate, opts?: { suppressToast?: boolean }) =>
    putData<Project, ProjectUpdate>(`/api/v1/projects/${id}`, data, opts?.suppressToast ? { suppressToast: true } : undefined),
  delete: (id: string) => deleteData(`/api/v1/projects/${id}`),
  /** W17 ADM-04 (ADMIN): every project, archived ones included, with when / by whom. */
  listWithArchived: (): Promise<Project[]> => getData('/api/v1/projects', { params: { include_archived: true } }),
  /** W17 ADM-04 (ADMIN): bring an archived project back; its API keys stay revoked. */
  restore: (id: string): Promise<ProjectRestoreResult> => postData(`/api/v1/projects/${id}/restore`, {}),
  reset: (id: string, body: ProjectResetRequest) =>
    postData<ProjectResetResponse, ProjectResetRequest>(`/api/v1/projects/${id}/reset`, body),
  /**
   * Reset the password of the project's auto-provisioned default QA-lead
   * account. Any user with project access can call this — the account is
   * shared, not personal — and the new password is returned in the body
   * for the caller to hand off.
   */
  resetDefaultQaLeadPassword: (id: string) =>
    postData<DefaultQaLeadResetResponse, Record<string, never>>(
      `/api/v1/projects/${id}/default-qa-lead/reset-password`,
      {},
    ),
}
