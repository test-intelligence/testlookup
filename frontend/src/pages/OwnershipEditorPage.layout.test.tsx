/**
 * UX redesign P5 on Ownership: Team channels (US-7.3) left for their own page
 * (`/settings/team-channels`, `TeamChannelsPage.test.tsx`), and Ownership
 * keeps a one-line link there. The page takes the template's compact header,
 * named as the settings sub-nav names it, with its help topic; the rules are
 * its primary content.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import OwnershipEditorPage from './OwnershipEditorPage'

const service = vi.hoisted(() => ({
  listTeamChannels: vi.fn(),
  deleteOwnershipRule: vi.fn(),
  importCodeowners: vi.fn(),
  refreshRules: vi.fn(),
  refreshCoverage: vi.fn(),
}))
vi.mock('@/services/ownershipService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/ownershipService')>()),
  listTeamChannels: service.listTeamChannels,
  deleteOwnershipRule: service.deleteOwnershipRule,
  importCodeowners: service.importCodeowners,
}))
vi.mock('@/hooks/useOwnershipRules', () => ({
  useOwnershipRules: () => ({
    rules: [
      {
        id: 'r1', project_id: 'proj-1', match_type: 'suite_name', match_pattern: 'auth-*', service_name: 'auth-service',
        team_name: 'Identity', team_contact: null, priority: 5, is_active: true, created_by: null,
        created_at: '2026-10-01T00:00:00Z', updated_at: null,
      },
      {
        id: 'r2', project_id: 'proj-1', match_type: 'suite_name', match_pattern: 'pay-*', service_name: 'pay-service',
        team_name: 'Payments', team_contact: null, priority: 4, is_active: true, created_by: null,
        created_at: '2026-10-01T00:00:00Z', updated_at: null,
      },
    ],
    isLoading: false,
    isError: false,
    refresh: service.refreshRules,
  }),
  useCodeownersCoverage: () => ({ coverage: null, refresh: service.refreshCoverage }),
}))
// FAIL-08: the Java source roots section sits between the rules and the Team
// channels line; it reads its own list, empty here.
vi.mock('@/hooks/useSourceRoots', () => ({
  useSourceRoots: () => ({
    roots: [], maxRoots: 20, isLoading: false, isError: false,
    refresh: vi.fn(), save: vi.fn(), preview: vi.fn(),
  }),
}))
const project = vi.hoisted(() => ({ id: 'proj-1' }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: { activeProjectId: string; projects: unknown[]; setActiveProject: () => void }) => unknown) =>
    selector({ activeProjectId: project.id, projects: [], setActiveProject: () => {} })),
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
    // The picker prompt (ProjectRequiredEmptyState), as every single-project page shows.
    expect(screen.getByText('Select a project')).toBeInTheDocument()
    expect(screen.getByText(/one project at a time/)).toBeInTheDocument()
  })
})

// ── W27b PAN-05: the rule's Delete and Import CODEOWNERS are in the page ───

const ruleRow = (pattern: string) => screen.getByText(pattern).closest('div.grid-cols-7') as HTMLElement

describe('OwnershipEditorPage in the page (W27b)', () => {
  it('a rule\'s Delete asks in a strip right under that row; the trigger sends nothing, the strip\'s Delete sends once; no native confirm', async () => {
    project.id = 'proj-1'
    const confirmSpy = vi.spyOn(window, 'confirm')
    service.deleteOwnershipRule.mockReset()
    service.deleteOwnershipRule.mockResolvedValue(undefined)
    service.refreshRules.mockClear()
    service.refreshCoverage.mockClear()
    renderPage()

    fireEvent.click(within(ruleRow('pay-*')).getByRole('button', { name: 'Delete' }))
    expect(service.deleteOwnershipRule, 'the trigger sends nothing').not.toHaveBeenCalled()
    const strip = document.querySelector('[data-inline-confirm]') as HTMLElement
    const holder = strip.closest('[data-in-page-row]') as HTMLElement
    expect(ruleRow('pay-*').nextElementSibling, 'the strip is right under the rule\'s row').toBe(holder)
    expect(holder.classList.contains('grid-cols-7'), 'the strip is outside the row grid').toBe(false)
    expect(strip.querySelector('p')?.textContent).toBe('Delete this ownership rule?')

    fireEvent.click(within(strip).getByRole('button', { name: 'Cancel' }))
    expect(document.querySelector('[data-inline-confirm]')).toBeNull()
    expect(service.deleteOwnershipRule).not.toHaveBeenCalled()

    fireEvent.click(within(ruleRow('pay-*')).getByRole('button', { name: 'Delete' }))
    fireEvent.click(document.querySelector('[data-inline-confirm-action]') as HTMLElement)
    await waitFor(() => expect(service.deleteOwnershipRule).toHaveBeenCalledTimes(1))
    expect(service.deleteOwnershipRule).toHaveBeenCalledWith('proj-1', 'r2')
    // The rules AND the coverage badge are re-read (pairedInvalidation's pair).
    await waitFor(() => expect(service.refreshCoverage).toHaveBeenCalledTimes(1))
    expect(service.refreshRules).toHaveBeenCalledTimes(1)
    expect(confirmSpy).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('Import CODEOWNERS opens right under the page header, named by its heading, with no X (no dialog)', () => {
    project.id = 'proj-1'
    const { container } = renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Import CODEOWNERS' }))
    const section = screen.getByRole('region', { name: 'Import CODEOWNERS' })
    expect(container.querySelector('[data-page-header]')?.nextElementSibling).toBe(section)
    expect(section.querySelector('h2')).toHaveAttribute('id', 'import-codeowners-title')
    expect(within(section).getByRole('radiogroup', { name: 'Import source' })).toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: 'Close' })).toBeNull()
    expect(within(section).getByRole('textbox'), 'focus moves to the text field').toHaveFocus()
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.click(within(section).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Import CODEOWNERS' })).toBeNull()
  })
})
