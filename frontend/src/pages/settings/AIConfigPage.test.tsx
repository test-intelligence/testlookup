/**
 * Hermetic tests for the AI settings page's live model status (PMF US-13.2).
 *
 * The page used to describe the fallback chain with hardcoded strings
 * ("ML if trained, else LLM if available, else Rules") and never query
 * runtime state, so an air-gapped install whose model pack never landed
 * looked identical to a healthy one. What this file pins:
 *
 *   - the chain renders from the API, per tier, with its reason;
 *   - the tier that will actually run is marked Active — and for a PINNED
 *     mode that is rules, not "first available";
 *   - a missing model shows the exact pull/side-load remedy;
 *   - unreachable Ollama is rendered as a connectivity problem and never
 *     as a missing model (no pull recipe offered);
 *   - a failed model-status fetch says only that the CHECK failed;
 *   - the pre-existing config form still saves unchanged.
 *
 * The service layer is mocked; the REAL SWR hook runs under an isolated
 * SWRConfig so the page's fetch wiring is exercised.
 */
import { createElement } from 'react'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

import type { AIConfigRead, AIModelStatusRead, MLStatusRead } from '@/services/appSettingsService'
import { helpTopicParam } from '@/components/help/helpTopics'

// ── mocks ───────────────────────────────────────────────────────────────────

const mockGetAIConfig = vi.fn()
const mockUpdateAIConfig = vi.fn()
const mockGetModelStatus = vi.fn()
const mockGetMLStatus = vi.fn()
const mockRetrainML = vi.fn()

vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: {
    getAIConfig: () => mockGetAIConfig(),
    updateAIConfig: (p: unknown) => mockUpdateAIConfig(p),
    getAIModelStatus: () => mockGetModelStatus(),
    getMLStatus: () => mockGetMLStatus(),
    retrainML: () => mockRetrainML(),
  },
}))

vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isAdmin: true }) }))

const mockToastSuccess = vi.fn()
const mockToastError = vi.fn()
vi.mock('react-hot-toast', () => ({
  default: {
    success: (...a: unknown[]) => mockToastSuccess(...a),
    error: (...a: unknown[]) => mockToastError(...a),
  },
}))

// Forwards the template props the page must pass (UX redesign P5).
vi.mock('@/components/ui/PageHeader', () => ({
  default: ({ title, compact, helpTopic }: { title: string; compact?: boolean; helpTopic?: string }) =>
    createElement('h1', { 'data-compact': compact ? 'true' : 'false', 'data-help-topic': helpTopic }, title),
}))
vi.mock('@/components/ui/LoadingSpinner', () => ({ default: () => createElement('div', null, 'loading') }))
vi.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => createElement('a', null, children),
}))

// ── fixtures ────────────────────────────────────────────────────────────────

function baseConfig(overrides: Partial<AIConfigRead> = {}): AIConfigRead {
  return {
    llm_provider: 'ollama',
    llm_model: 'qwen2.5:7b',
    llm_temperature: 0.1,
    llm_max_tokens: 4096,
    ai_offline_mode: true,
    ai_offline_mode_source: 'override',
    ai_offline_mode_env_pinned: false,
    embedding_provider: 'ollama',
    embedding_model: 'nomic-embed-text',
    ai_confidence_threshold: 80,
    ai_confidence_threshold_source: 'env_default',
    ai_timeout_seconds: 300,
    ai_timeout_seconds_source: 'env_default',
    deep_investigation_enabled: true,
    deep_investigation_enabled_source: 'env_default',
    finetune_enabled: false,
    finetune_enabled_source: 'env_default',
    openai_key_set: false,
    google_key_set: false,
    anthropic_key_set: false,
    openrouter_key_set: false,
    analysis_mode: 'auto',
    analysis_mode_source: 'env_default',
    ml_min_training_samples: 200,
    ml_model_available: false,
    ml_model_accuracy: null,
    ml_training_sample_count: 12,
    ml_human_label_count: 0,
    ml_human_label_floor: 50,
    ml_maturity: 'not_trained',
    knowledge_rag_enabled: false,
    ...overrides,
  }
}

