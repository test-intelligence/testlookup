/**
 * Settings › AI › Embedding (W12 SRCH-02, contract §2.4 / §4.2): the `data-embedding-index` block says which
 * embedder is in use, what is configured but not applied, a re-embed's progress and a failure; Re-embed now /
 * Retry for an admin only; the Provider select with OpenAI disabled offline; the warning before a Save that
 * re-embeds; a changed embedder's 422 under the card; `GET …/embedding-index` asked only while building.
 *
 * The service is mocked; the REAL `useEmbeddingIndex` (SWR) runs under an isolated SWRConfig.
 */
import { createElement } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

import type { AIConfigRead, EmbeddingGeneration, EmbeddingIndex } from '@/services/appSettingsService'

const mockGetAIConfig = vi.fn()
const mockUpdateAIConfig = vi.fn()
const mockGetModelStatus = vi.fn()
const mockGetMLStatus = vi.fn()
const mockGetEmbeddingIndex = vi.fn()
const mockRebuild = vi.fn()

vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: {
    getAIConfig: () => mockGetAIConfig(),
    updateAIConfig: (p: unknown, opts?: unknown) => mockUpdateAIConfig(p, opts),
    getAIModelStatus: () => mockGetModelStatus(),
    getMLStatus: () => mockGetMLStatus(),
    retrainML: vi.fn(),
    getEmbeddingIndex: () => mockGetEmbeddingIndex(),
    rebuildEmbeddingIndex: () => mockRebuild(),
  },
}))

let isAdmin = true
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isAdmin }) }))

const mockToastSuccess = vi.fn()
const mockToastError = vi.fn()
vi.mock('react-hot-toast', () => ({
  default: {
    success: (...a: unknown[]) => mockToastSuccess(...a),
    error: (...a: unknown[]) => mockToastError(...a),
  },
}))
vi.mock('@/components/ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => createElement('h1', null, title),
}))
vi.mock('@/components/ui/LoadingSpinner', () => ({ default: () => createElement('div', null, 'loading') }))
vi.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => createElement('a', null, children),
}))

// ── fixtures (the §1.1 shapes; the sentences are written out as the spec pins them) ─────────────

const gen = (provider: string, model: string, prefix = '', tag = ''): EmbeddingGeneration => ({
  provider, model, prefix, key: provider === 'default' ? 'default' : `${provider}:${model}`, tag,
})
const MINILM = gen('default', 'all-MiniLM-L6-v2')
const NOMIC = gen('ollama', 'nomic-embed-text:v1.5', '', 'e7d5a41f5')
const index = (overrides: Partial<EmbeddingIndex> = {}): EmbeddingIndex => ({
  active: MINILM, configured: MINILM, in_sync: true, status: 'idle', target: null, families: [], processed: 0,
  total: null, requested_at: null, switched_at: null, last_error: null, configured_note: null, ...overrides,
})
const NOT_APPLIED = index({ configured: NOMIC, in_sync: false })
const BUILDING = index({
  configured: NOMIC, in_sync: false, status: 'building', target: NOMIC, processed: 1200, total: 2290,
  families: [
    { family: 'test_case_search', state: 'running', processed: 1200, total: 2290 },
    { family: 'knowledge_chunks', state: 'done', processed: 340, total: 340 },
    { family: 'agent_memory_vectors', state: 'pending', processed: 0, total: null },
    { family: 'ai_analysis_cache', state: 'skipped', processed: 0, total: null },
  ],
})
const FAILED = index({
  configured: NOMIC, in_sync: false, status: 'failed', target: NOMIC, last_error: 'ConnectError while embedding test results',
})

const LINE = {
  inSync: 'In use: Bundled MiniLM (all-MiniLM-L6-v2).',
  notApplied: 'In use: Bundled MiniLM (all-MiniLM-L6-v2). Configured Ollama · nomic-embed-text:v1.5 is not in use yet.',
  building:
    'Re-embedding to Ollama · nomic-embed-text:v1.5: 1,200 / 2,290. Semantic search uses Bundled MiniLM until it ' +
    'finishes; cached analyses start empty.',
  families: 'Test results 1,200 / 2,290 · Knowledge chunks done · Agent memory pending',
  failed:
    'Re-embedding to Ollama · nomic-embed-text:v1.5 failed: ConnectError while embedding test results. Semantic ' +
    'search still uses Bundled MiniLM.',
  unreadable: 'Could not read the embedding index state.',
  warning:
    'Saving re-embeds every index into new collections; semantic search keeps using the current model until it finishes.',
  openaiOffline: 'OpenAI embeddings are refused while offline mode is on: choose Bundled MiniLM or Ollama.',
}

