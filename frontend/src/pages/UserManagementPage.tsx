import { useEffect, useState } from 'react'
import { Edit3, FolderOpen, Key, Plus, RefreshCw, Shield, Trash2, UserCheck, UserPlus, UserX, Users } from 'lucide-react'
import toast from 'react-hot-toast'
import { useUsers, useApiKeys, refreshUsers, refreshApiKeys } from '@/hooks/useUserManagement'
import { userManagementService, type UserItem, type UserRole } from '@/services/userManagementService'
import { projectsService } from '@/services/projectsService'
import { usePermissions } from '@/hooks/usePermissions'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
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

export default function UserManagementPage() {
  const [tab, setTab] = useState<'users' | 'apikeys' | 'project-members'>('users')
  const { canManageUsers, canGenerateApiKeys, isAdmin } = usePermissions()

  const tabClass = (t: typeof tab) =>
    `px-4 py-2 text-sm font-medium transition-colors ${
      tab === t ? 'text-[var(--color-text)] border-b-2 border-[var(--color-border)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
    }`

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[var(--color-text)]">User Management</h1>
          <p className="text-sm text-[var(--color-text-muted)] mt-0.5">Manage users, roles, project access, and API keys</p>
        </div>
      </div>

      <div className="flex gap-1 border-b border-[var(--color-border)]">
        <button onClick={() => setTab('users')} className={tabClass('users')}>
          <span className="flex items-center gap-2"><Users className="h-4 w-4" /> Users</span>
        </button>
        <button onClick={() => setTab('project-members')} className={tabClass('project-members')}>
          <span className="flex items-center gap-2"><FolderOpen className="h-4 w-4" /> Project Access</span>
        </button>
        <button onClick={() => setTab('apikeys')} className={tabClass('apikeys')}>
          <span className="flex items-center gap-2"><Key className="h-4 w-4" /> API Keys</span>
        </button>
      </div>

      {tab === 'users' && <UsersTab canManageUsers={canManageUsers} isAdmin={isAdmin} />}
      {tab === 'project-members' && <ProjectMembersTab isAdmin={isAdmin} canManageUsers={canManageUsers} />}
      {tab === 'apikeys' && <ApiKeysTab canGenerateApiKeys={canGenerateApiKeys} />}
    </div>
  )
}

// ── Users Tab ─────────────────────────────────────────────────

