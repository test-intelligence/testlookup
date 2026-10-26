/**
 * The Reviewer tab is READ-ONLY: `POST /reviewer-quality` defaults to `auto_disable=True` and can switch a project's
 * second-model reviewer off, so the page offers no control that could post a batch — only the nightly history.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useReviewerQualityRuns } from '@/hooks/useAiEval'
import { PROJECT_FIXTURE_ID, idleAiEvalSwr } from '@/test/aiEvalMocks'
import type { ReviewerQualityRun } from '@/types/aiEval'
import ReviewerQualityTab from './ReviewerQualityTab'
import { pointsText } from './aiEvalModel'

vi.mock('@/hooks/useAiEval', async () => (await import('@/test/aiEvalMocks')).aiEvalHookMocks())

function row(overrides: Partial<ReviewerQualityRun>): ReviewerQualityRun {
  return {
    id: 'rqA1b2C3d4',
    project_id: PROJECT_FIXTURE_ID,
    agent_id: 'agent.reviewer.v1',
    source: 'scheduled',
    status: 'pass',
    window_started_at: '2026-10-18T04:00:00+00:00',
    window_ended_at: '2026-10-25T04:00:00+00:00',
    evaluated_at: '2026-10-25T04:00:00+00:00',
    metrics: [
      { name: 'deterministic_semantic_recall', value: 0.7, n: 40, ci_low: 0.55, ci_high: 0.82, kind: 'ground_truth' },
      { name: 'second_model_semantic_recall', value: 0.74, n: 40, ci_low: 0.59, ci_high: 0.85, kind: 'ground_truth' },
      { name: 'clean_false_flag_rate', value: 0.05, n: 40, ci_low: 0.01, ci_high: 0.16, kind: 'ground_truth' },
      { name: 'human_false_omission_rate', value: null, n: 0, ci_low: null, ci_high: null, kind: 'ground_truth' },
    ],
    regressions: [],
    semantic_sample_counts: { semantic_swap: 20, semantic_drop: 20 },
    clean_sample_count: 40,
    human_outcome_count: 0,
    deterministic_recall: 0.7,
    second_model_recall: 0.74,
    recall_delta: 0.04,
    false_flag_rate: 0.05,
    false_omission_rate: null,
    auto_disable_eligible: false,
    auto_disable_applied: false,
    ...overrides,
  }
}

function renderTab(projectId: string | null) {
  return render(<MemoryRouter><ReviewerQualityTab projectId={projectId} /></MemoryRouter>)
}

const mockRuns = (stub: Parameters<typeof idleAiEvalSwr>[0]) =>
  vi.mocked(useReviewerQualityRuns).mockReturnValue(idleAiEvalSwr(stub) as unknown as ReturnType<typeof useReviewerQualityRuns>)

beforeEach(() => vi.clearAllMocks())

describe('per project only', () => {
  it('asks for a project under All Projects and reads nothing', () => {
    renderTab(null)
    expect(screen.getByText('Select a project')).toBeInTheDocument()
    expect(vi.mocked(useReviewerQualityRuns)).toHaveBeenCalledWith(null)
  })
})

describe('the G3 history, read-only', () => {
  it("shows the newest row's metrics with their 95% bounds and the history table", () => {
    mockRuns({ data: { items: [row({}), row({ id: 'rqB2c3D4e5', source: 'observation_batch', status: 'insufficient_samples', recall_delta: -0.025, auto_disable_applied: true })], total: 2 } })
    renderTab(PROJECT_FIXTURE_ID)
    const det = document.querySelector('[data-reviewer-metric="deterministic_semantic_recall"]') as HTMLElement
    expect(det).toHaveTextContent('70.0%')
    expect(det).toHaveTextContent('95%: 55.0%–82.0% · n 40')
    const omission = document.querySelector('[data-reviewer-metric="human_false_omission_rate"]') as HTMLElement
    expect(omission).toHaveTextContent('—')
    expect(omission).toHaveTextContent('Not measured')

    const first = document.querySelector('[data-reviewer-row="rqA1b2C3d4"]') as HTMLElement
    expect(within(first).getByText('Nightly')).toBeInTheDocument()
    expect(within(first).getByText('PASS')).toBeInTheDocument()
    expect(within(first).getByText('+4.0 pp')).toBeInTheDocument()
    expect(within(first).getByText('Kept')).toBeInTheDocument()
    const second = document.querySelector('[data-reviewer-row="rqB2c3D4e5"]') as HTMLElement
    expect(within(second).getByText('Batch')).toBeInTheDocument()
    expect(within(second).getByText('Not enough evidence')).toBeInTheDocument()
    expect(within(second).getByText('Retired')).toBeInTheDocument()
  })

  it('says it never changes the reviewer and offers no control that could post a batch', () => {
    mockRuns({ data: { items: [row({})], total: 1 } })
    renderTab(PROJECT_FIXTURE_ID)
    expect(screen.getByText("Measured nightly from the reviewer's recorded observations. This page never changes the reviewer's configuration.")).toBeInTheDocument()
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('says when nothing was measured yet, and when the read failed (with Retry)', () => {
    mockRuns({ data: { items: [], total: 0 } })
    const { unmount } = renderTab(PROJECT_FIXTURE_ID)
    expect(screen.getByText('No reviewer-quality measurement recorded for this project yet.')).toBeInTheDocument()
    unmount()
    const mutate = vi.fn()
    mockRuns({ error: new Error('boom'), mutate })
    renderTab(PROJECT_FIXTURE_ID)
    expect(screen.getByText(/Could not load the reviewer-quality history\./)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalled()
  })

  it('signs a difference in percentage points', () => {
    expect(pointsText(0.04)).toBe('+4.0 pp')
    expect(pointsText(-0.025)).toBe('−2.5 pp')
    expect(pointsText(0)).toBe('0.0 pp')
    expect(pointsText(null)).toBe('—')
  })
})