function config(overrides: Partial<AIConfigRead> = {}): AIConfigRead {
  return {
    llm_provider: 'ollama', llm_model: 'qwen2.5:7b', llm_temperature: 0.1, llm_max_tokens: 4096,
    ai_offline_mode: true, ai_offline_mode_source: 'override', ai_offline_mode_env_pinned: false,
    embedding_provider: 'default', embedding_model: 'all-MiniLM-L6-v2',
    ai_confidence_threshold: 80, ai_confidence_threshold_source: 'env_default',
    ai_timeout_seconds: 300, ai_timeout_seconds_source: 'env_default',
    deep_investigation_enabled: true, deep_investigation_enabled_source: 'env_default',
    finetune_enabled: false, finetune_enabled_source: 'env_default',
    openai_key_set: false, google_key_set: false, anthropic_key_set: false, openrouter_key_set: false,
    analysis_mode: 'auto', analysis_mode_source: 'env_default', ml_min_training_samples: 200,
    ml_model_available: false, ml_model_accuracy: null, ml_training_sample_count: 0, ml_human_label_count: 0,
    ml_human_label_floor: 50, ml_maturity: 'not_trained', knowledge_rag_enabled: false,
    rag_faithfulness_evaluator: 'ollama', rag_faithfulness_evaluator_effective: 'ollama',
    rag_faithfulness_evaluator_reason: 'configured', rag_faithfulness_gate_enabled: false,
    embedding_index: index(),
    ...overrides,
  }
}

async function renderPage(cfg: AIConfigRead) {
  mockGetAIConfig.mockResolvedValue(cfg)
  const { default: AIConfigPage } = await import('./AIConfigPage')
  render(createElement(
    SWRConfig,
    { value: { provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false, revalidateOnFocus: false } },
    createElement(AIConfigPage),
  ))
  const heading = await screen.findByRole('heading', { name: 'Embedding' })
  return heading.parentElement as HTMLElement
}

const block = (card: HTMLElement) => card.querySelector('[data-embedding-index]') as HTMLElement
const providerSelect = (card: HTMLElement) => within(card).getByLabelText('Provider') as HTMLSelectElement
const modelInput = (card: HTMLElement) => within(card).getByLabelText('Model') as HTMLInputElement

const MODEL_STATUS = {
  ollama_reachable: true, ollama_error: null, ollama_base_url: 'http://ollama:11434', installed_models: [],
  required: [], fallback_chain: [{ mode: 'rules', available: true, reason: null }], offline_mode: true,
  llm_provider: 'ollama', analysis_mode: 'auto', checked_at: '2026-10-24T00:00:00+00:00',
}
const ML_STATUS = {
  model_available: false, version: null, accuracy: null, trained_at: null, model_sample_count: null,
  available_samples: 0, min_training_samples: 200, human_labels: 0, human_label_floor: 50, maturity: 'not_trained',
  deps_installed: true, retrain_enabled: true, can_train: false, cannot_train_reason: 'insufficient_samples',
  cannot_train_message: 'Need 200 labelled samples, have 0.',
  training: { state: 'idle', task_id: null, queued_at: null, started_at: null, triggered_by: null }, last_run: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  isAdmin = true
  mockGetModelStatus.mockResolvedValue(MODEL_STATUS)
  mockGetMLStatus.mockResolvedValue(ML_STATUS)
})
afterEach(() => {
  vi.useRealTimers()
})