function mlStatus(overrides: Partial<MLStatusRead> = {}): MLStatusRead {
  return {
    model_available: false,
    version: null,
    accuracy: null,
    trained_at: null,
    model_sample_count: null,
    available_samples: 142,
    min_training_samples: 200,
    human_labels: 12,
    human_label_floor: 50,
    maturity: 'not_trained',
    deps_installed: true,
    retrain_enabled: true,
    can_train: false,
    cannot_train_reason: 'insufficient_samples',
    cannot_train_message: 'Need 200 labelled samples, have 142.',
    training: { state: 'idle', task_id: null, queued_at: null, started_at: null, triggered_by: null },
    last_run: null,
    ...overrides,
  }
}

function healthyStatus(overrides: Partial<AIModelStatusRead> = {}): AIModelStatusRead {
  return {
    ollama_reachable: true,
    ollama_error: null,
    ollama_base_url: 'http://ollama:11434',
    installed_models: ['qwen2.5:7b', 'nomic-embed-text'],
    required: [
      { name: 'qwen2.5:7b', purpose: 'llm', present: true, remedy: null },
      { name: 'nomic-embed-text', purpose: 'embedding', present: true, remedy: null },
    ],
    fallback_chain: [
      { mode: 'ml', available: false, reason: 'No trained ML classifier on disk — auto mode falls through to the next tier.' },
      { mode: 'llm', available: true, reason: null },
      { mode: 'rules', available: true, reason: null },
    ],
    offline_mode: true,
    llm_provider: 'ollama',
    analysis_mode: 'auto',
    checked_at: '2026-08-03T00:00:00+00:00',
    ...overrides,
  }
}

const PULL_REMEDY =
  "Model 'qwen2.5:7b' is not installed on Ollama. Pull it: docker compose exec ollama ollama pull qwen2.5:7b — " +
  'or, on an air-gapped host with no registry egress, side-load it (see the offline model pack guide).'

function missingModelStatus(): AIModelStatusRead {
  return healthyStatus({
    installed_models: ['nomic-embed-text'],
    required: [
      { name: 'qwen2.5:7b', purpose: 'llm', present: false, remedy: PULL_REMEDY },
      { name: 'nomic-embed-text', purpose: 'embedding', present: true, remedy: null },
    ],
    fallback_chain: [
      { mode: 'ml', available: false, reason: 'No trained ML classifier on disk — auto mode falls through to the next tier.' },
      { mode: 'llm', available: false, reason: PULL_REMEDY, reason_code: 'model_missing' },
      { mode: 'rules', available: true, reason: null },
    ],
  })
}

function unreachableStatus(): AIModelStatusRead {
  return healthyStatus({
    ollama_reachable: false,
    ollama_error: 'All connection attempts failed',
    installed_models: [],
    required: [
      { name: 'qwen2.5:7b', purpose: 'llm', present: false, remedy: null },
      { name: 'nomic-embed-text', purpose: 'embedding', present: false, remedy: null },
    ],
    fallback_chain: [
      { mode: 'ml', available: false, reason: 'No trained ML classifier on disk — auto mode falls through to the next tier.' },
      {
        mode: 'llm',
        available: false,
        reason:
          'Ollama is unreachable at http://ollama:11434 (All connection attempts failed) — this is a connectivity problem, not a missing model.',
        reason_code: 'unreachable',
      },
      { mode: 'rules', available: true, reason: null },
    ],
  })
}

async function renderPage() {
  const { default: AIConfigPage } = await import('./AIConfigPage')
  return render(
    createElement(
      SWRConfig,
      {
        value: {
          provider: () => new Map(),
          dedupingInterval: 0,
          shouldRetryOnError: false,
          revalidateOnFocus: false,
        },
      },
      createElement(AIConfigPage),
    ),
  )
}

function chainRow(mode: string): HTMLElement {
  return screen.getByTestId(`chain-${mode}`)
}

// ── tests ───────────────────────────────────────────────────────────────────

