/**
 * Hermetic tests for the GitLab integration settings page (PMF Epic 3 UI).
 *
 * Covers the load-bearing contract behaviours:
 *   - PUT payload shape: token sent ONLY when the user entered one; base_url,
 *     project_path, mr_comment_mode, commit_status_enabled carried verbatim;
 *     "Remove token" sends the contract's `token: ""` clear.
 *   - Failed GET → error state, NO form/Save (a Save from the default-seeded
 *     form would wipe a working config with defaults).
 *   - Dirty edits disable "Test connection" (it tests the SAVED config).
 *   - Test-connection surfaces ok/detail + resolved project id.
 *   - has_token UX: "Token set" + "Replace token" when true; input when false;
 *     token input cleared/re-hidden after a successful save.
 *   - All-Projects mode → select-a-project empty state, zero fetches.
 *   - Permission-denied (canAccessManagement: false) → read-only, no buttons.
 *
 * The service layer and project store are mocked; the REAL SWR hook runs
 * under an isolated SWRConfig so seeding/revalidation behaviour is covered.
 */
import { createElement } from 'react'
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

import type { GitLabConfig } from '@/types/gitlab'

// ── mocks ───────────────────────────────────────────────────────────────────

const mockGet = vi.fn()
const mockUpdate = vi.fn()
const mockTest = vi.fn()

vi.mock('@/services/gitlabIntegrationService', () => ({
  gitlabIntegrationService: {
    get: (...a: unknown[]) => mockGet(...a),
    update: (...a: unknown[]) => mockUpdate(...a),
    test: (...a: unknown[]) => mockTest(...a),
  },
}))

let mockCanManage = true
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: mockCanManage }),
}))

// Project store: mutable state so tests can flip into All-Projects mode.
// ALL_PROJECTS_ID is the REAL constant (importActual) so the page's sentinel
// comparison is tested against the production value, not an invented one.
const storeState: {
  activeProjectId: string | null
  activeProject: { id: string; name: string } | null
} = {
  activeProjectId: 'proj-1',
  activeProject: { id: 'proj-1', name: 'Checkout' },
}
vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  const useProjectStore = (sel: (s: typeof storeState) => unknown) => sel(storeState)
  return { useProjectStore, ALL_PROJECTS_ID: actual.ALL_PROJECTS_ID }
})

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

// Lightweight stand-ins for shared UI so the test stays hermetic.
vi.mock('@/components/ui/ExperimentalBadge', () => ({ default: () => null }))
vi.mock('@/components/ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => createElement('h1', null, title),
}))
vi.mock('@/components/ui/LoadingSpinner', () => ({ default: () => createElement('div', null, 'loading') }))
vi.mock('@/components/ui/EmptyState', () => ({
  default: ({ title }: { title: string }) => createElement('div', null, title),
}))
vi.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => createElement('a', null, children),
}))

function baseConfig(overrides: Partial<GitLabConfig> = {}): GitLabConfig {
  return {
    enabled: true,
    base_url: 'https://gitlab.com',
    project_path: 'acme/webapp',
    mr_comment_mode: 'failures_only',
    commit_status_enabled: true,
    has_token: false,
    last_error: null,
    last_error_at: null,
    ...overrides,
  }
}

let GitLabIntegrationPage: (typeof import('./GitLabIntegrationPage'))['default']

beforeAll(async () => {
  // Import once outside the individual tests' 5-second budgets. Router 7's
  // larger transform graph can make a per-test dynamic import time out only
  // under the full parallel suite even though the page is already rendered.
  const pageModule = await import('./GitLabIntegrationPage')
  GitLabIntegrationPage = pageModule.default
}, 30_000)

function renderPage() {
  return render(
    createElement(
      SWRConfig,
      {
        value: {
          provider: () => new Map(),
          dedupingInterval: 0,
          shouldRetryOnError: false,
          revalidateOnFocus: false,
        },
      },
      createElement(GitLabIntegrationPage),
    ),
  )
}

