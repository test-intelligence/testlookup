/**
 * Outbound webhooks — the template header and the two-column create form
 * (UX redesign P5 item 4), plus the create round-trip the form exists for.
 * W27b PAN-06 (W1): a webhook's delete asks inside its card, under the header
 * row, never with the browser's prompt.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockList = vi.fn()
const mockEvents = vi.fn()
const mockCreate = vi.fn()
const mockRemove = vi.fn()
vi.mock('@/services/outboundWebhookService', () => ({
  outboundWebhookService: {
    list: (...a: unknown[]) => mockList(...a),
    events: (...a: unknown[]) => mockEvents(...a),
    create: (...a: unknown[]) => mockCreate(...a),
    update: vi.fn(),
    remove: (...a: unknown[]) => mockRemove(...a),
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
const mockToastSuccess = vi.fn()
vi.mock('react-hot-toast', () => ({ default: { success: (m: string) => mockToastSuccess(m), error: vi.fn() } }))
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

  describe('a webhook delete asks in its card (W27b)', () => {
    const HOOK = {
      id: 'wh-1', project_id: 'proj-1', name: 'Release <b>bot</b>', target_url: 'https://hooks.example.com/r',
      events: ['run.completed'], enabled: true, max_retries: 5, total_delivered: 3, failure_count: 0,
      last_error: 'HTTP 502 from the endpoint', last_error_at: null, created_at: '2026-10-01T00:00:00Z', updated_at: null,
    }
    const strip = () => document.querySelector('[data-inline-confirm]') as HTMLElement | null

    it('asks under the header row, before Last error; the strip sends one DELETE, re-reads, toasts', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm')
      mockList.mockResolvedValue([HOOK])
      mockRemove.mockResolvedValue(undefined)
      renderPage()
      const del = await screen.findByRole('button', { name: 'Delete Release <b>bot</b>' })
      const reads = mockList.mock.calls.length

      fireEvent.click(del)
      expect(mockRemove).not.toHaveBeenCalled()
      const asked = strip() as HTMLElement
      // The name is text, quoted, in <strong> (never markup).
      expect(asked.querySelector('p')?.textContent).toBe('Delete webhook "Release <b>bot</b>"?')
      expect(asked.querySelector('strong')?.textContent).toBe('Release <b>bot</b>')
      expect(asked.querySelector('b')).toBeNull()
      // Inside the card: after the header row, before Last error.
      const headerRow = del.closest('[class~="py-2.5"]') as HTMLElement
      const card = headerRow.parentElement as HTMLElement
      expect(card.contains(asked)).toBe(true)
      expect(headerRow.contains(asked)).toBe(false)
      expect(asked.compareDocumentPosition(within(card).getByText(/Last error:/)) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

      fireEvent.click(within(asked).getByRole('button', { name: 'Delete' }))
      expect(strip()).toBeNull()
      await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledWith('Deleted'))
      expect(mockRemove).toHaveBeenCalledTimes(1)
      expect(mockRemove).toHaveBeenCalledWith('wh-1')
      expect(mockList.mock.calls.length).toBeGreaterThan(reads)
      expect(confirmSpy).not.toHaveBeenCalled()
      confirmSpy.mockRestore()
    })

    it('Cancel and Escape send nothing', async () => {
      mockList.mockResolvedValue([HOOK])
      renderPage()
      const del = await screen.findByRole('button', { name: 'Delete Release <b>bot</b>' })
      fireEvent.click(del)
      fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }))
      expect(strip()).toBeNull()
      fireEvent.click(del)
      fireEvent.keyDown(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
      expect(strip()).toBeNull()
      expect(mockRemove).not.toHaveBeenCalled()
    })
  })
})