describe('AIConfigPage — live model status', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetAIConfig.mockResolvedValue(baseConfig())
    mockUpdateAIConfig.mockResolvedValue(baseConfig())
    mockGetModelStatus.mockResolvedValue(healthyStatus())
    mockGetMLStatus.mockResolvedValue(mlStatus())
  })

  it('renders every tier of the live chain with its availability and reason', async () => {
    await renderPage()

    await waitFor(() => expect(screen.getByText('Model Availability & Fallback Chain')).toBeInTheDocument())

    expect(chainRow('ml')).toHaveTextContent('Machine Learning')
    expect(chainRow('ml')).toHaveTextContent('unavailable')
    expect(chainRow('ml')).toHaveTextContent('No trained ML classifier on disk')

    expect(chainRow('llm')).toHaveTextContent('available')
    expect(chainRow('rules')).toHaveTextContent('available')

    // Auto mode takes the first available tier — LLM here, not rules.
    expect(chainRow('llm')).toHaveTextContent('Active')
    expect(chainRow('rules')).not.toHaveTextContent('Active')
  })

  it('shows reachability and the installed-model count, not just a mode list', async () => {
    await renderPage()

    await waitFor(() => expect(screen.getByText(/Ollama reachable at/)).toBeInTheDocument())
    expect(screen.getByText(/2 models installed/)).toBeInTheDocument()
    expect(screen.getByText(/Offline mode: on/)).toBeInTheDocument()
  })

  it('marks rules Active when a pinned mode cannot run', async () => {
    mockGetAIConfig.mockResolvedValue(baseConfig({ analysis_mode: 'llm' }))
    mockGetModelStatus.mockResolvedValue({ ...missingModelStatus(), analysis_mode: 'llm' })
    await renderPage()

    await waitFor(() => expect(chainRow('rules')).toHaveTextContent('Active'))
    expect(chainRow('llm')).not.toHaveTextContent('Active')
  })

  it('names the pull / side-load remedy when a required model is missing', async () => {
    mockGetModelStatus.mockResolvedValue(missingModelStatus())
    await renderPage()

    await waitFor(() => expect(screen.getByTestId('required-llm')).toHaveTextContent('Missing'))
    expect(screen.getByTestId('required-llm')).toHaveTextContent('ollama pull qwen2.5:7b')
    expect(screen.getByTestId('required-llm')).toHaveTextContent('side-load')
    expect(screen.getByTestId('required-embedding')).toHaveTextContent('Installed')

    // The mode radio carries the same verdict.
    expect(screen.getByText('Model Missing')).toBeInTheDocument()
    // Reachable-but-incomplete is NOT a connectivity failure.
    expect(screen.queryByText(/Ollama unreachable/)).not.toBeInTheDocument()
  })

  it('distinguishes an unreachable backend from a missing model', async () => {
    mockGetModelStatus.mockResolvedValue(unreachableStatus())
    await renderPage()

    await waitFor(() => expect(screen.getByText(/Ollama unreachable at/)).toBeInTheDocument())
    // Said twice on purpose: in the banner and as the LLM tier's reason.
    expect(screen.getAllByText(/connectivity problem/).length).toBeGreaterThan(0)
    // No pull recipe while the daemon is down — it cannot be the fix yet.
    expect(screen.queryByText(/ollama pull/)).not.toBeInTheDocument()
    expect(screen.getByText('Unreachable')).toBeInTheDocument()
    expect(screen.queryByText('Model Missing')).not.toBeInTheDocument()

    // Rules still shown as the terminal fallback.
    expect(chainRow('rules')).toHaveTextContent('available')
  })

  it('reports a missing cloud API key as a key problem, not a missing model', async () => {
    // Regression (homelab, 2026-08-16): the badge was derived from
    // `ollama_reachable`, so every non-Ollama failure read "Model Missing".
    // An operator with an unset OpenRouter key was told to install a model.
    mockGetModelStatus.mockResolvedValue(
      healthyStatus({
        llm_provider: 'openrouter',
        offline_mode: false,
        fallback_chain: [
          { mode: 'ml', available: false, reason: 'No trained ML classifier on disk.' },
          {
            mode: 'llm',
            available: false,
            reason: "No API key configured for provider 'openrouter'.",
            reason_code: 'no_api_key',
          },
          { mode: 'rules', available: true, reason: null },
        ],
      }),
    )
    await renderPage()

    await waitFor(() => expect(screen.getByText('No API Key')).toBeInTheDocument())
    expect(screen.queryByText('Model Missing')).not.toBeInTheDocument()
    expect(screen.queryByText('Unreachable')).not.toBeInTheDocument()
  })

  it('shows the LLM tier Ready when a cloud provider is configured', async () => {
    // The user-visible goal of the OpenRouter work: a configured hosted
    // provider must read as usable, not as a broken local install.
    mockGetModelStatus.mockResolvedValue(
      healthyStatus({
        llm_provider: 'openrouter',
        offline_mode: false,
        fallback_chain: [
          { mode: 'ml', available: false, reason: 'No trained ML classifier on disk.' },
          { mode: 'llm', available: true, reason: null, reason_code: 'ok' },
          { mode: 'rules', available: true, reason: null },
        ],
      }),
    )
    await renderPage()

    await waitFor(() => expect(screen.getByText('Ready')).toBeInTheDocument())
    expect(screen.queryByText('Model Missing')).not.toBeInTheDocument()
    expect(chainRow('llm')).toHaveTextContent('available')
  })

  it('falls back to a neutral label for an unrecognised reason code', async () => {
    // Never invent a specific claim we cannot support.
    mockGetModelStatus.mockResolvedValue(
      healthyStatus({
        fallback_chain: [
          { mode: 'ml', available: false, reason: 'No trained ML classifier on disk.' },
          { mode: 'llm', available: false, reason: 'Something new.', reason_code: 'brand_new_code' },
          { mode: 'rules', available: true, reason: null },
        ],
      }),
    )
    await renderPage()

    await waitFor(() => expect(screen.getByText('Unavailable')).toBeInTheDocument())
    expect(screen.queryByText('Model Missing')).not.toBeInTheDocument()
  })

  it('says only that the CHECK failed when model status cannot be fetched', async () => {
    mockGetModelStatus.mockRejectedValue(new Error('boom'))
    await renderPage()

    await waitFor(() =>
      expect(screen.getByText(/Could not read model status from the API/)).toBeInTheDocument(),
    )
    expect(screen.queryByText(/Ollama unreachable/)).not.toBeInTheDocument()
    expect(screen.queryByText('Missing')).not.toBeInTheDocument()
  })

  it('re-checks on demand', async () => {
    await renderPage()

    // Wait on the BUTTON, not on a mock call count.
    //
    // The page renders a loading placeholder until the CONFIG fetch resolves.
    // The old assertion waited for `mockGetModelStatus` to have been *called* --
    // which happens immediately, before any promise resolves -- so it proved
    // nothing about the DOM. Under CI load the tree was still the placeholder
    // and the very next line threw:
    //
    //   TestingLibraryElementError: Unable to find an element with the text:
    //   Re-check
    //
    // Reproduced deterministically by delaying `mockGetAIConfig` by 80ms.
    // Waiting for the element the next line clicks is both the correct
    // synchronisation point and a stronger assertion: it proves the button
    // actually rendered.
    const recheck = await screen.findByText('Re-check')
    expect(mockGetModelStatus).toHaveBeenCalledTimes(1)

    fireEvent.click(recheck)

    await waitFor(() => expect(mockGetModelStatus).toHaveBeenCalledTimes(2))
  })

  it('keeps the existing configuration form working', async () => {
    await renderPage()
    await waitFor(() => expect(screen.getByDisplayValue('qwen2.5:7b')).toBeInTheDocument())

    fireEvent.change(screen.getByDisplayValue('qwen2.5:7b'), { target: { value: 'qwen2.5:14b' } })
    fireEvent.click(screen.getByText('Save Configuration'))

    await waitFor(() => expect(mockUpdateAIConfig).toHaveBeenCalledTimes(1))
    expect(mockUpdateAIConfig.mock.calls[0][0]).toMatchObject({
      llm_model: 'qwen2.5:14b',
      llm_provider: 'ollama',
      analysis_mode: 'auto',
    })
    expect(mockToastSuccess).toHaveBeenCalledWith('AI configuration saved')
  })
})

