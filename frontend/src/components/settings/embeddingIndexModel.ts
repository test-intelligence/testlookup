/**
 * The Embedding card's and the Collection prefix field's copy and logic (W12 SRCH-02, contract §2.4 / §4.3).
 *
 * Pure: the page and the component render what these return, and the tests import the same strings. The
 * sentences the server owns are COPIED from `backend/app/services/embedding_function.py`,
 * `chroma_collections.py` and `embedding_index_state.py` (§1.2); `tests/ci-e2e/settings-embedding.spec.ts`
 * pins them verbatim.
 */
import type { EmbeddingFamilyProgress, EmbeddingGeneration, EmbeddingIndex, StorageConfigRead } from '@/services/appSettingsService'
import { formatNumber } from '@/utils/formatters'

export const EMBEDDING_PROVIDERS = ['default', 'ollama', 'openai'] as const
export const EMBEDDING_PROVIDER_LABELS: Record<string, string> = {
  default: 'Bundled MiniLM',
  ollama: 'Ollama',
  openai: 'OpenAI',
}
/** The bundled embedder's model (read-only in the form). */
export const DEFAULT_EMBEDDING_MODEL = 'all-MiniLM-L6-v2'
export const EMBEDDING_MODEL_PLACEHOLDER = 'e.g. nomic-embed-text:v1.5'

export const EMBEDDING_STATE_UNREADABLE = 'Could not read the embedding index state.'
export const OPENAI_OFFLINE_REFUSED =
  'OpenAI embeddings are refused while offline mode is on: choose Bundled MiniLM or Ollama.'
export const EMBEDDING_SAVE_WARNING =
  'Saving re-embeds every index into new collections; semantic search keeps using the current model until it finishes.'

export const COLLECTION_PREFIX_HELP =
  "Up to 8 lower-case letters, digits or '-'. Every vector collection's name starts with it, so two deployments " +
  "can share one ChromaDB. Changing it re-embeds every index into new collections; search keeps the current ones " +
  'until that finishes.'
export const COLLECTION_PREFIX_PLACEHOLDER = 'none'
export const PREFIX_NONE_IN_USE = 'In use: collections carry no prefix.'
export const prefixInUseSentence = (prefix: string): string =>
  prefix ? `In use: collections start with “${prefix}_”.` : PREFIX_NONE_IN_USE

