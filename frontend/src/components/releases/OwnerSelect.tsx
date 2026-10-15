/**
 * A release's owner, chosen from the people who can work on its project
 * (REL-02).
 *
 * The project's member list also holds the per-project synthetic QA lead and
 * service accounts; neither can own a release (the server answers 422), so
 * they are not offered, and neither are deactivated users. The CURRENT owner
 * is always an option even when it is not a member: an administrator may own
 * a release without belonging to its project, and dropping them from the list
 * would silently change the owner on the next save.
 */
import { useMemo } from 'react'
import { useProjectMembers } from '@/hooks/useUserManagement'
import type { ProjectMember } from '@/services/userManagementService'
import type { ReleaseOwner } from '@/types/releases'

export interface OwnerSelectProps {
  projectId: string
  /** The selected user id; `''` = No owner. */
  value: string
  onChange: (userId: string) => void
  /** The release's owner as the server resolved it. */
  currentOwner?: ReleaseOwner | null
  id?: string
  disabled?: boolean
  className?: string
}

/** A member who may own a release: a person, active. */
function canOwnReleases(member: ProjectMember): boolean {
  return !member.is_service_account && !member.is_synthetic && member.is_active !== false
}

const labelOf = (member: ProjectMember) => member.full_name?.trim() || member.username

export default function OwnerSelect({
  projectId, value, onChange, currentOwner, id, disabled, className,
}: OwnerSelectProps) {
  const { data: members, isLoading } = useProjectMembers(projectId)
  const options = useMemo(() => {
    const eligible = (members ?? [])
      .filter(canOwnReleases)
      .map(m => ({ id: m.user_id, label: labelOf(m) }))
      .sort((a, b) => a.label.localeCompare(b.label))
    if (currentOwner && !eligible.some(o => o.id === currentOwner.id)) {
      eligible.unshift({ id: currentOwner.id, label: currentOwner.name })
    }
    return eligible
  }, [members, currentOwner])

  return (
    <select
      id={id}
      aria-label="Owner"
      value={value}
      onChange={e => onChange(e.target.value)}
      disabled={disabled}
      aria-busy={isLoading || undefined}
      className={className ?? 'input w-full'}
    >
      <option value="">No owner</option>
      {options.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
    </select>
  )
}
