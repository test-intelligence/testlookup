import { Navigate, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { Edit3, Plus, Shield, UserCheck, UserPlus, UserX } from 'lucide-react'
import toast from 'react-hot-toast'
import { useUsers, refreshUsers } from '@/hooks/useUserManagement'
import { userManagementService, type UserItem, type UserRole } from '@/services/userManagementService'
import { projectsService } from '@/services/projectsService'
import { usePermissions } from '@/hooks/usePermissions'
import { useAuthStore } from '@/store/authStore'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import PageHeader from '@/components/ui/PageHeader'
import Tabs, { type TabItem } from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import { helpTopicParam } from '@/components/help/helpTopics'
import { ProjectMembersTab } from './ProjectMembersTab'
import { copyTextToClipboard } from '@/utils/clipboard'
import type { Project } from '@/types/projects'

const ROLES: UserRole[] = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']

// Build an invitation URL safely. The backend returns a relative path like
// "/accept-invite?token=...". We refuse to render anything that tries to
// escape the current origin (protocol-relative "//evil.com", absolute
// "https://evil.com", or javascript:) — that would turn a compromised or
// buggy backend into a full phishing redirect.
function buildInvitationUrl(rawPath: string): string {
  if (typeof rawPath !== 'string' || rawPath.length === 0) return ''
  if (!rawPath.startsWith('/') || rawPath.startsWith('//')) return ''
  try {
    return new URL(rawPath, window.location.origin).toString()
  } catch {
    return ''
  }
}

const ROLE_COLORS: Record<UserRole, string> = {
  VIEWER: 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]',
  TESTER: 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)]',
  QA_ENGINEER: 'bg-[var(--status-passed-bg)]/50 text-[var(--status-passed)]',
  QA_LEAD: 'bg-[var(--status-broken-bg)]/50 text-[var(--status-broken)]',
  ADMIN: 'bg-[var(--status-failed-bg)]/50 text-[var(--status-failed)]',
}

/**
 * Re-read the signed-in user when the record just written is their own.
 *
 * `authStore.user` is client state persisted to localStorage, written only by
 * `setAuth`/`fetchUser`. `fetchUser`'s single caller is a `ProtectedRoute`
 * effect keyed on `[hasHydrated]`, which runs once per page load — so without
 * this the header identity, and `usePermissions()` which reads `user.role`,
 * keep the pre-edit values for the rest of the session.
 *
 * Every self-edit path needs it, not just the modal: the inline role dropdown
 * and the active/inactive toggle write the same record.
 */
async function syncSelfIfEdited(editedUserId: string): Promise<void> {
  if (editedUserId === useAuthStore.getState().user?.id) {
    await useAuthStore.getState().fetchUser()
  }
}


const HELP_TOPIC = helpTopicParam('/users')

type UsersPageTab = 'users' | 'project-members'

const TAB_IDS: readonly UsersPageTab[] = ['users', 'project-members']

/**
 * `?tab=` ids are a contract: the settings sub-nav links "Members & access" to
 * `/users?tab=project-members` (`settingsNav.ts`). The signed-in user's own
 * keys were a third tab here; they are "My API keys" at /settings/my-api-keys
 * now (UX redesign P5), where every role that may own a key can reach them —
 * this page is QA lead and admin only. `?tab=api-keys` redirects there.
 */
const TAB_ITEMS: readonly TabItem<UsersPageTab>[] = [
  { id: 'users', label: 'Users' },
  { id: 'project-members', label: 'Project access' },
]

export default function UserManagementPage() {
  const [tab, setTab] = useTabParam(TAB_IDS, 'users')
  const { canManageUsers, isAdmin } = usePermissions()
  const { search } = useLocation()
  // The old My API keys tab: its links (docs, bookmarks) land on the new page.
  if (new URLSearchParams(search).get('tab') === 'api-keys') return <Navigate to="/settings/my-api-keys" replace />

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        helpTopic={HELP_TOPIC}
        title="User Management"
        subtitle="Users and roles, and who has access to each project"
        tabs={<Tabs items={TAB_ITEMS} value={tab} onChange={setTab} ariaLabel="User management sections" />}
      />

      {tab === 'users' && <UsersTab canManageUsers={canManageUsers} isAdmin={isAdmin} />}
      {tab === 'project-members' && <ProjectMembersTab isAdmin={isAdmin} canManageUsers={canManageUsers} />}
    </div>
  )
}

