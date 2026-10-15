/**
 * REL-02: the owner picker offers the people who can own a release in the
 * project, never the synthetic QA lead, a service account or a deactivated
 * user, and always keeps the current owner (an administrator who is not a
 * member may own one).
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ProjectMember } from '@/services/userManagementService'

const useProjectMembers = vi.fn()
vi.mock('@/hooks/useUserManagement', () => ({
  useProjectMembers: (...args: unknown[]) => useProjectMembers(...args),
}))

import OwnerSelect from './OwnerSelect'

const member = (userId: string, fullName: string | null, username: string, flags: Partial<ProjectMember> = {}): ProjectMember => ({
  id: `m-${userId}`, user_id: userId, project_id: 'proj-1', role: 'QA_ENGINEER', created_at: '2026-01-01T00:00:00Z',
  email: `${username}@example.test`, username, full_name: fullName, ...flags,
})

const MEMBERS = [
  member('u-priya', 'Priya Nair', 'priya'),
  member('u-arun', null, 'arun'),
  member('u-qa-bot', 'QA lead (Project One)', 'qa-lead-proj-1', { is_synthetic: true }),
  member('u-ci', 'CI uploader', 'ci-uploader', { is_service_account: true }),
  member('u-gone', 'Former Colleague', 'former', { is_active: false }),
]

beforeEach(() => {
  useProjectMembers.mockReset()
  useProjectMembers.mockReturnValue({ data: MEMBERS, isLoading: false })
})

const optionLabels = () => within(screen.getByRole('combobox', { name: 'Owner' })).getAllByRole('option').map(o => o.textContent)

describe('OwnerSelect', () => {
  it("lists the project's people under No owner, without synthetic, service or inactive accounts", () => {
    render(<OwnerSelect projectId="proj-1" value="" onChange={vi.fn()} />)
    expect(useProjectMembers).toHaveBeenCalledWith('proj-1')
    expect(optionLabels()).toEqual(['No owner', 'arun', 'Priya Nair'])
  })

  it('keeps a current owner who is not a member (an administrator)', () => {
    render(<OwnerSelect projectId="proj-1" value="u-admin" onChange={vi.fn()} currentOwner={{ id: 'u-admin', name: 'Dana Admin' }} />)
    expect(optionLabels()).toEqual(['No owner', 'Dana Admin', 'arun', 'Priya Nair'])
    expect(screen.getByRole('combobox', { name: 'Owner' })).toHaveValue('u-admin')
  })

  it('does not list a current owner twice', () => {
    render(<OwnerSelect projectId="proj-1" value="u-priya" onChange={vi.fn()} currentOwner={{ id: 'u-priya', name: 'Priya Nair' }} />)
    expect(optionLabels()).toEqual(['No owner', 'arun', 'Priya Nair'])
  })

  it('hands back the chosen user id, and "" for No owner', () => {
    const onChange = vi.fn()
    render(<OwnerSelect projectId="proj-1" value="u-priya" onChange={onChange} />)
    const select = screen.getByRole('combobox', { name: 'Owner' })
    fireEvent.change(select, { target: { value: 'u-arun' } })
    fireEvent.change(select, { target: { value: '' } })
    expect(onChange.mock.calls).toEqual([['u-arun'], ['']])
  })
})
