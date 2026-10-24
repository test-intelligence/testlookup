/**
 * W04 G-D hermetic answers for Settings › AI (`docs/deferred-scope-2026-10-13/work/W04-contract.md` §2.2, §2.4,
 * §2.9, §3.1-§3.3, §4.2):
 *
 *  - `GET /settings/ai` with the five `*_source` fields and `ml_min_training_samples`; `PUT /settings/ai` merges
 *    the body into the stored config and answers every `*_source` as `ai_config` (contract §3.1: every key is
 *    stored after a save), and `GET /settings/ai/mode` then answers the saved toggle (what the deep CTAs read);
 *  - `GET /settings/ai/ml/status` and `POST /settings/ai/ml/retrain`: below the floor (409 with the detail the
 *    card renders), or enough samples (202), after which the status walks queued -> running -> a trained model,
 *    one step per poll.
 *
 * Every factory returns fresh state per call, and records the writes it answered (`writes`) so a spec asserts
 * the body, not only that something was called. Not a spec file. Never edit `fixtures.ts` / `fixtures-pages.ts`
 * for these (the contract's rule).
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { LAYOUT, NOW, USER } from './fixtures'

export const ADMIN = {
  ...USER,
  id: '00000000-0000-4000-8000-0000000000a1',
  username: 'admin',
  email: 'admin@example.test',
  full_name: 'Admin',
  role: 'ADMIN',
}

export const AI_CONFIG_PATH = '/api/v1/settings/ai'
export const AI_MODE_PATH = '/api/v1/settings/ai/mode'
export const MODEL_STATUS_PATH = '/api/v1/settings/ai/model-status'
export const ML_STATUS_PATH = '/api/v1/settings/ai/ml/status'
export const ML_RETRAIN_PATH = '/api/v1/settings/ai/ml/retrain'

export const MODEL_VERSION = '20260918_120000'
export const TASK_ID = 'c0ffee00-0000-4000-8000-000000000001'

const SOURCE_KEYS = [
  'analysis_mode_source',
  'ai_timeout_seconds_source',
  'deep_investigation_enabled_source',
  'finetune_enabled_source',
  'ai_confidence_threshold_source',
] as const

/** `GET /settings/ai`: nothing stored yet (every source `env_default`). */
export function aiConfig(overrides: Record<string, unknown> = {}): Record<string, unknown> {
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
    ml_training_sample_count: 142,
    ml_human_label_count: 12,
    ml_human_label_floor: 50,
    ml_maturity: 'not_trained',
    knowledge_rag_enabled: false,
    // W12 SRCH-02 (§1.1): the embedder IN USE, idle and in sync with the form's `ollama` · `nomic-embed-text`, so
    // this world reads as a W12 server (re-exported by `fixtures-search.ts`). No region shows the Embedding card.
    embedding_index: EMBEDDING_INDEX_IN_SYNC,
    ...overrides,
  }
}

const IN_SYNC_GENERATION = { provider: 'ollama', model: 'nomic-embed-text', prefix: '', key: 'ollama:nomic-embed-text', tag: 'e4cdca008' }

/** `AIConfigRead.embedding_index` (W12): the form's embedder in use since three days before `NOW`, nothing building. */
export const EMBEDDING_INDEX_IN_SYNC = {
  active: IN_SYNC_GENERATION,
  configured: IN_SYNC_GENERATION,
  in_sync: true,
  status: 'idle',
  target: null,
  families: [],
  processed: 0,
  total: null,
  requested_at: null,
  switched_at: new Date(NOW.getTime() - 3 * 86_400_000).toISOString(),
  last_error: null,
  configured_note: null,
}

export const MODEL_STATUS = {
  ollama_reachable: true,
  ollama_error: null,
  ollama_base_url: 'http://ollama:11434',
  installed_models: ['qwen2.5:7b', 'nomic-embed-text'],
  required: [
    { name: 'qwen2.5:7b', purpose: 'llm', present: true, remedy: null },
    { name: 'nomic-embed-text', purpose: 'embedding', present: true, remedy: null },
  ],
  fallback_chain: [
    { mode: 'ml', available: false, reason: 'No trained ML classifier on disk — auto mode falls through to the next tier.', reason_code: 'not_trained' },
    { mode: 'llm', available: true, reason: null, reason_code: 'ok' },
    { mode: 'rules', available: true, reason: null, reason_code: 'ok' },
  ],
  offline_mode: true,
  llm_provider: 'ollama',
  analysis_mode: 'auto',
  checked_at: NOW.toISOString(),
}

/** The same, with a trained classifier on disk (auto resolves to ML). */
export const MODEL_STATUS_TRAINED = {
  ...MODEL_STATUS,
  fallback_chain: [
    { mode: 'ml', available: true, reason: null, reason_code: 'ok' },
    { mode: 'llm', available: true, reason: null, reason_code: 'ok' },
    { mode: 'rules', available: true, reason: null, reason_code: 'ok' },
  ],
}

const IDLE ={ state: 'idle', task_id: null, queued_at: null, started_at: null, triggered_by: null }

