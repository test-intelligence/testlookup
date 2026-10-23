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
// Mutable so the W10 cases can render a QA engineer; every earlier case reads a QA lead.
const perms = vi.hoisted(() => ({ canAccessManagement: true }))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: perms.canAccessManagement }),
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

// ── W10 INT-07: the circuit breaker (paused badge + last error, Re-enable, the threshold) ──

describe('OutboundWebhooksPage — the circuit breaker (W10)', () => {
  const PAUSED = {
    id: 'wh-1', project_id: 'proj-1', name: 'Pager', target_url: 'https://hooks.example.com/x',
    events: ['run.completed'], enabled: false, has_secret: true, max_retries: 0, total_delivered: 2, failure_count: 3,
    last_delivered_at: null, last_failure_at: '2026-10-22T10:00:00Z', last_error: 'HTTP 500 receiver error',
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-22T10:00:00Z',
    consecutive_failures: 3, breaker_threshold: 3, disabled_reason: 'circuit_open', disabled_at: '2026-10-22T10:00:00Z',
  }
  const PAUSED_LINE =
    'Paused after 3 consecutive failures (a refused or unreachable target counts too). Last error: HTTP 500 receiver error'

  beforeEach(async () => {
    vi.clearAllMocks()
    perms.canAccessManagement = true
    mockList.mockResolvedValue([])
    mockEvents.mockResolvedValue({ events: CATALOG })
    mockCreate.mockResolvedValue({})
    const { outboundWebhookService } = await import('@/services/outboundWebhookService')
    vi.mocked(outboundWebhookService.update).mockReset()
  })

  it('a paused row reads PAUSED and says why, with its last error, instead of the plain Last error line', async () => {
    mockList.mockResolvedValue([PAUSED])
    renderPage()
    const line = await screen.findByText(PAUSED_LINE)
    expect(line.getAttribute('data-webhook-paused')).toBe('')
    expect(line.className).toContain('text-xs')
    expect(line.className).toContain('--ui-danger-ink')
    const chip = document.querySelector('[data-webhook-state]') as HTMLElement
    expect(chip.textContent).toBe('PAUSED')
    expect(chip.getAttribute('data-webhook-state')).toBe('paused')
    expect(chip.className).toContain('--ui-danger-')
    expect(chip.className).not.toMatch(/--status-(failed|broken)/)
    expect(screen.queryByText(/^Last error:/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Re-enable' }).className).toBe('btn-secondary btn-sm')
  })

  it('Re-enable PATCHes the full row with enabled: true and the stored threshold, re-reads the list, toasts', async () => {
    const { outboundWebhookService } = await import('@/services/outboundWebhookService')
    vi.mocked(outboundWebhookService.update).mockResolvedValue({ ...PAUSED, enabled: true } as never)
    mockList.mockResolvedValue([PAUSED])
    renderPage()
    const button = await screen.findByRole('button', { name: 'Re-enable' })
    const reads = mockList.mock.calls.length

    mockList.mockResolvedValue([{ ...PAUSED, enabled: true, consecutive_failures: 0, disabled_reason: null, disabled_at: null }])
    fireEvent.click(button)
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledWith('Webhook re-enabled'))
    expect(outboundWebhookService.update).toHaveBeenCalledTimes(1)
    expect(outboundWebhookService.update).toHaveBeenCalledWith('wh-1', {
      name: 'Pager',
      target_url: 'https://hooks.example.com/x',
      events: ['run.completed'],
      enabled: true,
      max_retries: 0,
      breaker_threshold: 3,
    })
    expect(mockList.mock.calls.length).toBeGreaterThan(reads)
    // The badge is gone once the list says so.
    await waitFor(() => expect(screen.queryByText(PAUSED_LINE)).toBeNull())
    expect(screen.queryByRole('button', { name: 'Re-enable' })).toBeNull()
    expect((document.querySelector('[data-webhook-state]') as HTMLElement).textContent).toBe('ENABLED')
  })

  it('a person\'s Disabled stays "DISABLED", with no paused line and no Re-enable', async () => {
    mockList.mockResolvedValue([{ ...PAUSED, disabled_reason: 'manual', consecutive_failures: 1 }])
    renderPage()
    await screen.findByText('Pager')
    expect((document.querySelector('[data-webhook-state]') as HTMLElement).textContent).toBe('DISABLED')
    expect(document.querySelector('[data-webhook-paused]')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Re-enable' })).toBeNull()
    expect(screen.getByText(/^Last error:/)).toBeInTheDocument()
  })

  it('an enabled row that is failing shows the streak beside the counts', async () => {
    mockList.mockResolvedValue([{ ...PAUSED, enabled: true, disabled_reason: null, disabled_at: null, consecutive_failures: 2, breaker_threshold: 3 }])
    renderPage()
    await waitFor(() => expect(document.querySelector('[data-webhook-streak]')).not.toBeNull())
    const streak = document.querySelector('[data-webhook-streak]') as HTMLElement
    expect(streak.textContent).toBe(' · 2/3 in a row')
    expect(streak.parentElement?.textContent).toBe('✓ 2 · ✗ 3 · 2/3 in a row')
  })

  it('a QA engineer sees the paused line but no Re-enable and no toggle action', async () => {
    perms.canAccessManagement = false
    mockList.mockResolvedValue([PAUSED])
    renderPage()
    await screen.findByText(PAUSED_LINE)
    expect(screen.queryByRole('button', { name: 'Re-enable' })).toBeNull()
    expect(document.querySelector('[data-webhook-state]')).toBeDisabled()
  })

  it('the create form sends breaker_threshold (default 10, as typed)', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /new webhook/i }))
    const threshold = screen.getByLabelText('Pause after (consecutive failed deliveries)')
    expect(threshold).toHaveValue(10)
    expect(threshold.closest('[class~="grid"]')?.className).toContain('xl:grid-cols-3')

    fireEvent.change(screen.getByPlaceholderText('PagerDuty for regressions'), { target: { value: 'Pager' } })
    fireEvent.change(screen.getByPlaceholderText('https://events.example.com/hooks/testlookup'), {
      target: { value: 'https://hooks.example.com/x' },
    })
    fireEvent.change(threshold, { target: { value: '3' } })
    fireEvent.click(screen.getByText('run.completed'))
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1))
    expect(mockCreate).toHaveBeenCalledWith('proj-1', expect.objectContaining({ breaker_threshold: 3, name: 'Pager' }))
  })

  it('refuses a threshold outside 1-100 without sending', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /new webhook/i }))
    fireEvent.change(screen.getByPlaceholderText('PagerDuty for regressions'), { target: { value: 'Pager' } })
    fireEvent.change(screen.getByPlaceholderText('https://events.example.com/hooks/testlookup'), {
      target: { value: 'https://hooks.example.com/x' },
    })
    fireEvent.click(screen.getByText('run.completed'))
    fireEvent.change(screen.getByLabelText('Pause after (consecutive failed deliveries)'), { target: { value: '0' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    expect(mockCreate).not.toHaveBeenCalled()
  })

  it('the help says how the breaker counts', async () => {
    renderPage()
    await waitFor(() => expect(document.querySelector('[data-webhook-breaker-help]')).not.toBeNull())
    const help = document.querySelector('[data-webhook-breaker-help]') as HTMLElement
    expect(help.textContent).toBe(
      'Circuit breaker: a subscription is paused after N deliveries in a row end failed (retries count once); deliveries meanwhile are kept as failed and can be replayed after Re-enable.',
    )
    expect(help.querySelector('strong')?.textContent).toBe('Circuit breaker:')
  })
})