describe('the status lines', () => {
  it('in sync: "In use: …", no Re-embed now, the bundled model read-only', async () => {
    const card = await renderPage(config())
    expect(block(card)).toHaveTextContent(LINE.inSync)
    expect(block(card)).not.toHaveTextContent('not in use yet')
    expect(within(card).queryByRole('button', { name: 'Re-embed now' })).toBeNull()
    expect(providerSelect(card).value).toBe('default')
    expect(modelInput(card).value).toBe('all-MiniLM-L6-v2')
    expect(modelInput(card).readOnly).toBe(true)
  })

  it('configured ≠ in use: both sentences, and Re-embed now starts the build an admin sees', async () => {
    mockRebuild.mockResolvedValue(BUILDING)
    const card = await renderPage(config({ embedding_provider: 'ollama', embedding_model: 'nomic-embed-text:v1.5', embedding_index: NOT_APPLIED }))
    expect(block(card)).toHaveTextContent(LINE.notApplied)

    fireEvent.click(within(card).getByRole('button', { name: 'Re-embed now' }))
    await waitFor(() => expect(block(card)).toHaveTextContent(LINE.building))
    expect(block(card)).toHaveTextContent(LINE.families)
    expect(mockRebuild).toHaveBeenCalledTimes(1)
    expect(mockUpdateAIConfig).not.toHaveBeenCalled()
    expect(within(card).queryByRole('button', { name: 'Re-embed now' })).toBeNull()
  })

  it('a refused Re-embed now says why inside the card, with no toast', async () => {
    const sentence = 'A re-embed is already running. Wait for it, or change the setting again to replace it.'
    mockRebuild.mockRejectedValue({ response: { status: 409, data: { detail: sentence } } })
    const card = await renderPage(config({ embedding_index: NOT_APPLIED }))
    fireEvent.click(within(card).getByRole('button', { name: 'Re-embed now' }))
    await waitFor(() => expect(within(card).getByText(sentence)).toBeInTheDocument())
    expect(mockToastError).not.toHaveBeenCalled()
    expect(block(card)).toHaveTextContent(LINE.notApplied)
  })

  it('failed: the reason, what stays in use, and Retry → the rebuild', async () => {
    mockRebuild.mockResolvedValue(BUILDING)
    const card = await renderPage(config({ embedding_index: FAILED }))
    expect(block(card)).toHaveTextContent(LINE.failed)
    fireEvent.click(within(card).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(block(card)).toHaveTextContent(LINE.building))
    expect(mockRebuild).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['not in sync', NOT_APPLIED],
    ['failed', FAILED],
  ])('a QA lead reads the state (%s) with no Re-embed now or Retry, and cannot edit', async (_label, state) => {
    isAdmin = false
    const card = await renderPage(config({ embedding_index: state }))
    expect(block(card).textContent).not.toBe('')
    expect(within(card).queryByRole('button', { name: /^(Re-embed now|Retry)$/ })).toBeNull()
    expect(providerSelect(card)).toBeDisabled()
  })

  it.each([
    ['null (the server could not read it)', null],
    ['missing (an older server)', undefined],
  ])('embedding_index %s reads "Could not read the embedding index state."', async (_label, value) => {
    const card = await renderPage(config({ embedding_index: value }))
    expect(within(card).getByText(LINE.unreadable)).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: /^(Re-embed now|Retry)$/ })).toBeNull()
  })
})

describe('an embedder name never splits across lines', () => {
  const names = (card: HTMLElement) =>
    Array.from(block(card).querySelectorAll('[data-embedder-name]')).map((el) => ({
      text: el.textContent,
      nowrap: el.classList.contains('whitespace-nowrap'),
    }))

  it.each([
    ['in sync', index(), LINE.inSync, ['Bundled MiniLM (all-MiniLM-L6-v2)']],
    ['configured ≠ in use', NOT_APPLIED, LINE.notApplied, ['Bundled MiniLM (all-MiniLM-L6-v2)', 'Ollama · nomic-embed-text:v1.5']],
    ['building', BUILDING, LINE.building, ['Ollama · nomic-embed-text:v1.5', 'Bundled MiniLM']],
    ['stalled', { ...BUILDING, stalled: true }, LINE.building, ['Ollama · nomic-embed-text:v1.5', 'Bundled MiniLM']],
    ['failed', FAILED, LINE.failed, ['Ollama · nomic-embed-text:v1.5', 'Bundled MiniLM']],
  ] as [string, EmbeddingIndex, string, string[]][])(
    '%s: each name is one whitespace-nowrap unit, and the sentence reads exactly as before',
    async (_label, state, sentence, expected) => {
      const card = await renderPage(config({ embedding_index: state }))
      expect(names(card)).toEqual(expected.map((text) => ({ text, nowrap: true })))
      expect((block(card).querySelector('p') as HTMLElement).textContent).toBe(sentence)
    },
  )
})

