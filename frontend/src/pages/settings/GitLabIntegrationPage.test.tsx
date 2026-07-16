/**
 * Hermetic tests for the GitLab integration settings page (PMF Epic 3 UI).
 *
 * Covers the load-bearing contract behaviours:
 *   - PUT payload shape: token sent ONLY when the user entered one; base_url,
 *     project_path, mr_comment_mode, commit_status_enabled carried verbatim.
 *   - Test-connection surfaces ok/detail + resolved project id.
 *   - has_token UX: "Token set" + "Replace token" when true; input when false.
 *   - MR comment mode select updates the payload.
 *
 * The service module and project store are mocked; SWR data is mocked so the
 * page renders synchronously without a network layer.
 */
import { createElement } from 'react'
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

import type { GitLabConfig } from '@/types/gitlab'

// ── mocks ───────────────────────────────────────────────────────────────────

const mockUpdate = vi.fn()
const mockTest = vi.fn()

vi.mock('@/services/gitlabIntegrationService', () => ({
  gitlabIntegrationService: {
    get: vi.fn(),
    update: (...a: unknown[]) => mockUpdate(...a),
    test: (...a: unknown[]) => mockTest(...a),
  },
}))

// The hook is mocked so we control the "config" the page seeds its form from,
// and so no real fetch runs.
let mockConfig: GitLabConfig | undefined
vi.mock('@/hooks/useGitlabIntegration', () => ({
  useGitlabIntegration: () => ({ data: mockConfig, isLoading: false, mutate: vi.fn() }),
  testGitlabConnection: (...a: unknown[]) => mockTest(...a),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: true }),
}))

// Project store: a specific (non-All-Projects) project selected.
vi.mock('@/store/projectStore', async () => {
  const ALL_PROJECTS_ID = '__ALL_PROJECTS__'
  const state = {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Checkout' },
  }
  const useProjectStore = (sel: (s: typeof state) => unknown) => sel(state)
  return { useProjectStore, ALL_PROJECTS_ID }
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

async function renderPage() {
  const { default: GitLabIntegrationPage } = await import('./GitLabIntegrationPage')
  return render(
    createElement(
      SWRConfig,
      { value: { provider: () => new Map(), dedupingInterval: 0 } },
      createElement(GitLabIntegrationPage),
    ),
  )
}

describe('GitLabIntegrationPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUpdate.mockResolvedValue(baseConfig())
    mockTest.mockResolvedValue({ ok: true, detail: 'Reached GitLab', project_id_resolved: '42' })
  })
  afterEach(() => {
    mockConfig = undefined
  })

  it('omits token from the PUT payload when the user did not enter one', async () => {
    mockConfig = baseConfig({ has_token: true, project_path: 'acme/webapp' })
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
    mockConfig = baseConfig({ has_token: false })
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
    mockConfig = baseConfig({ has_token: true })
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
    mockConfig = baseConfig({ has_token: false })
    await renderPage()

    expect(await screen.findByPlaceholderText('glpat-...')).toBeTruthy()
    expect(screen.queryByText('Token set')).toBeNull()
    expect(screen.queryByText('Replace token')).toBeNull()
  })

  it('reveals the token input via "Replace token" when a token is stored', async () => {
    mockConfig = baseConfig({ has_token: true })
    await renderPage()

    await waitFor(() => expect(screen.getByText('Token set')).toBeTruthy())
    expect(screen.queryByPlaceholderText('glpat-...')).toBeNull()

    fireEvent.click(screen.getByText('Replace token'))
    expect(screen.getByPlaceholderText('glpat-...')).toBeTruthy()
  })

  it('surfaces test-connection ok/detail + resolved project id', async () => {
    mockConfig = baseConfig({ has_token: true })
    await renderPage()

    await act(async () => {
      fireEvent.click(screen.getByText('Test connection'))
    })

    expect(mockTest).toHaveBeenCalledWith('proj-1')
    await waitFor(() => expect(screen.getByText(/Reached GitLab/)).toBeTruthy())
    expect(screen.getByText(/resolved project id: 42/)).toBeTruthy()
  })

  it('surfaces a failed connection test detail', async () => {
    mockConfig = baseConfig({ has_token: true })
    mockTest.mockResolvedValue({ ok: false, detail: '401 Unauthorized', project_id_resolved: null })
    await renderPage()

    await act(async () => {
      fireEvent.click(screen.getByText('Test connection'))
    })

    await waitFor(() => expect(screen.getByText(/401 Unauthorized/)).toBeTruthy())
    expect(screen.queryByText(/resolved project id/)).toBeNull()
  })

  it('renders an integration-health warning when last_error is present', async () => {
    mockConfig = baseConfig({
      has_token: true,
      last_error: 'commit status POST failed: 403',
      last_error_at: '2026-07-15T10:00:00Z',
    })
    await renderPage()

    await waitFor(() =>
      expect(screen.getByText(/commit status POST failed: 403/)).toBeTruthy(),
    )
  })
})
