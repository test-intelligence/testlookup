// Project Members Tab component — imported into UserManagementPage
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, KeyRound, ShieldCheck, UserMinus, UserPlus } from 'lucide-react'
import toast from 'react-hot-toast'
import { useUsers, useProjectMembers } from '@/hooks/useUserManagement'
import { userManagementService, type ProjectMember, type UserItem, type UserRole } from '@/services/userManagementService'
import { projectsService } from '@/services/projectsService'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { copyTextToClipboard } from '@/utils/clipboard'
import type { Project } from '@/types/projects'

const ROLES: UserRole[] = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']
const ROLE_COLORS: Record<UserRole, string> = {
  VIEWER: 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]',
  TESTER: 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)]',
  QA_ENGINEER: 'bg-[var(--status-passed-bg)]/50 text-[var(--status-passed)]',
  QA_LEAD: 'bg-[var(--status-broken-bg)]/50 text-[var(--status-broken)]',
  ADMIN: 'bg-[var(--status-failed-bg)]/50 text-[var(--status-failed)]',
}

export function ProjectMembersTab({ isAdmin, canManageUsers }: { isAdmin: boolean; canManageUsers: boolean }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string>('')
  const [showAddModal, setShowAddModal] = useState(false)
  // Pending default-QA-lead selection — committed by the Save button so a
  // mis-click on the picker doesn't immediately fire a PUT.
  const [defaultQaLeadDraft, setDefaultQaLeadDraft] = useState<string>('')
  const [savingDefault, setSavingDefault] = useState(false)
  const [resettingDefaultLead, setResettingDefaultLead] = useState(false)

  // Use SWR hook for users — reliable, auto-retries, and shares cache with Users tab
  const { data: allUsers, isLoading: usersLoading, error: usersError } = useUsers()

  // Members come from SWR (keyed on the selected project) rather than a
  // load-on-change effect; local edits below patch the cache via mutate.
  const {
    data: membersData,
    isLoading: loading,
    error: membersError,
    mutate: mutateMembers,
  } = useProjectMembers(selectedProjectId || null)
  const members = useMemo(() => membersData ?? [], [membersData])
  useEffect(() => {
    if (membersError) toast.error('Failed to load project members')
  }, [membersError])

  useEffect(() => {
    projectsService.list().then(setProjects).catch(() => {})
  }, [])

  // Reset the draft whenever the selected project changes, seeding from the
  // server value. The dropdown lists only QA_LEAD members of the project +
  // ADMIN users (admin bypass mirrors the backend role-check rule). Synced
  // during render via previous-value tracking rather than a setState-in-effect.
  const selectedProjectObj = projects.find(p => p.id === selectedProjectId)
  const draftSeed = selectedProjectObj?.default_qa_lead_user_id ?? ''
  const draftSeedKey = `${selectedProjectId}|${draftSeed}`
  const [prevDraftSeedKey, setPrevDraftSeedKey] = useState(draftSeedKey)
  if (prevDraftSeedKey !== draftSeedKey) {
    setPrevDraftSeedKey(draftSeedKey)
    setDefaultQaLeadDraft(draftSeed)
  }

  // Candidate list for the Default QA Lead picker. Filter to project members
  // whose role is QA_LEAD — the backend rejects anything else with 400, so
  // surfacing other users in the dropdown would just produce sad clicks.
  const qaLeadCandidates = useMemo(
    () => members.filter(m => m.role === 'QA_LEAD'),
    [members],
  )

  async function handleSaveDefaultQaLead() {
    if (!selectedProjectId) return
    const value = defaultQaLeadDraft || undefined  // backend treats absence as "no change"
    if (!value) {
      toast.error('Pick a QA Lead from the project members.')
      return
    }
    setSavingDefault(true)
    try {
      const updated = await projectsService.update(selectedProjectId, {
        default_qa_lead_user_id: value,
      })
      // Patch the local project list so the seeded draft matches the saved value.
      setProjects(prev => prev.map(p => p.id === updated.id ? updated : p))
      toast.success('Default QA Lead updated')
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || 'Failed to update default QA Lead')
    } finally {
      setSavingDefault(false)
    }
  }

  async function handleResetDefaultQaLeadPassword() {
    if (!selectedProjectId) return
    if (!confirm(
      'Reset the password of this project’s auto-provisioned QA Lead user?\n\n'
      + 'The new password will be displayed once — copy it before closing.'
    )) return
    setResettingDefaultLead(true)
    try {
      const result = await projectsService.resetDefaultQaLeadPassword(selectedProjectId)
      await copyTextToClipboard(result.password)
      toast.success(
        `Password reset for ${result.email}. Copied to clipboard.`,
        { duration: 8000 },
      )
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || 'Failed to reset password')
    } finally {
      setResettingDefaultLead(false)
    }
  }

  async function handleRemove(userId: string) {
    if (!selectedProjectId) return
    if (!confirm('Remove this user from the project?')) return
    try {
      await userManagementService.removeProjectMember(selectedProjectId, userId)
      toast.success('Member removed')
      mutateMembers(prev => (prev ?? []).filter(m => m.user_id !== userId), { revalidate: false })
    } catch { toast.error('Failed to remove member') }
  }

  async function handleRoleChange(userId: string, role: UserRole) {
    if (!selectedProjectId) return
    try {
      const updated = await userManagementService.updateProjectMemberRole(selectedProjectId, userId, role)
      mutateMembers(
        prev => (prev ?? []).map(m => m.user_id === userId ? { ...m, role: updated.role } : m),
        { revalidate: false },
      )
      toast.success('Role updated')
    } catch { toast.error('Failed to update role') }
  }

  const selectedProject = selectedProjectObj
  const memberUserIds = new Set(members.map(m => m.user_id))
  const nonMembers = (allUsers ?? []).filter(u => !memberUserIds.has(u.id) && u.is_active)
  const currentDefaultQaLead = members.find(
    m => m.user_id === (selectedProject?.default_qa_lead_user_id ?? ''),
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <label htmlFor="member-field-0" className="text-sm text-[var(--color-text-muted)] whitespace-nowrap">Select Project:</label>
        <select id="member-field-0"
          value={selectedProjectId}
          onChange={e => setSelectedProjectId(e.target.value)}
          className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 focus:outline-none focus:border-[var(--color-border)] flex-1 max-w-xs"
        >
          <option value="">— Pick a project —</option>
          {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        {selectedProjectId && canManageUsers && (
          <button
            onClick={() => setShowAddModal(true)}
            className="flex items-center gap-1.5 text-sm bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] px-3 py-2 rounded-lg font-medium"
          >
            <UserPlus className="h-4 w-4" /> Add Member
          </button>
        )}
      </div>

      {/* User loading error warning */}
      {usersError && (
        <div className="flex items-center gap-2 text-xs text-[var(--status-broken)] bg-[var(--status-broken-bg)]/20 border border-[var(--status-broken-bd)]/30 rounded px-3 py-2">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          Could not load users list. The &quot;Add Member&quot; dropdown may be empty. Check your permissions (QA_LEAD+ required).
        </div>
      )}

      {/* Default QA Lead card — admin-only edit, everyone can see the current value.
          Hidden until a project is picked + its members have loaded so the
          dropdown options aren't briefly empty on switch. */}
      {selectedProjectId && !loading && (
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4">
          <div className="flex items-start gap-3">
            <span className="inline-flex items-center justify-center rounded-md flex-none mt-0.5"
              style={{ width: 28, height: 28, background: 'color-mix(in srgb, var(--status-broken) 14%, transparent)', color: 'var(--status-broken)' }}
            >
              <ShieldCheck className="h-4 w-4" />
            </span>
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-semibold text-[var(--color-text)] m-0">Default QA Lead</h3>
              <p className="text-xs text-[var(--color-text-muted)] mt-0.5 leading-snug">
                Every new test suite ingested gets assigned to this user. They become the fallback
                owner when no suite-specific owner is set, and the recipient of auto-assigned failures.
              </p>

              {qaLeadCandidates.length === 0 ? (
                <p className="text-xs text-[var(--status-broken)] bg-[var(--status-broken-bg)]/20 border border-[var(--status-broken-bd)]/30 rounded px-2 py-1.5 mt-2 inline-block">
                  No project members have the QA_LEAD role yet. Add one below before setting a default.
                </p>
              ) : isAdmin ? (
                <div className="flex items-center gap-2 mt-2 flex-wrap">
                  <select
                    value={defaultQaLeadDraft}
                    onChange={e => setDefaultQaLeadDraft(e.target.value)}
                    className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-1.5 focus:outline-none focus:border-[var(--color-border)] min-w-[260px]"
                  >
                    <option value="">— Select a QA Lead —</option>
                    {qaLeadCandidates.map(m => (
                      <option key={m.user_id} value={m.user_id}>
                        {m.full_name || m.username} ({m.email})
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={handleSaveDefaultQaLead}
                    disabled={
                      savingDefault
                      || !defaultQaLeadDraft
                      || defaultQaLeadDraft === (selectedProject?.default_qa_lead_user_id ?? '')
                    }
                    className="flex items-center gap-1.5 text-xs bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] px-3 py-1.5 rounded-lg font-medium"
                  >
                    {savingDefault && <LoadingSpinner size="sm" />}
                    {savingDefault ? 'Saving…' : 'Save'}
                  </button>
                </div>
              ) : (
                <p className="text-xs text-[var(--color-text-secondary)] mt-2">
                  Current: {currentDefaultQaLead
                    ? `${currentDefaultQaLead.full_name || currentDefaultQaLead.username} (${currentDefaultQaLead.email})`
                    : 'Not set'}
                  <span className="ml-2 text-[var(--color-text-faint)]">(ADMIN required to change)</span>
                </p>
              )}

              <div className="mt-3 pt-3 border-t border-[var(--color-border)] flex items-center gap-2 flex-wrap">
                <button
                  onClick={handleResetDefaultQaLeadPassword}
                  disabled={resettingDefaultLead}
                  className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
                  title="Reset the auto-provisioned default QA Lead user's password"
                >
                  {resettingDefaultLead ? <LoadingSpinner size="sm" /> : <KeyRound className="h-3.5 w-3.5" />}
                  {resettingDefaultLead ? 'Resetting…' : 'Reset QA Lead password'}
                </button>
                <span className="text-[11px] text-[var(--color-text-faint)]">
                  New password is copied to your clipboard.
                </span>
              </div>
            </div>
          </div>
        </div>
      )}

      {!selectedProjectId ? (
        <div className="text-center py-16 text-[var(--color-text-muted)] text-sm">
          Select a project to view and manage its members.
        </div>
      ) : loading ? (
        <div className="flex justify-center py-12"><LoadingSpinner size="lg" /></div>
      ) : members.length === 0 ? (
        <div className="text-center py-16 text-[var(--color-text-muted)] text-sm">
          No members assigned to <span className="text-[var(--color-text-secondary)]">{selectedProject?.name}</span> yet.
          {canManageUsers && (
            <button onClick={() => setShowAddModal(true)} className="ml-2 text-[var(--color-text)] hover:text-[var(--color-text-secondary)] underline">
              Add the first member
            </button>
          )}
        </div>
      ) : (
        <div className="rounded-xl border border-[var(--color-border)] overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-[var(--color-bg-secondary)]/80">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">User</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Email</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Project Role</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Added</th>
                {isAdmin && <th className="px-4 py-3 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Actions</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)]">
              {members.map(m => (
                <tr key={m.id} className="hover:bg-[var(--color-bg-secondary)]/60 transition-colors">
                  <td className="px-4 py-3 text-[var(--color-text)] font-medium">{m.full_name || m.username}</td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)]">{m.email}</td>
                  <td className="px-4 py-3">
                    {canManageUsers ? (
                      <select
                        value={m.role}
                        onChange={e => handleRoleChange(m.user_id, e.target.value as UserRole)}
                        className="text-xs rounded px-2 py-1 border border-transparent bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] focus:outline-none"
                      >
                        {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                      </select>
                    ) : (
                      <span className={`text-xs px-2 py-0.5 rounded font-medium ${ROLE_COLORS[m.role] || 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]'}`}>{m.role}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)] text-xs">
                    {new Date(m.created_at).toLocaleDateString()}
                  </td>
                  {isAdmin && (
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => handleRemove(m.user_id)}
                        className="text-[var(--status-failed)]/70 hover:text-[var(--status-failed)] p-1 rounded transition-colors"
                        title="Remove from project"
                      >
                        <UserMinus className="h-4 w-4" />
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showAddModal && selectedProjectId && (
        <AddProjectMemberModal
          projectId={selectedProjectId}
          projectName={selectedProject?.name ?? ''}
          nonMembers={nonMembers}
          usersLoading={usersLoading}
          onClose={() => setShowAddModal(false)}
          onAdded={member => { mutateMembers(prev => [...(prev ?? []), member], { revalidate: false }); setShowAddModal(false) }}
        />
      )}
    </div>
  )
}

interface AddProjectMemberModalProps {
  projectId: string
  projectName: string
  nonMembers: UserItem[]
  usersLoading: boolean
  onClose: () => void
  onAdded: (member: ProjectMember) => void
}

function AddProjectMemberModal({ projectId, projectName, nonMembers, usersLoading, onClose, onAdded }: AddProjectMemberModalProps) {
  const [userId, setUserId] = useState('')
  const [role, setRole] = useState<UserRole>('TESTER')
  const [saving, setSaving] = useState(false)

  async function handleAdd() {
    if (!userId) { toast.error('Select a user'); return }
    setSaving(true)
    try {
      const member = await userManagementService.addProjectMember(projectId, userId, role)
      toast.success('Member added successfully')
      onAdded(member)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || 'Failed to add member')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="add-member-title" className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]/60" onClick={onClose}>
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-6 w-full max-w-md shadow-2xl" onClick={e => e.stopPropagation()}>
        <h2 id="add-member-title" className="text-base font-semibold text-[var(--color-text)] mb-1">Add Member to {projectName}</h2>
        <p className="text-xs text-[var(--color-text-muted)] mb-4">Assign a user to this project with a specific role.</p>
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">User</label>
            {usersLoading ? (
              <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)] py-2">
                <LoadingSpinner size="sm" /> Loading users…
              </div>
            ) : nonMembers.length === 0 ? (
              <p className="text-xs text-[var(--color-text-muted)] py-2">
                All users are already members of this project, or the users list could not be loaded.
              </p>
            ) : (
              <select
                value={userId}
                onChange={e => setUserId(e.target.value)}
                className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border-light)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-border)]"
              >
                <option value="">— Select user —</option>
                {nonMembers.map(u => (
                  <option key={u.id} value={u.id}>{u.full_name || u.username} ({u.email})</option>
                ))}
              </select>
            )}
          </div>
          <div>
            <label htmlFor="member-field-1" className="block text-xs text-[var(--color-text-muted)] mb-1">Project Role</label>
            <select id="member-field-1"
              value={role}
              onChange={e => setRole(e.target.value as UserRole)}
              className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border-light)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-border)]"
            >
              {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        </div>
        <div className="flex gap-3 justify-end mt-5">
          <button onClick={onClose} className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
          <button
            onClick={handleAdd}
            disabled={saving || !userId}
            className="px-4 py-2 text-sm bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] rounded-lg font-medium flex items-center gap-2"
          >
            {saving ? <LoadingSpinner size="sm" /> : <UserPlus className="h-4 w-4" />}
            {saving ? 'Adding…' : 'Add Member'}
          </button>
        </div>
      </div>
    </div>
  )
}