describe('GitLabIntegrationPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCanManage = true
    storeState.activeProjectId = 'proj-1'
    storeState.activeProject = { id: 'proj-1', name: 'Checkout' }
    mockGet.mockResolvedValue(baseConfig())
    mockUpdate.mockResolvedValue(baseConfig())
    mockTest.mockResolvedValue({ ok: true, detail: 'Reached GitLab', project_id_resolved: '42' })
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('omits token from the PUT payload when the user did not enter one', async () => {
    mockGet.mockResolvedValue(baseConfig({ has_token: true, project_path: 'acme/webapp' }))
    await renderPage()

    await waitFor(() => expect(screen.getByText('Token set')).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByText('Save'))
    })

    expect(mockUpdate).toHaveBeenCalledTimes(1)
    const [projectId, payload] = mockUpdate.mock.calls[0]
    expect(projectId).toBe('proj-1')
    expect(payload).toEqual({
      enabled: true,
      base_url: 'https://gitlab.com',
      project_path: 'acme/webapp',
      mr_comment_mode: 'failures_only',
      commit_status_enabled: true,
    })
    expect('token' in payload).toBe(false)
  })

  it('includes token in the PUT payload when the user enters one', async () => {
    mockGet.mockResolvedValue(baseConfig({ has_token: false }))
    await renderPage()

    const tokenInput = await screen.findByPlaceholderText('glpat-...')
    await act(async () => {
      fireEvent.change(tokenInput, { target: { value: 'glpat-secret' } })
    })
    await act(async () => {
      fireEvent.click(screen.getByText('Save'))
    })

    const payload = mockUpdate.mock.calls[0][1]
    expect(payload.token).toBe('glpat-secret')
    expect(payload.base_url).toBe('https://gitlab.com')
    expect(payload.project_path).toBe('acme/webapp')
  })

  it('carries edited base_url, project_path, commit_status and mode into the payload', async () => {
    mockGet.mockResolvedValue(baseConfig({ has_token: true }))
    await renderPage()

    await waitFor(() => expect(screen.getByText('Token set')).toBeTruthy())

    fireEvent.change(screen.getByPlaceholderText('https://gitlab.com'), {
      target: { value: 'https://gitlab.corp.com' },
    })
    fireEvent.change(screen.getByPlaceholderText('my-group/my-project'), {
      target: { value: 'team/service' },
    })
    // MR comment mode select → "always".
    const select = screen.getByRole('combobox')
    fireEvent.change(select, { target: { value: 'always' } })

    await act(async () => {
      fireEvent.click(screen.getByText('Save'))
    })

    const payload = mockUpdate.mock.calls[0][1]
    expect(payload.base_url).toBe('https://gitlab.corp.com')
    expect(payload.project_path).toBe('team/service')
    expect(payload.mr_comment_mode).toBe('always')
    expect(payload.commit_status_enabled).toBe(true)
  })

  it('shows an input (not "Token set") when no token is stored', async () => {
    mockGet.mockResolvedValue(baseConfig({ has_token: false }))
    await renderPage()

    expect(await screen.findByPlaceholderText('glpat-...')).toBeTruthy()
    expect(screen.queryByText('Token set')).toBeNull()
    expect(screen.queryByText('Replace token')).toBeNull()
  })

  it('reveals the token input via "Replace token" when a token is stored', async () => {
    mockGet.mockResolvedValue(baseConfig({ has_token: true }))
    await renderPage()

    await waitFor(() => expect(screen.getByText('Token set')).toBeTruthy())
    expect(screen.queryByPlaceholderText('glpat-...')).toBeNull()

    fireEvent.click(screen.getByText('Replace token'))
    expect(screen.getByPlaceholderText('glpat-...')).toBeTruthy()
  })

  it('clears and re-hides the token input after a successful save', async () => {
    mockGet.mockResolvedValue(baseConfig({ has_token: false }))
    mockUpdate.mockResolvedValue(baseConfig({ has_token: true }))
    await renderPage()

    const tokenInput = await screen.findByPlaceholderText('glpat-...')
    await act(async () => {
      fireEvent.change(tokenInput, { target: { value: 'glpat-secret' } })
    })
    await act(async () => {
      fireEvent.click(screen.getByText('Save'))
    })

    // The saved response (has_token: true) re-seeds the token UX: the input
    // is gone (draft cleared with it) and "Token set" is shown instead.
    await waitFor(() => expect(screen.getByText('Token set')).toBeTruthy())
    expect(screen.queryByPlaceholderText('glpat-...')).toBeNull()
  })

  it('sends token: "" on save after "Remove token" is confirmed', async () => {
    mockGet.mockResolvedValue(baseConfig({ has_token: true }))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await renderPage()

    await waitFor(() => expect(screen.getByText('Token set')).toBeTruthy())
    fireEvent.click(screen.getByText('Remove token'))
    expect(screen.getByText(/Token will be removed when you save/)).toBeTruthy()

    await act(async () => {
      fireEvent.click(screen.getByText('Save'))
    })

    const payload = mockUpdate.mock.calls[0][1]
    expect(payload.token).toBe('')
  })

  it('surfaces test-connection ok/detail + resolved project id', async () => {
    mockGet.mockResolvedValue(baseConfig({ has_token: true }))
    await renderPage()

    await screen.findByText('Test connection')
    await act(async () => {
      fireEvent.click(screen.getByText('Test connection'))
    })

    expect(mockTest).toHaveBeenCalledWith('proj-1')
    await waitFor(() => expect(screen.getByText(/Reached GitLab/)).toBeTruthy())
    expect(screen.getByText(/resolved project id: 42/)).toBeTruthy()
  })

  it('surfaces a failed connection test detail', async () => {
    mockGet.mockResolvedValue(baseConfig({ has_token: true }))
    mockTest.mockResolvedValue({ ok: false, detail: '401 Unauthorized', project_id_resolved: null })
    await renderPage()

    await screen.findByText('Test connection')
    await act(async () => {
      fireEvent.click(screen.getByText('Test connection'))
    })

    await waitFor(() => expect(screen.getByText(/401 Unauthorized/)).toBeTruthy())
    expect(screen.queryByText(/resolved project id/)).toBeNull()
  })

  it('disables "Test connection" while the form is dirty', async () => {
    mockGet.mockResolvedValue(baseConfig({ has_token: true }))
    await renderPage()

    await waitFor(() => expect(screen.getByText('Token set')).toBeTruthy())
    const testButton = screen.getByText('Test connection').closest('button') as HTMLButtonElement
    expect(testButton.disabled).toBe(false)

    fireEvent.change(screen.getByPlaceholderText('my-group/my-project'), {
      target: { value: 'team/other' },
    })

    expect(testButton.disabled).toBe(true)
    expect(testButton.title).toBe('Save your changes first — Test uses the saved configuration')

    // A successful save clears the dirty state and re-enables Test.
    await act(async () => {
      fireEvent.click(screen.getByText('Save'))
    })
    expect(testButton.disabled).toBe(false)
  })

  it('renders an integration-health warning when last_error is present', async () => {
    mockGet.mockResolvedValue(
      baseConfig({
        has_token: true,
        last_error: 'commit status POST failed: 403',
        last_error_at: '2026-07-15T10:00:00Z',
      }),
    )
    await renderPage()

    await waitFor(() =>
      expect(screen.getByText(/commit status POST failed: 403/)).toBeTruthy(),
    )
  })

  it('renders the select-a-project empty state in All-Projects mode without fetching', async () => {
    const { ALL_PROJECTS_ID } = await import('@/store/projectStore')
    // Guard against the mock drifting from the production sentinel.
    expect(ALL_PROJECTS_ID).toBe('all')
    storeState.activeProjectId = ALL_PROJECTS_ID
    storeState.activeProject = null
    await renderPage()

    expect(await screen.findByText('Select a project')).toBeTruthy()
    expect(mockGet).not.toHaveBeenCalled()
    expect(mockUpdate).not.toHaveBeenCalled()
    expect(screen.queryByText('Save')).toBeNull()
  })

  it('renders an error state without Save when the GET fails', async () => {
    mockGet.mockRejectedValue(new Error('network down'))
    await renderPage()

    await waitFor(() =>
      expect(screen.getByText(/Could not load the GitLab integration settings/)).toBeTruthy(),
    )
    // The form is withheld: a Save from the default-seeded form would wipe a
    // working configuration with defaults.
    expect(screen.queryByText('Save')).toBeNull()
    expect(screen.queryByText('Test connection')).toBeNull()
    expect(screen.getByText('Retry')).toBeTruthy()

    // Retry refetches; a successful response swaps in the real form.
    mockGet.mockResolvedValue(baseConfig({ has_token: true }))
    await act(async () => {
      fireEvent.click(screen.getByText('Retry'))
    })
    await waitFor(() => expect(screen.getByText('Save')).toBeTruthy())
    expect(screen.getByText('Token set')).toBeTruthy()
  })

  it('hides Save/Test/Replace for viewers without management access', async () => {
    mockCanManage = false
    mockGet.mockResolvedValue(baseConfig({ has_token: true }))
    await renderPage()

    await waitFor(() => expect(screen.getByText('Token set')).toBeTruthy())
    expect(screen.queryByText('Save')).toBeNull()
    expect(screen.queryByText('Test connection')).toBeNull()
    expect(screen.queryByText('Replace token')).toBeNull()
    expect(screen.queryByText('Remove token')).toBeNull()
    expect(screen.getByText(/You need the QA Lead role or higher/)).toBeTruthy()
  })
})
