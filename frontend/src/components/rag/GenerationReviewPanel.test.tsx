import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ragService } from '@/services/ragGenerationService'
import type { RagGenerateResponse } from '@/types/rag-generation'
import GenerationReviewPanel from './GenerationReviewPanel'

vi.mock('@/services/ragGenerationService', () => ({
  ragService: { acceptCase: vi.fn(), rejectCase: vi.fn() },
}))

const citation = (caseIndex: number, title: string) => ({
  case_index: caseIndex, vector_id: `v-${title}`, source_id: `s-${title}`, source_title: title,
  section_heading: null, chunk_text_preview: null, relevance_score: null, canonical_url: null,
})

const TWO_CASES: RagGenerateResponse = {
  batch_id: 'batch-1',
  generation_mode: 'grounded',
  test_cases: [
    { title: 'Coupon applies once', description: null, steps: [], test_type: 'functional', priority: 'high', severity: 'major' },
    { title: 'Tax rounds per line', description: null, steps: [], test_type: 'functional', priority: 'medium', severity: 'major' },
  ],
  citations: [citation(0, 'Checkout requirements'), citation(0, 'Cart persistence RFC'), citation(1, 'Tax rules')],
  coverage_summary: null,
  gaps_noted: [],
  created_ids: ['tc-1', 'tc-2'],
}

/** A generated case's card: the bordered block holding its heading. */
const cardOf = (title: string) => screen.getByRole('heading', { name: title, level: 4 }).closest('.space-y-2') as HTMLElement

// W27b PAN-05 (contract §2.1 assertion 4, flow L11): the citations open in the page, right after that case's card.
describe('GenerationReviewPanel citations in the page', () => {
  it("a case's Citations open right after its card; another case's replace them; Close closes them; nothing is read", () => {
    render(<GenerationReviewPanel result={TWO_CASES} />)
    expect(screen.queryByRole('complementary')).toBeNull()

    fireEvent.click(within(cardOf('Coupon applies once')).getByRole('button', { name: 'Citations' }))
    let cites = screen.getByRole('complementary', { name: 'Citations (2)' })
    expect(cardOf('Coupon applies once').nextElementSibling).toBe(cites)
    expect(within(cites).getByText('Checkout requirements')).toBeInTheDocument()
    expect(within(cites).getByText('Cart persistence RFC')).toBeInTheDocument()
    expect(document.querySelector('.fixed')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(within(cardOf('Tax rounds per line')).getByRole('button', { name: 'Citations' }))
    cites = screen.getByRole('complementary', { name: 'Citations (1)' })
    expect(screen.getAllByRole('complementary')).toHaveLength(1)
    expect(cardOf('Tax rounds per line').nextElementSibling).toBe(cites)
    expect(within(cites).getByText('Tax rules')).toBeInTheDocument()
    expect(screen.queryByText('Checkout requirements')).toBeNull()

    fireEvent.click(within(cites).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(ragService.acceptCase).not.toHaveBeenCalled()
    expect(ragService.rejectCase).not.toHaveBeenCalled()
  })

  it('Escape inside the citations closes them and focus returns to that Citations button', () => {
    render(<GenerationReviewPanel result={TWO_CASES} />)
    const button = within(cardOf('Coupon applies once')).getByRole('button', { name: 'Citations' })
    button.focus()
    fireEvent.click(button)
    const cites = screen.getByRole('complementary', { name: 'Citations (2)' })
    expect(cites.contains(document.activeElement)).toBe(true)

    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' })
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(button).toHaveFocus()
  })
})

describe('GenerationReviewPanel evidence state', () => {
  it('marks an uncited grounded case as unsupported', () => {
    render(<GenerationReviewPanel result={{
      batch_id: 'batch-1',
      generation_mode: 'grounded',
      test_cases: [{
        title: 'Suggested case', description: null, steps: [],
        test_type: 'functional', priority: 'medium', severity: 'major',
      }],
      citations: [], coverage_summary: null, gaps_noted: [], created_ids: [],
    }} />)
    expect(screen.getByText('No verified citations')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Citations' })).not.toBeInTheDocument()
  })
})
