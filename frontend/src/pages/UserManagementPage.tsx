/**
 * /users: users and roles, and who has access to each project.
 *
 * W27b PAN-06: the forms render IN THE PAGE (no modal): Add User and Invite
 * User open one at a time right under the Users tab toolbar, a row's Edit user
 * opens under that row (a full-width table row). Add User's temporary password
 * and Invite User's link are one-time secrets: shown once in their section,
 * which while it shows one can only be left through its own Done / Close (no
 * X, Escape swallowed), and which marks its slot dirty so opening the other
 * form there asks first. Project access (`ProjectMembersTab`) the same way.
 */
import { Navigate, useLocation } from 'react-router-dom'
import { Fragment, useEffect, useLayoutEffect, useState } from 'react'
import { Edit3, KeyRound, Plus, Shield, UserCheck, UserPlus, UserX } from 'lucide-react'
import toast from 'react-hot-toast'
import { useUsersPage, refreshUsers, refreshInvitations } from '@/hooks/useUserManagement'
import {
  userManagementService,
  type InviteUserResponse,
  type PasswordResetLinkResponse,
  type UserItem,
  type UserRole,
} from '@/services/userManagementService'
import { projectsService } from '@/services/projectsService'
import { usePermissions } from '@/hooks/usePermissions'
import { useAuthStore } from '@/store/authStore'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import PageHeader from '@/components/ui/PageHeader'
import Tabs, { type TabItem } from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import { helpTopicParam } from '@/components/help/helpTopics'
import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import InlineRowSection from '@/components/ui/inPage/InlineRowSection'
import { useInlineSlot, type InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useSlotCloser } from '@/hooks/useSlotCloser'
import { ProjectMembersTab } from './ProjectMembersTab'
import { copyTextToClipboard } from '@/utils/clipboard'
import { OneTimeLinkBox, PendingInvitationsTab } from './PendingInvitationsTab'
import type { Project } from '@/types/projects'

const ROLES: UserRole[] = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']

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

/**
 * While a one-time secret is shown, its section's slot stays dirty (W27b): opening the other form in that
 * place asks first instead of dropping the secret. The secret itself lives only in the section's state. A
 * layout effect: the slot is dirty before the browser can deliver the next click.
 */
function useSecretHoldsSlot(slot: InlineSlot | undefined, holding: boolean) {
  const setDirty = slot?.setDirty
  useLayoutEffect(() => {
    if (holding) setDirty?.(true)
  }, [holding, setDirty])
}


const HELP_TOPIC = helpTopicParam('/users')

type UsersPageTab = 'users' | 'project-members' | 'invitations'

const TAB_IDS: readonly UsersPageTab[] = ['users', 'project-members', 'invitations']

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
/** W17b ADM-01: admins also see the invitations nobody has accepted yet. */
const ADMIN_TAB_ITEMS: readonly TabItem<UsersPageTab>[] = [...TAB_ITEMS, { id: 'invitations', label: 'Pending invitations' }]

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
        tabs={<Tabs items={isAdmin ? ADMIN_TAB_ITEMS : TAB_ITEMS} value={tab} onChange={setTab} ariaLabel="User management sections" />}
      />

      {tab === 'users' && <UsersTab canManageUsers={canManageUsers} isAdmin={isAdmin} />}
      {tab === 'project-members' && <ProjectMembersTab isAdmin={isAdmin} canManageUsers={canManageUsers} />}
      {tab === 'invitations' && (isAdmin ? <PendingInvitationsTab /> : <UsersTab canManageUsers={canManageUsers} isAdmin={isAdmin} />)}
    </div>
  )
}

// ── Users Tab ─────────────────────────────────────────────────

/** The two forms under the Users tab toolbar (one at a time) and a row's Edit user (one per table). */
type ToolbarForm = 'add' | 'invite'
type RowForm = `edit:${string}` | `reset:${string}`

