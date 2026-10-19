/**
 * UX redesign P5 item 2: Team channels (US-7.3) out of Ownership, on their
 * own page at `/settings/team-channels`. The behaviour moved with them: teams
 * from the ownership rules plus any team that already has a channel, Save
 * upserts the row's buffer, Remove deletes, an empty target is refused, and
 * each failed load says so. New here: the row says what the dispatcher does
 * with the team's alerts (`load_team_channels` reads active channels only).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TeamChannelsPage from './TeamChannelsPage'
import type { OwnershipRule, TeamChannel } from '@/services/ownershipService'

const state = vi.hoisted(() => ({
  projectId: 'proj-1' as string,
  rules: [] as unknown[],
  rulesError: false,
  channels: [] as unknown[],
  channelsError: false,
  refreshChannels: vi.fn(),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: { activeProjectId: string; projects: unknown[]; setActiveProject: () => void }) => unknown) =>
    selector({ activeProjectId: state.projectId, projects: [], setActiveProject: () => {} })),
}))
vi.mock('@/hooks/useOwnershipRules', () => ({
  useOwnershipRules: () => ({ rules: state.rules, isLoading: false, isError: state.rulesError, refresh: vi.fn() }),
}))
vi.mock('@/hooks/useTeamChannels', () => ({
  useTeamChannels: () => ({ channels: state.channels, isLoading: false, isError: state.channelsError, refresh: state.refreshChannels }),
}))
const service = vi.hoisted(() => ({ upsertTeamChannel: vi.fn(), deleteTeamChannel: vi.fn() }))
vi.mock('@/services/ownershipService', () => ({
  upsertTeamChannel: service.upsertTeamChannel,
  deleteTeamChannel: service.deleteTeamChannel,
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

function rule(team: string): OwnershipRule {
  return {
    id: `rule-${team}`,
    project_id: 'proj-1',
    match_type: 'suite_name',
    match_pattern: `${team.toLowerCase()}-*`,
    service_name: `${team.toLowerCase()}-service`,
    team_name: team,
    team_contact: null,
    priority: 0,
    is_active: true,
    created_by: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: null,
  }
}

function channel(team: string, overrides: Partial<TeamChannel> = {}): TeamChannel {
  return {
    id: `ch-${team}`,
    project_id: 'proj-1',
    team_name: team,
    channel_type: 'slack',
    target: `https://hooks.slack.com/services/${team}`,
    is_active: true,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: null,
    ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <TeamChannelsPage />
    </MemoryRouter>,
  )
}

const row = (team: string) => document.querySelector(`[data-team-row="${team}"]`) as HTMLElement

describe('TeamChannelsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.projectId = 'proj-1'
    state.rules = [rule('Payments'), rule('Identity')]
    state.channels = [channel('Identity'), channel('Legacy', { channel_type: 'teams', is_active: false })]
    state.rulesError = false
    state.channelsError = false
  })

  it('has the compact template header with its help topic (the notifications section of Administration)', () => {
    const { container } = renderPage()
    expect(screen.getByRole('heading', { level: 1, name: 'Team channels' })).toBeInTheDocument()
    const header = container.querySelector('[data-page-header]')
    expect(header).toHaveAttribute('data-compact', 'true')
    const help = header?.querySelector('[data-help-topic]')
    expect(help).toHaveAttribute('data-help-topic', 'administration')
    expect(help).toHaveAttribute('data-help-anchor', 'notifications-and-webhooks')
  })

  it('lists the rules’ teams and the teams that already have a channel, sorted, in one primary table', () => {
    const { container } = renderPage()
    const table = screen.getByRole('table', { name: 'Team channels' })
    expect(container.querySelector('[data-primary]')).toContainElement(table)
    const teams = Array.from(container.querySelectorAll('[data-team-row]'), (r) => r.getAttribute('data-team-row'))
    expect(teams).toEqual(['Identity', 'Legacy', 'Payments'])
    // What the dispatcher does with each team today.
    expect(row('Identity')).toHaveTextContent('Routed to its channel')
    expect(row('Legacy')).toHaveTextContent('Paused')
    expect(row('Payments')).toHaveTextContent('Not routed')
    // Stored values fill the row; only a team with a channel can remove it.
    expect(within(row('Identity')).getByLabelText('Webhook URL or email for Identity')).toHaveValue('https://hooks.slack.com/services/Identity')
    expect(within(row('Legacy')).getByLabelText('Channel type for Legacy')).toHaveValue('teams')
    expect(within(row('Identity')).getByRole('button', { name: 'Remove' })).toBeInTheDocument()
    expect(within(row('Payments')).queryByRole('button', { name: 'Remove' })).toBeNull()
  })

  it('Save sends the row’s edited channel for that team, then refreshes the list', async () => {
    service.upsertTeamChannel.mockResolvedValue(channel('Payments'))
    renderPage()
    fireEvent.change(within(row('Payments')).getByLabelText('Channel type for Payments'), { target: { value: 'email' } })
    fireEvent.change(within(row('Payments')).getByLabelText('Webhook URL or email for Payments'), { target: { value: '  payments@example.com ' } })
    fireEvent.click(within(row('Payments')).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(service.upsertTeamChannel).toHaveBeenCalledWith('proj-1', 'Payments', {
      channel_type: 'email',
      target: 'payments@example.com',
    }))
    expect(state.refreshChannels).toHaveBeenCalled()
    expect(toast.success).toHaveBeenCalledWith('Channel saved for Payments')
  })

  it('refuses an empty target without a request', () => {
    renderPage()
    fireEvent.click(within(row('Payments')).getByRole('button', { name: 'Save' }))
    expect(service.upsertTeamChannel).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith('Enter a webhook URL or email address')
  })

  it('Remove deletes the team’s channel after a confirm', async () => {
    service.deleteTeamChannel.mockResolvedValue(undefined)
    renderPage()
    fireEvent.click(within(row('Identity')).getByRole('button', { name: 'Remove' }))
    // W27b: the question is asked in the row under it; its Remove sends.
    fireEvent.click(within(document.querySelector('[data-inline-confirm]') as HTMLElement).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(service.deleteTeamChannel).toHaveBeenCalledWith('proj-1', 'Identity'))
    expect(state.refreshChannels).toHaveBeenCalled()
  })

  /**
   * W27b PAN-06 (C1): Remove asks in a full-width row right under the team's
   * row, never with the browser's prompt. The row's Remove sends nothing; the
   * strip's Remove is the only sender, once; Cancel and Escape send nothing.
   * The strip row is `data-in-page-row` (never `data-team-row`) and spans every
   * column.
   */
  it('Remove asks in a full-width row under the team; the strip sends one DELETE', async () => {
    const nativeConfirm = vi.spyOn(window, 'confirm')
    service.deleteTeamChannel.mockResolvedValue(undefined)
    renderPage()
    const teamRows = document.querySelectorAll('[data-team-row]').length

    fireEvent.click(within(row('Identity')).getByRole('button', { name: 'Remove' }))
    expect(service.deleteTeamChannel).not.toHaveBeenCalled()
    const asked = document.querySelector('[data-inline-confirm]') as HTMLElement
    expect(asked.querySelector('p')?.textContent).toBe('Remove the notification channel for Identity?')
    expect(asked.querySelector('strong')?.textContent).toBe('Identity')
    const stripRow = row('Identity').nextElementSibling as HTMLElement
    expect(stripRow).toHaveAttribute('data-in-page-row')
    expect(stripRow.hasAttribute('data-team-row')).toBe(false)
    expect(stripRow.contains(asked)).toBe(true)
    expect(stripRow.querySelector('td')).toHaveAttribute('colspan', '4')
    expect(document.querySelectorAll('[data-team-row]')).toHaveLength(teamRows)

    fireEvent.click(within(asked).getByRole('button', { name: 'Remove' }))
    expect(document.querySelector('[data-inline-confirm]')).toBeNull()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Team channel removed'))
    expect(service.deleteTeamChannel).toHaveBeenCalledTimes(1)
    expect(service.deleteTeamChannel).toHaveBeenCalledWith('proj-1', 'Identity')
    expect(state.refreshChannels).toHaveBeenCalledTimes(1)
    expect(nativeConfirm).not.toHaveBeenCalled()
    nativeConfirm.mockRestore()
  })

  it('Remove: Cancel and Escape send nothing; one question per table', () => {
    renderPage()
    const strip = () => document.querySelector('[data-inline-confirm]') as HTMLElement | null
    fireEvent.click(within(row('Identity')).getByRole('button', { name: 'Remove' }))
    fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }))
    expect(strip()).toBeNull()

    fireEvent.click(within(row('Identity')).getByRole('button', { name: 'Remove' }))
    fireEvent.click(within(row('Legacy')).getByRole('button', { name: 'Remove' }))
    expect(document.querySelectorAll('[data-inline-confirm]')).toHaveLength(1)
    expect(row('Legacy').nextElementSibling?.contains(strip())).toBe(true)
    fireEvent.keyDown(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
    expect(strip()).toBeNull()
    expect(service.deleteTeamChannel).not.toHaveBeenCalled()
  })

  it('says when either load failed', () => {
    state.rulesError = true
    state.channelsError = true
    renderPage()
    const alerts = screen.getAllByRole('alert').map((a) => a.textContent)
    expect(alerts).toEqual([
      expect.stringContaining('Could not load the ownership rules'),
      'Could not load team channels',
    ])
  })

  it('with no teams, points at Ownership, where teams come from', () => {
    state.rules = []
    state.channels = []
    renderPage()
    expect(screen.getByRole('link', { name: 'add a rule on Ownership' })).toHaveAttribute('href', '/ownership')
  })

  it('needs one project, as Ownership does', () => {
    state.projectId = '__ALL__'
    renderPage()
    // The picker prompt (ProjectRequiredEmptyState), as every single-project page shows.
    expect(screen.getByText('Select a project')).toBeInTheDocument()
    expect(screen.getByText(/A team's channel is set per project/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })
})
