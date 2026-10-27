/**
 * ProjectMembersTab — Default QA Lead card tests (2026-05-14 feature).
 *
 * Pins three things added in this feature:
 *
 *  1. The picker lists ONLY project members with role === 'QA_LEAD'.
 *     Surfacing non-QA_LEAD users would just produce 400s from the
 *     backend's ``assert_user_is_qa_lead_on_project`` check.
 *
 *  2. ADMIN sees the editable picker; non-ADMIN sees a read-only label
 *     ending in "(ADMIN required to change)".
 *
 *  3. The Save button is disabled when the draft equals the persisted
 *     value (defence against double-PUT on accidental clicks).
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ProjectMembersTab } from './ProjectMembersTab'
import type { ProjectMember } from '@/services/userManagementService'
import type { Project } from '@/types/projects'

const PROJECT_ID = '00000000-0000-0000-0000-0000000000aa'
const QA_LEAD_ID = '00000000-0000-0000-0000-00000000000a'
const QA_ENGINEER_ID = '00000000-0000-0000-0000-00000000000b'

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: PROJECT_ID, name: 'GoogleProject', slug: 'google-project',
    is_active: true, created_at: '2026-05-01T00:00:00Z',
    default_qa_lead_user_id: null,
    ...overrides,
  }
}

function makeMember(role: 'QA_LEAD' | 'QA_ENGINEER' | 'ADMIN', overrides: Partial<ProjectMember> = {}): ProjectMember {
  return {
    id: `m-${role}`, project_id: PROJECT_ID,
    user_id: role === 'QA_LEAD' ? QA_LEAD_ID : QA_ENGINEER_ID,
    role,
    full_name: `${role} User`,
    username: role.toLowerCase(),
    email: `${role.toLowerCase()}@x.test`,
    created_at: '2026-05-01T00:00:00Z',
    ...overrides,
  }
}

const mockListProjectMembers = vi.fn()
const mockUpdateProject = vi.fn()
const mockUsersHook = vi.fn()
const mockProjectMembersHook = vi.fn()
const mockListProjects = vi.fn()
const mockResetQaLead = vi.fn()
const mockRemoveMember = vi.fn()

vi.mock('@/services/projectsService', () => ({
  projectsService: {
    list:   (...args: unknown[]) => mockListProjects(...args),
    update: (...args: unknown[]) => mockUpdateProject(...args),
    resetDefaultQaLeadPassword: (...args: unknown[]) => mockResetQaLead(...args),
  },
}))

vi.mock('@/services/userManagementService', () => ({
  userManagementService: {
    listProjectMembers:       (...args: unknown[]) => mockListProjectMembers(...args),
    addProjectMember:         vi.fn(),
    updateProjectMemberRole:  vi.fn(),
    removeProjectMember:      (...args: unknown[]) => mockRemoveMember(...args),
  },
}))

vi.mock('@/hooks/useUserManagement', () => ({
  // ADM-03: the Add Member picker searches a server page ({items, total}); fixtures stay plain lists.
  useUsersPage: (...args: unknown[]) => {
    const r = mockUsersHook(...args) as { data?: unknown[] } | undefined
    return { ...r, data: Array.isArray(r?.data) ? { items: r.data, total: r.data.length } : r?.data }
  },
  useProjectMembers: (...args: unknown[]) => mockProjectMembersHook(...args),
}))

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}))

async function renderAndPickProject(opts: { isAdmin: boolean; project?: Project; members?: ProjectMember[] }) {
  const project = opts.project ?? makeProject()
  mockListProjects.mockResolvedValue([project])
  mockListProjectMembers.mockResolvedValue(opts.members ?? [])
  mockUsersHook.mockReturnValue({ data: [], isLoading: false, error: null })
  // Members now flow through the useProjectMembers SWR hook (keyed on the
  // selected project); return data only once a project is selected.
  mockProjectMembersHook.mockImplementation((projectId: string | null) => ({
    data: projectId ? (opts.members ?? []) : undefined,
    isLoading: false,
    error: null,
    mutate: vi.fn(),
  }))

  render(<ProjectMembersTab isAdmin={opts.isAdmin} canManageUsers={true} />)

  // Wait for the project list dropdown to populate, then pick the project.
  // The "Select Project" label isn't tied to the select via ``htmlFor``,
  // so we locate the project picker by the option text it renders.
  await waitFor(() => expect(mockListProjects).toHaveBeenCalled())
  const projectOption = await screen.findByRole('option', { name: project.name })
  const projectSelect = projectOption.parentElement as HTMLSelectElement
  fireEvent.change(projectSelect, { target: { value: project.id } })
  await waitFor(() => expect(mockProjectMembersHook).toHaveBeenCalledWith(project.id))
}

describe('ProjectMembersTab — Default QA Lead', () => {
  beforeEach(() => {
    mockListProjectMembers.mockReset()
    mockListProjects.mockReset()
    mockUpdateProject.mockReset()
    mockUsersHook.mockReset()
    mockProjectMembersHook.mockReset()
  })

  it('shows the picker for ADMIN with only QA_LEAD candidates', async () => {
    await renderAndPickProject({
      isAdmin: true,
      members: [
        makeMember('QA_LEAD'),
        makeMember('QA_ENGINEER'),
      ],
    })

    expect(await screen.findByText(/Default QA Lead/)).toBeInTheDocument()

    // Find the Default QA Lead select by locating the option whose text
    // matches the QA_LEAD candidate, then walking up to its parent select.
    const qaLeadOption = await screen.findByRole('option', {
      name: /QA_LEAD User \(qa_lead@x\.test\)/,
    }) as HTMLOptionElement
    const picker = qaLeadOption.parentElement as HTMLSelectElement
    const optionLabels = Array.from(picker.options).map(o => o.textContent ?? '')
    // The picker must NOT surface the QA_ENGINEER member — surfacing them
    // would just produce 400s on save.
    expect(optionLabels.some(o => o.includes('qa_engineer@x.test'))).toBe(false)
    expect(optionLabels.some(o => o.includes('qa_lead@x.test'))).toBe(true)
  })

  it('marks a membership granted through a SCIM group (W20 ADM-09)', async () => {
    await renderAndPickProject({
      isAdmin: true,
      members: [{ ...makeMember('QA_ENGINEER'), source: 'scim:payments-qa' }, makeMember('QA_LEAD')],
    })
    const badges = await screen.findAllByTestId('member-source')
    expect(badges).toHaveLength(1)
    expect(badges[0]).toHaveTextContent('via SCIM group payments-qa')
  })

  it('warns when no member has role=QA_LEAD yet', async () => {
    await renderAndPickProject({
      isAdmin: true,
      members: [makeMember('QA_ENGINEER')],
    })

    expect(
      await screen.findByText(/No project members have the QA_LEAD role yet/),
    ).toBeInTheDocument()
  })

  it('renders read-only label for non-ADMIN viewers', async () => {
    await renderAndPickProject({
      isAdmin: false,
      project: makeProject({ default_qa_lead_user_id: QA_LEAD_ID }),
      members: [makeMember('QA_LEAD')],
    })

    expect(
      await screen.findByText(/ADMIN required to change/),
    ).toBeInTheDocument()
  })

  it('disables Save until the draft diverges from the persisted value', async () => {
    await renderAndPickProject({
      isAdmin: true,
      project: makeProject({ default_qa_lead_user_id: QA_LEAD_ID }),
      members: [makeMember('QA_LEAD')],
    })

    // The Save button starts disabled because the draft equals the
    // persisted value (the effect seeded the draft from the project).
    const saveBtn = await screen.findByRole('button', { name: /Save/i })
    expect(saveBtn).toBeDisabled()
  })
})

// ── W27b PAN-06: Add Member, Remove and the QA lead reset are in the page ──

describe('ProjectMembersTab in the page (W27b)', () => {
  beforeEach(() => {
    mockListProjectMembers.mockReset()
    mockListProjects.mockReset()
    mockUsersHook.mockReset()
    mockProjectMembersHook.mockReset()
    mockResetQaLead.mockReset()
    mockRemoveMember.mockReset()
  })

  it('Add Member opens right under the tab\'s top row, named after the project (no dialog)', async () => {
    await renderAndPickProject({ isAdmin: true, members: [makeMember('QA_LEAD')] })
    const trigger = screen.getByRole('button', { name: 'Add Member' })
    fireEvent.click(trigger)
    const section = screen.getByRole('region', { name: 'Add Member to GoogleProject' })
    const topRow = trigger.parentElement as HTMLElement
    expect(topRow.nextElementSibling).toBe(section)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
    fireEvent.click(within(section).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Add Member to GoogleProject' })).toBeNull()
  })

  it('ADM-03: Add Member searches the server, leaves members out, and says when more match than are shown', async () => {
    const lead = makeMember('QA_LEAD')
    await renderAndPickProject({ isAdmin: true, members: [lead] })
    const beyond = { id: 'u-sara', email: 'sara@x.test', username: 'eng_sara', full_name: 'Sara Eng',
                     role: 'QA_ENGINEER', is_active: true, created_at: '2026-05-01T00:00:00Z' }
    const already = { ...beyond, id: lead.user_id, username: 'lead', full_name: 'Already Member', email: 'lead@x.test' }
    mockUsersHook.mockImplementation((params: { q?: string }) => ({
      data: params?.q === 'sara' ? { items: [beyond, already], total: 41 } : { items: [], total: 0 },
      isLoading: false, error: null,
    }))
    fireEvent.click(screen.getByRole('button', { name: 'Add Member' }))
    const section = screen.getByRole('region', { name: 'Add Member to GoogleProject' })
    expect(mockUsersHook).toHaveBeenLastCalledWith({ is_active: true, page_size: 20 })
    fireEvent.change(within(section).getByLabelText('User'), { target: { value: 'sara' } })
    await waitFor(() => expect(mockUsersHook).toHaveBeenLastCalledWith({ is_active: true, page_size: 20, q: 'sara' }))
    expect(await within(section).findByRole('option', { name: 'Sara Eng (sara@x.test)' })).toBeInTheDocument()
    expect(within(section).queryByRole('option', { name: /Already Member/ })).toBeNull()
    expect(within(section).getByText('Showing the first 2 of 41 matches — type more to narrow.')).toBeInTheDocument()
  })

  it('Remove from project asks in a row under the member (colspan 5); the trigger sends nothing, the strip\'s Remove sends once; no native confirm', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    mockRemoveMember.mockResolvedValue(undefined)
    await renderAndPickProject({ isAdmin: true, members: [makeMember('QA_LEAD'), makeMember('QA_ENGINEER')] })
    const row = (await screen.findByText('QA_ENGINEER User')).closest('tr') as HTMLElement
    fireEvent.click(within(row).getByTitle('Remove from project'))
    expect(mockRemoveMember, 'the trigger sends nothing').not.toHaveBeenCalled()
    const formRow = row.nextElementSibling as HTMLElement
    expect(formRow).toHaveAttribute('data-in-page-row')
    expect(formRow.querySelector('td')).toHaveAttribute('colspan', '5')
    const strip = formRow.querySelector('[data-inline-confirm]') as HTMLElement
    expect(strip.querySelector('p')?.textContent).toBe('Remove this user from the project?')

    fireEvent.click(within(strip).getByRole('button', { name: 'Cancel' }))
    expect(document.querySelector('[data-inline-confirm]')).toBeNull()
    fireEvent.click(within(row).getByTitle('Remove from project'))
    fireEvent.click(document.querySelector('[data-inline-confirm-action]') as HTMLElement)
    await waitFor(() => expect(mockRemoveMember).toHaveBeenCalledTimes(1))
    expect(mockRemoveMember).toHaveBeenCalledWith(PROJECT_ID, QA_ENGINEER_ID)
    expect(confirmSpy).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('Reset QA Lead password asks under the card\'s footer row with the old sentence; only the strip\'s button sends', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    mockResetQaLead.mockResolvedValue({ email: 'qa@x.test', password: 'Pw-once-1' })
    await renderAndPickProject({ isAdmin: true, members: [makeMember('QA_LEAD')] })
    const trigger = await screen.findByRole('button', { name: 'Reset QA Lead password' })
    fireEvent.click(trigger)
    expect(mockResetQaLead, 'the trigger sends nothing').not.toHaveBeenCalled()
    const strip = document.querySelector('[data-inline-confirm]') as HTMLElement
    expect(trigger.parentElement?.nextElementSibling?.contains(strip), 'right under the footer row').toBe(true)
    expect(strip.querySelector('p')?.textContent).toBe(
      'Reset the password of this project’s auto-provisioned QA Lead user? The new password will be displayed once — copy it before closing.',
    )
    expect(within(strip).getByRole('button', { name: 'Reset password' })).toHaveAttribute('data-inline-confirm-action')
    fireEvent.click(within(strip).getByRole('button', { name: 'Reset password' }))
    await waitFor(() => expect(mockResetQaLead).toHaveBeenCalledTimes(1))
    expect(mockResetQaLead).toHaveBeenCalledWith(PROJECT_ID)
    expect(confirmSpy).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })
})