// ── Users Tab ─────────────────────────────────────────────────

function UsersTab({ canManageUsers, isAdmin }: { canManageUsers: boolean; isAdmin: boolean }) {
  const [showInviteModal, setShowInviteModal] = useState(false)
  const [showAddUserModal, setShowAddUserModal] = useState(false)
  const [editUser, setEditUser] = useState<UserItem | null>(null)
  const [filterRole, setFilterRole] = useState<UserRole | ''>('')
  const [filterActive, setFilterActive] = useState<'all' | 'active' | 'inactive'>('all')

  // Filters go to the SERVER. They used to be applied client-side over
  // whatever the one unpaginated request happened to return -- the API
  // defaults to page_size=50, so on a deployment with 132 users, selecting
  // "QA_ENGINEER" answered **2** against a truth of **5**, and "VIEWER"
  // answered 3 against 5, with nothing on screen saying the list was partial.
  // Filtering a truncated page answers a different question than the one the
  // operator asked.
  const PAGE_SIZE = 200 // the API's maximum
  const { data: users, isLoading } = useUsers({
    page_size: PAGE_SIZE,
    ...(filterRole ? { role: filterRole } : {}),
    ...(filterActive === 'all' ? {} : { is_active: filterActive === 'active' }),
  })

  const filtered = users ?? []
  // Exactly PAGE_SIZE rows means the server had more to give. Say so rather
  // than letting the table imply it is complete -- a silent cap is its own
  // defect.
  const isTruncated = filtered.length >= PAGE_SIZE

  async function handleRoleChange(userId: string, role: UserRole) {
    try {
      await userManagementService.updateUserRole(userId, role)
      refreshUsers()
      // Changing your OWN role from the inline dropdown: usePermissions()
      // reads authStore.user.role, so without this every permission gate keeps
      // the old role for the session.
      await syncSelfIfEdited(userId)
      toast.success('Role updated')
    } catch {
      toast.error('Failed to update role')
    }
  }

  async function handleToggleStatus(userId: string, currentActive: boolean) {
    try {
      await userManagementService.updateUserStatus(userId, !currentActive)
      refreshUsers()
      await syncSelfIfEdited(userId)
      toast.success(currentActive ? 'User deactivated' : 'User activated')
    } catch {
      toast.error('Failed to update status')
    }
  }

  if (isLoading) return <div className="flex justify-center py-12"><LoadingSpinner /></div>

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex gap-2">
          <select
            aria-label="Filter by role"
            value={filterRole}
            onChange={(e) => setFilterRole(e.target.value as UserRole | '')}
            className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-1.5"
          >
            <option value="">All roles</option>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <select
            aria-label="Filter by status"
            value={filterActive}
            onChange={(e) => setFilterActive(e.target.value as 'all' | 'active' | 'inactive')}
            className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-1.5"
          >
            <option value="all">All status</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
        </div>
        {isAdmin && (
          <div className="flex gap-2">
            <button
              onClick={() => setShowAddUserModal(true)}
              className="flex items-center gap-1.5 bg-[var(--status-passed-bg)] hover:bg-[var(--status-passed-bg)] text-[var(--color-text)] text-sm px-3 py-1.5 rounded transition-colors"
            >
              <UserPlus className="h-4 w-4" /> Add User
            </button>
            {canManageUsers && (
              <button
                onClick={() => setShowInviteModal(true)}
                className="flex items-center gap-1.5 bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] text-sm px-3 py-1.5 rounded transition-colors"
              >
                <Plus className="h-4 w-4" /> Invite User
              </button>
            )}
          </div>
        )}
      </div>

      {/* A capped list must say so. Silently rendering the first page as if
          it were the whole table is what made the role filter answer 2 when
          the truth was 5. */}
      {isTruncated && (
        <div
          className="rounded-lg border border-[var(--status-broken-bd)]/40 bg-[var(--status-broken-bg)]/10 px-4 py-2.5 text-xs text-[var(--status-broken)]"
          role="status"
        >
          Showing the first {PAGE_SIZE} users. More match this filter than are
          listed &mdash; narrow the role or status filter to see the rest.
        </div>
      )}

      {/* Table */}
      <div className="bg-[var(--color-bg-secondary)] rounded-lg border border-[var(--color-border)] overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)]/80">
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">User</th>
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Role</th>
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Status</th>
              {isAdmin && <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Actions</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--color-border)]">
            {filtered.map((user) => (
              <tr key={user.id} className="hover:bg-[var(--color-bg-hover)]/30 transition-colors">
                <td className="px-4 py-3">
                  <div className="font-medium text-[var(--color-text)]">{user.full_name || user.username}</div>
                  <div className="text-xs text-[var(--color-text-muted)]">{user.email}</div>
                </td>
                <td className="px-4 py-3">
                  {isAdmin ? (
                    <select
                      aria-label={`Role for ${user.username}`}
                      value={user.role}
                      onChange={(e) => handleRoleChange(user.id, e.target.value as UserRole)}
                      className="bg-[var(--color-bg-hover)] border border-[var(--color-border-light)] text-[var(--color-text)] text-xs rounded px-2 py-1"
                    >
                      {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  ) : (
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${ROLE_COLORS[user.role]}`}>
                      <Shield className="h-3 w-3" />{user.role}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                    user.is_active ? 'bg-[var(--status-passed-bg)]/50 text-[var(--status-passed)]' : 'bg-[var(--color-bg-hover)] text-[var(--color-text-muted)]'
                  }`}>
                    {user.is_active ? <UserCheck className="h-3 w-3" /> : <UserX className="h-3 w-3" />}
                    {user.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                {isAdmin && (
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setEditUser(user)}
                        className="text-[var(--color-text)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)]/30 p-1 rounded transition-colors"
                        title="Edit user"
                      >
                        <Edit3 className="h-3.5 w-3.5" />
                      </button>
                      <button
                        onClick={() => handleToggleStatus(user.id, user.is_active)}
                        className={`text-xs px-2 py-1 rounded transition-colors ${
                          user.is_active
                            ? 'text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/30'
                            : 'text-[var(--status-passed)] hover:bg-[var(--status-passed-bg)]/30'
                        }`}
                      >
                        {user.is_active ? 'Deactivate' : 'Activate'}
                      </button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-[var(--color-text-muted)]">No users found</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {showInviteModal && <InviteUserModal onClose={() => setShowInviteModal(false)} />}
      {showAddUserModal && <AddUserModal onClose={() => setShowAddUserModal(false)} />}
      {editUser && <EditUserModal user={editUser} onClose={() => setEditUser(null)} />}
    </div>
  )
}

// ── Edit User Modal ──────────────────────────────────────────

function EditUserModal({ user, onClose }: { user: UserItem; onClose: () => void }) {
  const [email, setEmail] = useState(user.email)
  const [username, setUsername] = useState(user.username)
  const [fullName, setFullName] = useState(user.full_name ?? '')
  const [role, setRole] = useState<UserRole>(user.role)
  const [isActive, setIsActive] = useState(user.is_active)
  const [saving, setSaving] = useState(false)

  // Project assignment
  const [projects, setProjects] = useState<Project[]>([])
  const [assignProjectId, setAssignProjectId] = useState('')
  const [assignRole, setAssignRole] = useState<UserRole>('QA_ENGINEER')
  const [assignedProjects, setAssignedProjects] = useState<Array<{ project_id: string; project_name: string; role: UserRole }>>([])

  useEffect(() => {
    projectsService.list().then(setProjects).catch(() => {})
    // Load user's memberships in a single call (replaces N+1 per-project fetch)
    userManagementService.getUserMemberships(user.id)
      .then(memberships => setAssignedProjects(memberships.map(m => ({
        project_id: m.project_id,
        project_name: m.project_name,
        role: m.role,
      }))))
      .catch(() => {})
  }, [user.id])

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const updates: Record<string, unknown> = {}
      if (email !== user.email) updates.email = email
      if (username !== user.username) updates.username = username
      if (fullName !== (user.full_name ?? '')) updates.full_name = fullName || null
      if (role !== user.role) updates.role = role
      if (isActive !== user.is_active) updates.is_active = isActive

      if (Object.keys(updates).length > 0) {
        await userManagementService.updateUserProfile(user.id, updates as Parameters<typeof userManagementService.updateUserProfile>[1])
        refreshUsers()
        // Editing YOURSELF also has to refresh the identity the header renders.
        // `authStore.user` is client state, not an SWR key: it is written only
        // by setAuth/fetchUser, and fetchUser's single caller is a
        // ProtectedRoute effect keyed on [hasHydrated], which runs once per
        // page load. The store persists `user` to localStorage, so the old
        // name survives navigation and only a hard reload clears it.
        await syncSelfIfEdited(user.id)
        toast.success('User updated')
      } else {
        toast('No changes to save')
      }
      onClose()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || 'Failed to update user')
    } finally {
      setSaving(false)
    }
  }

  async function handleAssignProject() {
    if (!assignProjectId) return
    try {
      await userManagementService.addProjectMember(assignProjectId, user.id, assignRole)
      const proj = projects.find(p => p.id === assignProjectId)
      setAssignedProjects(prev => [...prev, { project_id: assignProjectId, project_name: proj?.name ?? '', role: assignRole }])
      setAssignProjectId('')
      toast.success('Added to project')
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || 'Failed to add to project')
    }
  }

  async function handleRemoveFromProject(projectId: string) {
    try {
      await userManagementService.removeProjectMember(projectId, user.id)
      setAssignedProjects(prev => prev.filter(p => p.project_id !== projectId))
      toast.success('Removed from project')
    } catch {
      toast.error('Failed to remove from project')
    }
  }

  const unassignedProjects = projects.filter(p => !assignedProjects.some(a => a.project_id === p.id))

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="edit-user-title" className="fixed inset-0 bg-[var(--color-bg)]/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg w-full max-w-lg p-6 space-y-5 max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <h2 id="edit-user-title" className="text-lg font-semibold text-[var(--color-text)]">Edit User</h2>

        <form onSubmit={handleSave} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="user-field-0" className="block text-sm text-[var(--color-text-muted)] mb-1">Email</label>
              <input id="user-field-0"
                type="email"
                required
                value={email}
                onChange={e => setEmail(e.target.value)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
              />
            </div>
            <div>
              <label htmlFor="user-field-1" className="block text-sm text-[var(--color-text-muted)] mb-1">Username</label>
              <input id="user-field-1"
                type="text"
                required
                value={username}
                onChange={e => setUsername(e.target.value)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
              />
            </div>
          </div>
          <div>
            <label htmlFor="user-field-2" className="block text-sm text-[var(--color-text-muted)] mb-1">Full Name</label>
            <input id="user-field-2"
              type="text"
              value={fullName}
              onChange={e => setFullName(e.target.value)}
              className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
              placeholder="Jane Doe"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="user-field-3" className="block text-sm text-[var(--color-text-muted)] mb-1">Role</label>
              <select id="user-field-3"
                value={role}
                onChange={e => setRole(e.target.value as UserRole)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
              >
                {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="user-field-4" className="block text-sm text-[var(--color-text-muted)] mb-1">Status</label>
              <select id="user-field-4"
                value={isActive ? 'active' : 'inactive'}
                onChange={e => setIsActive(e.target.value === 'active')}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
              >
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </select>
            </div>
          </div>

          {/* Project memberships */}
          <div className="border-t border-[var(--color-border)] pt-4">
            <p className="text-sm font-medium text-[var(--color-text-secondary)] mb-2">Project Access</p>
            {assignedProjects.length > 0 ? (
              <div className="space-y-1.5 mb-3">
                {assignedProjects.map(ap => (
                  <div key={ap.project_id} className="flex items-center justify-between bg-[var(--color-bg-card)]/60 rounded px-3 py-1.5">
                    <span className="text-sm text-[var(--color-text-secondary)]">{ap.project_name}</span>
                    <div className="flex items-center gap-2">
                      <span className={`text-xs px-1.5 py-0.5 rounded ${ROLE_COLORS[ap.role]}`}>{ap.role}</span>
                      <button
                        type="button"
                        onClick={() => handleRemoveFromProject(ap.project_id)}
                        className="text-[var(--status-failed)]/70 hover:text-[var(--status-failed)] text-xs"
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-[var(--color-text-muted)] mb-3">Not assigned to any projects.</p>
            )}
            {unassignedProjects.length > 0 && (
              <div className="flex items-center gap-2">
                <select
                  aria-label="Project to add"
                  value={assignProjectId}
                  onChange={e => setAssignProjectId(e.target.value)}
                  className="flex-1 bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-xs rounded px-2 py-1.5"
                >
                  <option value="">Add to project…</option>
                  {unassignedProjects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
                <select
                  aria-label="Role in project"
                  value={assignRole}
                  onChange={e => setAssignRole(e.target.value as UserRole)}
                  className="bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-xs rounded px-2 py-1.5"
                >
                  {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
                <button
                  type="button"
                  onClick={handleAssignProject}
                  disabled={!assignProjectId}
                  className="text-xs bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-40 text-[var(--color-btn-primary-text)] px-2 py-1.5 rounded"
                >
                  Add
                </button>
              </div>
            )}
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-4 py-2">
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] text-sm px-4 py-2 rounded transition-colors"
            >
              {saving ? 'Saving…' : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Add User Modal (admin direct create) ──────────────────────

function AddUserModal({ onClose }: { onClose: () => void }) {
  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [fullName, setFullName] = useState('')
  const [role, setRole] = useState<UserRole>('QA_ENGINEER')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<{ id: string; username: string; temp_password: string } | null>(null)

  // Project assignment after creation
  const [projects, setProjects] = useState<Project[]>([])
  const [assignProjectId, setAssignProjectId] = useState('')
  const [assignRole, setAssignRole] = useState<UserRole>('QA_ENGINEER')
  const [assignedProjects, setAssignedProjects] = useState<string[]>([])

  useEffect(() => {
    projectsService.list().then(setProjects).catch(() => {})
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    try {
      const res = await userManagementService.createUser(email, username, role, fullName || undefined)
      setResult({ id: res.id, username: res.username, temp_password: res.temp_password })
      refreshUsers()
      toast.success('User created')
    } catch {
      toast.error('Failed to create user')
    } finally {
      setLoading(false)
    }
  }

  async function handleAssignProject() {
    if (!assignProjectId || !result) return
    try {
      await userManagementService.addProjectMember(assignProjectId, result.id, assignRole)
      setAssignedProjects(prev => [...prev, assignProjectId])
      setAssignProjectId('')
      toast.success('Added to project')
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || 'Failed to add to project')
    }
  }

  const unassignedProjects = projects.filter(p => !assignedProjects.includes(p.id))

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="add-user-title" className="fixed inset-0 bg-[var(--color-bg)]/60 flex items-center justify-center z-50">
      <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg w-full max-w-lg p-6 space-y-4">
        <h2 id="add-user-title" className="text-lg font-semibold text-[var(--color-text)]">Add User</h2>
        {result ? (
          <div className="space-y-4">
            <p className="text-sm text-[var(--color-text-secondary)]">
              User <strong className="text-[var(--color-text)]">{result.username}</strong> created. Share the temporary password:
            </p>
            <div className="bg-[var(--color-bg-card)] border border-[var(--status-broken-bd)]/50 rounded p-3">
              <p className="text-xs text-[var(--color-text-muted)] mb-1">Temporary password (shown once):</p>
              <code className="text-sm text-[var(--status-broken)] font-bold break-all">{result.temp_password}</code>
            </div>
            <p className="text-xs text-[var(--color-text-muted)]">The user should change this password after first login.</p>

            {/* Project assignment section */}
            <div className="border-t border-[var(--color-border)] pt-3">
              <p className="text-sm font-medium text-[var(--color-text-secondary)] mb-2">Add to Projects (optional)</p>
              {assignedProjects.length > 0 && (
                <div className="space-y-1 mb-2">
                  {assignedProjects.map(pid => {
                    const proj = projects.find(p => p.id === pid)
                    return (
                      <div key={pid} className="flex items-center gap-2 text-xs text-[var(--status-passed)]">
                        <UserCheck className="h-3 w-3" />
                        {proj?.name ?? pid}
                      </div>
                    )
                  })}
                </div>
              )}
              {unassignedProjects.length > 0 && (
                <div className="flex items-center gap-2">
                  <select
                    aria-label="Project to add"
                    value={assignProjectId}
                    onChange={e => setAssignProjectId(e.target.value)}
                    className="flex-1 bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-xs rounded px-2 py-1.5"
                  >
                    <option value="">Select project…</option>
                    {unassignedProjects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                  <select
                    aria-label="Role in project"
                    value={assignRole}
                    onChange={e => setAssignRole(e.target.value as UserRole)}
                    className="bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-xs rounded px-2 py-1.5"
                  >
                    {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                  <button
                    onClick={handleAssignProject}
                    disabled={!assignProjectId}
                    className="text-xs bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-40 text-[var(--color-btn-primary-text)] px-2 py-1.5 rounded"
                  >
                    Add
                  </button>
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={async () => {
                  const ok = await copyTextToClipboard(result.temp_password)
                  if (ok) toast.success('Copied!')
                  else toast.error('Clipboard access denied — copy manually')
                }}
                className="text-sm text-[var(--color-text)] hover:text-[var(--color-text-secondary)] px-3 py-1.5"
              >
                Copy Password
              </button>
              <button
                onClick={onClose}
                className="bg-[var(--color-bg-hover)] hover:bg-[var(--color-bg-card)] text-[var(--color-text)] text-sm px-4 py-2 rounded"
              >
                Done
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Two columns, as in Edit User (P5 item 4): email | username,
                full name | role. */}
            <div data-add-user-fields="" className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="user-field-5" className="block text-sm text-[var(--color-text-muted)] mb-1">Email address</label>
                <input id="user-field-5" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                  className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
                  placeholder="user@company.com" />
              </div>
              <div>
                <label htmlFor="user-field-6" className="block text-sm text-[var(--color-text-muted)] mb-1">Username</label>
                <input id="user-field-6" type="text" required value={username} onChange={(e) => setUsername(e.target.value)}
                  className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
                  placeholder="jdoe" />
              </div>
              <div>
                <label htmlFor="user-field-7" className="block text-sm text-[var(--color-text-muted)] mb-1">Full name (optional)</label>
                <input id="user-field-7" type="text" value={fullName} onChange={(e) => setFullName(e.target.value)}
                  className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
                  placeholder="Jane Doe" />
              </div>
              <div>
                <label htmlFor="user-field-8" className="block text-sm text-[var(--color-text-muted)] mb-1">Role</label>
                <select id="user-field-8" value={role} onChange={(e) => setRole(e.target.value as UserRole)}
                  className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]">
                  {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={onClose} className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-4 py-2">Cancel</button>
              <button type="submit" disabled={loading}
                className="bg-[var(--status-passed-bg)] hover:bg-[var(--status-passed-bg)] disabled:opacity-50 text-[var(--color-text)] text-sm px-4 py-2 rounded transition-colors">
                {loading ? 'Creating…' : 'Create User'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

// ── Invite Modal ──────────────────────────────────────────────

function InviteUserModal({ onClose }: { onClose: () => void }) {
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<UserRole>('QA_ENGINEER')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<{ invitation_link: string } | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    try {
      const res = await userManagementService.inviteUser(email, role)
      setResult(res)
      refreshUsers()
      toast.success('Invitation created')
    } catch {
      toast.error('Failed to create invitation')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="invite-user-title" className="fixed inset-0 bg-[var(--color-bg)]/60 flex items-center justify-center z-50">
      <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg w-full max-w-md p-6 space-y-4">
        <h2 id="invite-user-title" className="text-lg font-semibold text-[var(--color-text)]">Invite User</h2>
        {result ? (() => {
          const inviteUrl = buildInvitationUrl(result.invitation_link)
          return (
            <div className="space-y-3">
              <p className="text-sm text-[var(--color-text-secondary)]">Invitation created. Share this link with the user:</p>
              <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded p-3">
                <code className="text-xs text-[var(--status-passed)] break-all">
                  {inviteUrl || 'Invalid invitation link received from server.'}
                </code>
              </div>
              <div className="flex justify-end">
                <button
                  disabled={!inviteUrl}
                  onClick={async () => {
                    if (!inviteUrl) return
                    const ok = await copyTextToClipboard(inviteUrl)
                    if (ok) toast.success('Copied!')
                    else toast.error('Clipboard access denied — copy manually')
                  }}
                  className="text-sm text-[var(--color-text)] hover:text-[var(--color-text-secondary)] mr-3 disabled:opacity-40"
                >Copy link</button>
                <button onClick={onClose} className="bg-[var(--color-bg-hover)] hover:bg-[var(--color-bg-card)] text-[var(--color-text)] text-sm px-4 py-2 rounded">Close</button>
              </div>
            </div>
          )
        })() : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="user-field-9" className="block text-sm text-[var(--color-text-muted)] mb-1">Email address</label>
              <input id="user-field-9" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
                placeholder="user@company.com" />
            </div>
            <div>
              <label htmlFor="user-field-10" className="block text-sm text-[var(--color-text-muted)] mb-1">Role</label>
              <select id="user-field-10" value={role} onChange={(e) => setRole(e.target.value as UserRole)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]">
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={onClose} className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-4 py-2">Cancel</button>
              <button type="submit" disabled={loading}
                className="bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] text-sm px-4 py-2 rounded transition-colors">
                {loading ? 'Sending…' : 'Send Invitation'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
