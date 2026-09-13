/**
 * E8.5: the review banner on AI report surfaces.
 *
 * It states the review status the report's envelope carries, and says nothing
 * when there is no true statement to make: deterministic content, or a payload
 * without a review block. A banner must never imply a review happened.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) =>
    selector({ activeProjectId: 'proj-1', projects: [], setActiveProject: vi.fn() }),
  ),
}))

import ReviewBanner from './ReviewBanner'
import type { ReviewEnvelopeFields } from '@/types/review'

const renderBanner = (envelope: ReviewEnvelopeFields | null | undefined) =>
  render(
    <MemoryRouter>
      <ReviewBanner envelope={envelope} />
    </MemoryRouter>,
  )

describe('ReviewBanner', () => {
  it('marks a pending report as a draft with its disclaimer and a way to the queue', () => {
    renderBanner({
      requires_human_review: true,
      review: { state: 'pending_review', message: 'Human review required before use.', review_id: 'r1' },
      ai_disclaimer: 'AI-generated content. Verify before acting.',
    })
    const banner = screen.getByTestId('review-banner')
    expect(banner).toHaveAttribute('data-review-state', 'pending_review')
    expect(screen.getByText('Draft: awaiting human review')).toBeInTheDocument()
    expect(screen.getByText('AI-generated content. Verify before acting.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open review queue/ })).toHaveAttribute('href', '/reviews')
  })

  it('shows an accepted report as reviewed, without the draft link', () => {
    renderBanner({
      review: { state: 'accepted', message: 'Reviewed and accepted.', reviewed_at: '2026-09-12T11:00:00Z' },
    })
    expect(screen.getByText('Reviewed and accepted')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Open review queue/ })).not.toBeInTheDocument()
  })

  it('warns that a rejected report should not be relied on', () => {
    renderBanner({ review: { state: 'rejected', message: 'Do not rely on it.' } })
    expect(screen.getByTestId('review-banner')).toHaveAttribute('data-review-state', 'rejected')
  })

  it.each([
    ['deterministic content', { review: { state: 'not_applicable' as const, message: 'Not AI-generated.' } }],
    ['a payload without a review block', { executive_summary: 'x' } as ReviewEnvelopeFields],
    ['no payload', undefined],
  ])('renders nothing for %s', (_label, envelope) => {
    renderBanner(envelope)
    expect(screen.queryByTestId('review-banner')).not.toBeInTheDocument()
  })
})
