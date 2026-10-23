/**
 * Regression tests for the Integrations settings page.
 *
 * Primary contract under test (theme-token migration): the "(set)" credential
 * indicators — shown next to each secret field once a token is stored — must
 * use the per-theme success token `--status-passed`, NOT a raw Tailwind palette
 * class (`text-emerald-400`), which is illegible in the light theme. One
 * indicator renders per provider whose token is stored.
 *
 * The service and permissions hook are mocked so the page renders hermetically
 * without a network layer.
 */
import { createElement } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { IntegrationsConfigRead } from '@/services/appSettingsService'

// ── mocks ───────────────────────────────────────────────────────────────────

const mockGet = vi.fn()
const mockUpdate = vi.fn()

vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: {
    getIntegrationsConfig: (...a: unknown[]) => mockGet(...a),
    updateIntegrationsConfig: (...a: unknown[]) => mockUpdate(...a),
  },
}))

// Mutable so the W10 cases can render a QA lead; every earlier case reads ADMIN.
const perms = vi.hoisted(() => ({ isAdmin: true }))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: perms.isAdmin }),
}))

const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({
  default: toastMock,
}))

vi.mock('@/components/ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => createElement('h1', null, title),
}))
vi.mock('@/components/ui/LoadingSpinner', () => ({
  default: () => createElement('div', null, 'loading'),
}))
vi.mock('react-router-dom', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => createElement('a', { href: to }, children),
}))

function baseConfig(overrides: Partial<IntegrationsConfigRead> = {}): IntegrationsConfigRead {
  return {
    jira_enabled: false,
    jira_domain: null,
    jira_email: null,
    jira_token_set: false,
    jira_default_project_key: '',
    splunk_enabled: false,
    splunk_base_url: null,
    splunk_token_set: false,
    ocp_enabled: false,
    ocp_api_url: null,
    ocp_token_set: false,
    ocp_default_namespace: '',
    slack_enabled: false,
    slack_webhook_url: null,
    slack_webhook_set: false,
    slack_default_channel: '',
    teams_enabled: false,
    teams_webhook_url: null,
    teams_webhook_set: false,
    github_repo: null,
    github_token_set: false,
    ...overrides,
  }
}

async function renderPage() {
  const { default: IntegrationsPage } = await import('./IntegrationsPage')
  return act(async () => {
    render(createElement(IntegrationsPage))
  })
}

describe('IntegrationsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders a "(set)" indicator for each provider whose token is stored', async () => {
    mockGet.mockResolvedValue(baseConfig({
      jira_token_set: true,
      splunk_token_set: true,
      ocp_token_set: true,
      // GitHub is no longer configured here (P5 item 3), so its stored token
      // has no indicator on this page: three, not four.
      github_token_set: true,
    }))
    await renderPage()

    await waitFor(() => expect(screen.getAllByText('(set)').length).toBe(3))
  })

  it('uses the --status-passed theme token (not a raw palette green) for "(set)"', async () => {
    mockGet.mockResolvedValue(baseConfig({ jira_token_set: true }))
    await renderPage()

    const badge = await screen.findByText('(set)')
    // Migrated to the per-theme success token so it stays legible in light mode.
    expect(badge.className).toContain('text-[var(--status-passed)]')
    expect(badge.className).not.toContain('emerald')
  })

  it('omits the "(set)" indicator when no token is stored', async () => {
    mockGet.mockResolvedValue(baseConfig())
    await renderPage()

    await waitFor(() => expect(screen.getByText('Jira')).toBeTruthy())
    expect(screen.queryByText('(set)')).toBeNull()
  })
})