/** The settings page template (UX redesign P5, plan item 4). */
describe('AIConfigPage — layout', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetAIConfig.mockResolvedValue(baseConfig())
    mockGetModelStatus.mockResolvedValue(healthyStatus())
    mockGetMLStatus.mockResolvedValue(mlStatus())
  })

  it('renders the compact header with the route help topic', async () => {
    await renderPage()
    const heading = await screen.findByRole('heading', { level: 1, name: 'AI Configuration' })
    expect(heading).toHaveAttribute('data-compact', 'true')
    expect(heading).toHaveAttribute('data-help-topic', helpTopicParam('/settings/ai'))
  })

  it('pairs the cards in two columns at >= 1280 px instead of a max-w-2xl column', async () => {
    const { container } = await renderPage()
    await waitFor(() => expect(screen.getByText('Model Availability & Fallback Chain')).toBeInTheDocument())

    expect(container.querySelector('.max-w-2xl')).toBeNull()
    const grid = container.querySelector('[data-settings-form-grid]') as HTMLElement
    expect(grid).toHaveClass('grid', 'grid-cols-1', 'xl:grid-cols-2')
    // Row by row: mode | live availability, LLM | embedding, pipeline | RAG, then the keys across.
    const cards = Array.from(grid.children)
    expect(cards.map((c) => c.querySelector('h3')?.textContent)).toEqual([
      'Analysis Engine',
      'Model Availability & Fallback Chain',
      'LLM Provider',
      'Embedding',
      'Pipeline Settings',
      'Knowledge-Grounded Generation',
      'Cloud API Keys',
    ])
    expect(cards.filter((c) => c.classList.contains('xl:col-span-2')).map((c) => c.querySelector('h3')?.textContent))
      .toEqual(['Cloud API Keys'])

    // Save stays under the grid, inside the same form.
    const save = screen.getByRole('button', { name: /save configuration/i })
    expect(grid.contains(save)).toBe(false)
    expect(save.closest('form')).toBe(grid.closest('form'))
  })

  it('keeps the reachability line one text run in the half-width card', async () => {
    await renderPage()
    const line = await screen.findByText(/Ollama reachable at/)
    // The text sits in a span, not as bare text nodes of the flex row.
    expect(line.tagName).toBe('SPAN')
    expect(line).toHaveTextContent('Ollama reachable at http://ollama:11434 — 2 models installed')
  })
})

