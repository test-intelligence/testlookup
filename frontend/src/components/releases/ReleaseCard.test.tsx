/**
 * REL-01 / REL-02 / REL-08: the release card renders the server's recorded
 * verdict (Pass rate · Evidence · Runs, coloured by the verdict, no browser
 * threshold), the release's own owner and phases, the Active pill, a safe
 * external link and the QA-lead-only Evaluate button and Release actions
 * menu — none of which open the card when clicked.
 *
 * Cards are built through the real `deriveRelease`, from `Release` rows the
 * shape the API returns.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { Release, ReleaseGateSummary, ReleasePhase } from '@/types/releases'
import ReleaseCard from './ReleaseCard'
import { deriveRelease } from './mapping'
import type { DerivedRelease } from './types'

const TWO_HOURS_AGO = new Date(Date.now() - 2 * 3600 * 1000).toISOString()

function gate(overrides: Partial<ReleaseGateSummary> = {}): ReleaseGateSummary {
  return {
    verdict: 'GO',
    decided_at: TWO_HOURS_AGO,
    pass_rate: 97.5,
    denominator: 42,
    evidence_count: 40,
    run_count: 3,
    status_counts: { PASSED: 39, FAILED: 1 },
    measured: true,
    insufficient_reason: null,
    blocking_reasons: [],
    blocking_total: 0,
    ...overrides,
  }
}

function phase(id: string, name: string, status = 'pending', order_index = 0): ReleasePhase {
  return {
    id,
    release_id: 'rel-1',
    name,
    phase_type: 'custom',
    status,
    description: null,
    order_index,
    planned_start: null,
    planned_end: null,
    actual_start: null,
    actual_end: null,
    exit_criteria: null,
    notes: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
  }
}

function release(overrides: Partial<Release> = {}): DerivedRelease {
  return deriveRelease({
    id: 'rel-1',
    project_id: 'proj-1',
    project_name: 'Checkout Platform',
    name: '2026.11',
    version: 'v11',
    description: null,
    status: 'in_progress',
    planned_date: null,
    released_at: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    phases: [phase('ph-1', 'Build verification')],
    owner: { id: 'u-1', name: 'Priya Nair' },
    ...overrides,
  })
}

type CardProps = Parameters<typeof ReleaseCard>[0]
function renderCard(props: CardProps) {
  return render(
    <MemoryRouter>
      <ReleaseCard {...props} />
    </MemoryRouter>,
  )
}

const tile = (label: string) => document.querySelector(`[data-gate-tile="${label}"]`) as HTMLElement | null

describe('the verdict cluster', () => {
  it('GO: Pass rate · Evidence · Runs of the recorded verdict, green, with its age', () => {
    renderCard({ release: release({ gate: gate() }) })
    expect(tile('Pass rate')).toHaveTextContent('Pass rate97.5%over 40 tests')
    expect(tile('Evidence')).toHaveTextContent('Evidence40of 42 in scope')
    expect(tile('Runs')).toHaveTextContent('Runs3in the last verdict')
    const value = tile('Pass rate')?.children[1] as HTMLElement
    expect(value.style.color).toBe('var(--status-passed)')
    expect(screen.getByText('Go')).toBeInTheDocument()
    const age = screen.getByText('Evaluated 2h ago')
    expect(age).toHaveAttribute('title', TWO_HOURS_AGO)
    // No browser-side threshold or the old tiles.
    expect(document.body.textContent).not.toMatch(/Composite|Coverage|Flake|need ≥|cap 1\.5/)
  })

  it('NO_GO: the pass rate is red, whatever its value', () => {
    renderCard({ release: release({ gate: gate({ verdict: 'NO_GO', pass_rate: 99.9, blocking_reasons: ['a (s) is FAILED'], blocking_total: 1 }) }) })
    expect((tile('Pass rate')?.children[1] as HTMLElement).style.color).toBe('var(--status-failed)')
    expect(screen.getByText('No-Go')).toBeInTheDocument()
  })

  it('a Conditional Go is neutral — tone follows the verdict, never a number', () => {
    renderCard({ release: release({ gate: gate({ verdict: 'CONDITIONAL_GO', pass_rate: 99 }) }) })
    expect((tile('Pass rate')?.children[1] as HTMLElement).style.color).toBe('var(--color-text)')
  })

  it('NOT_EVALUATED: the line under the badge is the server\'s reason; no measured pass rate is invented', () => {
    renderCard({
      release: release({
        gate: gate({ verdict: 'NOT_EVALUATED', pass_rate: null, evidence_count: 0, measured: false, insufficient_reason: '0 of 42 tests carry evidence; 5 required' }),
      }),
    })
    expect(screen.getByText('Not evaluated')).toBeInTheDocument()
    expect(screen.getByText('0 of 42 tests carry evidence; 5 required')).toHaveAttribute('title', TWO_HOURS_AGO)
    expect(tile('Pass rate')).toHaveTextContent('Pass rate—no test has run')
  })

  it('never evaluated: "Not evaluated yet", no tiles, no age', () => {
    renderCard({ release: release({ gate: null }) })
    expect(screen.getByText('Not evaluated yet')).toBeInTheDocument()
    expect(tile('Pass rate')).toBeNull()
    expect(screen.queryByText(/^Evaluated /)).toBeNull()
  })

  it('a planning card renders no tiles even when a verdict exists', () => {
    renderCard({ release: release({ status: 'planning', gate: gate() }) })
    expect(tile('Pass rate')).toBeNull()
    expect(tile('Evidence')).toBeNull()
    expect(tile('Runs')).toBeNull()
  })

  it('keeps the card\'s accessible name "{name} {version} — {decision}"', () => {
    renderCard({ release: release({ gate: gate() }) })
    expect(screen.getByRole('article', { name: '2026.11 v11 — go' })).toBeInTheDocument()
  })
})

describe('the Evaluate button (QA lead only)', () => {
  it('renders only when onEvaluate is given', () => {
    const { unmount } = renderCard({ release: release() })
    expect(screen.queryByTestId('release-evaluate')).toBeNull()
    unmount()
    renderCard({ release: release(), onEvaluate: vi.fn() })
    expect(screen.getByTestId('release-evaluate')).toBeInTheDocument()
  })

  it('reads "Evaluate gate" before any verdict and "Re-evaluate" once one exists', () => {
    const { unmount } = renderCard({ release: release({ gate: null }), onEvaluate: vi.fn() })
    expect(screen.getByTestId('release-evaluate')).toHaveTextContent('Evaluate gate')
    unmount()
    renderCard({ release: release({ gate: gate() }), onEvaluate: vi.fn() })
    expect(screen.getByTestId('release-evaluate')).toHaveTextContent('Re-evaluate')
  })

  it('evaluates this release once and does not open the card', () => {
    const onEvaluate = vi.fn()
    const onClick = vi.fn()
    renderCard({ release: release(), onEvaluate, onClick })
    fireEvent.click(screen.getByTestId('release-evaluate'))
    expect(onEvaluate).toHaveBeenCalledTimes(1)
    expect(onEvaluate).toHaveBeenCalledWith('rel-1')
    expect(onClick).not.toHaveBeenCalled()
  })

  it('is busy while evaluating', () => {
    renderCard({ release: release(), onEvaluate: vi.fn(), evaluating: true })
    const button = screen.getByTestId('release-evaluate')
    expect(button).toHaveTextContent('Evaluating…')
    expect(button).toBeDisabled()
  })

  it('is offered for in-progress and released releases only', () => {
    for (const status of ['planning', 'cancelled']) {
      const { unmount } = renderCard({ release: release({ status }), onEvaluate: vi.fn() })
      expect(screen.queryByTestId('release-evaluate'), status).toBeNull()
      unmount()
    }
    renderCard({ release: release({ status: 'released' }), onEvaluate: vi.fn() })
    expect(screen.getByTestId('release-evaluate')).toBeInTheDocument()
  })
})

describe('blockers', () => {
  const reasons = Array.from({ length: 20 }, (_, i) => `test_${i} (checkout-e2e) is FAILED`)

  it('lists the first three server reasons, then "+N more" counted from the server total', () => {
    renderCard({ release: release({ gate: gate({ verdict: 'NO_GO', blocking_reasons: reasons, blocking_total: 25 }) }) })
    expect(screen.getByText('test_0 (checkout-e2e) is FAILED')).toBeInTheDocument()
    expect(screen.getByText('test_2 (checkout-e2e) is FAILED')).toBeInTheDocument()
    expect(screen.queryByText('test_3 (checkout-e2e) is FAILED')).toBeNull()
    expect(document.querySelector('[data-blockers-more]')).toHaveTextContent('+22 more · open the release for the full list')
  })

  // W27 PAN-02: beside an open release the card is ~510 px at 1280; the rows
  // truncate their names, so their list has zero intrinsic width and never
  // sets the card's min-content (it still fills the card in layout).
  it('the blocker list does not size the card', () => {
    renderCard({ release: release({ gate: gate({ verdict: 'NO_GO', blocking_reasons: reasons, blocking_total: 25 }) }) })
    expect(document.querySelector('[data-card-blockers]')).toHaveClass('w-0', 'min-w-full')
  })

  it('links a reason to the release\'s gate panel in-app, without opening the card', () => {
    const onClick = vi.fn()
    renderCard({ release: release({ gate: gate({ verdict: 'NO_GO', blocking_reasons: ['test_pay (checkout-e2e) is FAILED'], blocking_total: 1 }) }), onClick })
    const link = screen.getByRole('link', { name: /Open gate/ })
    expect(link).toHaveAttribute('href', '/releases/rel-1#gate')
    fireEvent.click(link)
    expect(onClick).not.toHaveBeenCalled()
    expect(document.querySelector('[data-blockers-more]')).toBeNull()
  })
})

describe('owner chip (REL-02)', () => {
  it('shows the owner\'s initials and name', () => {
    renderCard({ release: release() })
    expect(document.querySelector('[data-owner-chip="set"]')).toHaveTextContent('PN')
    expect(screen.getByText('Priya Nair')).toBeInTheDocument()
    expect(screen.queryByText('Checkout Platform')).toBeNull()
  })

  it('reads "No owner" with a dashed avatar when there is none', () => {
    renderCard({ release: release({ owner: null }) })
    expect(screen.getByText('No owner')).toBeInTheDocument()
    expect(document.querySelector('[data-owner-chip="none"]')).toHaveTextContent('–')
    expect(screen.queryByText('Checkout Platform')).toBeNull()
  })
})

describe('phases', () => {
  it('shows the release\'s own phase names', () => {
    renderCard({ release: release({ phases: [phase('a', 'Build verification', 'completed', 0), phase('b', 'Soak', 'in_progress', 1)] }) })
    const pipeline = screen.getByRole('list', { name: 'Release phase pipeline' })
    expect(within(pipeline).getAllByRole('listitem').map(li => li.textContent)).toEqual(['Build verification', 'Soak'])
    expect(within(pipeline).getByText('Soak').closest('[role="listitem"]')).toHaveAttribute('title', 'Soak — active')
  })

  it('draws at most 8 chips, then +N', () => {
    renderCard({ release: release({ phases: Array.from({ length: 10 }, (_, i) => phase(`p${i}`, `Phase ${i}`, 'pending', i)) }) })
    const items = within(screen.getByRole('list', { name: 'Release phase pipeline' })).getAllByRole('listitem')
    expect(items).toHaveLength(9)
    expect(items[8]).toHaveTextContent('+2')
  })

  it('a release with no phases shows the callout, no pipeline (planning and in progress alike)', () => {
    for (const status of ['planning', 'in_progress']) {
      const { unmount } = renderCard({ release: release({ status, phases: [] }) })
      expect(screen.queryByRole('list', { name: 'Release phase pipeline' }), status).toBeNull()
      expect(document.querySelector('[data-planning-callout]'), status).toHaveTextContent('No phases scoped yet')
      unmount()
    }
  })
})

describe('the pass/fail bar follows the verdict status counts', () => {
  it('passed / failed / broken / skipped', () => {
    renderCard({ release: release({ gate: gate({ status_counts: { PASSED: 30, FAILED: 3, BROKEN: 2, SKIPPED: 1, UNKNOWN: 1 } }) }) })
    expect(document.querySelector('[data-pass-fail-bar]')).toHaveTextContent(/30 3 2 2 ?\/ 37/)
  })

  it('no counts, no bar', () => {
    renderCard({ release: release({ gate: null }) })
    expect(document.querySelector('[data-pass-fail-bar]')).toBeNull()
  })
})

describe('Active pill, external link, Release actions menu (REL-08)', () => {
  it('shows the ACTIVE pill on the active release only', () => {
    const { unmount } = renderCard({ release: release({ is_active: true }) })
    expect(document.querySelector('[data-release-active]')).toHaveTextContent('ACTIVE')
    unmount()
    renderCard({ release: release() })
    expect(document.querySelector('[data-release-active]')).toBeNull()
  })

  it('links an http(s) source in a new tab with noopener noreferrer, without opening the card', () => {
    const onClick = vi.fn()
    renderCard({ release: release({ external_url: 'https://github.com/acme/shop/milestone/3', source_system: 'github' }), onClick })
    const link = screen.getByRole('link', { name: /GitHub/ })
    expect(link).toHaveAttribute('href', 'https://github.com/acme/shop/milestone/3')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    fireEvent.click(link)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('renders no link for javascript: (or any non-http scheme), even on a hand-built row', () => {
    const { unmount } = renderCard({ release: release({ external_url: 'javascript:alert(1)', source_system: 'jira' }) })
    expect(document.querySelector('[data-release-external]')).toBeNull()
    unmount()
    // A view model that skipped the mapping's filter still renders no link.
    renderCard({ release: { ...release(), externalUrl: 'javascript:alert(1)', sourceSystem: 'jira' } })
    expect(document.querySelector('[data-release-external]')).toBeNull()
    expect(document.querySelector('a[href^="javascript"]')).toBeNull()
  })

  it('offers "Set as active release" to a QA lead, without opening the card', () => {
    const onSetActive = vi.fn()
    const onClick = vi.fn()
    renderCard({ release: release(), onSetActive, onClick })
    fireEvent.click(screen.getByRole('button', { name: 'Release actions' }))
    expect(onClick).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Set as active release' }))
    expect(onSetActive).toHaveBeenCalledWith('rel-1')
    expect(onClick).not.toHaveBeenCalled()
  })

  it('has no menu for a viewer, for the active release, or for a finished release', () => {
    const cases: [string, CardProps][] = [
      ['no onSetActive', { release: release() }],
      ['already active', { release: release({ is_active: true }), onSetActive: vi.fn() }],
      ['released', { release: release({ status: 'released' }), onSetActive: vi.fn() }],
      ['cancelled', { release: release({ status: 'cancelled' }), onSetActive: vi.fn() }],
    ]
    for (const [name, props] of cases) {
      const { unmount } = renderCard(props)
      expect(screen.queryByRole('button', { name: 'Release actions' }), name).toBeNull()
      unmount()
    }
    // A planned release can be made active.
    renderCard({ release: release({ status: 'planning' }), onSetActive: vi.fn() })
    expect(screen.getByRole('button', { name: 'Release actions' })).toBeInTheDocument()
  })
})
