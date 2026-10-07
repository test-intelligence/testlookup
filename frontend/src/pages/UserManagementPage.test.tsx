/**
 * /users in the settings layout (UX redesign P5).
 *
 *  - The tabs live in `?tab=` (`Tabs` + `useTabParam`): the settings sub-nav
 *    links "Members & access" to `/users?tab=project-members`, which used to
 *    open the Users tab because the page kept its tab in local state.
 *  - The signed-in user's own keys were a tab here; they are "My API keys" at
 *    /settings/my-api-keys now (PersonalKeysPage.test.tsx), reachable by every
 *    role that may own one. An old `?tab=api-keys` link lands there.
 *  - The template header (compact, help topic) replaces the hand-made <h1>.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const useApiKeysSpy = vi.fn()
let apiKeys: unknown[] = []

vi.mock('@/hooks/useUserManagement', () => ({
  useUsers: () => ({ data: [], isLoading: false }),
  refreshUsers: vi.fn(),
}))
vi.mock('@/hooks/useApiKeys', () => ({
  useApiKeys: (...args: unknown[]) => {
    useApiKeysSpy(...args)
    return { data: apiKeys, isLoading: false }
  },
  refreshApiKeys: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: true, canManageUsers: true, canGenerateApiKeys: true }),
}))
vi.mock('@/services/projectsService', () => ({
  projectsService: { list: vi.fn().mockResolvedValue([]) },
}))
vi.mock('@/services/userManagementService', () => ({
  userManagementService: {
    updateUserRole: vi.fn(),
    updateUserStatus: vi.fn(),
    revokeApiKey: vi.fn(),
    createApiKey: vi.fn(),
    createUser: vi.fn(),
  },
}))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('./ProjectMembersTab', () => ({
  ProjectMembersTab: () => <div data-testid="project-members-tab" />,
}))

import UserManagementPage from './UserManagementPage'

function LocationProbe() {
  const { pathname, search } = useLocation()
  return (
    <>
      <output data-testid="path">{pathname}</output>
      <output data-testid="search">{search}</output>
    </>
  )
}

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <UserManagementPage />
      <LocationProbe />
    </MemoryRouter>,
  )
}

function tab(name: string) {
  return within(screen.getByRole('tablist', { name: 'User management sections' })).getByRole('tab', { name })
}

beforeEach(() => {
  useApiKeysSpy.mockReset()
  apiKeys = []
})

describe('UserManagementPage — template header', () => {
  it('renders a compact PageHeader with the administration help topic, not a bare <h1>', () => {
    renderAt('/users')
    expect(screen.getByRole('heading', { level: 1, name: 'User Management' })).toBeInTheDocument()
    expect(document.querySelector('[data-page-header]')).toHaveAttribute('data-compact', 'true')
    expect(screen.getByRole('button', { name: 'Help: User Management' })).toHaveAttribute('data-help-topic', 'administration')
  })
})

describe('UserManagementPage — tabs in ?tab=', () => {
  it('opens the Users tab by default', () => {
    renderAt('/users')
    expect(tab('Users')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('combobox', { name: 'Filter by role' })).toBeInTheDocument()
    expect(screen.queryByTestId('project-members-tab')).toBeNull()
  })

  it('opens Project access from ?tab=project-members (the sub-nav\'s "Members & access" link)', () => {
    renderAt('/users?tab=project-members')
    expect(tab('Project access')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('project-members-tab')).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Filter by role' })).toBeNull()
  })

  it('an old ?tab=api-keys link lands on My API keys, its own page now', () => {
    renderAt('/users?tab=api-keys')
    expect(screen.getByTestId('path').textContent).toBe('/settings/my-api-keys')
  })

  it('writes the chosen tab to the URL, and drops it for the default', () => {
    renderAt('/users')
    fireEvent.click(tab('Project access'))
    expect(screen.getByTestId('search').textContent).toBe('?tab=project-members')
    expect(screen.getByTestId('project-members-tab')).toBeInTheDocument()

    fireEvent.click(tab('Users'))
    expect(screen.getByTestId('search').textContent).toBe('')
  })

  it('falls back to Users for an unknown tab id', () => {
    renderAt('/users?tab=apikeys')
    expect(tab('Users')).toHaveAttribute('aria-selected', 'true')
  })
})

describe('UserManagementPage — no keys of its own', () => {
  it('has no My API keys tab (the keys are on /settings/my-api-keys), and never asks for them', () => {
    renderAt('/users')
    expect(screen.queryByRole('tab', { name: 'My API keys' })).toBeNull()
    expect(screen.queryByRole('tab', { name: 'API Keys' })).toBeNull()
    expect(useApiKeysSpy).not.toHaveBeenCalled()
  })
})

describe('UserManagementPage — two-column Add User form', () => {
  it('pairs email | username and full name | role', () => {
    renderAt('/users')
    fireEvent.click(screen.getByRole('button', { name: /add user/i }))
    const dialog = screen.getByRole('dialog', { name: 'Add User' })
    const fields = dialog.querySelector('[data-add-user-fields]') as HTMLElement
    expect(fields.className).toContain('grid-cols-2')
    for (const label of ['Email address', 'Username', 'Full name (optional)', 'Role']) {
      expect(within(fields).getByLabelText(label)).toBeInTheDocument()
    }
  })
})
