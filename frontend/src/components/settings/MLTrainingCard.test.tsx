/**
 * The ML classifier card on Settings › AI (W04 AIC-05).
 *
 * States pinned: no model + below the floor (the button explains why it cannot
 * train), can train (Train now -> POST once -> re-read -> Training…), queued,
 * running (the old model line stays), trained (the model line, no "last
 * attempt"), a failed last attempt (failure tone), a refused press (409 message
 * in the card), nightly training switched off, and a non-admin (no button, the
 * sample and model lines only).
 *
 * The REAL `useMLStatus` hook runs under an isolated SWRConfig; only the
 * service is mocked.
 */
import { createElement } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

import type { MLStatusRead } from '@/services/appSettingsService'

const mockGetMLStatus = vi.fn()
const mockRetrainML = vi.fn()
vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: {
    getMLStatus: () => mockGetMLStatus(),
    retrainML: () => mockRetrainML(),
  },
}))

import MLTrainingCard from './MLTrainingCard'
import { modelLine, refusalMessage, samplesLine } from './mlTrainingCopy'

const IDLE = { state: 'idle', task_id: null, queued_at: null, started_at: null, triggered_by: null }
const ago = (ms: number) => new Date(Date.now() - ms).toISOString()

function status(overrides: Partial<MLStatusRead> = {}): MLStatusRead {
  return {
    model_available: false,
    version: null,
    accuracy: null,
    trained_at: null,
    model_sample_count: null,
    available_samples: 642,
    min_training_samples: 200,
    human_labels: 57,
    human_label_floor: 50,
    maturity: 'not_trained',
    deps_installed: true,
    retrain_enabled: true,
    can_train: true,
    cannot_train_reason: null,
    cannot_train_message: null,
    training: { ...IDLE },
    last_run: null,
    ...overrides,
  }
}

const BELOW_FLOOR = status({
  available_samples: 142, human_labels: 12, can_train: false,
  cannot_train_reason: 'insufficient_samples', cannot_train_message: 'Need 200 labelled samples, have 142.',
})
const TRAINED = status({
  model_available: true, version: '20261014_031000', accuracy: 0.84, trained_at: ago(5_000),
  model_sample_count: 642, maturity: 'human_calibrated',
})

function renderCard(isAdmin = true) {
  return render(
    createElement(
      SWRConfig,
      { value: { provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false } },
      createElement(MLTrainingCard, { isAdmin }),
    ),
  )
}

const card = () => document.querySelector('[data-ml-training-card]') as HTMLElement
const part = (attr: string) => document.querySelector(`[data-${attr}]`) as HTMLElement | null

