/**
 * W23a FAIL-11 (contract §4.4 G-D): the Overview Insights card. Real
 * `useInsights` and real SWR; only the service is faked, so a call below is a
 * request the backend would see.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { insight, insightList } from '@/test/insightCadenceMocks'

vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isQaEngineer: true }) }))
const service = vi.hoisted(() => ({ listInsights: vi.fn(), dismissInsight: vi.fn() }))
vi.mock('@/services/insightsService', () => service)

import InsightsCard from './InsightsCard'

function renderCard(projectId: string | null = 'p1') {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter>
        <InsightsCard projectId={projectId} projectName="Checkout" />
      </MemoryRouter>
    </SWRConfig>,
  )
}
const rows = () => document.querySelectorAll('[data-insight-row]')

describe('InsightsCard', () => {
  beforeEach(() => {
    service.listInsights.mockReset()
    service.listInsights.mockResolvedValue(insightList([insight()]))
  })

  it('All Projects: no card and NO request', async () => {
    const { container } = renderCard(null)
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(container.querySelector('[data-insights-card]')).toBeNull()
    expect(service.listInsights).not.toHaveBeenCalled()
  })

  it('one read on load (7 d, no dismissed rows); the header, the subtitle and Open in Inbox', async () => {
    renderCard()
    await waitFor(() => expect(rows()).toHaveLength(1))
    expect(service.listInsights).toHaveBeenCalledTimes(1)
    expect(service.listInsights).toHaveBeenCalledWith('p1', '7d', false, undefined)
    expect(screen.getByRole('region', { name: 'Insights' })).toBeInTheDocument()
    expect(screen.getByText('Patterns across runs · checked every hour')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open in Inbox' })).toHaveAttribute('href', '/my-failures?tab=insights')
    expect(screen.getByRole('button', { name: '7 d' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('at most 5 rows, then "Showing 5 of 12" from the server total', async () => {
    const items = Array.from({ length: 6 }, (_, i) => insight({ id: `row-${i}` }))
    service.listInsights.mockResolvedValue({ ...insightList(items), total: 12 })
    renderCard()
    await waitFor(() => expect(rows()).toHaveLength(5))
    const showing = document.querySelector('[data-insights-showing]')
    expect(showing).toHaveTextContent('Showing 5 of 12 · Show all in the Inbox')
    expect(screen.getByRole('link', { name: 'Show all in the Inbox' })).toHaveAttribute('href', '/my-failures?tab=insights')
  })

  it('no "Showing" line when every row is on the card', async () => {
    service.listInsights.mockResolvedValue(insightList([insight(), insight()]))
    renderCard()
    await waitFor(() => expect(rows()).toHaveLength(2))
    expect(document.querySelector('[data-insights-showing]')).toBeNull()
  })

  it('the range toggle changes the request (24 h)', async () => {
    renderCard()
    await waitFor(() => expect(rows()).toHaveLength(1))
    fireEvent.click(screen.getByRole('button', { name: '24 h' }))
    await waitFor(() => expect(service.listInsights).toHaveBeenCalledWith('p1', '24h', false, undefined))
    expect(screen.getByRole('button', { name: '24 h' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('the flag off: the whole body is the sentence', async () => {
    service.listInsights.mockResolvedValue({ ...insightList([insight()]), enabled: false })
    renderCard()
    expect(await screen.findByText('Insights are off for this project (feature flag cross_run_insights).')).toBeInTheDocument()
    expect(rows()).toHaveLength(0)
  })

  it('a failed or malformed read says "Could not load the insights."', async () => {
    service.listInsights.mockResolvedValue([insight()])
    const { unmount } = renderCard()
    expect(await screen.findByText('Could not load the insights.')).toBeInTheDocument()
    unmount()
    service.listInsights.mockRejectedValue(new Error('boom'))
    renderCard()
    expect(await screen.findByText('Could not load the insights.')).toBeInTheDocument()
  })
})
