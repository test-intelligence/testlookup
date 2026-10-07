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

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: true }),
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
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
