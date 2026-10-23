import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

import IntegrationHealthPage from './IntegrationHealthPage'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'
// W10 INT-04 cases (below).
import { MemoryRouter } from 'react-router-dom'
import { useProjectStore } from '@/store/projectStore'
import { SwrMutateBridge } from '@/components/SwrMutateBridge'
import type { IntegrationStatus } from '@/services/integrationHealthService'
import type { Project } from '@/types/projects'

vi.mock('@/services/integrationHealthService', () => ({
  getAllStatus: vi.fn(),
  getHealthTrends: vi.fn(),
  getProviderHistory: vi.fn(),
  triggerProbe: vi.fn(),
}))

vi.mock('react-hot-toast', () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

// W10 INT-04: the page asks nothing below QA_LEAD. Mutable so a case can render
// a QA engineer; every earlier case reads a QA lead (`management`).
const perms = vi.hoisted(() => ({ canAccessManagement: true }))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: perms.canAccessManagement }),
}))

describe('IntegrationHealthPage', () => {
  it('renders the shared workflow language for provider health', async () => {
    const { getAllStatus, getHealthTrends, getProviderHistory } = await import('@/services/integrationHealthService')

    ;(getAllStatus as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        provider: 'jira',
        status: 'healthy',
        last_checked_at: '2026-04-03T15:00:00Z',
        message: 'Healthy',
        response_ms: 120,
        consecutive_failures: 0,
        last_success_at: '2026-04-03T15:00:00Z',
      },
    ])
    ;(getHealthTrends as ReturnType<typeof vi.fn>).mockResolvedValue([])
    ;(getProviderHistory as ReturnType<typeof vi.fn>).mockResolvedValue([])

    render(<IntegrationHealthPage />)

    expect((await screen.findAllByText(/jira/i)).length).toBeGreaterThan(0)
    expect(screen.getByText(/Current Status/i)).toBeInTheDocument()

    // P2 item 3: the health workflow timeline is a collapsed "Pipeline"
    // disclosure at the bottom of the page; nothing renders until opened.
    const pipeline = screen.getByRole('button', { name: 'Pipeline' })
    expect(pipeline).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText(/Health workflow/i)).toBeNull()
    expect(screen.getByText(/Current Status/i).compareDocumentPosition(pipeline) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(pipeline)
    expect(screen.getByText(/Health workflow/i)).toBeInTheDocument()
  })
})

describe('IntegrationHealthPage — the settings page template (UX redesign P5)', () => {
  it('has the compact header with the route help topic; Probe All Now stays its one action', async () => {
    const { getAllStatus, getHealthTrends, getProviderHistory } = await import('@/services/integrationHealthService')
    ;(getAllStatus as ReturnType<typeof vi.fn>).mockResolvedValue([])
    ;(getHealthTrends as ReturnType<typeof vi.fn>).mockResolvedValue([])
    ;(getProviderHistory as ReturnType<typeof vi.fn>).mockResolvedValue([])

    render(<IntegrationHealthPage />)
    expectTemplateHeader('Integration Health', '/settings/integration-health')
    expect(screen.getByRole('button', { name: /probe all now/i }).closest('[data-page-header]')).not.toBeNull()
  })
})

