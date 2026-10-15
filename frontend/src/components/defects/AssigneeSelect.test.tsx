/**
 * W06 DEF-01 §2.2-13: the assignee picker offers the people who can be
 * assigned in the project (the W05 owner rule), never the synthetic QA lead,
 * a service account or a deactivated user, starts with "Unassigned", and
 * always keeps the current assignee (an administrator who is not a member).
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ProjectMember } from '@/services/userManagementService'

const useProjectMembers = vi.fn()
vi.mock('@/hooks/useUserManagement', () => ({
  useProjectMembers: (...args: unknown[]) => useProjectMembers(...args),
}))

import AssigneeSelect from './AssigneeSelect'

const member = (userId: string, fullName: string | null, username: string, flags: Partial<ProjectMember> = {}): ProjectMember => ({
  id: `m-${userId}`, user_id: userId, project_id: 'p-1', role: 'QA_ENGINEER', created_at: '2026-01-01T00:00:00Z',
  email: `${username}@example.test`, username, full_name: fullName, ...flags,
})

const MEMBERS = [
  member('u-priya', 'Priya Nair', 'priya'),
  member('u-arun', null, 'arun'),
  member('u-qa-bot', 'QA lead (Project One)', 'qa-lead-p-1', { is_synthetic: true }),
  member('u-ci', 'CI uploader', 'ci-uploader', { is_service_account: true }),
  member('u-gone', 'Former Colleague', 'former', { is_active: false }),
]

beforeEach(() => {
  useProjectMembers.mockReset()
  useProjectMembers.mockReturnValue({ data: MEMBERS, isLoading: false })
})

const select = () => screen.getByRole('combobox', { name: 'Assignee' })
const optionLabels = () => within(select()).getAllByRole('option').map((o) => o.textContent)

describe('AssigneeSelect', () => {
  it("lists the project's people under Unassigned, without synthetic, service or inactive accounts", () => {
    render(<AssigneeSelect projectId="p-1" value="" onChange={vi.fn()} />)
    expect(useProjectMembers).toHaveBeenCalledWith('p-1')
    expect(optionLabels()).toEqual(['Unassigned', 'arun', 'Priya Nair'])
  })

  it('keeps a current assignee who is not a member (an administrator)', () => {
    render(<AssigneeSelect projectId="p-1" value="u-admin" onChange={vi.fn()} currentAssignee={{ id: 'u-admin', name: 'Dana Admin' }} />)
    expect(optionLabels()).toEqual(['Unassigned', 'Dana Admin', 'arun', 'Priya Nair'])
    expect(select()).toHaveValue('u-admin')
  })

  it('does not list a current assignee twice', () => {
    render(<AssigneeSelect projectId="p-1" value="u-priya" onChange={vi.fn()} currentAssignee={{ id: 'u-priya', name: 'Priya Nair' }} />)
    expect(optionLabels()).toEqual(['Unassigned', 'arun', 'Priya Nair'])
  })

  it('hands back the chosen id and name, and "" / null for Unassigned', () => {
    const onChange = vi.fn()
    render(<AssigneeSelect projectId="p-1" value="u-priya" onChange={onChange} />)
    fireEvent.change(select(), { target: { value: 'u-arun' } })
    fireEvent.change(select(), { target: { value: '' } })
    expect(onChange.mock.calls).toEqual([['u-arun', 'arun'], ['', null]])
  })
})
