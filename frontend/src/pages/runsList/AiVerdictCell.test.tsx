/**
 * The AI-verdict cell is presentational (FAIL-04): the page reads every run's
 * verdict in ONE batch request and hands each cell its value. The markup per
 * state is the one the column always drew: a skeleton while the batch loads,
 * a pill that opens the run's Analysis tab, or "—" with the reason as title.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import AiVerdictCell from './AiVerdictCell'
import { NO_VERDICT_TITLE, UNAVAILABLE_RUN_TITLE, type AiVerdict } from './aiVerdict'

function renderCell(verdict: AiVerdict | undefined, isLoading = false) {
  return render(
    <MemoryRouter>
      <AiVerdictCell run={{ id: 'run-7' }} verdict={verdict} isLoading={isLoading} />
    </MemoryRouter>,
  )
}

describe('AiVerdictCell — one state, one markup', () => {
  it('a 56 px skeleton while the batch loads (or before it has an answer)', () => {
    const { container, unmount } = renderCell({ recommendation: 'GO', review: null }, true)
    expect(container.querySelector('[data-ai-verdict="loading"]')).toHaveAttribute('aria-busy', 'true')
    unmount()
    const pending = renderCell(undefined)
    expect(pending.container.querySelector('[data-ai-verdict="loading"]')).not.toBeNull()
  })

  it('a pill that opens the run\'s Analysis tab, the review state under it', () => {
    renderCell({ recommendation: 'NO_GO', review: 'draft' })
    const link = screen.getByRole('link')
    expect(link).toHaveAttribute('href', '/runs/run-7?tab=analysis')
    expect(link).toHaveAttribute('data-ai-verdict', 'NO_GO')
    expect(link).toHaveTextContent(/^No-Godraft$/)
    expect(link).toHaveAttribute('title', expect.stringContaining('awaiting human review'))
  })

  it('"—" with the reason as its title, and no link', () => {
    const { container } = renderCell({ recommendation: null, reason: 'not-summarised' })
    const none = container.querySelector('[data-ai-verdict="none"]') as HTMLElement
    expect(none).toHaveTextContent(/^—$/)
    expect(none).toHaveAttribute('data-ai-verdict-reason', 'not-summarised')
    expect(none).toHaveAttribute('title', NO_VERDICT_TITLE['not-summarised'])
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('a verdict\'s own title wins over the reason\'s default (an unavailable run)', () => {
    const { container } = renderCell({ recommendation: null, reason: 'unavailable', title: UNAVAILABLE_RUN_TITLE })
    const none = container.querySelector('[data-ai-verdict="none"]') as HTMLElement
    expect(none).toHaveAttribute('data-ai-verdict-reason', 'unavailable')
    expect(none).toHaveAttribute('title', 'AI verdict unavailable: the run is not available to you')
  })
})
