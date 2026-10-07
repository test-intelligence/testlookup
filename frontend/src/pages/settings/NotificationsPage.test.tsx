/**
 * UX redesign P5 item 2: `/settings/notifications` is the admin page, "Email
 * (SMTP) & channels" — the mail server and the shared channels, and nothing
 * personal. A person's own channels are My notifications
 * (`MyNotificationsPage.test.tsx`); a team's are Team channels.
 *
 * The shared channels are the deployment's Slack and Teams webhooks
 * (`GET /api/v1/settings/integrations`), which a person's Slack or Teams
 * preference is delivered to when it has no webhook of its own. They are
 * edited on Integrations; this page says whether each one delivers, in the
 * dispatcher's terms (on AND a webhook), and a failed read is said as one.
 *
 * Item 4: the SMTP form is two columns at >= 1280 px, not held to max-w-2xl.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import NotificationsPage from './NotificationsPage'
import type { IntegrationsConfigRead } from '@/services/appSettingsService'

const settings = vi.hoisted(() => ({
  getSmtpConfig: vi.fn(),
  getIntegrationsConfig: vi.fn(),
}))
vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: {
    getSmtpConfig: settings.getSmtpConfig,
    getIntegrationsConfig: settings.getIntegrationsConfig,
    updateSmtpConfig: vi.fn(),
    testSmtpConfig: vi.fn(),
  },
}))
const personal = vi.hoisted(() => ({ preferences: vi.fn(), history: vi.fn() }))
vi.mock('@/hooks/useNotifications', () => ({
  useNotificationPreferences: () => {
    personal.preferences()
    return { data: [], mutate: vi.fn(), isLoading: false }
  },
  useNotificationHistory: () => {
    personal.history()
    return { data: [], mutate: vi.fn() }
  },
  invalidateNotifications: vi.fn(),
}))

const SMTP = {
  enabled: true,
  host: 'smtp.corp.example.com',
  port: 465,
  user: 'mailer@corp.example.com',
  from_address: 'alerts@corp.example.com',
  implicit_tls: true,
  password_set: true,
}

function integrations(overrides: Partial<IntegrationsConfigRead>): IntegrationsConfigRead {
  return {
    jira_enabled: false, jira_domain: null, jira_email: null, jira_token_set: false, jira_default_project_key: 'QA',
    splunk_enabled: false, splunk_base_url: null, splunk_token_set: false,
    ocp_enabled: false, ocp_api_url: null, ocp_token_set: false, ocp_default_namespace: 'default',
    slack_enabled: false, slack_webhook_url: null, slack_webhook_set: false, slack_default_channel: '#qa-alerts',
    teams_enabled: false, teams_webhook_url: null, teams_webhook_set: false,
    github_repo: null, github_token_set: false,
    ...overrides,
  }
}

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter>
        <NotificationsPage />
      </MemoryRouter>
    </SWRConfig>,
  )
}

const sharedRow = (id: string) => document.querySelector(`[data-shared-channel="${id}"]`) as HTMLElement

describe('NotificationsPage — Email (SMTP) & channels', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    settings.getSmtpConfig.mockResolvedValue(SMTP)
    settings.getIntegrationsConfig.mockResolvedValue(integrations({}))
  })

  it('is named for what it holds, in the compact template header (the old name is gone)', () => {
    const { container } = renderPage()
    expect(screen.getByRole('heading', { level: 1, name: 'Email (SMTP) & channels' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Notification Settings' })).toBeNull()
    const header = container.querySelector('[data-page-header]')
    expect(header).toHaveAttribute('data-compact', 'true')
    expect(header?.querySelector('[data-help-topic]')).toHaveAttribute('data-help-topic', 'administration')
  })

  it('renders none of the personal preferences: no channel cards, no events, no history, no preference request', async () => {
    const { container } = renderPage()
    await screen.findByDisplayValue('smtp.corp.example.com')
    expect(container.querySelector('[data-channel-card]')).toBeNull()
    expect(screen.queryByText('Notify me when')).toBeNull()
    expect(screen.queryByText(/View notification history/)).toBeNull()
    expect(screen.queryByText('Global defaults')).toBeNull()
    expect(personal.preferences).not.toHaveBeenCalled()
    expect(personal.history).not.toHaveBeenCalled()
    // ...and says where they went.
    expect(screen.getByRole('link', { name: 'My notifications' })).toHaveAttribute('href', '/settings/my-notifications')
  })

  it('the SMTP form is two columns at >= 1280 px, and the page is not held to max-w-2xl', async () => {
    const { container } = renderPage()
    await screen.findByDisplayValue('smtp.corp.example.com')
    expect(container.querySelector('.max-w-2xl')).toBeNull()
    const smtp = screen.getByRole('region', { name: 'Email server (SMTP)' })
    const grid = smtp.querySelector('[data-form-grid]') as HTMLElement
    expect(grid.className).toContain('xl:grid-cols-2')
    // Server and sender, then the credentials: all four fields in the grid.
    for (const label of [/^SMTP Host/, /^Port/, /^From Address/, /^Username/, /^Password/]) {
      expect(within(grid).getByLabelText(label)).toBeInTheDocument()
    }
  })

  it('the shared channels say whether each delivers, as the dispatcher decides it (on AND a webhook)', async () => {
    settings.getIntegrationsConfig.mockResolvedValue(
      integrations({ slack_enabled: true, slack_webhook_set: true, teams_enabled: true, teams_webhook_set: false }),
    )
    renderPage()
    await waitFor(() => expect(sharedRow('slack')).toBeTruthy())
    expect(sharedRow('slack')).toHaveTextContent('Delivering')
    expect(sharedRow('teams')).toHaveTextContent('No webhook')
    expect(sharedRow('teams')).toHaveTextContent('nothing is delivered')
    // Edited in one place: Integrations.
    expect(screen.getByRole('link', { name: /Edit in Integrations/ })).toHaveAttribute('href', '/settings/integrations')
  })

  it('off is off, with or without a webhook, and the Slack default channel (unused by delivery) is not shown', async () => {
    settings.getIntegrationsConfig.mockResolvedValue(integrations({ slack_webhook_set: true }))
    renderPage()
    await waitFor(() => expect(sharedRow('slack')).toBeTruthy())
    expect(sharedRow('slack')).toHaveTextContent('Off')
    expect(sharedRow('slack')).toHaveTextContent('A webhook is set; delivery is switched off')
    expect(sharedRow('teams')).toHaveTextContent('Off, and no webhook is set')
    expect(screen.queryByText(/#qa-alerts/)).toBeNull()
  })

  it('a failed read of the shared channels is said as one, never drawn as "Off"', async () => {
    settings.getIntegrationsConfig.mockRejectedValue({ response: { status: 500, data: {} } })
    renderPage()
    const alert = await screen.findByTestId('shared-channels-unavailable')
    expect(alert).toHaveAttribute('role', 'alert')
    expect(sharedRow('slack')).toBeNull()
    expect(sharedRow('teams')).toBeNull()
    // The team channels link does not depend on that read.
    expect(within(sharedRow('team-channels')).getByRole('link', { name: 'Team channels' })).toHaveAttribute(
      'href',
      '/settings/team-channels',
    )
  })
})