describe('a stalled build (its worker died or never claimed it)', () => {
  const STALLED_LINE = 'Stalled — the re-embed stopped before it finished. Re-embed now to resume.'
  const STALLED = { ...BUILDING, stalled: true }

  it('a live build shows neither the stalled sentence nor Re-embed now', async () => {
    const card = await renderPage(config({ embedding_index: { ...BUILDING, stalled: false } }))
    expect(block(card)).toHaveTextContent(LINE.building)
    expect(within(card).queryByText(STALLED_LINE)).toBeNull()
    expect(within(card).queryByRole('button', { name: /^(Re-embed now|Retry)$/ })).toBeNull()
  })

  it('keeps the progress, says it stopped, and Re-embed now resumes it (the poll starts again)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockRebuild.mockResolvedValue({ ...BUILDING, stalled: false, processed: 1300 })
    mockGetEmbeddingIndex.mockResolvedValue({ ...BUILDING, stalled: false, processed: 1400 })
    const card = await renderPage(config({ embedding_index: STALLED }))
    expect(block(card)).toHaveTextContent(LINE.building)
    expect(block(card)).toHaveTextContent(LINE.families)
    expect(within(card).getByText(STALLED_LINE)).toBeInTheDocument()

    // Stalled: nothing is polled.
    await act(async () => { await vi.advanceTimersByTimeAsync(12_000) })
    expect(mockGetEmbeddingIndex).not.toHaveBeenCalled()

    fireEvent.click(within(card).getByRole('button', { name: 'Re-embed now' }))
    await waitFor(() => expect(within(card).queryByText(STALLED_LINE)).toBeNull())
    expect(mockRebuild).toHaveBeenCalledTimes(1)
    expect(within(card).queryByRole('button', { name: 'Re-embed now' })).toBeNull()
    await act(async () => { await vi.advanceTimersByTimeAsync(5_100) })
    expect(mockGetEmbeddingIndex).toHaveBeenCalled()
  })

  it('a QA lead reads that it stalled but has no Re-embed now', async () => {
    isAdmin = false
    const card = await renderPage(config({ embedding_index: STALLED }))
    expect(within(card).getByText(STALLED_LINE)).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: 'Re-embed now' })).toBeNull()
  })
})

describe('the Model field fits a full model name', () => {
  it.each([
    ['an admin (editable)', true],
    ['a QA lead (disabled)', false],
  ])('spans the card for %s and carries the whole name; Provider keeps its half', async (_label, admin) => {
    // At half width the field had ≈ 190 px of text at 1280 on DejaVu and cut nomic-embed-text:v1.5 off.
    isAdmin = admin
    const card = await renderPage(config({ embedding_provider: 'ollama', embedding_model: 'nomic-embed-text:v1.5', embedding_index: NOT_APPLIED }))
    const fields = card.querySelector('.grid') as HTMLElement
    expect(fields).toHaveClass('grid-cols-2')
    expect(modelInput(card).parentElement).toBe(card.querySelector('[data-embedding-model-field]'))
    expect(modelInput(card).parentElement).toHaveClass('col-span-2')
    expect(providerSelect(card).parentElement).not.toHaveClass('col-span-2')
    expect(modelInput(card).value).toBe('nomic-embed-text:v1.5')
    expect(modelInput(card).title).toBe('nomic-embed-text:v1.5')
  })

  it('the bundled model reads in full too', async () => {
    const card = await renderPage(config())
    expect(modelInput(card).parentElement).toHaveClass('col-span-2')
    expect(modelInput(card).title).toBe('all-MiniLM-L6-v2')
  })
})

