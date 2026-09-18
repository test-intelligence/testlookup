import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import GenerationReviewPanel from './GenerationReviewPanel'

vi.mock('@/services/ragGenerationService', () => ({
  ragService: { acceptCase: vi.fn(), rejectCase: vi.fn() },
}))

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
