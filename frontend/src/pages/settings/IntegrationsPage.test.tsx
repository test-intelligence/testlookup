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
import { act, render, screen, waitFor } from '@testing-library/react'
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
  Link: ({ children }: { children: React.ReactNode }) => createElement('a', null, children),
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
      github_token_set: true,
    }))
    await renderPage()

    await waitFor(() => expect(screen.getAllByText('(set)').length).toBe(4))
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
