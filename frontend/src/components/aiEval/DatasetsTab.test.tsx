/**
 * The Datasets tab: legacy runs keep their label ("legacy label-agreement metric", never recomputed), a replay shows
 * its progress while it runs and its honest numbers when it ends, and every write is an admin's — a QA lead sees the
 * controls disabled with the reason, never a 403 toast.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useCandidateOptions, useEvalRunItems, useEvalRuns, useSetBaseline } from '@/hooks/useAiEval'
import { type AIEvalDataset, type AIEvalRun, runEvaluation, runPreReleaseGate } from '@/services/aiEvalService'
import {
  DATASET_FIXTURE_ID,
  ITEMS_FIXTURE,
  PROJECT_FIXTURE_ID,
  REPLAY_RUN_FIXTURE,
  REPLAY_RUN_ID,
  RUNNING_RUN_FIXTURE,
  idleAiEvalSwr,
} from '@/test/aiEvalMocks'
import DatasetsTab from './DatasetsTab'
import { NOT_COMPLETE_TITLE } from './EvalRunsList'

vi.mock('@/hooks/useAiEval', async () => (await import('@/test/aiEvalMocks')).aiEvalHookMocks())
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/services/aiEvalService', () => ({
  createDatasetFromFeedback: vi.fn(),
  runEvaluation: vi.fn(),
  runPreReleaseGate: vi.fn(),
  seedGoldenDatasets: vi.fn(),
}))

const DATASETS: AIEvalDataset[] = [
  { id: DATASET_FIXTURE_ID, name: 'Golden: Failure Classification', description: null, task_type: 'classification', item_count: 12, is_active: true, created_by: null, created_at: '2026-10-25T09:00:00+00:00' },
  { id: 'dsB2c3D4e5', name: 'Golden: Release Decisions', description: null, task_type: 'release_decision', item_count: 9, is_active: true, created_by: null, created_at: '2026-10-25T09:00:00+00:00' },
]

const LEGACY_RUN: AIEvalRun = {
  id: 'rnC3d4E5f6', dataset_id: DATASET_FIXTURE_ID, model_name: 'AnalysisAgent', task_type: 'classification',
  precision: 0.7, recall: 0.7, f1_score: 0.7, accuracy: 0.75, agreement_rate: null, total_items: 12, correct_items: 9,
  fallback_used: false, evaluated_at: '2026-10-20T09:00:00+00:00', duration_ms: 40,
}

function renderTab({ isAdmin = true, projectId = PROJECT_FIXTURE_ID as string | null } = {}) {
  const props = { onDatasetsChanged: vi.fn(), onEvaluated: vi.fn(), onBaselineSet: vi.fn() }
  render(<DatasetsTab datasets={DATASETS} loading={false} isAdmin={isAdmin} projectId={projectId} {...props} />)
  return props
}

function runs(items: AIEvalRun[]) {
  vi.mocked(useEvalRuns).mockReturnValue(idleAiEvalSwr({ data: { items, total: items.length } }) as unknown as ReturnType<typeof useEvalRuns>)
}

const row = (id: string) => document.querySelector(`[data-dataset-row="${id}"]`) as HTMLElement
const runRow = (id: string) => document.querySelector(`[data-run-row="${id}"]`) as HTMLElement

beforeEach(() => {
  vi.clearAllMocks()
  runs([REPLAY_RUN_FIXTURE, LEGACY_RUN])
})

describe('runs open lazily under their dataset', () => {
  it('reads nothing until Runs opens, then lists replay and legacy runs with their labels', () => {
    renderTab()
    expect(vi.mocked(useEvalRuns)).not.toHaveBeenCalled()
    fireEvent.click(within(row(DATASET_FIXTURE_ID)).getByRole('button', { name: 'Runs' }))
    expect(vi.mocked(useEvalRuns)).toHaveBeenCalledWith({ dataset_id: DATASET_FIXTURE_ID, limit: 20 }, true)

    const replay = runRow(REPLAY_RUN_ID)
    expect(within(replay).getByText('Replay · Rules')).toBeInTheDocument()
    expect(within(replay).getByText('Completed')).toBeInTheDocument()
    expect(replay).toHaveTextContent('Acc 81.8%')
    expect(replay).toHaveTextContent('11 scored · 1 unscored · 0 errors')
    expect(within(runRow(LEGACY_RUN.id)).getByText('Legacy label-agreement metric')).toBeInTheDocument()
    expect(within(runRow(LEGACY_RUN.id)).queryByRole('button', { name: 'Gate' })).toBeNull()
  })

  it('shows a running replay’s progress and no actions until it ends', () => {
    runs([RUNNING_RUN_FIXTURE])
    renderTab()
    fireEvent.click(within(row(DATASET_FIXTURE_ID)).getByRole('button', { name: 'Runs' }))
    const running = runRow(RUNNING_RUN_FIXTURE.id)
    expect(within(running).getByText('Running')).toBeInTheDocument()
    expect(running.querySelector('[data-run-progress]')).toHaveTextContent('4 of 12 items')
    expect(within(running).queryByRole('button', { name: 'Items' })).toBeNull()
  })

  it('never shows 0% for a replay that scored nothing', () => {
    runs([{ ...REPLAY_RUN_FIXTURE, accuracy: 0, f1_score: 0, scored_items: 0, unscored_items: 12, measured: false }])
    renderTab()
    fireEvent.click(within(row(DATASET_FIXTURE_ID)).getByRole('button', { name: 'Runs' }))
    const replay = runRow(REPLAY_RUN_ID)
    expect(replay).toHaveTextContent('Acc —')
    expect(replay).toHaveTextContent('Not measured')
    expect(replay).not.toHaveTextContent('0.0%')
  })

  it("shows a stopped run's sentence and keeps Gate / Set as baseline for a completed replay only", () => {
    runs([{ ...REPLAY_RUN_FIXTURE, status: 'partial', error_code: 'worker_lost', error_text: 'The evaluation stopped part-way (no progress for 15 minutes); the scored items are kept.' }])
    renderTab()
    fireEvent.click(within(row(DATASET_FIXTURE_ID)).getByRole('button', { name: 'Runs' }))
    const partial = runRow(REPLAY_RUN_ID)
    expect(partial).toHaveTextContent('The evaluation stopped part-way')
    for (const name of ['Gate', 'Set as baseline']) {
      expect(within(partial).getByRole('button', { name })).toBeDisabled()
      expect(within(partial).getByRole('button', { name })).toHaveAttribute('title', NOT_COMPLETE_TITLE)
    }
    expect(within(partial).getByRole('button', { name: 'Items' })).toBeEnabled()
  })
})

describe('an admin acts on a completed replay', () => {
  it('opens its items inline', () => {
    vi.mocked(useEvalRunItems).mockReturnValue(idleAiEvalSwr({ data: ITEMS_FIXTURE }) as unknown as ReturnType<typeof useEvalRunItems>)
    renderTab()
    fireEvent.click(within(row(DATASET_FIXTURE_ID)).getByRole('button', { name: 'Runs' }))
    fireEvent.click(within(runRow(REPLAY_RUN_ID)).getByRole('button', { name: 'Items' }))
    expect(vi.mocked(useEvalRunItems)).toHaveBeenCalledWith(REPLAY_RUN_ID, { limit: 25, offset: 0 }, true)
    expect(runRow(REPLAY_RUN_ID).querySelectorAll('[data-item-row]')).toHaveLength(4)
  })

  it('gates the run (its id is the candidate) and shows the result inline', async () => {
    vi.mocked(runPreReleaseGate).mockResolvedValue({
      status: 'fail', task_type: 'classification', agent_name: 'AnalysisAgent', current_metrics: null, baseline_metrics: null,
      rule_results: [{ rule: 'no_regression', passed: false, detail: 'Accuracy dropped 9.0% (max allowed: 5.0%)' }],
      evaluated_at: '2026-10-25T09:00:00+00:00', metric_kind: 'replay', candidate_run_id: REPLAY_RUN_ID,
    })
    renderTab()
    fireEvent.click(within(row(DATASET_FIXTURE_ID)).getByRole('button', { name: 'Runs' }))
    fireEvent.click(within(runRow(REPLAY_RUN_ID)).getByRole('button', { name: 'Gate' }))
    await waitFor(() => expect(runPreReleaseGate).toHaveBeenCalledWith('classification', 'AnalysisAgent', undefined, REPLAY_RUN_ID))
    const card = await within(runRow(REPLAY_RUN_ID)).findByTestId('gate-result')
    expect(card.querySelector('[data-verdict]')).toHaveTextContent('FAIL')
    expect(card.querySelector('[data-rule="no_regression"]')).toHaveAttribute('data-passed', 'false')
  })

  it('sets it as the baseline after an inline confirm', async () => {
    const props = renderTab()
    fireEvent.click(within(row(DATASET_FIXTURE_ID)).getByRole('button', { name: 'Runs' }))
    fireEvent.click(within(runRow(REPLAY_RUN_ID)).getByRole('button', { name: 'Set as baseline' }))
    const confirm = screen.getByTestId('run-baseline-confirm')
    expect(confirm).toHaveTextContent('Make this run the Classification baseline for AnalysisAgent? The gate compares later candidates against its accuracy and F1.')
    fireEvent.click(within(confirm).getByRole('button', { name: 'Set as baseline' }))
    const save = vi.mocked(useSetBaseline).mock.results[0].value
    await waitFor(() => expect(save).toHaveBeenCalledWith({ task_type: 'classification', agent_name: 'AnalysisAgent', eval_run_id: REPLAY_RUN_ID }))
    await waitFor(() => expect(props.onBaselineSet).toHaveBeenCalled())
  })
})

describe('the dataset actions', () => {
  it('Run label agreement is the legacy POST', async () => {
    vi.mocked(runEvaluation).mockResolvedValue(LEGACY_RUN)
    const props = renderTab()
    fireEvent.click(within(row(DATASET_FIXTURE_ID)).getByRole('button', { name: 'Run label agreement' }))
    await waitFor(() => expect(runEvaluation).toHaveBeenCalledWith(DATASET_FIXTURE_ID))
    await waitFor(() => expect(props.onEvaluated).toHaveBeenCalled())
  })

  it('Evaluate candidate opens the in-page form under its row, one at a time', () => {
    vi.mocked(useCandidateOptions).mockReturnValue(idleAiEvalSwr({
      data: { tiers: [{ tier: 'rules', available: true }], offline_mode: false, max_items: 200, supported_tiers_by_task: {} },
    }) as unknown as ReturnType<typeof useCandidateOptions>)
    renderTab()
    fireEvent.click(within(row(DATASET_FIXTURE_ID)).getByRole('button', { name: 'Evaluate candidate' }))
    const form = within(row(DATASET_FIXTURE_ID)).getByRole('region', { name: 'Evaluate candidate' })
    expect(form.closest('[data-in-page-row]')).not.toBeNull()
    fireEvent.click(within(row('dsB2c3D4e5')).getByRole('button', { name: 'Evaluate candidate' }))
    expect(within(row(DATASET_FIXTURE_ID)).queryByRole('region', { name: 'Evaluate candidate' })).toBeNull()
    expect(within(row('dsB2c3D4e5')).getByRole('region', { name: 'Evaluate candidate' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('a QA lead sees every write disabled with the reason, and no admin read is made', () => {
    renderTab({ isAdmin: false })
    for (const name of ['Generate from Feedback', 'Seed Golden Datasets']) {
      expect(screen.getByRole('button', { name })).toBeDisabled()
      expect(screen.getByRole('button', { name })).toHaveAttribute('title', 'Needs an administrator.')
    }
    const first = row(DATASET_FIXTURE_ID)
    for (const name of ['Run label agreement', 'Evaluate candidate']) {
      expect(within(first).getByRole('button', { name })).toBeDisabled()
      expect(within(first).getByRole('button', { name })).toHaveAttribute('title', 'Needs an administrator.')
    }
    fireEvent.click(within(first).getByRole('button', { name: 'Runs' }))
    expect(within(runRow(REPLAY_RUN_ID)).getByRole('button', { name: 'Items' })).toBeDisabled()
    expect(vi.mocked(useCandidateOptions)).not.toHaveBeenCalled()
    expect(vi.mocked(useEvalRunItems)).not.toHaveBeenCalled()
  })
})
