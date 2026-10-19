import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

import IntegrationHealthPage from './IntegrationHealthPage'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'

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