/** `GET /settings/ai/ml/status`: no model; `samples` usable labels against the 200 floor. */
export function mlStatus(samples: number, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const enough = samples >= 200
  return {
    model_available: false,
    version: null,
    accuracy: null,
    trained_at: null,
    model_sample_count: null,
    available_samples: samples,
    min_training_samples: 200,
    human_labels: enough ? 57 : 12,
    human_label_floor: 50,
    maturity: 'not_trained',
    deps_installed: true,
    retrain_enabled: true,
    can_train: enough,
    cannot_train_reason: enough ? null : 'insufficient_samples',
    cannot_train_message: enough ? null : `Need 200 labelled samples, have ${samples}.`,
    training: { ...IDLE },
    last_run: null,
    ...overrides,
  }
}

/** What the status answers after a successful run of the job (the lock released). */
function trainedStatus(samples: number): Record<string, unknown> {
  return mlStatus(samples, {
    model_available: true,
    version: MODEL_VERSION,
    accuracy: 0.84,
    trained_at: NOW.toISOString(),
    model_sample_count: samples,
    maturity: 'human_calibrated',
    last_run: {
      status: 'trained',
      message: `Model ${MODEL_VERSION} trained on ${samples} samples (accuracy 0.84).`,
      finished_at: NOW.toISOString(),
      triggered_by: 'user:admin',
      accuracy: 0.84,
      version: MODEL_VERSION,
      sample_count: samples,
    },
  })
}

export interface RecordedWrite {
  line: string
  body: unknown
}

const bodyOf = (request: ApiRequest): unknown => {
  try {
    return request.route.request().postDataJSON()
  } catch {
    return null
  }
}

export interface AIConfigFixtureOptions {
  /** Usable labelled samples (`available_samples`); 200 is the floor. Default 642. */
  samples?: number
  /** Overrides of the stored `GET /settings/ai` answer. */
  config?: Record<string, unknown>
  /** A model is already in service when the page opens (the end state of "Train now"). */
  trained?: boolean
}

/**
 * Settings › AI, stateful: a PUT is stored and echoed (every `*_source` then `ai_config`), the mode answer
 * follows the saved toggle, and "Train now" walks the ML status queued -> running -> trained, one step per
 * status poll after the POST.
 */
export function aiConfigHandlers(options: AIConfigFixtureOptions = {}) {
  const samples = options.samples ?? 642
  const state = {
    config: aiConfig(options.config),
    writes: [] as RecordedWrite[],
    /** Status polls answered since the POST (null before any press). */
    pollsSinceTrain: null as number | null,
  }

  const status = () => {
    if (state.pollsSinceTrain === null) return options.trained ? trainedStatus(samples) : mlStatus(samples)
    state.pollsSinceTrain += 1
    const lock = { task_id: TASK_ID, queued_at: NOW.toISOString(), triggered_by: 'user:admin' }
    if (state.pollsSinceTrain === 1) return mlStatus(samples, { training: { ...IDLE, ...lock, state: 'queued' } })
    if (state.pollsSinceTrain === 2) {
      return mlStatus(samples, { training: { ...IDLE, ...lock, state: 'running', started_at: NOW.toISOString() } })
    }
    return trainedStatus(samples)
  }

  const handlers: ApiHandlers = [
    [AI_CONFIG_PATH, () => state.config],
    [
      AI_CONFIG_PATH,
      (request) => {
        const body = (bodyOf(request) ?? {}) as Record<string, unknown>
        state.writes.push({ line: `PUT ${AI_CONFIG_PATH}`, body })
        const saved: Record<string, unknown> = { ...state.config }
        for (const [key, value] of Object.entries(body)) {
          if (value !== undefined && !key.endsWith('_api_key')) saved[key] = value
        }
        for (const key of SOURCE_KEYS) saved[key] = 'ai_config'
        state.config = saved
        return saved
      },
      'PUT',
    ],
    [
      AI_MODE_PATH,
      () => ({
        analysis_mode: state.config.analysis_mode,
        ai_offline_mode: state.config.ai_offline_mode,
        deep_investigation_enabled: state.config.deep_investigation_enabled,
      }),
    ],
    [
      MODEL_STATUS_PATH,
      // The fallback chain agrees with the card: once a model is in service the ML tier is available.
      () => (options.trained || (state.pollsSinceTrain ?? 0) >= 3 ? MODEL_STATUS_TRAINED : MODEL_STATUS),
    ],
    [ML_STATUS_PATH, status],
    [
      ML_RETRAIN_PATH,
      (request) => {
        state.writes.push({ line: `POST ${ML_RETRAIN_PATH}`, body: bodyOf(request) })
        if (samples < 200) {
          return respond(409, {
            detail: { code: 'insufficient_samples', message: `Need 200 labelled samples, have ${samples}.`, have: samples, need: 200 },
          })
        }
        const already = state.pollsSinceTrain !== null
        if (!already) state.pollsSinceTrain = 0
        return respond(202, { task_id: TASK_ID, state: 'queued', already_running: already })
      },
      'POST',
    ],
    ['/api/v1/auth/me/dismissals', () => ({ dismissed: [] })],
    // After the page's own answers: the chrome's (LAYOUT answers /settings/ai with a subset; first match wins).
    ...LAYOUT,
  ]
  return { handlers, state }
}