// W29a UIX-07 / UIX-08: a probe's health is a UI meaning, not a test result.
describe('IntegrationHealthPage — the health colours say what they mean (W29a)', () => {
  const status = (provider: string, s: string) => ({
    provider, status: s, last_checked_at: null, message: null, response_ms: null,
    consecutive_failures: 0, last_success_at: null,
  })

  async function renderWith(statuses: ReturnType<typeof status>[]) {
    const { getAllStatus, getHealthTrends, getProviderHistory } = await import('@/services/integrationHealthService')
    ;(getAllStatus as ReturnType<typeof vi.fn>).mockResolvedValue(statuses)
    ;(getHealthTrends as ReturnType<typeof vi.fn>).mockResolvedValue([])
    ;(getProviderHistory as ReturnType<typeof vi.fn>).mockResolvedValue([])
    render(
      <SWRConfig value={{ provider: () => new Map() }}>
        <IntegrationHealthPage />
      </SWRConfig>,
    )
    await screen.findByText('degraded')
  }

  it('degraded is the warning hue (never skipped-gold); down and auth_error are danger; healthy is success', async () => {
    await renderWith([
      status('jira', 'healthy'),
      status('splunk', 'degraded'),
      status('slack', 'down'),
      status('github', 'auth_error'),
      status('smtp', 'timeout'),
    ])
    const chip = (word: string) => screen.getByText(word)
    expect(chip('degraded').className).toContain('text-[var(--ui-warning-ink)]')
    expect(chip('degraded').className).toContain('bg-[var(--ui-warning-bg)]')
    expect(chip('degraded').className).not.toContain('--status-skipped')
    expect(chip('timeout').className).toContain('--ui-warning')
    expect(chip('down').className).toContain('text-[var(--ui-danger-ink)]')
    expect(chip('auth_error').className).toContain('text-[var(--ui-danger-ink)]')
    expect(chip('healthy').className).toContain('text-[var(--ui-success-ink)]')
    for (const word of ['healthy', 'degraded', 'down', 'auth_error', 'timeout']) {
      expect(chip(word).className).not.toMatch(/--status-/)
    }
  })

  it('Probe All Now is the shared primary button', async () => {
    await renderWith([status('splunk', 'degraded')])
    const probe = screen.getByRole('button', { name: /probe all now/i })
    expect(probe.className).toBe('btn-primary')
  })
})

// ── W10 INT-04: each project's GitHub / GitLab row, Probe now per row, paused webhooks, nothing below QA_LEAD ──

