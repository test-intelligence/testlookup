/**
 * Two API-key surfaces, named apart (UX redesign P5 item 3).
 *
 * `/settings/api-keys` is the PROJECT's keys for CI result streaming:
 * "Streaming API keys". The keys a person owns are "My API keys" on the Users
 * page. `GET /api/v1/keys?project_id=` answers an admin with the project's
 * keys but a non-admin with every key they own — bound to any project or none
 * — so the page keeps only the keys bound to the active project.
 *
 * Named for what it tests (the streaming keys), not `ApiKeysPage.test.tsx`:
 * `.gitignore`'s security glob `*apikey*` matches that name case-insensitively
 * on Windows, and `git add` there silently left the file out of the commit.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiKey } from '@/types/apiKey'
import ApiKeysPage from './ApiKeysPage'

let hookState: { data?: ApiKey[]; isLoading: boolean; error?: unknown; mutate: () => void }
let projectState = { activeProjectId: 'project-a', activeProject: { id: 'project-a', name: 'Project A' } }
let permissionsState = { isAdmin: true, role: 'ADMIN' }
const useApiKeysSpy = vi.fn()

vi.mock('@/services/apiKeyService', () => ({ apiKeyService: { create: vi.fn(), revoke: vi.fn() } }))
vi.mock('@/hooks/useApiKeys', () => ({
  useApiKeys: (projectId: unknown) => {
    useApiKeysSpy(projectId)
    return hookState
  },
  refreshApiKeys: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => permissionsState }))
vi.mock('@/store/authStore', () => ({
  useAuthStore: (selector: (state: { sessionGeneration: number; user: { id: string } }) => unknown) =>
    selector({ sessionGeneration: 1, user: { id: 'u-1' } }),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: typeof projectState) => unknown) => selector(projectState),
}))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

function key(id: string, projectId: string | null): ApiKey {
  return {
    id,
    name: `key-${id}`,
    key_hint: `qai_${id}...`,
    scopes: projectId ? ['stream:write'] : [],
    project_id: projectId,
    is_active: true,
    expires_at: null,
    last_used_at: null,
    created_at: '2026-10-01T00:00:00Z',
  }
}

function renderPage() {
  return render(<ApiKeysPage />, { wrapper: MemoryRouter })
}

beforeEach(() => {
  useApiKeysSpy.mockReset()
  hookState = { data: [], isLoading: false, mutate: vi.fn() }
  projectState = { activeProjectId: 'project-a', activeProject: { id: 'project-a', name: 'Project A' } }
  permissionsState = { isAdmin: true, role: 'ADMIN' }
})

describe('ApiKeysPage — "Streaming API keys"', () => {
  it('is titled "Streaming API keys", not "API Keys", with the template header', () => {
    renderPage()
    expect(screen.getByRole('heading', { level: 1, name: 'Streaming API keys' })).toBeInTheDocument()
    // A string name matches the whole accessible name (Testing Library's default).
    expect(screen.queryByRole('heading', { name: 'API Keys' })).toBeNull()
    expect(document.querySelector('[data-page-header]')).toHaveAttribute('data-compact', 'true')
    expect(screen.getByRole('button', { name: 'Help: Streaming API keys' })).toHaveAttribute('data-help-topic', 'administration')
  })

  it('keeps the name in All Projects mode', () => {
    // The project picker in the select-a-project state reads these two.
    projectState = {
      activeProjectId: '__ALL__',
      activeProject: null as unknown as typeof projectState.activeProject,
      projects: [],
      setActiveProject: vi.fn(),
    } as typeof projectState
    renderPage()
    expect(screen.getByRole('heading', { level: 1, name: 'Streaming API keys' })).toBeInTheDocument()
  })

  it('points to "My API keys" for the keys a person owns', () => {
    renderPage()
    const link = screen.getByRole('link', { name: 'My API keys' })
    expect(link.getAttribute('href')).toBe('/settings/my-api-keys')
  })

  it('asks for the active project\'s keys', () => {
    renderPage()
    expect(useApiKeysSpy).toHaveBeenCalledWith('project-a')
  })

  it('lists only keys bound to the active project (a non-admin is sent all of their own)', () => {
    permissionsState = { isAdmin: false, role: 'QA_LEAD' }
    hookState = {
      data: [key('a', 'project-a'), key('b', 'project-b'), key('personal', null)],
      isLoading: false,
      mutate: vi.fn(),
    }
    renderPage()

    expect(screen.getByText('key-a')).toBeInTheDocument()
    expect(screen.queryByText('key-b')).toBeNull()
    expect(screen.queryByText('key-personal')).toBeNull()
  })

  it('tells a non-admin with no key on this project exactly that', () => {
    permissionsState = { isAdmin: false, role: 'QA_LEAD' }
    hookState = { data: [key('personal', null)], isLoading: false, mutate: vi.fn() }
    renderPage()

    expect(screen.getByText('No streaming keys yet')).toBeInTheDocument()
    expect(screen.getByText('None of your keys are bound to this project.')).toBeInTheDocument()
    expect(screen.queryByText('key-personal')).toBeNull()
  })

  it('lists no personal (unbound) key while no project is picked yet', () => {
    projectState = { activeProjectId: null as unknown as string, activeProject: null as unknown as typeof projectState.activeProject }
    hookState = { data: [key('personal', null)], isLoading: false, mutate: vi.fn() }
    renderPage()

    expect(screen.queryByText('key-personal')).toBeNull()
    expect(screen.getByText('No streaming keys yet')).toBeInTheDocument()
  })

  it('lists every key bound to the project for an admin', () => {
    hookState = { data: [key('a', 'project-a'), key('c', 'project-a')], isLoading: false, mutate: vi.fn() }
    renderPage()

    expect(screen.getByText('key-a')).toBeInTheDocument()
    expect(screen.getByText('key-c')).toBeInTheDocument()
  })
})
