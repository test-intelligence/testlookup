/**
 * UX redesign P5 on Ownership: Team channels (US-7.3) left for their own page
 * (`/settings/team-channels`, `TeamChannelsPage.test.tsx`), and Ownership
 * keeps a one-line link there. The page takes the template's compact header,
 * named as the settings sub-nav names it, with its help topic; the rules are
 * its primary content.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import OwnershipEditorPage from './OwnershipEditorPage'

const service = vi.hoisted(() => ({ listTeamChannels: vi.fn() }))
vi.mock('@/services/ownershipService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/ownershipService')>()),
  listTeamChannels: service.listTeamChannels,
}))
vi.mock('@/hooks/useOwnershipRules', () => ({
  useOwnershipRules: () => ({
    rules: [
      {
        id: 'r1', project_id: 'proj-1', match_type: 'suite_name', match_pattern: 'auth-*', service_name: 'auth-service',
        team_name: 'Identity', team_contact: null, priority: 5, is_active: true, created_by: null,
        created_at: '2026-10-01T00:00:00Z', updated_at: null,
      },
    ],
    isLoading: false,
    isError: false,
    refresh: vi.fn(),
  }),
  useCodeownersCoverage: () => ({ coverage: null, refresh: vi.fn() }),
}))
const project = vi.hoisted(() => ({ id: 'proj-1' }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: { activeProjectId: string }) => unknown) => selector({ activeProjectId: project.id })),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

function renderPage() {
  return render(
    <MemoryRouter>
      <OwnershipEditorPage />
    </MemoryRouter>,
  )
}

describe('OwnershipEditorPage after P5', () => {
  it('no longer has the team channels section, and does not ask for team channels', () => {
    project.id = 'proj-1'
    renderPage()
    expect(screen.queryByRole('heading', { name: /Team Notification Channels/i })).toBeNull()
    expect(screen.queryByText(/Teams without a channel fall back/)).toBeNull()
    expect(screen.queryByRole('combobox')).toBeNull() // the per-team channel type selects
    expect(service.listTeamChannels).not.toHaveBeenCalled()
  })

  it('keeps one line linking to Team channels', () => {
    project.id = 'proj-1'
    const { container } = renderPage()
    const line = container.querySelector('[data-team-channels-link]') as HTMLElement
    expect(line).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Team channels' })).toHaveAttribute('href', '/settings/team-channels')
  })

  it('takes the compact template header, named as the sub-nav names it (the old title is gone)', () => {
    project.id = 'proj-1'
    const { container } = renderPage()
    expect(screen.getByRole('heading', { level: 1, name: 'Ownership' })).toBeInTheDocument()
    expect(screen.queryByText('Service Ownership Map')).toBeNull()
    const header = container.querySelector('[data-page-header]')
    expect(header).toHaveAttribute('data-compact', 'true')
    expect(header?.querySelector('[data-help-topic]')).toHaveAttribute('data-help-topic', 'administration')
    // One primary and one secondary action, in the header.
    expect(header).toContainElement(screen.getByRole('button', { name: 'Add Rule' }))
    expect(header).toContainElement(screen.getByRole('button', { name: 'Import CODEOWNERS' }))
  })

  it('the rules are the primary content, and come before the Team channels line', () => {
    project.id = 'proj-1'
    const { container } = renderPage()
    const primary = container.querySelector('[data-primary]') as HTMLElement
    expect(primary).toHaveTextContent('auth-*')
    const line = container.querySelector('[data-team-channels-link]') as HTMLElement
    expect(primary.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('in All Projects the prompt keeps the same header', () => {
    project.id = '__ALL__'
    renderPage()
    expect(screen.getByRole('heading', { level: 1, name: 'Ownership' })).toBeInTheDocument()
    expect(screen.getByText(/Select a project to manage ownership rules/)).toBeInTheDocument()
  })
})
