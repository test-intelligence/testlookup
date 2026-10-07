/**
 * /users in the settings layout (UX redesign P5).
 *
 *  - The tabs live in `?tab=` (`Tabs` + `useTabParam`): the settings sub-nav
 *    links "Members & access" to `/users?tab=project-members`, which used to
 *    open the Users tab because the page kept its tab in local state.
 *  - The API-keys tab is the signed-in user's OWN keys (`GET /api/v1/keys`
 *    with no project is owner-only for every role), so it is "My API keys",
 *    named apart from the project's "Streaming API keys" (item 3).
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
  const { search } = useLocation()
  return <output data-testid="search">{search}</output>
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

  it('opens My API keys from ?tab=api-keys', () => {
    renderAt('/users?tab=api-keys')
    expect(tab('My API keys')).toHaveAttribute('aria-selected', 'true')
    expect(document.querySelector('[data-my-api-keys-intro]')).not.toBeNull()
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

describe('UserManagementPage — "My API keys"', () => {
  it('names the tab "My API keys"; the old "API Keys" label is gone', () => {
    renderAt('/users')
    expect(tab('My API keys')).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'API Keys' })).toBeNull()
  })

  it('lists the signed-in user\'s own keys: the hook is called without a project', () => {
    renderAt('/users?tab=api-keys')
    expect(useApiKeysSpy).toHaveBeenCalled()
    for (const call of useApiKeysSpy.mock.calls) expect(call[0]).toBeUndefined()
  })

  it('says whose keys they are and points to the project\'s Streaming API keys', () => {
    renderAt('/users?tab=api-keys')
    const intro = document.querySelector('[data-my-api-keys-intro]') as HTMLElement
    expect(intro.textContent).toMatch(/API keys you own/)
    expect(intro.textContent).toMatch(/acts as you/)
    const link = within(intro).getByRole('link', { name: 'Streaming API keys' })
    expect(link.getAttribute('href')).toBe('/settings/api-keys')
    expect(screen.getByText(/You have no API keys/)).toBeInTheDocument()
  })

  it('generates "my" key in a dialog with name | expiry side by side', () => {
    renderAt('/users?tab=api-keys')
    fireEvent.click(screen.getByRole('button', { name: /generate key/i }))
    const dialog = screen.getByRole('dialog', { name: 'Generate my API key' })
    const fields = dialog.querySelector('[data-create-key-fields]') as HTMLElement
    expect(fields.className).toContain('grid-cols-2')
    expect(within(fields).getByLabelText('Key name')).toBeInTheDocument()
    expect(within(fields).getByLabelText('Expiry (days, optional)')).toBeInTheDocument()
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
