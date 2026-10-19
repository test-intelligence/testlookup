/**
 * W06 DEF-03 §2.7-8: New defect gains an optional "Affects release" select —
 * the project's releases from the top bar's cached list (`{name} {version}`,
 * shipped ones with their date), first option "Not specified" — and sends
 * `affects_releases: [id]` ONLY when a release is chosen.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Release } from '@/types/releases'

const releases = vi.hoisted(() => ({ items: [] as Partial<Release>[], calls: [] as unknown[][] }))
vi.mock('@/hooks/useReleases', () => ({
  useReleases: (...args: unknown[]) => {
    releases.calls.push(args)
    return { data: { items: releases.items, total: releases.items.length }, isLoading: false, error: undefined }
  },
}))

const analytics = vi.hoisted(() => ({ createDefect: vi.fn() }))
vi.mock('@/services/analyticsService', () => ({ analyticsService: analytics }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

import DefectIntakeModal from './DefectIntakeModal'

const release = (id: string, extra: Partial<Release>): Partial<Release> => ({
  id, project_id: 'p-1', name: `Release ${id}`, version: null, status: 'planned', released_at: null, ...extra,
})

beforeEach(() => {
  releases.calls = []
  releases.items = [
    release('r-shipped', { name: 'Checkout', version: '2.4', status: 'released', released_at: '2026-10-01T15:00:00Z' }),
    release('r-next', { name: 'Checkout', version: '2.5' }),
    release('r-foreign', { project_id: 'p-2', name: 'Billing', version: '9.0' }),
  ]
  analytics.createDefect.mockReset()
  analytics.createDefect.mockResolvedValue({ id: 'd-123456789', project_id: 'p-1', title: 'x', severity: 'P1' })
  toast.success.mockReset()
})

function renderModal() {
  const onClose = vi.fn()
  render(<DefectIntakeModal projectId="p-1" onClose={onClose} />)
  return { onClose }
}

const affects = () => screen.getByRole('combobox', { name: 'Affects release' })

async function submit() {
  fireEvent.change(screen.getByLabelText(/^Title/), { target: { value: 'Checkout total is wrong' } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Create defect' }))
  })
}

describe('DefectIntakeModal — Affects release', () => {
  it("offers Not specified, then this project's releases from the cached list", () => {
    renderModal()
    expect(releases.calls[0]).toEqual([undefined, { cached: true }])
    const labels = within(affects()).getAllByRole('option').map((o) => o.textContent)
    expect(labels).toEqual(['Not specified', 'Checkout 2.4 · shipped Oct 1, 2026', 'Checkout 2.5'])
    expect(affects()).toHaveValue('')
    expect(screen.getByText('A defect filed against a release that has already shipped counts toward the escape rate.'))
      .toBeInTheDocument()
  })

  it('sends no affects_releases when none is chosen', async () => {
    renderModal()
    await submit()
    expect(analytics.createDefect).toHaveBeenCalledTimes(1)
    expect(analytics.createDefect.mock.calls[0][0]).not.toHaveProperty('affects_releases')
  })

  it('sends affects_releases: [id] for the chosen release', async () => {
    renderModal()
    fireEvent.change(affects(), { target: { value: 'r-shipped' } })
    await submit()
    expect(analytics.createDefect).toHaveBeenCalledWith(expect.objectContaining({
      project_id: 'p-1', title: 'Checkout total is wrong', affects_releases: ['r-shipped'],
    }))
  })

  it('renders the server refusal of a release inside the form', async () => {
    analytics.createDefect.mockRejectedValue({
      response: { status: 422, data: { detail: 'One or more of these releases are not in this project.' } },
    })
    const { onClose } = renderModal()
    fireEvent.change(affects(), { target: { value: 'r-next' } })
    await submit()
    expect(screen.getByText('One or more of these releases are not in this project.')).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })
})

// W29a UIX-07: the four severities read the ONE severity palette: P0 danger,
// P1 warning, P2 info, P3 neutral — the intake's P2 / P3 are no longer orange /
// passed-green.
describe('DefectIntakeModal — severity chips', () => {
  const chip = (id: string) => document.querySelector(`[data-severity="${id}"]`) as HTMLElement
  const chosen = (id: string) => {
    fireEvent.click(chip(id))
    return chip(id).getAttribute('style') ?? ''
  }

  it('opens on P1 in the warning tone with its ink', () => {
    renderModal()
    expect(chip('P1').style.color).toBe('var(--ui-warning-ink)')
    expect(chip('P1').getAttribute('style')).not.toMatch(/--status-/)
  })

  it('P2 is the info tone and P3 the neutral; neither names a status hue', () => {
    renderModal()
    const p2 = chosen('P2')
    expect(chip('P2').style.color).toBe('var(--ui-info-ink)')
    expect(p2).not.toMatch(/--status-/)
    const p3 = chosen('P3')
    expect(chip('P3').style.color).toBe('var(--color-text-muted)')
    expect(p3).not.toMatch(/--status-passed|--status-broken/)
    // Only the chosen chip carries a tone; the others are the neutral class look.
    expect(chip('P2').getAttribute('style') ?? '').toBe('')
  })

  it('P0 is the danger tone', () => {
    renderModal()
    chosen('P0')
    expect(chip('P0').style.color).toBe('var(--ui-danger-ink)')
  })
})
