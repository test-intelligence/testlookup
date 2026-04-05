import { api } from './api'

export type UserRole = 'VIEWER' | 'TESTER' | 'QA_ENGINEER' | 'QA_LEAD' | 'ADMIN'

export interface UserItem {
  id: string
  email: string
  username: string
  full_name: string | null
  role: UserRole
  is_active: boolean
  created_at: string
}

export interface ProjectMember {
  id: string
  user_id: string
  project_id: string
  role: UserRole
  created_at: string
  email: string
  username: string
  full_name: string | null
}

export interface ApiKey {
  id: string
  name: string
  key_hint: string
  scopes: string[]
  is_active: boolean
  expires_at: string | null
  last_used_at: string | null
  created_at: string
}

export interface ApiKeyCreated extends ApiKey {
  raw_key: string
}

export interface InviteUserResponse {
  id: string
  email: string
  role: UserRole
  expires_at: string
  invitation_link: string
}

export interface AdminCreateUserResponse {
  id: string
  email: string
  username: string
  full_name: string | null
  role: UserRole
  is_active: boolean
  created_at: string
  temp_password: string
}

export const userManagementService = {
  // Users
  listUsers: (params?: { is_active?: boolean; role?: UserRole }) =>
    api.get<UserItem[]>('/api/v1/users', { params }).then((r) => r.data),

  updateUserRole: (userId: string, role: UserRole) =>
    api.patch<UserItem>(`/api/v1/users/${userId}/role`, { role }).then((r) => r.data),

  updateUserStatus: (userId: string, is_active: boolean) =>
    api.patch<UserItem>(`/api/v1/users/${userId}/status`, { is_active }).then((r) => r.data),

  inviteUser: (email: string, role: UserRole) =>
    api.post<InviteUserResponse>('/api/v1/users/invite', { email, role }).then((r) => r.data),

  createUser: (email: string, username: string, role: UserRole, full_name?: string) =>
    api
      .post<AdminCreateUserResponse>('/api/v1/users', { email, username, role, full_name })
      .then((r) => r.data),

  updateUserProfile: (userId: string, updates: {
    email?: string
    username?: string
    full_name?: string | null
    role?: UserRole
    is_active?: boolean
  }) =>
    api.patch<UserItem>(`/api/v1/users/${userId}`, updates).then((r) => r.data),

  // User memberships (aggregated — single call replaces N+1)
  getUserMemberships: (userId: string) =>
    api.get<Array<{ project_id: string; project_name: string; role: UserRole; created_at: string }>>(
      `/api/v1/users/${userId}/memberships`,
    ).then((r) => r.data),

  // Project members
  listProjectMembers: (projectId: string) =>
    api.get<ProjectMember[]>(`/api/v1/projects/${projectId}/members`).then((r) => r.data),

  addProjectMember: (projectId: string, user_id: string, role: UserRole) =>
    api
      .post<ProjectMember>(`/api/v1/projects/${projectId}/members`, { user_id, role })
      .then((r) => r.data),

  updateProjectMemberRole: (projectId: string, userId: string, role: UserRole) =>
    api
      .patch<ProjectMember>(`/api/v1/projects/${projectId}/members/${userId}`, { role })
      .then((r) => r.data),

  removeProjectMember: (projectId: string, userId: string) =>
    api.delete(`/api/v1/projects/${projectId}/members/${userId}`),

  // API Keys
  listApiKeys: () => api.get<ApiKey[]>('/api/v1/keys').then((r) => r.data),

  createApiKey: (name: string, scopes: string[], expires_days?: number) =>
    api
      .post<ApiKeyCreated>('/api/v1/keys', { name, scopes, expires_days })
      .then((r) => r.data),

  revokeApiKey: (keyId: string) => api.delete(`/api/v1/keys/${keyId}`),
}