describe('the Provider select and the warning before Save', () => {
  it('offers Bundled MiniLM · Ollama · OpenAI; OpenAI disabled with the sentence while offline', async () => {
    const card = await renderPage(config())
    const options = Array.from(providerSelect(card).options)
    expect(options.map((o) => o.textContent)).toEqual(['Bundled MiniLM', 'Ollama', 'OpenAI'])
    expect(options[2].disabled).toBe(true)
    expect(options[2].title).toBe(LINE.openaiOffline)
    expect(options[1].disabled).toBe(false)
  })

  it('OpenAI is offered once the offline toggle is off', async () => {
    const card = await renderPage(config())
    fireEvent.click(screen.getByLabelText(/Offline Mode/i))
    const openai = Array.from(providerSelect(card).options)[2]
    expect(openai.disabled).toBe(false)
    expect(openai.title).toBe('')
  })

  it('keeps a stored unsupported provider visible, with the server note', async () => {
    const note = "The stored provider 'cohere' is not supported; Bundled MiniLM (all-MiniLM-L6-v2) stays in use."
    const card = await renderPage(config({
      embedding_provider: 'cohere', embedding_model: 'embed-v3',
      embedding_index: index({ configured: gen('cohere', 'embed-v3'), in_sync: false, configured_note: note }),
    }))
    expect(providerSelect(card).value).toBe('cohere')
    expect(Array.from(providerSelect(card).options).map((o) => o.textContent)).toContain('cohere (not supported)')
    expect(block(card)).toHaveTextContent(note)
    expect(within(card).queryByRole('button', { name: 'Re-embed now' })).toBeNull()
  })

  it('warns only while the form differs from the saved embedder; Save sends it and shows the build', async () => {
    mockUpdateAIConfig.mockResolvedValue(config({ embedding_provider: 'ollama', embedding_model: 'nomic-embed-text:v1.5', embedding_index: BUILDING }))
    const card = await renderPage(config())
    expect(within(card).queryByText(LINE.warning)).toBeNull()

    fireEvent.change(providerSelect(card), { target: { value: 'ollama' } })
    expect(modelInput(card).value).toBe('')
    expect(modelInput(card).readOnly).toBe(false)
    expect(modelInput(card).placeholder).toBe('e.g. nomic-embed-text:v1.5')
    fireEvent.change(modelInput(card), { target: { value: 'nomic-embed-text:v1.5' } })
    expect(within(card).getByText(LINE.warning)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /save configuration/i }))
    await waitFor(() => expect(mockUpdateAIConfig).toHaveBeenCalledTimes(1))
    const [payload, opts] = mockUpdateAIConfig.mock.calls[0]
    expect(payload).toMatchObject({ embedding_provider: 'ollama', embedding_model: 'nomic-embed-text:v1.5' })
    expect(opts).toEqual({ suppressToast: true })
    await waitFor(() => expect(block(card)).toHaveTextContent(LINE.building))
    expect(within(card).queryByText(LINE.warning)).toBeNull()
    expect(mockToastSuccess).toHaveBeenCalledWith('AI configuration saved')
  })

  it('going back to the saved embedder clears the warning; a save that keeps it raises the toast as before', async () => {
    mockUpdateAIConfig.mockResolvedValue(config())
    const card = await renderPage(config())
    fireEvent.change(providerSelect(card), { target: { value: 'ollama' } })
    expect(within(card).getByText(LINE.warning)).toBeInTheDocument()
    fireEvent.change(providerSelect(card), { target: { value: 'default' } })
    expect(within(card).queryByText(LINE.warning)).toBeNull()
    expect(modelInput(card).value).toBe('all-MiniLM-L6-v2')

    fireEvent.click(screen.getByRole('button', { name: /save configuration/i }))
    await waitFor(() => expect(mockUpdateAIConfig).toHaveBeenCalledTimes(1))
    expect(mockUpdateAIConfig.mock.calls[0][1]).toBeUndefined()
  })

  it('a changed embedder refused with a 422 shows the sentence under the card, not a toast', async () => {
    const sentence = 'Name the embedding model (for example nomic-embed-text:v1.5).'
    mockUpdateAIConfig.mockRejectedValue({ response: { status: 422, data: { detail: sentence } } })
    const card = await renderPage(config())
    fireEvent.change(providerSelect(card), { target: { value: 'ollama' } })
    fireEvent.click(screen.getByRole('button', { name: /save configuration/i }))
    await waitFor(() => expect(within(card).getByText(sentence)).toBeInTheDocument())
    expect(mockToastError).not.toHaveBeenCalled()
    expect(mockToastSuccess).not.toHaveBeenCalled()
    fireEvent.change(modelInput(card), { target: { value: 'nomic-embed-text:v1.5' } })
    expect(within(card).queryByText(sentence)).toBeNull()
  })
})

describe('GET /settings/ai/embedding-index is asked only while building', () => {
  it('idle: nothing on load, nothing later', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await renderPage(config({ embedding_index: NOT_APPLIED }))
    await act(async () => { await vi.advanceTimersByTimeAsync(12_000) })
    expect(mockGetEmbeddingIndex).not.toHaveBeenCalled()
  })

  it('building: every 5 s until the switch, then the in-use line and no more reads', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockGetEmbeddingIndex.mockResolvedValue(index({ active: NOMIC, configured: NOMIC, switched_at: '2026-10-24T14:05:00Z' }))
    const card = await renderPage(config({ embedding_index: BUILDING }))
    expect(block(card)).toHaveTextContent(LINE.building)
    expect(mockGetEmbeddingIndex).not.toHaveBeenCalled()

    await act(async () => { await vi.advanceTimersByTimeAsync(5_100) })
    await waitFor(() => expect(block(card)).toHaveTextContent('In use: Ollama (nomic-embed-text:v1.5).'))
    expect(block(card)).toHaveTextContent('In use since 24 Oct 2026 14:05 UTC.')
    const reads = mockGetEmbeddingIndex.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
    expect(mockGetEmbeddingIndex.mock.calls.length).toBe(reads)
  })
})
