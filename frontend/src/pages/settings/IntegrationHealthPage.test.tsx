import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

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
