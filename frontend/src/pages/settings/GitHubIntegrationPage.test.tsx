/**
 * GitHub is configured in ONE place (UX redesign P5 item 3): this page, per
 * project. The Integrations page lost its global GitHub card and links here.
 *
 * Pinned here: the page renders the whole GitHub form (repository, API URL,
 * token), its template header (compact, help topic), the two-column field
 * layout at >= 1280 px (P5 item 4), and that Save sends what was typed.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { GitHubIntegrationRead } from '@/services/githubIntegrationService'

const mockGet = vi.fn()
const mockUpsert = vi.fn()
vi.mock('@/services/githubIntegrationService', () => ({
  githubIntegrationService: {
    get: (...a: unknown[]) => mockGet(...a),
    upsert: (...a: unknown[]) => mockUpsert(...a),
    test: vi.fn(),
    remove: vi.fn(),
  },
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: true }),
}))
vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  const state = { activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Checkout' } }
  return {
    ALL_PROJECTS_ID: actual.ALL_PROJECTS_ID,
    useProjectStore: (sel: (s: typeof state) => unknown) => sel(state),
  }
})
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/components/ui/ExperimentalBadge', () => ({ default: () => null }))

import GitHubIntegrationPage from './GitHubIntegrationPage'

function existing(overrides: Partial<GitHubIntegrationRead> = {}): GitHubIntegrationRead {
  return {
    id: 'gh-1',
    project_id: 'proj-1',
    enabled: true,
    repo_owner: 'acme',
    repo_name: 'web',
    api_base_url: 'https://api.github.com',
    has_pat: true,
    pr_comment_mode: 'failures_only',
    last_posted_at: null,
    last_error: null,
    last_error_at: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/settings/github']}>
      <GitHubIntegrationPage />
    </MemoryRouter>,
  )
}

describe('GitHubIntegrationPage — the one place GitHub is configured', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the repository, API URL and token fields for the active project', async () => {
    mockGet.mockResolvedValue(existing())
    renderPage()

    expect(await screen.findByLabelText('Owner / Organization')).toHaveValue('acme')
    expect(screen.getByLabelText('Repository name')).toHaveValue('web')
    expect(screen.getByLabelText('API base URL')).toHaveValue('https://api.github.com')
    expect(screen.getByPlaceholderText('ghp_...')).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledWith('proj-1')
  })

  it('has the template header: compact, with the integrations help topic', async () => {
    mockGet.mockResolvedValue(existing())
    renderPage()

    const help = await screen.findByRole('button', { name: 'Help: GitHub Integration' })
    expect(help).toHaveAttribute('data-help-topic', 'integrations')
    expect(document.querySelector('[data-page-header]')).toHaveAttribute('data-compact', 'true')
  })

  it('pairs the API URL and the token in a grid that goes two-up at xl', async () => {
    mockGet.mockResolvedValue(existing())
    renderPage()

    const url = await screen.findByLabelText('API base URL')
    const grid = url.closest('[data-github-connection]') as HTMLElement
    expect(grid).not.toBeNull()
    expect(grid.className).toContain('xl:grid-cols-2')
    expect(grid.contains(screen.getByPlaceholderText('ghp_...'))).toBe(true)
  })

  it('saves the typed repository and token for the project', async () => {
    mockGet.mockResolvedValue(existing({ has_pat: false }))
    mockUpsert.mockResolvedValue(existing({ repo_name: 'api' }))
    renderPage()

    fireEvent.change(await screen.findByLabelText('Repository name'), { target: { value: 'api' } })
    fireEvent.change(screen.getByPlaceholderText('ghp_...'), { target: { value: 'ghp_new' } })
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(mockUpsert).toHaveBeenCalledTimes(1))
    expect(mockUpsert).toHaveBeenCalledWith(
      'proj-1',
      expect.objectContaining({ repo_owner: 'acme', repo_name: 'api', pat: 'ghp_new' }),
    )
  })
})