// UX redesign P5 item 3: GitHub is configured in one place, /settings/github.
describe('IntegrationsPage — GitHub lives at /settings/github', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders no GitHub card: no GitHub heading, repository field or token field', async () => {
    mockGet.mockResolvedValue(baseConfig({ github_repo: 'acme/web', github_token_set: true }))
    await renderPage()
    await waitFor(() => expect(screen.getByText('Jira')).toBeTruthy())

    expect(screen.queryByRole('heading', { name: 'GitHub' })).toBeNull()
    expect(screen.queryByLabelText('Repository')).toBeNull()
    expect(screen.queryByPlaceholderText('ghp_...')).toBeNull()
    expect(screen.queryByDisplayValue('acme/web')).toBeNull()
    expect(document.getElementById('integration-secret-3')).toBeNull()
  })

  it('points to the one place GitHub is configured', async () => {
    mockGet.mockResolvedValue(baseConfig())
    await renderPage()

    const link = await screen.findByRole('link', { name: 'GitHub settings' })
    expect(link.getAttribute('href')).toBe('/settings/github')
  })

  it('saves without a github_repo, leaving the stored value to the server-side merge', async () => {
    mockGet.mockResolvedValue(baseConfig({ github_repo: 'acme/web' }))
    mockUpdate.mockResolvedValue(baseConfig({ github_repo: 'acme/web' }))
    await renderPage()

    // W10 INT-01: an untouched Save sends nothing (§0.3), so change one field first.
    fireEvent.click((await screen.findAllByLabelText('Enabled'))[0])
    fireEvent.click(await screen.findByRole('button', { name: /save integrations/i }))
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1))
    const payload = mockUpdate.mock.calls[0][0] as Record<string, unknown>
    expect(payload).not.toHaveProperty('github_repo')
    expect(payload).not.toHaveProperty('github_token')
    expect(payload).toHaveProperty('jira_enabled')
  })
})

// UX redesign P5 item 4: two columns of cards at >= 1280 px, not one max-w-2xl column.
describe('IntegrationsPage — two-column form', () => {
  it('lays the cards out in a grid that goes two-up at xl', async () => {
    mockGet.mockResolvedValue(baseConfig())
    await renderPage()
    await waitFor(() => expect(screen.getByText('Jira')).toBeTruthy())

    const form = document.querySelector('[data-integrations-form]') as HTMLElement
    expect(form).not.toBeNull()
    expect(form.className).toContain('xl:grid-cols-2')
    expect(form.className).not.toMatch(/max-w-/)
  })
})

// ── W10 INT-01 / INT-02: only the changed fields, inline 422s, the environment hints, no Default Channel ──

function err422(detail: unknown) {
  return Object.assign(new Error('Request failed with status code 422'), { response: { status: 422, data: { detail } } })
}