/**
 * AI_OFFLINE_MODE is a hard ceiling in the environment (security fix
 * 2026-08-03): the stored setting may tighten it, never loosen it. The page's
 * job is to never accept a click it knows the backend will refuse.
 */
describe('AIConfigPage — offline mode pinned by the environment', () => {
  function offlineToggle(): HTMLInputElement {
    return screen.getByLabelText(/Offline Mode/i) as HTMLInputElement
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockUpdateAIConfig.mockResolvedValue(baseConfig())
    mockGetModelStatus.mockResolvedValue(healthyStatus())
    mockGetMLStatus.mockResolvedValue(mlStatus())
  })

  it('disables the toggle and names the env var + remedy when env-pinned', async () => {
    mockGetAIConfig.mockResolvedValue(
      baseConfig({ ai_offline_mode: true, ai_offline_mode_source: 'env', ai_offline_mode_env_pinned: true }),
    )
    await renderPage()

    await waitFor(() => expect(offlineToggle()).toBeDisabled())
    expect(offlineToggle()).toBeChecked()

    const note = screen.getByTestId('offline-mode-pinned-note')
    expect(note).toHaveTextContent('AI_OFFLINE_MODE')
    expect(note).toHaveTextContent(/cannot be turned off from this page/i)
    // The remedy is the environment variable, not a UI action.
    expect(note).toHaveTextContent(/AI_OFFLINE_MODE=false/)
    expect(screen.getByText('Pinned by environment')).toBeInTheDocument()
  })

  it('leaves the toggle editable when the environment permits egress', async () => {
    mockGetAIConfig.mockResolvedValue(
      baseConfig({ ai_offline_mode: true, ai_offline_mode_source: 'override', ai_offline_mode_env_pinned: false }),
    )
    await renderPage()

    await waitFor(() => expect(offlineToggle()).toBeInTheDocument())
    expect(offlineToggle()).not.toBeDisabled()
    expect(screen.queryByTestId('offline-mode-pinned-note')).not.toBeInTheDocument()
  })

  it('surfaces the failure if a disable request reaches the backend anyway', async () => {
    /*
     * The disabled input is the first line of defence, but it is only a DOM
     * attribute — a scripted client, or an older tab, can still PUT
     * ai_offline_mode=false. The backend answers 409; what must NOT happen is
     * the page swallowing it and looking saved. (The shared Axios interceptor
     * additionally toasts the backend's own detail, which names the env var.)
     */
    mockGetAIConfig.mockResolvedValue(
      baseConfig({ ai_offline_mode: true, ai_offline_mode_source: 'env', ai_offline_mode_env_pinned: true }),
    )
    mockUpdateAIConfig.mockRejectedValue({ response: { status: 409 } })
    await renderPage()

    await waitFor(() => expect(offlineToggle()).toBeDisabled())
    fireEvent.click(screen.getByText('Save Configuration'))

    await waitFor(() => expect(mockToastError).toHaveBeenCalled())
    expect(mockToastSuccess).not.toHaveBeenCalled()
    // The toggle still reports the truth: offline, pinned, read-only.
    expect(offlineToggle()).toBeChecked()
    expect(offlineToggle()).toBeDisabled()
  })
})

