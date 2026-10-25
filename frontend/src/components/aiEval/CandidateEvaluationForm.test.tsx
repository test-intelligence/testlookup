/**
 * Evaluate candidate: the tiers the server offers for the dataset's task type, each unavailable one disabled with the
 * server's sentence; the replay needs one project (its LLM budget and release policy); the POST body is exactly the
 * candidate shown — the llm tier's model and prompt version are assertions, never injections.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useCandidateOptions, useEvaluateCandidate } from '@/hooks/useAiEval'
import type { AIEvalDataset } from '@/services/aiEvalService'
import { DATASET_FIXTURE_ID, PROJECT_FIXTURE_ID, REPLAY_RUN_ID, idleAiEvalSwr } from '@/test/aiEvalMocks'
import type { CandidateOptions } from '@/types/aiEval'
import CandidateEvaluationForm, { OFFLINE_NOTE } from './CandidateEvaluationForm'

vi.mock('@/hooks/useAiEval', async () => (await import('@/test/aiEvalMocks')).aiEvalHookMocks())

const PROMPT_TAG = 'fast_classifier_system@v1:abc+react_triage@v3:def'
const ML_REASON = 'No ML model is trained yet; the ML tier would answer with the rules engine.'

function options(overrides: Partial<CandidateOptions> = {}): CandidateOptions {
  return {
    tiers: [
      { tier: 'rules', available: true },
      { tier: 'ml', available: false, reason: ML_REASON },
      { tier: 'llm', available: true, provider: 'ollama', model: 'llama3.1:8b-instruct' },
    ],
    prompt_version: PROMPT_TAG,
    offline_mode: false,
    max_items: 200,
    supported_tiers_by_task: {
      classification: ['rules', 'ml', 'llm'],
      root_cause: ['rules', 'ml', 'llm'],
      duplicate_detection: ['rules'],
      release_decision: ['rules'],
    },
    ...overrides,
  }
}

function dataset(overrides: Partial<AIEvalDataset> = {}): AIEvalDataset {
  return {
    id: DATASET_FIXTURE_ID, name: 'Golden: Failure Classification', description: null, task_type: 'classification',
    item_count: 12, is_active: true, created_by: null, created_at: '2026-10-25T09:00:00+00:00', ...overrides,
  }
}

function renderForm({ projectId = PROJECT_FIXTURE_ID as string | null, ds = dataset(), onQueued = vi.fn() } = {}) {
  render(<CandidateEvaluationForm dataset={ds} projectId={projectId} onClose={vi.fn()} onQueued={onQueued} />)
  return { onQueued }
}

const evaluate = () => vi.mocked(useEvaluateCandidate)() as unknown as ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useCandidateOptions).mockReturnValue(idleAiEvalSwr({ data: options() }) as unknown as ReturnType<typeof useCandidateOptions>)
})

describe('the tiers come from the candidate options', () => {
  it('offers the task type’s tiers, an unavailable one disabled with the server’s sentence, the local model named', () => {
    renderForm()
    expect(screen.getByRole('radio', { name: 'Rules' })).toBeChecked()
    expect(screen.getByRole('radio', { name: /^ML/ })).toBeDisabled()
    expect(screen.getByText(ML_REASON)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Local model · ollama · llama3.1:8b-instruct' })).toBeEnabled()
  })

  it('offers only the rules tier for duplicate detection', () => {
    renderForm({ ds: dataset({ task_type: 'duplicate_detection', name: 'Golden: Duplicates', item_count: 8 }) })
    expect(screen.getAllByRole('radio').map((r) => (r as HTMLInputElement).value)).toEqual(['rules'])
  })

  it('says when offline mode limits the candidates', () => {
    vi.mocked(useCandidateOptions).mockReturnValue(idleAiEvalSwr({ data: options({ offline_mode: true }) }) as unknown as ReturnType<typeof useCandidateOptions>)
    renderForm()
    expect(screen.getByText(OFFLINE_NOTE)).toBeInTheDocument()
  })

  it('says it could not load the options, with Retry', () => {
    const mutate = vi.fn()
    vi.mocked(useCandidateOptions).mockReturnValue(idleAiEvalSwr({ error: new Error('boom'), mutate }) as unknown as ReturnType<typeof useCandidateOptions>)
    renderForm()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalled()
    expect(screen.getByText(/Could not load the candidate options\./)).toBeInTheDocument()
  })
})

describe('the replay needs one project', () => {
  it('under All Projects says why and keeps Start disabled', () => {
    renderForm({ projectId: null })
    expect(screen.getByTestId('candidate-project-note')).toHaveTextContent(
      'Choose a project in the top bar: the replay uses its LLM budget and release policy.',
    )
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled()
  })
})

describe('the POST body is the candidate shown', () => {
  it('a rules replay sends the tier only, the project and the item count', async () => {
    const { onQueued } = renderForm()
    expect((screen.getByRole('spinbutton') as HTMLInputElement).value).toBe('12')
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))
    await waitFor(() => expect(evaluate()).toHaveBeenCalledWith(DATASET_FIXTURE_ID, {
      candidate: { tier: 'rules' }, project_id: PROJECT_FIXTURE_ID, limit: 5,
    }))
    await waitFor(() => expect(onQueued).toHaveBeenCalledWith(REPLAY_RUN_ID))
  })

  it('a local-model replay asserts the model and the prompt version it showed', async () => {
    renderForm()
    fireEvent.click(screen.getByRole('radio', { name: 'Local model · ollama · llama3.1:8b-instruct' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))
    await waitFor(() => expect(evaluate()).toHaveBeenCalledWith(DATASET_FIXTURE_ID, {
      candidate: { tier: 'llm', model: 'llama3.1:8b-instruct', prompt_version: PROMPT_TAG },
      project_id: PROJECT_FIXTURE_ID,
      limit: 12,
    }))
  })

  it('refuses an item count past the cap without posting', () => {
    renderForm()
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '13' } })
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled()
  })

  it("shows a refusal's sentence in the form", async () => {
    evaluate().mockRejectedValueOnce({ response: { data: { detail: { code: 'candidate_offline_blocked', message: 'Offline mode is on and openai is not a local provider: only rules, ML or a local model can be evaluated.' } } } })
    renderForm()
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Offline mode is on and openai is not a local provider')
  })
})