describe('IntegrationsPage — W10 saves only what changed', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    perms.isAdmin = true
  })

  it('Save sends only the changed field, with suppressToast', async () => {
    mockGet.mockResolvedValue(baseConfig({ splunk_base_url: 'https://splunk.env.example:8089', environment_fields: ['splunk_base_url'] }))
    mockUpdate.mockResolvedValue(baseConfig({ jira_domain: 'acme.atlassian.net' }))
    await renderPage()

    fireEvent.change(await screen.findByLabelText('Domain'), { target: { value: 'acme.atlassian.net' } })
    fireEvent.click(screen.getByRole('button', { name: /save integrations/i }))
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1))
    expect(mockUpdate).toHaveBeenCalledWith({ jira_domain: 'acme.atlassian.net' }, { suppressToast: true })
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Integrations saved'))
  })

  it('an untouched Save sends nothing and says so in the page', async () => {
    mockGet.mockResolvedValue(baseConfig())
    await renderPage()

    fireEvent.click(await screen.findByRole('button', { name: /save integrations/i }))
    expect(await screen.findByText('No changes to save')).toBeInTheDocument()
    expect(mockUpdate).not.toHaveBeenCalled()
    expect(toastMock.error).not.toHaveBeenCalled()
  })

  it('a 422 shows "Target host is not allowed" under Base URL, keeps the typed value, and raises no toast', async () => {
    mockGet.mockResolvedValue(baseConfig())
    mockUpdate.mockRejectedValue(err422([{ loc: ['body', 'splunk_base_url'], msg: 'Target host is not allowed', type: 'target_not_allowed' }]))
    await renderPage()

    const base = await screen.findByLabelText('Base URL')
    fireEvent.change(base, { target: { value: 'http://127.0.0.1:6379' } })
    fireEvent.click(screen.getByRole('button', { name: /save integrations/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Target host is not allowed')
    expect(alert.getAttribute('data-field-error')).toBe('splunk_base_url')
    expect(base.getAttribute('aria-describedby')).toContain(alert.id)
    expect(base).toHaveValue('http://127.0.0.1:6379')
    expect(mockUpdate).toHaveBeenCalledWith({ splunk_base_url: 'http://127.0.0.1:6379' }, { suppressToast: true })
    expect(toastMock.error).not.toHaveBeenCalled()
    expect(toastMock.success).not.toHaveBeenCalled()

    // Editing the field clears its sentence.
    fireEvent.change(base, { target: { value: 'https://splunk.corp.example:8089' } })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('any other failure is one toast, "Could not save integrations."', async () => {
    mockGet.mockResolvedValue(baseConfig())
    mockUpdate.mockRejectedValue(Object.assign(new Error('boom'), { response: { status: 500, data: { detail: 'boom' } } }))
    await renderPage()

    fireEvent.click((await screen.findAllByLabelText('Enabled'))[1])
    fireEvent.click(screen.getByRole('button', { name: /save integrations/i }))
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1))
    expect(toastMock.error).toHaveBeenCalledWith('Could not save integrations.')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('environment_fields: "From the deployment environment" under the field, "(set in the environment)" beside the token', async () => {
    mockGet.mockResolvedValue(baseConfig({
      splunk_base_url: 'https://splunk.env.example:8089',
      splunk_token_set: true,
      jira_token_set: true,
      environment_fields: ['splunk_base_url', 'splunk_api_token'],
    }))
    await renderPage()

    const base = await screen.findByLabelText('Base URL')
    const hint = screen.getByText('From the deployment environment')
    expect(hint.getAttribute('data-env-hint')).toBe('splunk_base_url')
    expect(base.getAttribute('aria-describedby')).toBe(hint.id)
    expect(screen.getAllByText('From the deployment environment')).toHaveLength(1)
    expect(document.querySelector('[data-secret-state="splunk_api_token"]')?.textContent).toBe('(set in the environment)')
    expect(document.querySelector('[data-secret-state="jira_api_token"]')?.textContent).toBe('(set)')

    // Typing over the environment's value drops the hint.
    fireEvent.change(base, { target: { value: 'https://splunk.corp.example:8089' } })
    expect(screen.queryByText('From the deployment environment')).toBeNull()
  })

  it('a QA lead (not admin) sees every field disabled and no Save', async () => {
    perms.isAdmin = false
    mockGet.mockResolvedValue(baseConfig())
    await renderPage()

    await screen.findByLabelText('Base URL')
    expect(screen.queryByRole('button', { name: /save integrations/i })).toBeNull()
    const form = document.querySelector('[data-integrations-form]') as HTMLElement
    const inputs = Array.from(form.querySelectorAll('input'))
    expect(inputs.length).toBeGreaterThan(0)
    for (const input of inputs) expect(input).toBeDisabled()
    expect(mockUpdate).not.toHaveBeenCalled()
  })
})

describe('IntegrationsPage — W10 INT-02: no Slack Default Channel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    perms.isAdmin = true
  })

  it('has no Default Channel field; the help line under the webhook URL links Team channels', async () => {
    mockGet.mockResolvedValue(baseConfig({ slack_default_channel: '#qa-alerts' }))
    await renderPage()
    await waitFor(() => expect(screen.getByText('Slack')).toBeTruthy())

    expect(screen.queryByText('Default Channel')).toBeNull()
    expect(screen.queryByLabelText('Default Channel')).toBeNull()
    expect(screen.queryByDisplayValue('#qa-alerts')).toBeNull()

    const help = document.querySelector('[data-slack-channel-help]') as HTMLElement
    expect(help).not.toBeNull()
    expect(help.textContent).toBe('The channel is fixed by the incoming webhook. Route other channels per team in Team channels.')
    expect(help.className).toContain('text-xs')
    const link = screen.getByRole('link', { name: 'Team channels' })
    expect(link.getAttribute('href')).toBe('/settings/team-channels')
    expect(help.contains(link)).toBe(true)

    // Under the Slack card's webhook URL, which it describes; the grid holds that one field.
    const webhook = screen.getAllByLabelText(/^Webhook URL/)[0]
    expect(webhook.getAttribute('aria-describedby')).toContain(help.id)
    const grid = help.closest('[class~="grid"]') as HTMLElement
    expect(grid.className).toBe('grid grid-cols-2 gap-3 xl:grid-cols-1')
    expect(grid.children).toHaveLength(1)
  })

  it('a save body never carries slack_default_channel', async () => {
    mockGet.mockResolvedValue(baseConfig({ slack_default_channel: '#qa-alerts' }))
    mockUpdate.mockResolvedValue(baseConfig())
    await renderPage()

    fireEvent.click((await screen.findAllByLabelText('Enabled'))[3])
    fireEvent.change(screen.getAllByLabelText(/^Webhook URL/)[0], { target: { value: 'https://hooks.slack.com/services/T/B/X' } })
    fireEvent.click(screen.getByRole('button', { name: /save integrations/i }))
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1))
    const body = mockUpdate.mock.calls[0][0] as Record<string, unknown>
    expect(body).toEqual({ slack_enabled: true, slack_webhook_url: 'https://hooks.slack.com/services/T/B/X' })
    expect(body).not.toHaveProperty('slack_default_channel')
  })
})
