/**
 * The Gate tab. Two shipped defects are pinned here: the result was compared with `PASS` while the server answers
 * `pass` / `fail` / `insufficient_samples` (every run toasted "No baseline configured"), and Duplicate Detection asked
 * `ClusterAgent` while the agent stack gates `DefectPromotionAgent`.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useEvalRuns, useSetBaseline } from '@/hooks/useAiEval'
import { type EvalBaseline, type EvalGateResult, runPreReleaseGate } from '@/services/aiEvalService'
import { REPLAY_RUN_FIXTURE, REPLAY_RUN_ID } from '@/test/aiEvalMocks'
import EvalGateTab from './EvalGateTab'

vi.mock('@/hooks/useAiEval', async () => (await import('@/test/aiEvalMocks')).aiEvalHookMocks())
vi.mock('@/services/aiEvalService', () => ({ runPreReleaseGate: vi.fn() }))

const runGate = vi.mocked(runPreReleaseGate)

function gateResult(overrides: Partial<EvalGateResult>): EvalGateResult {
  return {
    status: 'pass',
    task_type: 'classification',
    agent_name: 'AnalysisAgent',
    current_metrics: { accuracy: 0.9, precision: 0.88, recall: 0.86, f1_score: 0.87, total: 12, correct: 11 },
    baseline_metrics: null,
    rule_results: [{ rule: 'min_accuracy', passed: true, detail: 'Accuracy 0.9000 >= 0.80 threshold' }],
    evaluated_at: '2026-10-25T09:00:00+00:00',
    metric_kind: 'label_agreement',
    ...overrides,
  }
}

function Harness({ isAdmin = true, baselines = [], initial = null }: { isAdmin?: boolean; baselines?: EvalBaseline[]; initial?: EvalGateResult | null }) {
  const [result, setResult] = useState<EvalGateResult | null>(initial)
  return (
    <EvalGateTab baselines={baselines} loading={false} isAdmin={isAdmin} result={result} onResult={setResult} onBaselineSet={vi.fn()} />
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useEvalRuns).mockImplementation((q) => ({
    data: q.task_type === 'classification' ? { items: [REPLAY_RUN_FIXTURE], total: 1 } : { items: [], total: 0 },
    error: undefined,
    isLoading: false,
    mutate: vi.fn(),
  }) as unknown as ReturnType<typeof useEvalRuns>)
})

describe('the four gates come from DEFAULT_GATES', () => {
  it('gates duplicate detection with DefectPromotionAgent (the agent stack default)', async () => {
    runGate.mockResolvedValue(gateResult({ task_type: 'duplicate_detection', agent_name: 'DefectPromotionAgent' }))
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Gate: Duplicate Detection' }))
    await waitFor(() => expect(runGate).toHaveBeenCalledWith('duplicate_detection', 'DefectPromotionAgent', undefined, undefined))
  })

  it('asks every gate for its completed replay runs, and sends the chosen one as the candidate', async () => {
    runGate.mockResolvedValue(gateResult({ metric_kind: 'replay', candidate_run_id: REPLAY_RUN_ID }))
    render(<Harness />)
    expect(vi.mocked(useEvalRuns)).toHaveBeenCalledWith(
      { task_type: 'classification', metric_kind: 'replay', status: 'completed', limit: 20 },
      true,
    )
    fireEvent.change(screen.getByLabelText('Candidate run', { selector: '#gate-candidate-classification' }), {
      target: { value: REPLAY_RUN_ID },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Gate: Classification' }))
    await waitFor(() => expect(runGate).toHaveBeenCalledWith('classification', 'AnalysisAgent', undefined, REPLAY_RUN_ID))
  })
})

describe('the result card reads the server verdict', () => {
  it.each([
    ['pass', 'PASS', 'ui-success'],
    ['fail', 'FAIL', 'ui-danger'],
    ['insufficient_samples', 'Not enough evidence', 'ui-warning'],
  ])('%s → %s, coloured', async (status, label, token) => {
    runGate.mockResolvedValue(gateResult({ status }))
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Gate: Classification' }))
    const card = await screen.findByTestId('gate-result')
    const chip = card.querySelector('[data-verdict]') as HTMLElement
    expect(chip).toHaveTextContent(label)
    expect(chip.className).toContain(`--${token}-bg`)
    expect(screen.queryByText(/No baseline configured/)).toBeNull()
  })

  it('lists a failed no_regression rule with its detail', async () => {
    runGate.mockResolvedValue(gateResult({
      status: 'fail',
      metric_kind: 'replay',
      candidate_run_id: REPLAY_RUN_ID,
      rule_results: [{ rule: 'no_regression', passed: false, detail: 'Accuracy dropped 12.0% (max allowed: 5.0%)' }],
    }))
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Gate: Classification' }))
    const rule = (await screen.findByTestId('gate-result')).querySelector('[data-rule="no_regression"]') as HTMLElement
    expect(rule).toHaveAttribute('data-passed', 'false')
    expect(within(rule).getByText('Accuracy dropped 12.0% (max allowed: 5.0%)')).toBeInTheDocument()
  })

  it("shows a refused gate's sentence in place", async () => {
    runGate.mockRejectedValue({ response: { data: { detail: { code: 'candidate_run_not_complete', message: 'Only a completed replay run can be gated; this one is partial.' } } } })
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Gate: Classification' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Only a completed replay run can be gated; this one is partial.')
  })
})

describe('Set as baseline sets a baseline of the result’s own metric kind', () => {
  it('a replay result names its run', async () => {
    render(<Harness initial={gateResult({ metric_kind: 'replay', candidate_run_id: REPLAY_RUN_ID })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Set as baseline' }))
    const confirm = screen.getByTestId('gate-baseline-confirm')
    expect(confirm).toHaveTextContent(/^Make this run the Classification baseline for AnalysisAgent\?/)
    fireEvent.click(within(confirm).getByRole('button', { name: 'Set as baseline' }))
    const save = vi.mocked(useSetBaseline).mock.results[0].value
    await waitFor(() => expect(save).toHaveBeenCalledWith({ task_type: 'classification', agent_name: 'AnalysisAgent', eval_run_id: REPLAY_RUN_ID }))
    expect(await screen.findByRole('status')).toHaveTextContent('Baseline set for AnalysisAgent.')
  })

  it("a legacy result sends today's body (a legacy baseline)", async () => {
    render(<Harness initial={gateResult({ metric_kind: 'label_agreement' })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Set as baseline' }))
    fireEvent.click(within(screen.getByTestId('gate-baseline-confirm')).getByRole('button', { name: 'Set as baseline' }))
    const save = vi.mocked(useSetBaseline).mock.results[0].value
    await waitFor(() => expect(save).toHaveBeenCalledWith({ task_type: 'classification', agent_name: 'AnalysisAgent' }))
  })
})

describe('a QA lead reads, an admin acts', () => {
  it('disables every gate and Set as baseline with the reason', () => {
    render(<Harness isAdmin={false} initial={gateResult({})} />)
    for (const name of ['Gate: Classification', 'Gate: Root Cause', 'Gate: Duplicate Detection', 'Gate: Release Decision', 'Set as baseline']) {
      const button = screen.getByRole('button', { name })
      expect(button).toBeDisabled()
      expect(button).toHaveAttribute('title', 'Needs an administrator.')
    }
  })
})

describe('active baselines', () => {
  it('says how to set one when there is none', () => {
    render(<Harness />)
    expect(screen.getByText('No baselines yet. Set one from a candidate run (Datasets) or a gate result.')).toBeInTheDocument()
  })

  it('labels each baseline with its metric kind', () => {
    const base = {
      task_type: 'classification', agent_name: 'AnalysisAgent', model_name: null, baseline_accuracy: 0.9, baseline_f1: 0.88,
      min_accuracy: 0.8, min_f1: 0.75, max_regression_pct: 5, created_at: null,
    }
    render(<Harness baselines={[
      { ...base, id: 'blA1b2C3d4', prompt_version: 'v1' },
      { ...base, id: 'blB2c3D4e5', prompt_version: 'replay-rules', metric_kind: 'replay', eval_run_id: REPLAY_RUN_ID },
    ]} />)
    const legacy = document.querySelector('[data-baseline="blA1b2C3d4"]') as HTMLElement
    const replay = document.querySelector('[data-baseline="blB2c3D4e5"]') as HTMLElement
    expect(within(legacy).getByText('Legacy label agreement')).toBeInTheDocument()
    expect(within(replay).getByText('Replay')).toBeInTheDocument()
  })
})
