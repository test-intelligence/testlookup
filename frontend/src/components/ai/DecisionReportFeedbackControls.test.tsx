import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DecisionClaim, DecisionReportVersion } from '@/services/runIntelligenceService'
import DecisionReportFeedbackControls from './DecisionReportFeedbackControls'
import { submitDecisionReportFeedback } from '@/services/runIntelligenceService'

vi.mock('@/services/runIntelligenceService', async importOriginal => {
  const actual = await importOriginal<typeof import('@/services/runIntelligenceService')>()
  return { ...actual, submitDecisionReportFeedback: vi.fn() }
})

const submit = vi.mocked(submitDecisionReportFeedback)
const reportVersion: DecisionReportVersion = {
  report_id: 'report-1',
  report_version: 2,
  status: 'published',
  generated_at: '2026-08-12T00:00:00Z',
}
const claims: DecisionClaim[] = [{
  claim_id: 'claim-1',
  kind: 'inference',
  text: 'The database is the likely cause.',
  confidence: 0.8,
  confidence_basis: 'Stack trace evidence',
  evidence: [{ id: 'artifact-1', type: 'artifact' }],
  counter_evidence: [],
  source_stage: 'release_risk',
}]

beforeEach(() => {
  submit.mockReset()
  submit.mockResolvedValue({ feedback_id: 'feedback-1', status: 'recorded', report_id: 'report-1', report_version: 2 })
})

describe('DecisionReportFeedbackControls', () => {
  it('records utility feedback against the immutable report version', async () => {
    render(<MemoryRouter><DecisionReportFeedbackControls runId="run-1" reportVersion={reportVersion} claims={claims} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'Useful' }))
    await waitFor(() => expect(submit).toHaveBeenCalledWith('run-1', 'report-1', {
      report_version: 2,
      feedback_kind: 'utility',
      utility_rating: 'useful',
    }))
    expect(screen.getByRole('status')).toHaveTextContent('utility feedback recorded')
  })

  it('requires reason and bound evidence when correcting a claim', async () => {
    render(<MemoryRouter><DecisionReportFeedbackControls runId="run-1" reportVersion={reportVersion} claims={claims} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'claim-1' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Correct value'), { target: { value: 'service timeout' } })
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'The stack trace names the timeout boundary.' } })
    fireEvent.change(screen.getByLabelText('Supporting evidence'), { target: { value: 'artifact-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record correction' }))
    await waitFor(() => expect(submit).toHaveBeenCalledWith('run-1', 'report-1', {
      report_version: 2,
      feedback_kind: 'claim_correction',
      claim_id: 'claim-1',
      correction_type: 'category',
      corrected_value: 'service timeout',
      reason: 'The stack trace names the timeout boundary.',
      evidence_ids: ['artifact-1'],
    }))
  })

  it('keeps keyboard focus in the correction dialog and restores the claim trigger', () => {
    render(<MemoryRouter><DecisionReportFeedbackControls runId="run-1" reportVersion={reportVersion} claims={claims} /></MemoryRouter>)
    const trigger = screen.getByRole('button', { name: 'claim-1' })
    trigger.focus()
    fireEvent.click(trigger)

    const dialog = screen.getByRole('dialog')
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])',
    ))
    expect(dialog.contains(document.activeElement)).toBe(true)

    focusable[0].focus()
    fireEvent.keyDown(focusable[0], { key: 'Tab', shiftKey: true })
    expect(focusable[focusable.length - 1]).toHaveFocus()
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Tab' })
    expect(focusable[0]).toHaveFocus()

    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})
