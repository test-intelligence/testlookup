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
  /**
   * The member list includes the per-project synthetic QA lead and service
   * accounts; a release owner picker leaves them out (REL-02). Optional: an
   * older server omits them.
   */
  is_service_account?: boolean
  is_synthetic?: boolean
  is_active?: boolean
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

export interface UsersPageParams {
  is_active?: boolean
  role?: UserRole
  q?: string
  page?: number
  page_size?: number
}

export interface UsersPage {
  items: UserItem[]
  /** Users matching the filters on every page; null when the server did not say. */
  total: number | null
}

export const userManagementService = {
  // Users
  /**
   * List users. ``role`` and ``is_active`` are applied by the SERVER over the
   * whole table -- filtering the fetched page client-side answers the wrong
   * question once the table is larger than one page.
   *
   * ``page_size`` is capped at 200 by the API. A caller that receives exactly
   * ``page_size`` rows must assume the list is truncated.
   */
  listUsers: (params?: { is_active?: boolean; role?: UserRole; page_size?: number }) =>
    api.get<UserItem[]>('/api/v1/users', { params }).then((r) => r.data),

  /**
   * ADM-03: one page of users and the number matching the filters across
   * every page (the ``X-Total-Count`` header; the body stays a bare list).
   * ``q`` is matched by the SERVER on username, email and full name. An older
   * server without the header answers ``total: null`` -- never a guess.
   */
  listUsersPage: (params: UsersPageParams) =>
    api.get<UserItem[]>('/api/v1/users', { params }).then((r) => {
      const items = Array.isArray(r.data) ? r.data : []
      const raw = r.headers?.['x-total-count']
      const total = raw == null || raw === '' || Number.isNaN(Number(raw)) ? null : Number(raw)
      return { items, total } as UsersPage
    }),

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