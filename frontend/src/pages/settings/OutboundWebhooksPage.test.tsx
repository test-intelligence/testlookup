/**
 * Outbound webhooks — the template header and the two-column create form
 * (UX redesign P5 item 4), plus the create round-trip the form exists for.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockList = vi.fn()
const mockEvents = vi.fn()
const mockCreate = vi.fn()
vi.mock('@/services/outboundWebhookService', () => ({
  outboundWebhookService: {
    list: (...a: unknown[]) => mockList(...a),
    events: (...a: unknown[]) => mockEvents(...a),
    create: (...a: unknown[]) => mockCreate(...a),
    update: vi.fn(),
    remove: vi.fn(),
    test: vi.fn(),
    deliveries: vi.fn().mockResolvedValue([]),
    replayDelivery: vi.fn(),
  },
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: true }),
}))
vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  const state = { activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Checkout' } }
  return {
    ALL_PROJECTS_ID: actual.ALL_PROJECTS_ID,
    useProjectStore: (sel: (s: typeof state) => unknown) => sel(state),
  }
})
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/components/ui/ExperimentalBadge', () => ({ default: () => null }))

import OutboundWebhooksPage from './OutboundWebhooksPage'

const CATALOG = [
  { event_type: 'run.completed', description: 'A run finished' },
  { event_type: 'defect.promoted', description: 'A failure became a defect' },
]

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/settings/webhooks']}>
      <OutboundWebhooksPage />
    </MemoryRouter>,
  )
}

describe('OutboundWebhooksPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockList.mockResolvedValue([])
    mockEvents.mockResolvedValue({ events: CATALOG })
    mockCreate.mockResolvedValue({})
  })

  it('has the template header: compact, with the integrations help topic', async () => {
    renderPage()
    const help = await screen.findByRole('button', { name: 'Help: Outbound Webhooks' })
    expect(help).toHaveAttribute('data-help-topic', 'integrations')
    expect(document.querySelector('[data-page-header]')).toHaveAttribute('data-compact', 'true')
  })

  it('opens a create form whose endpoint fields and event list go two-up at xl', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /new webhook/i }))

    const url = screen.getByPlaceholderText('https://events.example.com/hooks/testlookup')
    const endpoint = url.closest('[data-webhook-endpoint]') as HTMLElement
    expect(endpoint).not.toBeNull()
    expect(endpoint.className).toContain('xl:grid-cols-2')
    expect(endpoint.contains(screen.getByPlaceholderText('Shared secret — stored encrypted'))).toBe(true)

    const events = document.querySelector('[data-webhook-events]') as HTMLElement
    expect(events.className).toContain('xl:grid-cols-2')
    expect(events.querySelectorAll('input[type="checkbox"]')).toHaveLength(CATALOG.length)
  })

  it('creates the subscription with the typed name, URL, secret and events', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /new webhook/i }))

    fireEvent.change(screen.getByPlaceholderText('PagerDuty for regressions'), { target: { value: 'Pager' } })
    fireEvent.change(screen.getByPlaceholderText('https://events.example.com/hooks/testlookup'), {
      target: { value: 'https://hooks.example.com/x' },
    })
    fireEvent.change(screen.getByPlaceholderText('Shared secret — stored encrypted'), { target: { value: 's3cret' } })
    fireEvent.click(screen.getByText('run.completed'))
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1))
    expect(mockCreate).toHaveBeenCalledWith(
      'proj-1',
      expect.objectContaining({
        name: 'Pager',
        target_url: 'https://hooks.example.com/x',
        secret: 's3cret',
        events: ['run.completed'],
      }),
    )
  })
})
