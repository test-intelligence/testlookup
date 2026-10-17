/**
 * GitHub is configured in ONE place (UX redesign P5 item 3): this page, per
 * project. The Integrations page lost its global GitHub card and links here.
 *
 * Pinned here: the page renders the whole GitHub form (repository, API URL,
 * token), its template header (compact, help topic), the two-column field
 * layout at >= 1280 px (P5 item 4), and that Save sends what was typed.
 * W27b PAN-06 (I1): Remove asks in the page, inside the Repository card under
 * its action row; the strip's Delete is the only sender.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { GitHubIntegrationRead } from '@/services/githubIntegrationService'

const mockGet = vi.fn()
const mockUpsert = vi.fn()
const mockRemove = vi.fn()
vi.mock('@/services/githubIntegrationService', () => ({
  githubIntegrationService: {
    get: (...a: unknown[]) => mockGet(...a),
    upsert: (...a: unknown[]) => mockUpsert(...a),
    test: vi.fn(),
    remove: (...a: unknown[]) => mockRemove(...a),
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
const mockToastSuccess = vi.fn()
vi.mock('react-hot-toast', () => ({ default: { success: (m: string) => mockToastSuccess(m), error: vi.fn() } }))
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

  describe('Remove asks in the Repository card (W27b)', () => {
    const strip = () => document.querySelector('[data-inline-confirm]') as HTMLElement | null

    it('asks under the action row, inside the card; the strip Delete removes, once', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm')
      mockGet.mockResolvedValue(existing())
      mockRemove.mockResolvedValue(undefined)
      renderPage()
      const remove = await screen.findByRole('button', { name: 'Remove' })

      fireEvent.click(remove)
      expect(mockRemove).not.toHaveBeenCalled()
      const asked = strip() as HTMLElement
      expect(asked.querySelector('p')?.textContent).toBe('Delete GitHub integration for this project?')
      // Inside the Repository card, right after the action row (not inside that flex row).
      const actionRow = remove.parentElement as HTMLElement
      expect(actionRow.nextElementSibling).toBe(asked)
      expect(actionRow.contains(asked)).toBe(false)
      expect(screen.getByRole('heading', { name: 'Repository' }).closest('section')?.contains(asked)).toBe(true)

      fireEvent.click(within(asked).getByRole('button', { name: 'Delete' }))
      expect(strip()).toBeNull()
      await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledWith('Integration removed'))
      expect(mockRemove).toHaveBeenCalledTimes(1)
      expect(mockRemove).toHaveBeenCalledWith('proj-1')
      expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull()
      expect(screen.getByLabelText('Repository name')).toHaveValue('')
      expect(confirmSpy).not.toHaveBeenCalled()
      confirmSpy.mockRestore()
    })

    it('Cancel and Escape send nothing', async () => {
      mockGet.mockResolvedValue(existing())
      renderPage()
      fireEvent.click(await screen.findByRole('button', { name: 'Remove' }))
      fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }))
      expect(strip()).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
      fireEvent.keyDown(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
      expect(strip()).toBeNull()
      expect(mockRemove).not.toHaveBeenCalled()
    })
  })
})