/**
 * W04 AIC-01/02/04/05: the runtime now obeys what this page stores. Each value
 * says where it comes from ("Set here" / "Server default") and when it applies;
 * the Timeout floor is 5 s; the ML card replaces the hard-coded "/ 200" banner.
 */
describe('AIConfigPage — config sources, the timeout floor and the ML card (W04)', () => {
  function hint(key: string): HTMLElement {
    const el = document.querySelector(`[data-config-source-key="${key}"]`)
    if (!el) throw new Error(`no source hint for ${key}`)
    return el as HTMLElement
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockGetAIConfig.mockResolvedValue(baseConfig())
    mockUpdateAIConfig.mockResolvedValue(baseConfig())
    mockGetModelStatus.mockResolvedValue(healthyStatus())
    mockGetMLStatus.mockResolvedValue(mlStatus())
  })

  it('says "Server default" with when each value applies, while nothing is stored', async () => {
    await renderPage()
    await screen.findByText('Deep Investigation')

    expect(hint('deep_investigation_enabled')).toHaveTextContent('Server default · Applies within a minute.')
    expect(hint('finetune_enabled')).toHaveTextContent(
      'Server default · Applies within a minute. Switches the export-triggered tasks only; training itself needs a GPU or a hosted provider.',
    )
    expect(hint('ai_confidence_threshold')).toHaveTextContent('Server default · Applies to new pipelines.')
    expect(hint('analysis_mode')).toHaveTextContent(/^Server default$/)
    expect(screen.getByTestId('timeout-hint')).toHaveTextContent(
      'Server default · Applies to every LLM call; an agent may set a lower limit. Applies to new pipelines; a running pipeline keeps its value.',
    )
    expect(screen.getByTestId('timeout-hint')).toHaveAttribute('data-config-source-key', 'ai_timeout_seconds')
    for (const key of ['deep_investigation_enabled', 'finetune_enabled', 'ai_confidence_threshold', 'ai_timeout_seconds', 'analysis_mode']) {
      expect(hint(key)).toHaveAttribute('data-source', 'env_default')
    }
  })

  it('says "Set here" for each value the API reports as stored', async () => {
    mockGetAIConfig.mockResolvedValue(baseConfig({
      deep_investigation_enabled_source: 'ai_config',
      finetune_enabled_source: 'ai_config',
      ai_confidence_threshold_source: 'ai_config',
      ai_timeout_seconds_source: 'ai_config',
      analysis_mode_source: 'ai_config',
    }))
    await renderPage()
    await screen.findByText('Deep Investigation')

    for (const key of ['deep_investigation_enabled', 'finetune_enabled', 'ai_confidence_threshold', 'ai_timeout_seconds', 'analysis_mode']) {
      expect(hint(key)).toHaveTextContent(/^Set here/)
      expect(hint(key)).toHaveAttribute('data-source', 'ai_config')
    }
  })

  it('accepts a 5 s timeout (the floor was 30)', async () => {
    await renderPage()
    const timeout = (await screen.findByLabelText('Timeout (seconds)')) as HTMLInputElement
    expect(timeout).toHaveAttribute('min', '5')
    expect(timeout).toHaveAttribute('max', '1800')

    fireEvent.change(timeout, { target: { value: '5' } })
    fireEvent.click(screen.getByText('Save Configuration'))
    await waitFor(() => expect(mockUpdateAIConfig).toHaveBeenCalledTimes(1))
    expect(mockUpdateAIConfig.mock.calls[0][0]).toMatchObject({ ai_timeout_seconds: 5 })
  })

  it('unticking Deep Investigation saves false, and the hint then reads "Set here"', async () => {
    mockUpdateAIConfig.mockResolvedValue(baseConfig({
      deep_investigation_enabled: false,
      deep_investigation_enabled_source: 'ai_config',
    }))
    await renderPage()
    const deep = (await screen.findByLabelText('Deep Investigation')) as HTMLInputElement
    expect(deep).toBeChecked()
    expect(hint('deep_investigation_enabled')).toHaveTextContent(/^Server default/)

    fireEvent.click(deep)
    fireEvent.click(screen.getByText('Save Configuration'))

    await waitFor(() => expect(mockUpdateAIConfig).toHaveBeenCalledTimes(1))
    expect(mockUpdateAIConfig.mock.calls[0][0]).toMatchObject({ deep_investigation_enabled: false })
    await waitFor(() => expect(hint('deep_investigation_enabled')).toHaveTextContent('Set here · Applies within a minute.'))
    // The ML card is re-read with the rest of the page's caches.
    await waitFor(() => expect(mockGetMLStatus).toHaveBeenCalledTimes(2))
  })

  it('mounts the ML card under the mode radios, replacing the hard-coded "/ 200" banner', async () => {
    mockGetAIConfig.mockResolvedValue(baseConfig({ analysis_mode: 'ml' }))
    const { container } = await renderPage()
    const card = await waitFor(() => {
      const el = container.querySelector('[data-ml-training-card]')
      if (!el?.querySelector('[data-ml-samples]')) throw new Error('card not ready')
      return el as HTMLElement
    })
    expect(card.querySelector('[data-ml-samples]')).toHaveTextContent(
      'Samples 142 / 200 · human labels 12 · no model yet',
    )
    // Inside the Analysis Engine card, after the radios and the mode's source hint.
    const engine = screen.getByText('Analysis Engine').closest('.card') as HTMLElement
    expect(engine.contains(card)).toBe(true)
    const radios = engine.querySelectorAll('input[name="analysis_mode"]')
    expect(radios[radios.length - 1].compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.queryByText(/ML model not yet trained/)).not.toBeInTheDocument()
    expect(container.textContent).not.toMatch(/\/ 200 labeled samples/)
  })

  it('the ML radio badge follows the card once a model is trained', async () => {
    // GET /settings/ai was read before Train now; the card's live status is newer.
    mockGetAIConfig.mockResolvedValue(baseConfig({ ml_model_available: false }))
    mockGetMLStatus.mockResolvedValue(mlStatus({
      model_available: true, version: '20261014_031000', accuracy: 0.84,
      trained_at: new Date().toISOString(), model_sample_count: 642, maturity: 'human_calibrated',
      available_samples: 642, human_labels: 57, can_train: true, cannot_train_reason: null, cannot_train_message: null,
    }))
    await renderPage()

    await waitFor(() => expect(screen.getByText('Ready (84% accuracy)')).toBeInTheDocument())
    expect(screen.queryByText('Not Trained')).not.toBeInTheDocument()
  })
})
