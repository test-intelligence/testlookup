import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import IntegrationHealthPage from './IntegrationHealthPage'

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

    expect(await screen.findByText(/Health workflow/i)).toBeInTheDocument()
    expect(screen.getAllByText(/jira/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/Current Status/i)).toBeInTheDocument()
  })
})
