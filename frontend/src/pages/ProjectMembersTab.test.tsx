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
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
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
const mockListProjects = vi.fn()

vi.mock('@/services/projectsService', () => ({
  projectsService: {
    list:   (...args: unknown[]) => mockListProjects(...args),
    update: (...args: unknown[]) => mockUpdateProject(...args),
  },
}))

vi.mock('@/services/userManagementService', () => ({
  userManagementService: {
    listProjectMembers:       (...args: unknown[]) => mockListProjectMembers(...args),
    addProjectMember:         vi.fn(),
    updateProjectMemberRole:  vi.fn(),
    removeProjectMember:      vi.fn(),
  },
}))

vi.mock('@/hooks/useUserManagement', () => ({
  useUsers: (...args: unknown[]) => mockUsersHook(...args),
}))

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}))

async function renderAndPickProject(opts: { isAdmin: boolean; project?: Project; members?: ProjectMember[] }) {
  const project = opts.project ?? makeProject()
  mockListProjects.mockResolvedValue([project])
  mockListProjectMembers.mockResolvedValue(opts.members ?? [])
  mockUsersHook.mockReturnValue({ data: [], isLoading: false, error: null })

  render(<ProjectMembersTab isAdmin={opts.isAdmin} canManageUsers={true} />)

  // Wait for the project list dropdown to populate, then pick the project.
  // The "Select Project" label isn't tied to the select via ``htmlFor``,
  // so we locate the project picker by the option text it renders.
  await waitFor(() => expect(mockListProjects).toHaveBeenCalled())
  const projectOption = await screen.findByRole('option', { name: project.name })
  const projectSelect = projectOption.parentElement as HTMLSelectElement
  fireEvent.change(projectSelect, { target: { value: project.id } })
  await waitFor(() => expect(mockListProjectMembers).toHaveBeenCalledWith(project.id))
}

describe('ProjectMembersTab — Default QA Lead', () => {
  beforeEach(() => {
    mockListProjectMembers.mockReset()
    mockListProjects.mockReset()
    mockUpdateProject.mockReset()
    mockUsersHook.mockReset()
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
