/**
 * W06 DEF-01: a defect's assignee, chosen from the people who can work on its
 * project. The same rule as a release owner (`components/releases/OwnerSelect`,
 * W05 REL-02, and the server's `release_owner_service.is_eligible_person`):
 * the project's member list also holds the per-project synthetic QA lead and
 * service accounts, which cannot be assigned (the server answers 422), so they
 * are not offered, and neither are deactivated users. The CURRENT assignee is
 * always an option even when it is not a member (an administrator may be
 * assigned without belonging to the project): dropping it would silently
 * change the assignee on the next save.
 */
import { useMemo } from 'react'
import { useProjectMembers } from '@/hooks/useUserManagement'
import type { ProjectMember } from '@/services/userManagementService'

export interface AssigneeOption {
  id: string
  name: string
}

export interface AssigneeSelectProps {
  projectId: string
  /** The selected user id; `''` = Unassigned. */
  value: string
  /** The chosen id (`''` = Unassigned) and the name shown for it (`null` for Unassigned). */
  onChange: (userId: string, name: string | null) => void
  /** The defect's assignee as the server resolved it. */
  currentAssignee?: AssigneeOption | null
  id?: string
  disabled?: boolean
  className?: string
}

/** A member who may be assigned a defect: a person, active. */
function canBeAssigned(member: ProjectMember): boolean {
  return !member.is_service_account && !member.is_synthetic && member.is_active !== false
}

const labelOf = (member: ProjectMember) => member.full_name?.trim() || member.username

export default function AssigneeSelect({
  projectId, value, onChange, currentAssignee, id, disabled, className,
}: AssigneeSelectProps) {
  const { data: members, isLoading } = useProjectMembers(projectId)
  const options = useMemo(() => {
    const eligible = (members ?? [])
      .filter(canBeAssigned)
      .map((m) => ({ id: m.user_id, name: labelOf(m) }))
      .sort((a, b) => a.name.localeCompare(b.name))
    if (currentAssignee && !eligible.some((o) => o.id === currentAssignee.id)) {
      eligible.unshift({ id: currentAssignee.id, name: currentAssignee.name })
    }
    return eligible
  }, [members, currentAssignee])

  return (
    <select
      id={id}
      aria-label="Assignee"
      value={value}
      onChange={(e) => {
        const next = e.target.value
        onChange(next, next ? options.find((o) => o.id === next)?.name ?? null : null)
      }}
      disabled={disabled}
      aria-busy={isLoading || undefined}
      className={className ?? 'input w-full'}
    >
      <option value="">Unassigned</option>
      {options.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
    </select>
  )
}