describe('MLTrainingCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('below the floor: no model yet, and the button says why it cannot train', async () => {
    mockGetMLStatus.mockResolvedValue(BELOW_FLOOR)
    renderCard()

    await waitFor(() => expect(part('ml-samples')).toHaveTextContent(
      'Samples 142 / 200 · human labels 12 · no model yet',
    ))
    const button = screen.getByRole('button', { name: 'Train now' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', 'Need 200 labelled samples, have 142.')
    expect(part('ml-cannot-train')).toHaveTextContent('Need 200 labelled samples, have 142.')
    expect(part('ml-model-line')).toBeNull()
    expect(part('ml-schedule')).toHaveTextContent(
      'A model is trained nightly at 03:10 UTC once enough labelled samples exist, or on demand.',
    )
    expect(card()).toHaveAttribute('data-ml-training-state', 'idle')
  })

  it('can train: Train now posts once, re-reads the status and shows Training…', async () => {
    mockGetMLStatus
      .mockResolvedValueOnce(status())
      .mockResolvedValue(status({ training: { ...IDLE, state: 'queued', task_id: 't-1', queued_at: ago(0), triggered_by: 'user:alice' } }))
    mockRetrainML.mockResolvedValue({ task_id: 't-1', state: 'queued', already_running: false })
    renderCard()

    const button = await screen.findByRole('button', { name: 'Train now' })
    expect(button).toBeEnabled()
    expect(part('ml-cannot-train')).toBeNull()
    fireEvent.click(button)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Training…' })).toBeDisabled())
    expect(mockRetrainML).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(card()).toHaveAttribute('data-ml-training-state', 'queued'))
    expect(mockGetMLStatus.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('running: the button reads Training…, and the old model line stays', async () => {
    mockGetMLStatus.mockResolvedValue({
      ...TRAINED,
      training: { ...IDLE, state: 'running', task_id: 't-2', queued_at: ago(60_000), started_at: ago(30_000), triggered_by: 'beat' },
      last_run: { status: 'below_threshold', message: 'Accuracy 0.61 is below the deployment threshold 0.80; the previous model is kept.',
                  finished_at: ago(86_400_000), triggered_by: 'beat', accuracy: 0.61, version: null, sample_count: 600 },
    })
    renderCard()

    await waitFor(() => expect(screen.getByRole('button', { name: 'Training…' })).toBeDisabled())
    expect(card()).toHaveAttribute('data-ml-training-state', 'running')
    expect(part('ml-model-line')).toHaveTextContent('Model 20261014_031000')
    expect(part('ml-last-run')).toBeNull() // never shown while training
  })

  it('trained: the model line with accuracy and age, and no "last attempt"', async () => {
    mockGetMLStatus.mockResolvedValue({
      ...TRAINED,
      last_run: { status: 'trained', message: 'Model 20261014_031000 trained on 642 samples (accuracy 0.84).',
                  finished_at: ago(5_000), triggered_by: 'user:alice', accuracy: 0.84, version: '20261014_031000', sample_count: 642 },
    })
    renderCard()

    await waitFor(() => expect(part('ml-model-line')).toHaveTextContent(
      'Model 20261014_031000 · accuracy 0.84 · trained just now',
    ))
    expect(part('ml-model-line')).toHaveAttribute('title', TRAINED.trained_at)
    expect(part('ml-samples')).toHaveTextContent('Samples 642 / 200 · human labels 57 · model 20261014_031000')
    expect(part('ml-last-run')).toBeNull()
    expect(screen.getByRole('button', { name: 'Train now' })).toBeEnabled()
  })

  it('a failed last attempt is shown in the failure tone', async () => {
    mockGetMLStatus.mockResolvedValue(status({
      last_run: { status: 'error', message: 'disk full', finished_at: ago(2 * 3_600_000 + 60_000),
                  triggered_by: 'beat', accuracy: null, version: null, sample_count: null },
    }))
    renderCard()

    await waitFor(() => expect(part('ml-last-run')).toHaveTextContent('Last attempt 2h ago: disk full'))
    expect(part('ml-last-run')?.className).toContain('status-failed')
    expect(part('ml-last-run')).toHaveAttribute('data-ml-last-run-status', 'error')
  })

  it('a not-trained last attempt is muted, with its message', async () => {
    mockGetMLStatus.mockResolvedValue(status({
      last_run: { status: 'insufficient_data', message: 'Need 200 samples, have 180.', finished_at: ago(10 * 60_000),
                  triggered_by: 'beat', accuracy: null, version: null, sample_count: 180 },
    }))
    renderCard()

    await waitFor(() => expect(part('ml-last-run')).toHaveTextContent('Last attempt 10m ago: Need 200 samples, have 180.'))
    expect(part('ml-last-run')?.className).not.toContain('status-failed')
  })

  it('a refused press (409) renders the server message in the card', async () => {
    mockGetMLStatus.mockResolvedValue(status())
    mockRetrainML.mockRejectedValue({
      response: { status: 409, data: { detail: { code: 'insufficient_samples', message: 'Need 200 labelled samples, have 199.', have: 199, need: 200 } } },
    })
    renderCard()

    fireEvent.click(await screen.findByRole('button', { name: 'Train now' }))

    await waitFor(() => expect(part('ml-cannot-train')).toHaveTextContent('Need 200 labelled samples, have 199.'))
    expect(part('ml-cannot-train')?.className).toContain('status-failed')
  })

  it('says when nightly training is switched off on the server', async () => {
    mockGetMLStatus.mockResolvedValue(status({ retrain_enabled: false }))
    renderCard()

    await waitFor(() => expect(part('ml-schedule')).toHaveTextContent(
      'Nightly training is switched off on this server (ML_RETRAIN_ENABLED); Train now still works.',
    ))
  })

  it('a non-admin sees the sample and model lines only', async () => {
    mockGetMLStatus.mockResolvedValue({
      ...TRAINED,
      can_train: false, cannot_train_reason: 'insufficient_samples', cannot_train_message: 'Need 200 labelled samples, have 142.',
      last_run: { status: 'error', message: 'disk full', finished_at: ago(60_000), triggered_by: 'beat',
                  accuracy: null, version: null, sample_count: null },
    })
    renderCard(false)

    await waitFor(() => expect(part('ml-samples')).toBeInTheDocument())
    expect(part('ml-model-line')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
    expect(part('ml-train-now')).toBeNull()
    expect(part('ml-cannot-train')).toBeNull()
    expect(part('ml-last-run')).toBeNull()
    expect(part('ml-schedule')).toBeNull()
  })

  it('says only that the read failed when the status cannot be fetched', async () => {
    mockGetMLStatus.mockRejectedValue(new Error('boom'))
    renderCard()

    await waitFor(() => expect(card()).toHaveTextContent('Could not read the ML training status from the API.'))
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('MLTrainingCard copy helpers', () => {
  it('formats the sample and model lines', () => {
    expect(samplesLine(BELOW_FLOOR)).toBe('Samples 142 / 200 · human labels 12 · no model yet')
    expect(modelLine({ ...TRAINED, accuracy: null, trained_at: null })).toBe('Model 20261014_031000 · accuracy —')
  })

  it('reads the refusal from a 409 object detail, a 503 string detail, or falls back', () => {
    expect(refusalMessage({ response: { data: { detail: { message: 'scikit-learn and joblib are not installed on this server.' } } } }))
      .toBe('scikit-learn and joblib are not installed on this server.')
    expect(refusalMessage({ response: { data: { detail: 'Training queue unavailable' } } })).toBe('Training queue unavailable')
    expect(refusalMessage(new Error('network'))).toBe('Training could not be started. Try again shortly.')
  })
})