describe('IntegrationHealthPage — per-project rows (W10)', () => {
  const JIRA: IntegrationStatus = {
    provider: 'jira', status: 'healthy', last_checked_at: '2026-10-22T10:00:00Z', message: 'Healthy', response_ms: 120,
    consecutive_failures: 0, last_success_at: '2026-10-22T10:00:00Z', scope_key: '', kind: 'global', probed: true,
  }
  const PAYMENTS_GH: IntegrationStatus = {
    provider: 'github', status: 'healthy', last_checked_at: '2026-10-22T10:05:00Z', message: 'Repository acme/payments reachable',
    response_ms: 88, consecutive_failures: 0, last_success_at: '2026-10-22T10:05:00Z',
    scope_key: 'p-1', kind: 'project', project_id: 'p-1', project_name: 'Payment Service', label: 'Payment Service',
    target: 'ghe.corp.example.com/api/v3', probed: true,
  }
  const GATEWAY_GL: IntegrationStatus = {
    provider: 'gitlab', status: 'unknown', last_checked_at: null, message: 'Not probed yet. Probe now, or wait for the 15-minute check.',
    response_ms: null, consecutive_failures: 0, last_success_at: null,
    scope_key: 'p-2', kind: 'project', project_id: 'p-2', project_name: 'API Gateway', label: 'API Gateway',
    target: 'gitlab.fake.test', probed: false,
  }
  const PAUSED_HOOK: IntegrationStatus = {
    provider: 'webhook', status: 'paused', last_checked_at: '2026-10-22T09:00:00Z', message: 'HTTP 500 receiver error',
    response_ms: null, consecutive_failures: 10, last_success_at: null,
    scope_key: 'wh-1', kind: 'webhook', project_id: 'p-1', project_name: 'Payment Service', label: 'Release bot',
    target: 'hooks.fake.test', probed: true, breaker_threshold: 10,
  }
  const PROJECTS = [
    { id: 'p-1', name: 'Payment Service', slug: 'payments' },
    { id: 'p-2', name: 'API Gateway', slug: 'api-gateway' },
  ] as unknown as Project[]

  async function services() {
    return import('@/services/integrationHealthService')
  }

  async function renderAll(rows: IntegrationStatus[]) {
    const svc = await services()
    // The factory answers by argument: `'all'` is the page's read; anything else the deployment-wide rows.
    ;(svc.getAllStatus as ReturnType<typeof vi.fn>).mockImplementation(async (scope?: unknown) =>
      scope === 'all' ? rows : rows.filter((r) => (r.kind ?? 'global') === 'global'),
    )
    ;(svc.getHealthTrends as ReturnType<typeof vi.fn>).mockResolvedValue([])
    ;(svc.getProviderHistory as ReturnType<typeof vi.fn>).mockResolvedValue([])
    ;(svc.triggerProbe as ReturnType<typeof vi.fn>).mockResolvedValue([])
    return render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <SwrMutateBridge />
        <MemoryRouter>
          <IntegrationHealthPage />
        </MemoryRouter>
      </SWRConfig>,
    )
  }

  beforeEach(async () => {
    perms.canAccessManagement = true
    const svc = await services()
    for (const fn of [svc.getAllStatus, svc.getHealthTrends, svc.getProviderHistory, svc.triggerProbe]) {
      ;(fn as ReturnType<typeof vi.fn>).mockReset()
    }
    useProjectStore.setState({ projects: PROJECTS, activeProjectId: 'all', activeProject: null })
  })

  it('reads ?scope=all once; project rows render with their label and target, an unprobed one as "Not probed yet"', async () => {
    await renderAll([JIRA, PAYMENTS_GH, GATEWAY_GL])
    const gh = await screen.findByRole('region', { name: 'GitHub projects' })
    const svc = await services()
    expect(svc.getAllStatus).toHaveBeenCalledTimes(1)
    expect(svc.getAllStatus).toHaveBeenCalledWith('all')

    const card = within(gh).getByText('GitHub · Payment Service').closest('[data-health-row]') as HTMLElement
    expect(card.getAttribute('data-health-row')).toBe('p-1')
    expect(card.className).toContain('card')
    expect(within(card).getByText('ghe.corp.example.com/api/v3')).toBeInTheDocument()
    expect(within(card).getByText('healthy')).toBeInTheDocument()
    expect(within(gh).getByText('GitHub · Payment Service').getAttribute('title')).toBe('GitHub · Payment Service')

    const gl = screen.getByRole('region', { name: 'GitLab projects' })
    const glCard = within(gl).getByText('GitLab · API Gateway').closest('[data-health-row]') as HTMLElement
    const chip = within(glCard).getByText('Not probed yet')
    expect(chip.getAttribute('data-health-status')).toBe('not_probed')
    expect(within(glCard).queryByText('healthy')).toBeNull()

    // The global cards keep the deployment-wide rows only (a project row is not a second "github" card).
    expect(screen.getByText('jira')).toBeInTheDocument()
    expect(screen.queryAllByText('github')).toHaveLength(0)
    expect(screen.queryAllByText('gitlab')).toHaveLength(0)
  })

  it('Probe now on a project row probes that project only, then re-reads the status', async () => {
    await renderAll([JIRA, PAYMENTS_GH])
    const gh = await screen.findByRole('region', { name: 'GitHub projects' })
    const svc = await services()
    fireEvent.click(within(gh).getByRole('button', { name: 'Probe now' }))
    await waitFor(() => expect(svc.triggerProbe).toHaveBeenCalledTimes(1))
    expect(svc.triggerProbe).toHaveBeenCalledWith('github', 'p-1')
    await waitFor(() => expect(svc.getAllStatus).toHaveBeenCalledTimes(2))
  })

  it('a global card\'s Probe now keeps today\'s call (the provider alone)', async () => {
    await renderAll([JIRA, PAYMENTS_GH])
    await screen.findByRole('region', { name: 'GitHub projects' })
    const svc = await services()
    const jiraCard = screen.getByText('jira').closest('div.rounded-lg') as HTMLElement
    fireEvent.click(within(jiraCard).getByRole('button', { name: 'Probe now' }))
    await waitFor(() => expect(svc.triggerProbe).toHaveBeenCalledTimes(1))
    expect(svc.triggerProbe).toHaveBeenCalledWith('jira')
  })

  it('Open GitHub settings is a link to /settings/github whose click makes the row\'s project the active one', async () => {
    await renderAll([JIRA, PAYMENTS_GH])
    const gh = await screen.findByRole('region', { name: 'GitHub projects' })
    const link = within(gh).getByRole('link', { name: 'Open GitHub settings' })
    expect(link.getAttribute('href')).toBe('/settings/github')
    expect(useProjectStore.getState().activeProjectId).toBe('all')
    fireEvent.click(link)
    expect(useProjectStore.getState().activeProjectId).toBe('p-1')
    expect(useProjectStore.getState().activeProject?.name).toBe('Payment Service')
  })

  it('a row whose project the reader does not have offers no settings link', async () => {
    useProjectStore.setState({ projects: [], activeProjectId: 'all', activeProject: null })
    await renderAll([JIRA, PAYMENTS_GH])
    const gh = await screen.findByRole('region', { name: 'GitHub projects' })
    expect(within(gh).queryByRole('link')).toBeNull()
    expect(within(gh).getByRole('button', { name: 'Probe now' })).toBeInTheDocument()
  })

  it('a paused webhook row: name and project, the count and threshold, its last error, Open Outbound webhooks', async () => {
    await renderAll([JIRA, PAUSED_HOOK])
    const section = await screen.findByRole('region', { name: 'Paused webhooks' })
    expect(within(section).getByText('Webhook Release bot · Payment Service')).toBeInTheDocument()
    expect(within(section).getByText('Paused after 10 consecutive failures (threshold 10)')).toBeInTheDocument()
    expect(within(section).getByText('Last error: HTTP 500 receiver error')).toBeInTheDocument()
    const chip = within(section).getByText('paused')
    expect(chip.className).toContain('--ui-danger-')
    const link = within(section).getByRole('link', { name: 'Open Outbound webhooks' })
    expect(link.getAttribute('href')).toBe('/settings/webhooks')
    fireEvent.click(link)
    expect(useProjectStore.getState().activeProjectId).toBe('p-1')
    expect(within(section).queryByRole('button', { name: 'Probe now' })).toBeNull()
  })

  it('the History select lists the global providers and the project rows; a project row reads its own history', async () => {
    await renderAll([JIRA, PAYMENTS_GH, PAUSED_HOOK])
    await screen.findByRole('region', { name: 'GitHub projects' })
    fireEvent.click(screen.getByRole('button', { name: 'Probe History' }))
    const select = await screen.findByRole('combobox')
    const options = Array.from((select as HTMLSelectElement).options).map((o) => [o.value, o.textContent])
    expect(options).toEqual([
      ['', 'Select provider...'],
      ['jira', 'jira'],
      ['github:p-1', 'GitHub · Payment Service'],
    ])
    const svc = await services()
    fireEvent.change(select, { target: { value: 'github:p-1' } })
    await waitFor(() => expect(svc.getProviderHistory).toHaveBeenCalledWith('github', 7, 'p-1'))
    // The tab shows Loading… while a history loads, so the select is a new element after it.
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveValue('github:p-1'))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'jira' } })
    await waitFor(() => expect(svc.getProviderHistory).toHaveBeenCalledWith('jira', 7))
  })

  it('a QA engineer (not management) sees the role line and the page asks nothing', async () => {
    perms.canAccessManagement = false
    await renderAll([JIRA, PAYMENTS_GH])
    expect(await screen.findByText('Integration health needs the QA lead role or above.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Integration Health' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /probe all now/i })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Current Status' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Pipeline' })).toBeNull()
    // Give SWR a beat: nothing is ever asked.
    await new Promise((r) => setTimeout(r, 20))
    const svc = await services()
    expect(svc.getAllStatus).not.toHaveBeenCalled()
    expect(svc.getHealthTrends).not.toHaveBeenCalled()
    expect(svc.getProviderHistory).not.toHaveBeenCalled()
    expect(svc.triggerProbe).not.toHaveBeenCalled()
  })
})