/** The per-family labels of the build's progress line (`COLLECTION_FAMILIES[*].base`). */
export const FAMILY_LABELS: Record<string, string> = {
  test_case_search: 'Test results',
  knowledge_chunks: 'Knowledge chunks',
  agent_memory_vectors: 'Agent memory',
  ai_analysis_cache: 'Cached analyses',
  open_defects: 'Open defects',
  failure_clusters: 'Failure clusters',
  dupdetect: 'Duplicate detection',
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const pad = (n: number) => String(n).padStart(2, '0')

/** `EmbedderSpec.from_settings`'s rule: trimmed, lower-cased, empty = the bundled embedder. */
export function normaliseProvider(provider: string | null | undefined): string {
  return String(provider ?? '').trim().toLowerCase() || 'default'
}

export function isSupportedProvider(provider: string | null | undefined): boolean {
  return (EMBEDDING_PROVIDERS as readonly string[]).includes(normaliseProvider(provider))
}

/** The embedder key the server compares (`default` | `<provider>:<model>`). */
export function embedderKey(provider: string | null | undefined, model: string | null | undefined): string {
  const normalised = normaliseProvider(provider)
  return normalised === 'default' ? 'default' : `${normalised}:${String(model ?? '').trim()}`
}

export function providerLabel(provider: string): string {
  return EMBEDDING_PROVIDER_LABELS[normaliseProvider(provider)] ?? provider
}

/**
 * A piece of a status sentence. `nowrap`: an embedder's name (`Ollama · nomic-embed-text:v1.5`), which the card
 * renders as one unit (`whitespace-nowrap`) so a narrow card moves it to the next line whole instead of breaking
 * it at a hyphen (`nomic-embed-` / `text:v1.5`). The text itself is unchanged: the parts join to `lead`.
 */
export interface TextPart {
  text: string
  nowrap: boolean
}

/** Longer names may wrap: one unbreakable unit wider than the ≈ 360 px card would scroll it sideways. */
export const NOWRAP_MAX_CHARS = 40

const plain = (text: string): TextPart => ({ text, nowrap: false })
const unit = (text: string): TextPart => ({ text, nowrap: text.length <= NOWRAP_MAX_CHARS })
const joinParts = (parts: TextPart[]) => parts.map((p) => p.text).join('')

/** `Bundled MiniLM (all-MiniLM-L6-v2)`: the generation in use. */
export function inUseName(gen: EmbeddingGeneration): string {
  return `${providerLabel(gen.provider)} (${gen.model})`
}

/** `Ollama · nomic-embed-text:v1.5` as one unit, then its prefix when it differs from the one in use. */
function generationParts(gen: EmbeddingGeneration, active?: EmbeddingGeneration): TextPart[] {
  const parts = [unit(`${providerLabel(gen.provider)} · ${gen.model}`)]
  if (active && (gen.prefix ?? '') !== (active.prefix ?? '')) {
    parts.push(plain(gen.prefix ? ` with prefix “${gen.prefix}”` : ' with no prefix'))
  }
  return parts
}

/**
 * `Ollama · nomic-embed-text:v1.5`: a configured or target generation; its prefix is named only when it
 * differs from the generation in use (a prefix change keeps the embedder).
 */
export function generationName(gen: EmbeddingGeneration, active?: EmbeddingGeneration): string {
  return joinParts(generationParts(gen, active))
}

/** `24 Oct 2026 14:05 UTC`, or null for a missing or unparseable time. */
export function formatUtc(iso: string | null | undefined): string | null {
  if (!iso) return null
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return null
  return `${at.getUTCDate()} ${MONTHS[at.getUTCMonth()]} ${at.getUTCFullYear()} ${pad(at.getUTCHours())}:${pad(at.getUTCMinutes())} UTC`
}

const count = (processed: number | undefined, total: number | null | undefined) =>
  total == null ? `${formatNumber(processed ?? 0)} so far` : `${formatNumber(processed ?? 0)} / ${formatNumber(total)}`

function familyText(entry: EmbeddingFamilyProgress): string {
  const label = FAMILY_LABELS[entry.family] ?? entry.family
  if (entry.state === 'running') return `${label} ${count(entry.processed, entry.total)}`
  return `${label} ${entry.state}`
}

/** `Test results 1,200 / 2,290 · Knowledge chunks done · Agent memory pending` (skipped caches are left out). */
export function familiesLine(families: EmbeddingFamilyProgress[] | undefined): string | null {
  const shown = (families ?? []).filter((f) => f.state !== 'skipped')
  return shown.length ? shown.map(familyText).join(' · ') : null
}

export type EmbeddingAction = 'reembed' | 'retry'

export interface EmbeddingStatusLines {
  kind: 'unreadable' | 'idle' | 'building' | 'failed'
  /** The `text-sm` line. */
  lead: string
  /** `lead` in pieces: the card renders each embedder name as one unbreakable unit. */
  leadParts: TextPart[]
  /** `In use since 24 Oct 2026 14:05 UTC.` once a generation was switched to (idle only). */
  since: string | null
  /** The per-family progress (building only). */
  families: string | null
  /** The server's `configured_note` (an unsupported stored provider, an ignored stored prefix). */
  note: string | null
  /** A building index whose worker died or was never claimed (`stalled: true`): `EMBEDDING_STALLED`. */
  stalled: string | null
  /**
   * The admin button the state offers: Re-embed now (configured ≠ in use, or a stalled build to resume) or Retry
   * (a failed build). None while a live build runs.
   */
  action: EmbeddingAction | null
}

/** A build in `building` that no worker is running any more (G-C5: lease expired / never claimed). */
export const EMBEDDING_STALLED = 'Stalled — the re-embed stopped before it finished. Re-embed now to resume.'

/** A building index that stopped: the rebuild resumes it, and there is nothing to poll for. */
export function isStalled(index: EmbeddingIndex | null | undefined): boolean {
  return index?.status === 'building' && index.stalled === true
}

const lastError = (text: string | null | undefined) => (text ?? '').trim().replace(/\.+$/, '')

/** What the `data-embedding-index` block says for one `EmbeddingIndex` (contract §2.4). */
export function statusLines(index: EmbeddingIndex | null | undefined): EmbeddingStatusLines {
  if (!index || !index.active || !index.configured) {
    return {
      kind: 'unreadable', lead: EMBEDDING_STATE_UNREADABLE, leadParts: [plain(EMBEDDING_STATE_UNREADABLE)], since: null,
      families: null, note: null, stalled: null, action: null,
    }
  }
  const { active, configured } = index
  const note = index.configured_note || null
  const target = index.target ?? configured
  const inUse = unit(providerLabel(active.provider))
  if (index.status === 'building') {
    const stalled = isStalled(index)
    const leadParts = [
      plain('Re-embedding to '), ...generationParts(target, active), plain(`: ${count(index.processed, index.total)}. `),
      plain('Semantic search uses '), inUse, plain(' until it finishes; cached analyses start empty.'),
    ]
    return {
      kind: 'building',
      lead: joinParts(leadParts),
      leadParts,
      since: null,
      families: familiesLine(index.families),
      note,
      stalled: stalled ? EMBEDDING_STALLED : null,
      action: stalled ? 'reembed' : null,
    }
  }
  if (index.status === 'failed') {
    const reason = lastError(index.last_error)
    const leadParts = [
      plain('Re-embedding to '), ...generationParts(target, active), plain(` failed${reason ? `: ${reason}` : ''}. `),
      plain('Semantic search still uses '), inUse, plain('.'),
    ]
    return {
      kind: 'failed',
      lead: joinParts(leadParts),
      leadParts,
      since: null,
      families: null,
      note,
      stalled: null,
      action: 'retry',
    }
  }
  const pending = !index.in_sync && isSupportedProvider(configured.provider)
  const since = formatUtc(index.switched_at)
  const leadParts = [
    plain('In use: '), unit(inUseName(active)), plain('.'),
    ...(pending ? [plain(' Configured '), ...generationParts(configured, active), plain(' is not in use yet.')] : []),
  ]
  return {
    kind: 'idle',
    lead: joinParts(leadParts),
    leadParts,
    since: since ? `In use since ${since}.` : null,
    families: null,
    note,
    stalled: null,
    action: pending ? 'reembed' : null,
  }
}

// ── The form (Provider select, Model, the warning before Save) ─────────────

export interface ProviderOption {
  value: string
  label: string
  disabled: boolean
  title?: string
}

/**
 * Bundled MiniLM · Ollama · OpenAI (OpenAI disabled, with the server's sentence, while offline); a stored
 * provider the server does not support stays visible as one more option `<value> (not supported)`.
 */
export function providerOptions(current: string | null | undefined, offline: boolean): ProviderOption[] {
  const options: ProviderOption[] = EMBEDDING_PROVIDERS.map((value) => ({
    value,
    label: EMBEDDING_PROVIDER_LABELS[value],
    disabled: value === 'openai' && offline,
    ...(value === 'openai' && offline ? { title: OPENAI_OFFLINE_REFUSED } : {}),
  }))
  if (current && !isSupportedProvider(current)) {
    options.push({ value: current, label: `${current} (not supported)`, disabled: false })
  }
  return options
}

/** The select's value for a stored provider: a supported one normalised (`Ollama` → `ollama`), else as stored. */
export function providerValue(provider: string | null | undefined): string {
  return isSupportedProvider(provider) ? normaliseProvider(provider) : String(provider ?? '')
}

export interface EmbedderPair {
  provider?: string | null
  model?: string | null
}

/** True when the form's embedder is not the saved one (Save would start a re-embed). */
export function embedderChanged(form: EmbedderPair, saved: EmbedderPair): boolean {
  return embedderKey(form.provider, form.model) !== embedderKey(saved.provider, saved.model)
}

/**
 * The model a provider change puts in the form: the bundled model for Bundled MiniLM, the saved model when
 * going back to the saved provider, otherwise empty (the placeholder names an example).
 */
export function modelForProvider(next: string, saved: EmbedderPair): string {
  if (normaliseProvider(next) === 'default') return DEFAULT_EMBEDDING_MODEL
  if (normaliseProvider(next) === normaliseProvider(saved.provider)) return String(saved.model ?? '')
  return ''
}

// ── The Collection prefix (Settings › Data & storage) ──────────────────────

/** The field's value: the configured prefix the stored value means; an older server's raw value otherwise. */
export function prefixFieldValue(config: Pick<StorageConfigRead, 'chroma_collection' | 'collection_prefix'>): string {
  return config.collection_prefix ?? config.chroma_collection ?? ''
}

/**
 * The "In use" line from `active_collection_prefix`: null when an older server sends no such key; the
 * unreadable sentence when the server could not read the embedding index state.
 */
export function prefixInUseLine(config: Pick<StorageConfigRead, 'active_collection_prefix'>): string | null {
  if (config.active_collection_prefix === undefined) return null
  if (config.active_collection_prefix === null) return EMBEDDING_STATE_UNREADABLE
  return prefixInUseSentence(config.active_collection_prefix)
}

/** Re-embed now / Retry when the server answered without a sentence (a network error, a 500). */
export const REBUILD_NOT_STARTED = 'Could not start the re-embed. Try again in a moment.'

/**
 * The server's sentence (a string `detail`) of a refusal with one of `statuses` (default: a 422), or null for
 * anything else (a validation array, another status, no response).
 */
export function refusalSentence(err: unknown, statuses: readonly number[] = [422]): string | null {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response
  if (response?.status === undefined || !statuses.includes(response.status)) return null
  const detail = response.data?.detail
  return typeof detail === 'string' && detail.trim() ? detail : null
}
