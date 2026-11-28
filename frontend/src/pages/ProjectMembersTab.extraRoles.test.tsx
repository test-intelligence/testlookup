/**
 * Extra job roles on a project's members (enterprise roles E00-S05, D-16).
 *
 * The row shows a member's extra roles; "Extra roles" opens a picker under the row and Save sends the
 * member's role with the chosen extra roles (the server checks the grant ceiling and names what is missing);
 * Add Member sends extra roles only when some are chosen.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ProjectMembersTab } from './ProjectMembersTab'
import type { ProjectMember } from '@/services/userManagementService'

const fx = vi.hoisted(() => ({
  PROJECT: { id: 'p1', name: 'Payments', slug: 'payments', is_active: true, created_at: '', default_qa_lead_user_id: null },
  DEV: {
    id: 'm1', project_id: 'p1', user_id: 'u1', role: 'DEVELOPER', additional_roles: ['RELEASE_MANAGER'],
    full_name: 'Dana Dev', username: 'dana', email: 'dana@x.test', created_at: '2026-05-01T00:00:00Z',
  } as ProjectMember,
}))
const DEV = fx.DEV

const svc = vi.hoisted(() => ({ update: vi.fn(), add: vi.fn(), mutate: vi.fn() }))
vi.mock('@/services/projectsService', () => ({
  projectsService: { list: vi.fn().mockResolvedValue([fx.PROJECT]), update: vi.fn(), resetDefaultQaLeadPassword: vi.fn() },
}))
vi.mock('@/services/userManagementService', () => ({
  userManagementService: {
    listProjectMembers: vi.fn(),
    addProjectMember: (...a: unknown[]) => svc.add(...a),
    updateProjectMemberRole: (...a: unknown[]) => svc.update(...a),
    removeProjectMember: vi.fn(),
  },
}))
vi.mock('@/hooks/useUserManagement', () => ({
  useUsersPage: () => ({ data: { items: [], total: 0 }, isLoading: false, error: null }),
  useProjectMembers: (projectId: string | null) => ({
    data: projectId ? [fx.DEV] : undefined, isLoading: false, error: null, mutate: svc.mutate,
  }),
}))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

async function renderAndPick(isAdmin = true) {
  render(<ProjectMembersTab isAdmin={isAdmin} canManageUsers={true} />)
  const option = await screen.findByRole('option', { name: 'Payments' })
  fireEvent.change(option.parentElement as HTMLSelectElement, { target: { value: 'p1' } })
  return (await screen.findByText('Dana Dev')).closest('tr') as HTMLElement
}

beforeEach(() => {
  vi.clearAllMocks()
  svc.update.mockResolvedValue({ ...DEV, additional_roles: ['PRODUCT_MANAGER'] })
})

describe('ProjectMembersTab — extra job roles', () => {
  it('shows a member\'s extra roles under their role', async () => {
    const row = await renderAndPick()
    expect(within(row).getByText('+ Release Manager')).toBeInTheDocument()
  })

  it('edits the extra roles under the row and saves them with the member\'s role', async () => {
    const row = await renderAndPick()
    fireEvent.click(within(row).getByRole('button', { name: 'Extra roles' }))
    // The primary role is not offered again.
    expect(within(row).queryByLabelText('Developer')).toBeNull()
    fireEvent.click(within(row).getByLabelText('Release Manager'))
    fireEvent.click(within(row).getByLabelText('Product Manager'))
    fireEvent.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(svc.update).toHaveBeenCalledWith('p1', 'u1', 'DEVELOPER', ['PRODUCT_MANAGER']))
    expect(svc.mutate).toHaveBeenCalled()
  })

  it('a QA lead is not offered the manager roles, but a role the member holds stays shown (D-17)', async () => {
    const row = await renderAndPick(false)
    const rowRoles = Array.from((within(row).getAllByRole('combobox')[0] as HTMLSelectElement).options).map((o) => o.value)
    expect(rowRoles).toEqual(['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN', 'DEVELOPER'])
    fireEvent.click(within(row).getByRole('button', { name: 'Extra roles' }))
    // Release Manager is held (an administrator gave it): shown, and can be removed. The other two are not offered.
    expect(within(row).getByLabelText('Release Manager')).toBeChecked()
    expect(within(row).queryByLabelText('Project Manager')).toBeNull()
    expect(within(row).queryByLabelText('Product Manager')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /add member/i }))
    const form = await screen.findByRole('region', { name: /add member/i })
    const addRoles = Array.from((within(form).getByLabelText('Project Role') as HTMLSelectElement).options).map((o) => o.value)
    expect(addRoles).toEqual(['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN', 'DEVELOPER'])
    expect(within(form).queryByLabelText('Release Manager')).toBeNull()
  })

  it('an administrator is offered every role', async () => {
    await renderAndPick(true)
    fireEvent.click(screen.getByRole('button', { name: /add member/i }))
    const form = await screen.findByRole('region', { name: /add member/i })
    const addRoles = Array.from((within(form).getByLabelText('Project Role') as HTMLSelectElement).options).map((o) => o.value)
    expect(addRoles).toContain('RELEASE_MANAGER')
    expect(within(form).getByLabelText('Product Manager')).toBeInTheDocument()
  })

  it('a role change from the row keeps the extra roles (none sent)', async () => {
    const row = await renderAndPick()
    const select = within(row).getAllByRole('combobox')[0] as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'TESTER' } })
    await waitFor(() => expect(svc.update).toHaveBeenCalledWith('p1', 'u1', 'TESTER'))
  })
})
