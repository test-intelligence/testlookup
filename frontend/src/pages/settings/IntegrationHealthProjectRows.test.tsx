/**
 * W10 INT-04: the project rows and the paused webhooks under Integration
 * Health's global cards — text-only rendering, the width plan's classes, the
 * per-row Probe now, the settings link that switches the project first.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { IntegrationStatus } from '@/services/integrationHealthService'
import type { Project } from '@/types/projects'
import { useProjectStore } from '@/store/projectStore'
import IntegrationHealthProjectRows from './IntegrationHealthProjectRows'

function row(overrides: Partial<IntegrationStatus>): IntegrationStatus {
  return {
    provider: 'github', status: 'healthy', last_checked_at: null, message: null, response_ms: null,
    consecutive_failures: 0, last_success_at: null, kind: 'project', ...overrides,
  }
}

const GH = row({
  scope_key: 'p-1', project_id: 'p-1', project_name: 'Payment Service', label: 'Payment <b>Service</b>',
  target: 'ghe.corp.example.com/api/v3', response_ms: 88, message: 'Repository acme/payments reachable',
  last_checked_at: '2026-10-22T10:05:00Z',
})
const GH_BAD = row({
  scope_key: 'p-3', project_id: 'p-3', project_name: 'Checkout', label: 'Checkout', status: 'auth_error',
  message: 'HTTP 401: the token was refused', consecutive_failures: 2,
})
const GL = row({ provider: 'gitlab', scope_key: 'p-2', project_id: 'p-2', project_name: 'API Gateway', label: 'API Gateway', status: 'unknown', probed: false })
const HOOK = row({
  provider: 'webhook', kind: 'webhook', scope_key: 'wh-1', project_id: 'p-1', project_name: 'Payment Service', label: 'Release bot',
  status: 'paused', consecutive_failures: 10, breaker_threshold: 10, message: '<script>alert(1)</script>', target: 'hooks.fake.test',
})

function renderRows(rows: IntegrationStatus[], onProbe = vi.fn(), probing = false) {
  render(
    <MemoryRouter>
      <IntegrationHealthProjectRows rows={rows} probing={probing} onProbe={onProbe} />
    </MemoryRouter>,
  )
  return onProbe
}

describe('IntegrationHealthProjectRows', () => {
  beforeEach(() => {
    useProjectStore.setState({
      projects: [{ id: 'p-1', name: 'Payment Service' }, { id: 'p-2', name: 'API Gateway' }] as unknown as Project[],
      activeProjectId: 'all',
      activeProject: null,
    })
  })

  it('renders nothing without project or webhook rows', () => {
    const { container } = render(
      <MemoryRouter>
        <IntegrationHealthProjectRows rows={[row({ kind: 'global', provider: 'jira' })]} probing={false} onProbe={vi.fn()} />
      </MemoryRouter>,
    )
    expect(container.innerHTML).toBe('')
  })

  it('one section per provider, cards on the .card surface in the three-up grid, the title wrapping (never cut) with the chip on its own line', () => {
    renderRows([GH, GH_BAD, GL])
    const gh = screen.getByRole('region', { name: 'GitHub projects' })
    expect(within(gh).getByRole('heading', { name: 'GitHub projects' })).toBeInTheDocument()
    const cards = gh.querySelectorAll('[data-health-kind="project"]')
    expect(cards).toHaveLength(2)
    expect((cards[0] as HTMLElement).className).toContain('card')
    expect((cards[0] as HTMLElement).className).not.toContain('bg-[var(--color-bg-card)]')
    expect((cards[0].parentElement as HTMLElement).className).toBe('grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3')
    const title = within(cards[0] as HTMLElement).getByText('GitHub · Payment <b>Service</b>')
    expect(title.className).toContain('min-w-0')
    expect(title.className).toContain('break-words')
    expect(title.className).not.toMatch(/\btruncate\b|line-clamp|whitespace-nowrap|overflow-hidden/)
    expect(title.getAttribute('data-health-title')).toBe('')
    expect(title.getAttribute('title')).toBe('GitHub · Payment <b>Service</b>')
    // The chip is on a line of its own under the title, not beside it.
    const chip = (cards[0] as HTMLElement).querySelector('[data-health-status]') as HTMLElement
    expect(chip.parentElement?.contains(title)).toBe(false)
    expect(chip.parentElement?.className).toContain('flex-wrap')
    expect(title.compareDocumentPosition(chip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // Names are text, never markup.
    expect(gh.querySelector('b')).toBeNull()
    const target = within(cards[0] as HTMLElement).getByText('ghe.corp.example.com/api/v3')
    expect(target.className).toContain('font-mono')
    expect(target.className).toContain('[overflow-wrap:anywhere]')
    expect(within(cards[1] as HTMLElement).getByText('Consecutive failures: 2').className).toContain('--ui-danger-ink')
    expect(screen.getByRole('region', { name: 'GitLab projects' })).toBeInTheDocument()
  })

  it('"Not probed yet" is neutral, never a health colour', () => {
    renderRows([GL])
    const chip = screen.getByText('Not probed yet')
    expect(chip.className).toContain('bg-[var(--color-bg-secondary)]')
    expect(chip.className).toContain('text-[var(--color-text-muted)]')
    expect(chip.className).not.toMatch(/--ui-(success|warning|danger)/)
  })

  it('Probe now hands the row\'s provider and project; it is off while a probe runs', () => {
    const onProbe = renderRows([GH, GL])
    const gl = screen.getByRole('region', { name: 'GitLab projects' })
    fireEvent.click(within(gl).getByRole('button', { name: 'Probe now' }))
    expect(onProbe).toHaveBeenCalledWith('gitlab', 'p-2')
  })

  it('every button is disabled while probing', () => {
    renderRows([GH, GL], vi.fn(), true)
    for (const b of screen.getAllByRole('button', { name: 'Probe now' })) expect(b).toBeDisabled()
  })

  it('Open GitLab settings links /settings/gitlab and selects the row\'s project first; no link for a project the reader lacks', () => {
    renderRows([GL, GH_BAD])
    const gl = screen.getByRole('region', { name: 'GitLab projects' })
    const link = within(gl).getByRole('link', { name: 'Open GitLab settings' })
    expect(link.getAttribute('href')).toBe('/settings/gitlab')
    fireEvent.click(link)
    expect(useProjectStore.getState().activeProjectId).toBe('p-2')
    // Checkout (p-3) is not in the reader's projects.
    const gh = screen.getByRole('region', { name: 'GitHub projects' })
    expect(within(gh).queryByRole('link')).toBeNull()
  })

  it('a paused webhook: its line, its last error as text, the danger chip, Open Outbound webhooks', () => {
    renderRows([HOOK])
    const section = screen.getByRole('region', { name: 'Paused webhooks' })
    const card = section.querySelector('[data-health-kind="webhook"]') as HTMLElement
    expect(card.getAttribute('data-health-row')).toBe('wh-1')
    expect(within(card).getByText('Webhook Release bot · Payment Service')).toBeInTheDocument()
    expect(within(card).getByText('Webhook Release bot · Payment Service').className).not.toMatch(/\btruncate\b/)
    expect((card.querySelector('[data-health-paused-line]') as HTMLElement).textContent).toBe('Paused after 10 consecutive failures (threshold 10)')
    expect(within(card).getByText('Last error: <script>alert(1)</script>')).toBeInTheDocument()
    expect(card.querySelector('script')).toBeNull()
    expect(within(card).getByText('paused').className).toContain('--ui-danger-')
    expect(within(card).getByRole('link', { name: 'Open Outbound webhooks' }).getAttribute('href')).toBe('/settings/webhooks')
    expect(within(card).queryByRole('button')).toBeNull()
  })
})