function UsersTab({ canManageUsers, isAdmin }: { canManageUsers: boolean; isAdmin: boolean }) {
  const { data: users, isLoading } = useUsers()
  const [showInviteModal, setShowInviteModal] = useState(false)
  const [showAddUserModal, setShowAddUserModal] = useState(false)
  const [editUser, setEditUser] = useState<UserItem | null>(null)
  const [filterRole, setFilterRole] = useState<UserRole | ''>('')
  const [filterActive, setFilterActive] = useState<'all' | 'active' | 'inactive'>('all')

  const filtered = (users ?? []).filter((u) => {
    if (filterRole && u.role !== filterRole) return false
    if (filterActive === 'active' && !u.is_active) return false
    if (filterActive === 'inactive' && u.is_active) return false
    return true
  })

  async function handleRoleChange(userId: string, role: UserRole) {
    try {
      await userManagementService.updateUserRole(userId, role)
      refreshUsers()
      toast.success('Role updated')
    } catch {
      toast.error('Failed to update role')
    }
  }

  async function handleToggleStatus(userId: string, currentActive: boolean) {
    try {
      await userManagementService.updateUserStatus(userId, !currentActive)
      refreshUsers()
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
            value={filterRole}
            onChange={(e) => setFilterRole(e.target.value as UserRole | '')}
            className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-1.5"
          >
            <option value="">All roles</option>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <select
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
    <div className="fixed inset-0 bg-[var(--color-bg)]/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg w-full max-w-lg p-6 space-y-5 max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-[var(--color-text)]">Edit User</h2>

        <form onSubmit={handleSave} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">Email</label>
              <input
                type="email"
                required
                value={email}
                onChange={e => setEmail(e.target.value)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
              />
            </div>
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">Username</label>
              <input
                type="text"
                required
                value={username}
                onChange={e => setUsername(e.target.value)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
              />
            </div>
          </div>
          <div>
            <label className="block text-sm text-[var(--color-text-muted)] mb-1">Full Name</label>
            <input
              type="text"
              value={fullName}
              onChange={e => setFullName(e.target.value)}
              className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
              placeholder="Jane Doe"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">Role</label>
              <select
                value={role}
                onChange={e => setRole(e.target.value as UserRole)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
              >
                {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">Status</label>
              <select
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
                  value={assignProjectId}
                  onChange={e => setAssignProjectId(e.target.value)}
                  className="flex-1 bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-xs rounded px-2 py-1.5"
                >
                  <option value="">Add to project…</option>
                  {unassignedProjects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
                <select
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
    <div className="fixed inset-0 bg-[var(--color-bg)]/60 flex items-center justify-center z-50">
      <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg w-full max-w-md p-6 space-y-4">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">Add User</h2>
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
                    value={assignProjectId}
                    onChange={e => setAssignProjectId(e.target.value)}
                    className="flex-1 bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-xs rounded px-2 py-1.5"
                  >
                    <option value="">Select project…</option>
                    {unassignedProjects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                  <select
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
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">Email address</label>
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
                placeholder="user@company.com" />
            </div>
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">Username</label>
              <input type="text" required value={username} onChange={(e) => setUsername(e.target.value)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
                placeholder="jdoe" />
            </div>
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">Full name (optional)</label>
              <input type="text" value={fullName} onChange={(e) => setFullName(e.target.value)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
                placeholder="Jane Doe" />
            </div>
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">Role</label>
              <select value={role} onChange={(e) => setRole(e.target.value as UserRole)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]">
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
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
    <div className="fixed inset-0 bg-[var(--color-bg)]/60 flex items-center justify-center z-50">
      <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg w-full max-w-md p-6 space-y-4">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">Invite User</h2>
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
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">Email address</label>
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]"
                placeholder="user@company.com" />
            </div>
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">Role</label>
              <select value={role} onChange={(e) => setRole(e.target.value as UserRole)}
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

// ── API Keys Tab ──────────────────────────────────────────────

function ApiKeysTab({ canGenerateApiKeys }: { canGenerateApiKeys: boolean }) {
  const { data: keys, isLoading } = useApiKeys()
  const [showCreateModal, setShowCreateModal] = useState(false)

  async function handleRevoke(keyId: string, name: string) {
    if (!confirm(`Revoke key "${name}"? This cannot be undone.`)) return
    try {
      await userManagementService.revokeApiKey(keyId)
      refreshApiKeys()
      toast.success('API key revoked')
    } catch {
      toast.error('Failed to revoke key')
    }
  }

  if (isLoading) return <div className="flex justify-center py-12"><LoadingSpinner /></div>

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        {canGenerateApiKeys && (
          <button onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-1.5 bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] text-sm px-3 py-1.5 rounded transition-colors">
            <Plus className="h-4 w-4" /> Generate Key
          </button>
        )}
      </div>
      <div className="bg-[var(--color-bg-secondary)] rounded-lg border border-[var(--color-border)] overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)]/80">
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Name</th>
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Key</th>
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Scopes</th>
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Expires</th>
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Last Used</th>
              <th className="text-left px-4 py-3 text-[var(--color-text-muted)] font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--color-border)]">
            {(keys ?? []).map((k) => (
              <tr key={k.id} className="hover:bg-[var(--color-bg-hover)]/30 transition-colors">
                <td className="px-4 py-3 font-medium text-[var(--color-text)]">{k.name}</td>
                <td className="px-4 py-3"><code className="text-xs text-[var(--status-passed)] bg-[var(--color-bg-card)] px-2 py-0.5 rounded">{k.key_hint}</code></td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    {k.scopes.length > 0 ? k.scopes.map((s) => (
                      <span key={s} className="text-xs bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] px-1.5 py-0.5 rounded">{s}</span>
                    )) : <span className="text-xs text-[var(--color-text-muted)]">all</span>}
                  </div>
                </td>
                <td className="px-4 py-3 text-[var(--color-text-muted)] text-xs">{k.expires_at ? new Date(k.expires_at).toLocaleDateString() : <span className="text-[var(--color-text-muted)]">Never</span>}</td>
                <td className="px-4 py-3 text-[var(--color-text-muted)] text-xs">{k.last_used_at ? new Date(k.last_used_at).toLocaleDateString() : <span className="text-[var(--color-text-muted)]">—</span>}</td>
                <td className="px-4 py-3">
                  <button onClick={() => handleRevoke(k.id, k.name)} className="text-[var(--status-failed)] hover:text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/20 p-1 rounded transition-colors">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </td>
              </tr>
            ))}
            {(keys ?? []).length === 0 && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-[var(--color-text-muted)]">No API keys yet. Generate one to authenticate CI/CD pipelines.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {showCreateModal && <CreateApiKeyModal onClose={() => setShowCreateModal(false)} />}
    </div>
  )
}

// ── Create API Key Modal ──────────────────────────────────────

const AVAILABLE_SCOPES = ['test:read', 'test:write', 'report:read', 'report:write', 'admin:read']

function CreateApiKeyModal({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<string[]>([])
  const [expiresDays, setExpiresDays] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [createdKey, setCreatedKey] = useState<string | null>(null)

  function toggleScope(s: string) {
    setScopes((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    try {
      const result = await userManagementService.createApiKey(name, scopes, expiresDays ? parseInt(expiresDays) : undefined)
      setCreatedKey(result.raw_key)
      refreshApiKeys()
    } catch {
      toast.error('Failed to generate API key')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-[var(--color-bg)]/60 flex items-center justify-center z-50">
      <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg w-full max-w-md p-6 space-y-4">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">Generate API Key</h2>
        {createdKey ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 p-3 bg-[var(--status-broken-bg)]/30 border border-[var(--status-broken-bd)]/50 rounded text-[var(--status-broken)] text-xs">
              <RefreshCw className="h-4 w-4 flex-shrink-0" />
              <span>Copy this key now — it will not be shown again.</span>
            </div>
            <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded p-3">
              <code className="text-xs text-[var(--status-passed)] break-all">{createdKey}</code>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={async () => {
                  const ok = await copyTextToClipboard(createdKey)
                  if (ok) toast.success('Copied!')
                  else toast.error('Clipboard access denied — copy manually')
                }}
                className="text-sm text-[var(--color-text)] hover:text-[var(--color-text-secondary)] px-3 py-1.5">Copy</button>
              <button onClick={onClose} className="bg-[var(--color-bg-hover)] hover:bg-[var(--color-bg-card)] text-[var(--color-text)] text-sm px-4 py-2 rounded">Done</button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">Key name</label>
              <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. GitHub Actions CI"
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]" />
            </div>
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-2">Scopes (leave empty for full access)</label>
              <div className="flex flex-wrap gap-2">
                {AVAILABLE_SCOPES.map((s) => (
                  <button key={s} type="button" onClick={() => toggleScope(s)}
                    className={`text-xs px-2 py-1 rounded border transition-colors ${scopes.includes(s) ? 'bg-white/10 border-[var(--color-border-light)] text-[var(--color-text-secondary)]' : 'bg-[var(--color-bg-hover)] border-[var(--color-border-light)] text-[var(--color-text-muted)] hover:border-[var(--color-border-light)]'}`}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">Expiry (days, optional)</label>
              <input type="number" min={1} max={365} value={expiresDays} onChange={(e) => setExpiresDays(e.target.value)} placeholder="Never expires"
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)]" />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={onClose} className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-4 py-2">Cancel</button>
              <button type="submit" disabled={loading || !name}
                className="bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] text-sm px-4 py-2 rounded transition-colors">
                {loading ? 'Generating…' : 'Generate'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
