import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CompliancePacks, ShippingThisWeek } from './RightRail'
import type { DerivedRelease, ReleaseStage } from './types'

/**
 * Build a minimal ``DerivedRelease`` for the shipping-panel filter test. Only
 * the fields ``ShippingThisWeek`` reads (``name``, ``version``, ``stage``,
 * ``dueAt``, ``gate.decision``) carry meaningful values; the rest are inert
 * placeholders.
 */
function makeRelease(overrides: Partial<DerivedRelease> & { dueAt: string | null; stage: ReleaseStage }): DerivedRelease {
  return {
    source: {} as DerivedRelease['source'],
    id: 'rel-1',
    name: 'Release',
    version: 'v1.0',
    ownerInitials: 'AB',
    ownerName: 'A B',
    gate: { decision: 'go', composite: null, coverage: null, flakePct: null, updatedAt: null },
    totals: null,
    phases: [],
    blockers: [],
    ...overrides,
  }
}

describe('ShippingThisWeek', () => {
  it('keeps in-progress releases with a due date inside the 7-day window', () => {
    const today = new Date()
    render(
      <ShippingThisWeek
        releases={[
          makeRelease({ id: 'soon', name: 'Soon', stage: 'in_progress', dueAt: today.toISOString() }),
        ]}
        onOpen={vi.fn()}
      />,
    )

    expect(screen.getByText('Soon')).toBeInTheDocument()
    expect(screen.queryByText(/No releases due/i)).not.toBeInTheDocument()
  })

  it('drops releases whose dueAt is null (the narrowed-filter guard)', () => {
    render(
      <ShippingThisWeek
        releases={[
          makeRelease({ id: 'nodate', name: 'NoDate', stage: 'in_progress', dueAt: null }),
        ]}
        onOpen={vi.fn()}
      />,
    )

    expect(screen.queryByText('NoDate')).not.toBeInTheDocument()
    expect(screen.getByText(/No releases due/i)).toBeInTheDocument()
  })

  it('drops releases that are not in progress even when a due date is set', () => {
    const today = new Date()
    render(
      <ShippingThisWeek
        releases={[
          makeRelease({ id: 'done', name: 'Done', stage: 'released', dueAt: today.toISOString() }),
        ]}
        onOpen={vi.fn()}
      />,
    )

    expect(screen.queryByText('Done')).not.toBeInTheDocument()
    expect(screen.getByText(/No releases due/i)).toBeInTheDocument()
  })
})

/**
 * The compliance-pack rail must not invent audit evidence.
 *
 * `CompliancePacks` derived its rows from release *stage* alone and rendered:
 *
 *     4.2 MB · sha256:d6b975f7…            [Download]
 *
 * Confirmed on the live deployment. Neither value came from a pack:
 *
 *   * `sha256:` was `fakeSha(id)` — a 32-bit `hash*31 + charCode` over the
 *     release UUID, zero-padded to 8 hex chars. Not SHA-256, not computed over
 *     any archive, and identical every render because it only depends on the id.
 *   * `4.2 MB` was a string literal, shown for every released row.
 *   * `GET /api/v1/releases/{id}/compliance-packs` returned `[]` for the very
 *     release displaying that checksum. No pack existed at all.
 *   * Both buttons were `<button type="button">` with no `onClick` — inert.
 *
 * Why this is the worst place for a placeholder: the feature's entire premise
 * is "sealed with a tamper-evident SHA-256 checksum chain". A fabricated
 * `sha256:` beside a release name, in a panel titled "Compliance packs", is
 * exactly the pixel an auditor would screenshot. The app already ships the
 * honest implementation — `CompliancePackPanel` lists real packs and downloads
 * real bytes with the real `manifest_sha256` — so this rail was a decorative
 * mock sitting next to working code.
 *
 * The rail legitimately cannot list real packs: it is client-derived from the
 * `DerivedRelease[]` the portfolio already has, and packs are a per-release
 * fetch. So it becomes what it can honestly be — a pointer to the releases
 * eligible for a pack, which opens the real panel.
 */
describe('CompliancePacks — no fabricated audit evidence', () => {
  const released = makeRelease({ stage: 'released', dueAt: null, id: 'rel-released', name: 'Checkout' })

  it('renders no checksum, because it has not read any pack', () => {
    render(<CompliancePacks releases={[released]} onOpen={vi.fn()} />)
    expect(
      screen.queryByText(/sha256:/i),
      'a SHA-256 is being displayed for a pack this component never fetched',
    ).toBeNull()
  })

  it('renders no file size, because it has not read any pack', () => {
    render(<CompliancePacks releases={[released]} onOpen={vi.fn()} />)
    expect(
      screen.queryByText(/\d+(\.\d+)?\s*(KB|MB|GB)/i),
      'a pack size is being displayed for a pack this component never fetched',
    ).toBeNull()
  })

  it('does not claim a pack is pending for an undecided release', () => {
    const inProgress = makeRelease({ stage: 'in_progress', dueAt: null, id: 'rel-wip' })
    render(<CompliancePacks releases={[inProgress]} onOpen={vi.fn()} />)
    expect(screen.queryByText(/pack pending/i)).toBeNull()
  })

  it('its action opens the release, where the real pack panel lives', async () => {
    const onOpen = vi.fn()
    render(<CompliancePacks releases={[released]} onOpen={onOpen} />)
    const button = screen.getByRole('button', { name: /pack/i })
    button.click()
    expect(onOpen, 'the action was inert — it had no handler at all').toHaveBeenCalledWith('rel-released')
  })

  it('still shows an honest empty state', () => {
    render(<CompliancePacks releases={[]} onOpen={vi.fn()} />)
    expect(screen.getByText(/no compliance packs yet/i)).toBeTruthy()
  })
})