function UsersTab({ canManageUsers, isAdmin }: { canManageUsers: boolean; isAdmin: boolean }) {
  // W27b: one open form under the toolbar (Add User / Invite User), one under a table row (Edit user).
  const toolbarSlot = useInlineSlot<ToolbarForm>()
  const closeToolbar = useSlotCloser(toolbarSlot)
  const rowSlot = useInlineSlot<RowForm>()
  const closeRow = useSlotCloser(rowSlot)
  const [filterRole, setFilterRole] = useState<UserRole | ''>('')
  const [filterActive, setFilterActive] = useState<'all' | 'active' | 'inactive'>('all')
  // A filter change re-reads the list (the table waits behind the spinner), so the user under edit leaves
  // the table: the row form closes first, after the discard question when it has typed input.
  const changeFilter = (apply: () => void) => rowSlot.guard(() => { rowSlot.close(); apply() })

  // Filters go to the SERVER. They used to be applied client-side over
  // whatever the one unpaginated request happened to return -- the API
  // defaults to page_size=50, so on a deployment with 132 users, selecting
  // "QA_ENGINEER" answered **2** against a truth of **5**, and "VIEWER"
  // answered 3 against 5, with nothing on screen saying the list was partial.
  // Filtering a truncated page answers a different question than the one the
  // operator asked.
  // ADM-03: the server pages (50 a page) and searches (username, email, full
  // name); the total comes back with the page, so the pager can say
  // "1–50 of 132" instead of a 200-row cap notice.
  const PAGE_SIZE = 50
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  useEffect(() => {
    const id = setTimeout(() => { setQuery(search.trim()); setPage(1) }, 300)
    return () => clearTimeout(id)
  }, [search])
  const { data: usersPage, isLoading } = useUsersPage({
    page,
    page_size: PAGE_SIZE,
    ...(query ? { q: query } : {}),
    ...(filterRole ? { role: filterRole } : {}),
    ...(filterActive === 'all' ? {} : { is_active: filterActive === 'active' }),
  })

  const filtered = Array.isArray(usersPage?.items) ? usersPage.items : []
  const total = usersPage?.total ?? null
  const firstShown = filtered.length ? (page - 1) * PAGE_SIZE + 1 : 0
  const lastShown = (page - 1) * PAGE_SIZE + filtered.length
  // Without a total (an older server) a full page means there may be more.
  const hasNext = total != null ? lastShown < total : filtered.length >= PAGE_SIZE

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

  // Today's loading state while nothing is open. With a form open the tab stays and only the table waits:
  // a re-read never unmounts a typed form or a one-time secret.
  if (isLoading && toolbarSlot.openKey === null && rowSlot.openKey === null) {
    return <div className="flex justify-center py-12"><LoadingSpinner /></div>
  }

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex gap-2">
          <select
            aria-label="Filter by role"
            value={filterRole}
            onChange={(e) => {
              const value = e.target.value as UserRole | ''
              changeFilter(() => { setFilterRole(value); setPage(1) })
            }}
            className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-1.5"
          >
            <option value="">All roles</option>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <select
            aria-label="Filter by status"
            value={filterActive}
            onChange={(e) => {
              const value = e.target.value as 'all' | 'active' | 'inactive'
              changeFilter(() => { setFilterActive(value); setPage(1) })
            }}
            className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-1.5"
          >
            <option value="all">All status</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
          <input
            type="search"
            aria-label="Search users"
            placeholder="Search name, username or email"
            value={search}
            maxLength={100}
            onChange={(e) => {
              const value = e.target.value
              changeFilter(() => setSearch(value))
            }}
            className="w-64 bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-1.5"
          />
        </div>
        {isAdmin && (
          <div className="flex gap-2">
            <button
              onClick={() => toolbarSlot.open('add')}
              className="flex items-center gap-1.5 bg-[var(--status-passed-bg)] hover:bg-[var(--status-passed-bg)] text-[var(--color-text)] text-sm px-3 py-1.5 rounded transition-colors"
            >
              <UserPlus className="h-4 w-4" /> Add User
            </button>
            {canManageUsers && (
              <button
                onClick={() => toolbarSlot.open('invite')}
                className="btn-primary btn-sm flex items-center gap-1.5"
              >
                <Plus className="h-4 w-4" /> Invite User
              </button>
            )}
          </div>
        )}
      </div>

      {/* W27b: Add User / Invite User open here, under the toolbar, one at a time. */}
      {toolbarSlot.openKey === 'add' && <AddUserModal slot={toolbarSlot} onClose={() => closeToolbar('add')} />}
      {toolbarSlot.openKey === 'invite' && <InviteUserModal slot={toolbarSlot} onClose={() => closeToolbar('invite')} />}


      {/* Table */}
      {isLoading ? (
        <div className="flex justify-center py-12"><LoadingSpinner /></div>
      ) : (
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
            {filtered.map((user) => {
              const editKey: RowForm = `edit:${user.id}`
              const resetKey: RowForm = `reset:${user.id}`
              const isSelf = user.id === useAuthStore.getState().user?.id
              return (
              <Fragment key={user.id}>
              <tr className="hover:bg-[var(--color-bg-hover)]/30 transition-colors">
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
                        onClick={() => rowSlot.open(editKey)}
                        className="text-[var(--color-text)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)]/30 p-1 rounded transition-colors"
                        title="Edit user"
                      >
                        <Edit3 className="h-3.5 w-3.5" />
                      </button>
                      {/* W17b ADM-02: a single-use link; the admin never sees or sets the password. */}
                      {!isSelf && user.is_active && (
                        <button
                          onClick={() => rowSlot.open(resetKey)}
                          className="text-[var(--color-text)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)]/30 p-1 rounded transition-colors"
                          title="Send password reset link"
                          aria-label={`Send password reset link to ${user.username}`}
                        >
                          <KeyRound className="h-3.5 w-3.5" />
                        </button>
                      )}
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
              {/* W27b: Edit user opens under its row (admin only, so the table has its 4 columns). */}
              {rowSlot.openKey === editKey && (
                <InlineRowSection colSpan={4}>
                  <EditUserModal user={user} slot={rowSlot} onClose={() => closeRow(editKey)} />
                </InlineRowSection>
              )}
              {rowSlot.openKey === resetKey && (
                <InlineRowSection colSpan={4}>
                  <PasswordResetLinkSection user={user} slot={rowSlot} onClose={() => closeRow(resetKey)} />
                </InlineRowSection>
              )}
              </Fragment>
              )
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-[var(--color-text-muted)]">
                  {query ? `No users match “${query}”` : 'No users found'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      )}

      {/* ADM-03: the pager. The count is the server's filtered total. */}
      {(page > 1 || hasNext || filtered.length > 0) && (
        <div className="flex items-center justify-end gap-3 text-xs text-[var(--color-text-muted)]" data-testid="users-pager">
          <span>
            {firstShown}–{lastShown}{total != null ? ` of ${total}` : ''}
          </span>
          <button
            type="button"
            onClick={() => rowSlot.guard(() => { rowSlot.close(); setPage((n) => Math.max(1, n - 1)) })}
            disabled={page <= 1}
            className="btn-secondary btn-sm"
          >
            Prev
          </button>
          <button
            type="button"
            onClick={() => rowSlot.guard(() => { rowSlot.close(); setPage((n) => n + 1) })}
            disabled={!hasNext}
            className="btn-secondary btn-sm"
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}

// ── Edit User (in the page, under its row) ───────────────────

function EditUserModal({ user, onClose, slot }: { user: UserItem; onClose: () => void; slot?: InlineSlot }) {
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
    <InlineFormSection
      title="Edit User"
      titleId="edit-user-title"
      onClose={onClose}
      canClose={!saving}
      showClose={false}
      slot={slot}
      onSubmit={handleSave}
      footer={
        <>
          <button type="button" onClick={onClose} className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-4 py-2">
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving}
            className="btn-primary"
          >
            {saving ? 'Saving…' : 'Save Changes'}
          </button>
        </>
      }
    >
        <div className="max-w-2xl space-y-4">
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
                  className="btn-primary btn-sm"
                >
                  Add
                </button>
              </div>
            )}
          </div>
        </div>
    </InlineFormSection>
  )
}

// ── Add User (admin direct create; in the page, under the toolbar) ──

function AddUserModal({ onClose, slot }: { onClose: () => void; slot?: InlineSlot }) {
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

  // The temporary password is shown once, here: while it is, the slot stays dirty, so opening Invite User
  // in its place asks first instead of dropping it.
  useSecretHoldsSlot(slot, result !== null)

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
    <InlineFormSection
      title="Add User"
      titleId="add-user-title"
      onClose={onClose}
      // While the password is shown its only exit is Done (as in the modal: no X, Escape swallowed).
      canClose={result === null && !loading}
      showClose={false}
      slot={slot}
    >
      <div className="max-w-2xl">
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
                    className="btn-primary btn-sm"
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
    </InlineFormSection>
  )
}

// ── Invite User (in the page, under the toolbar) ─────────────

function InviteUserModal({ onClose, slot }: { onClose: () => void; slot?: InlineSlot }) {
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<UserRole>('QA_ENGINEER')
  const [projects, setProjects] = useState<Project[]>([])
  const [projectIds, setProjectIds] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<InviteUserResponse | null>(null)

  // The invitation link is shown once, here: while it is, opening Add User in its place asks first.
  useSecretHoldsSlot(slot, result !== null)

  useEffect(() => {
    projectsService.list().then((list) => setProjects(Array.isArray(list) ? list : [])).catch(() => {})
  }, [])

  function toggleProject(id: string) {
    setProjectIds((prev) => (prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    try {
      // W17b ADM-01: the invitee joins the chosen projects, with the invited role, when they accept.
      const res = await userManagementService.inviteUser(
        email,
        role,
        projectIds.map((project_id) => ({ project_id, role })),
      )
      setResult(res)
      refreshUsers()
      void refreshInvitations()
      toast.success(res.email_sent ? 'Invitation emailed' : 'Invitation created')
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(typeof detail === 'string' && detail ? detail : 'Failed to create invitation')
    } finally {
      setLoading(false)
    }
  }

  return (
    <InlineFormSection
      title="Invite User"
      titleId="invite-user-title"
      onClose={onClose}
      // While the link is shown its only exit is Close (as in the modal: no X, Escape swallowed).
      canClose={result === null && !loading}
      showClose={false}
      slot={slot}
    >
      <div className="max-w-2xl">
        {result ? (
          <div className="space-y-3">
            <OneTimeLinkBox
              link={result.invitation_link}
              path="/accept-invite"
              emailSent={result.email_sent === true}
              email={result.email || email}
              what="Invitation"
            />
            <p className="text-xs text-[var(--color-text-muted)]">
              The link works once and expires in 7 days. It is listed under Pending invitations until it is accepted.
            </p>
            <div className="flex justify-end">
              <button onClick={onClose} className="bg-[var(--color-bg-hover)] hover:bg-[var(--color-bg-card)] text-[var(--color-text)] text-sm px-4 py-2 rounded">Close</button>
            </div>
          </div>
        ) : (
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
            {projects.length > 0 && (
              <fieldset>
                <legend className="block text-sm text-[var(--color-text-muted)] mb-1">Projects (optional)</legend>
                <div className="max-h-40 overflow-y-auto grid grid-cols-2 gap-1 bg-[var(--color-bg-input)] border border-[var(--color-border)] rounded p-2">
                  {projects.map((p) => (
                    <label key={p.id} className="flex items-center gap-2 text-sm text-[var(--color-text)]">
                      <input type="checkbox" checked={projectIds.includes(p.id)} onChange={() => toggleProject(p.id)} />
                      <span className="truncate" title={p.name}>{p.name}</span>
                    </label>
                  ))}
                </div>
                <p className="mt-1 text-xs text-[var(--color-text-faint)]">They join these projects as {role} when they accept.</p>
              </fieldset>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={onClose} className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-4 py-2">Cancel</button>
              <button type="submit" disabled={loading}
                className="btn-primary">
                {loading ? 'Sending…' : 'Send Invitation'}
              </button>
            </div>
          </form>
        )}
      </div>
    </InlineFormSection>
  )
}

// ── Send password reset link (W17b ADM-02, under its row) ─────

function PasswordResetLinkSection({ user, onClose, slot }: { user: UserItem; onClose: () => void; slot?: InlineSlot }) {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<PasswordResetLinkResponse | null>(null)

  // A reset link is a one-time secret like the invitation link: shown once, held until Close.
  useSecretHoldsSlot(slot, result !== null)

  async function issue() {
    setLoading(true)
    try {
      const res = await userManagementService.issuePasswordResetLink(user.id)
      setResult(res)
      toast.success(res.email_sent ? `Reset link emailed to ${user.email}` : 'Reset link created')
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(typeof detail === 'string' && detail ? detail : 'Failed to create a reset link')
    } finally {
      setLoading(false)
    }
  }

  return (
    <InlineFormSection
      title={`Send password reset link to ${user.full_name || user.username}`}
      titleId={`reset-link-${user.id}`}
      onClose={onClose}
      canClose={result === null && !loading}
      showClose={false}
      slot={slot}
    >
      <div className="max-w-2xl space-y-3">
        {result ? (
          <>
            <OneTimeLinkBox link={result.link} path="/reset-password/confirm" emailSent={result.email_sent} email={user.email} what="Reset link" />
            <p className="text-xs text-[var(--color-text-muted)]">
              The link works once and expires in 60 minutes. Older links for this account stopped working.
              Using it signs the account out everywhere; two-factor sign-in stays on.
            </p>
            <div className="flex justify-end">
              <button onClick={onClose} className="bg-[var(--color-bg-hover)] hover:bg-[var(--color-bg-card)] text-[var(--color-text)] text-sm px-4 py-2 rounded">Close</button>
            </div>
          </>
        ) : (
          <>
            <p className="text-sm text-[var(--color-text-secondary)]">
              Creates a single-use link that lets <strong>{user.email}</strong> choose a new password. You never see or
              set the password. It is emailed when SMTP is configured; otherwise copy it and send it yourself.
            </p>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={onClose} className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-4 py-2">Cancel</button>
              <button type="button" onClick={issue} disabled={loading} className="btn-primary">
                {loading ? 'Creating…' : 'Create reset link'}
              </button>
            </div>
          </>
        )}
      </div>
    </InlineFormSection>
  )
}
