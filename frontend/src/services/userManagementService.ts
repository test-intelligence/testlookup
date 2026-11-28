import type { JobRole, Role } from '@/types/permissions'
import { api } from './api'

/** Every role (enterprise roles E00): the five ranks, the four job roles, AGENT (agents only). */
export type UserRole = Role
export type { JobRole }

/** Only the extra roles given, so an older server sees the body it always saw. */
function withExtras<T extends object>(body: T, additional_roles?: JobRole[]): T & { additional_roles?: JobRole[] } {
  return additional_roles === undefined ? body : { ...body, additional_roles }
}

export interface UserItem {
  id: string
  email: string
  username: string
  full_name: string | null
  role: UserRole
  /** Enterprise roles D-16: extra job roles beside `role` (which alone gives the rank). An older server omits it. */
  additional_roles?: JobRole[]
  is_active: boolean
  created_at: string
  /** W31 STRAT-12: the account was erased (pseudonymised) — render "Erased user" + the badge, no actions. */
  is_erased?: boolean
  erased_at?: string | null
  /** W31: a service / synthetic account holds no personal data — no "Personal data" action on its row. */
  is_service_account?: boolean
  is_synthetic?: boolean
}

export interface ProjectMember {
  id: string
  user_id: string
  project_id: string
  role: UserRole
  /** Extra job roles in this project. On a write, the person's global roles and these intersect. */
  additional_roles?: JobRole[]
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
  /** W20 ADM-09: null = granted by a person; `scim:<group>` = granted through that SCIM group. */
  source?: string | null
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
  additional_roles?: JobRole[]
  expires_at: string
  /** `{base}/accept-invite#token=…` — shown once; only its hash is stored (W17b ADM-01). */
  invitation_link: string
  /** True when the link was also emailed (SMTP configured and the send worked). An older server omits it. */
  email_sent?: boolean
}

export interface InviteProjectMembership {
  project_id: string
  role: UserRole
  additional_roles?: JobRole[]
}

export type InvitationStatus = 'pending' | 'expired' | 'used' | 'revoked'

/** W17b ADM-01: a row of the Pending invitations tab. Never carries a token. */
export interface InvitationSummary {
  id: string
  email: string
  role: UserRole
  additional_roles?: JobRole[]
  status: InvitationStatus
  expires_at: string
  created_at: string | null
  used_at: string | null
  revoked_at: string | null
  invited_by_name: string | null
  project_memberships: Array<{
    project_id: string
    project_name: string | null
    role: string
    additional_roles?: JobRole[]
  }>
}

/** W17b ADM-02: an admin-issued reset link; the admin never sees or sets the password. */
export interface PasswordResetLinkResponse {
  link: string
  email_sent: boolean
  expires_at: string
}

export interface AdminCreateUserResponse {
  id: string
  email: string
  username: string
  full_name: string | null
  role: UserRole
  additional_roles?: JobRole[]
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

  inviteUser: (
    email: string,
    role: UserRole,
    project_memberships: InviteProjectMembership[] = [],
    additional_roles?: JobRole[],
  ) =>
    // The Invite User form shows the refusal itself (an e-mail on a reserved domain such as .local is a
    // 422 naming the field), so the global toast stays quiet: before, it raised a second, generic one.
    api
      .post<InviteUserResponse>(
        '/api/v1/users/invite',
        withExtras({ email, role, project_memberships }, additional_roles),
        { suppressToast: true },
      )
      .then((r) => r.data),

  listInvitations: () =>
    api.get<InvitationSummary[]>('/api/v1/users/invitations').then((r) => (Array.isArray(r.data) ? r.data : [])),

  revokeInvitation: (invitationId: string) =>
    api.post<InvitationSummary>(`/api/v1/users/invitations/${invitationId}/revoke`).then((r) => r.data),

  resendInvitation: (invitationId: string) =>
    api.post<InviteUserResponse>(`/api/v1/users/invitations/${invitationId}/resend`).then((r) => r.data),

  issuePasswordResetLink: (userId: string) =>
    api.post<PasswordResetLinkResponse>(`/api/v1/users/${userId}/password-reset-link`).then((r) => r.data),

  // Add User and Edit User show the refusal in the form, as Invite User does.
  createUser: (email: string, username: string, role: UserRole, full_name?: string, additional_roles?: JobRole[]) =>
    api
      .post<AdminCreateUserResponse>(
        '/api/v1/users',
        withExtras({ email, username, role, full_name }, additional_roles),
        { suppressToast: true },
      )
      .then((r) => r.data),

  updateUserProfile: (userId: string, updates: {
    email?: string
    username?: string
    full_name?: string | null
    role?: UserRole
    /** `[]` clears the extra roles; omitted keeps them. */
    additional_roles?: JobRole[]
    is_active?: boolean
  }) =>
    api.patch<UserItem>(`/api/v1/users/${userId}`, updates, { suppressToast: true }).then((r) => r.data),

  // User memberships (aggregated — single call replaces N+1)
  getUserMemberships: (userId: string) =>
    api.get<Array<{
      project_id: string
      project_name: string
      role: UserRole
      additional_roles?: JobRole[]
      created_at: string
    }>>(
      `/api/v1/users/${userId}/memberships`,
    ).then((r) => r.data),

  // Project members
  listProjectMembers: (projectId: string) =>
    api.get<ProjectMember[]>(`/api/v1/projects/${projectId}/members`).then((r) => r.data),

  addProjectMember: (projectId: string, user_id: string, role: UserRole, additional_roles?: JobRole[]) =>
    api
      .post<ProjectMember>(`/api/v1/projects/${projectId}/members`, withExtras({ user_id, role }, additional_roles))
      .then((r) => r.data),

  /** `additional_roles`: `[]` clears them, omitted keeps them. */
  updateProjectMemberRole: (projectId: string, userId: string, role: UserRole, additional_roles?: JobRole[]) =>
    api
      .patch<ProjectMember>(`/api/v1/projects/${projectId}/members/${userId}`, withExtras({ role }, additional_roles))
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