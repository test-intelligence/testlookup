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

export const projectsService = {
  list: (): Promise<Project[]> => getData('/api/v1/projects'),
  get: (id: string): Promise<Project> => getData(`/api/v1/projects/${id}`),
  create: (data: Partial<Project>) => postData<Project, Partial<Project>>('/api/v1/projects', data),
  update: (id: string, data: ProjectUpdate) => putData<Project, ProjectUpdate>(`/api/v1/projects/${id}`, data),
  delete: (id: string) => deleteData(`/api/v1/projects/${id}`),
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
