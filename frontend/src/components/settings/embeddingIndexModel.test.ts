/**
 * The Embedding card's copy and logic (W12 SRCH-02, contract §2.4). The expected sentences are written out
 * here (not imported) where `tests/ci-e2e/settings-embedding.spec.ts` pins the same text, so a reworded
 * model fails here first.
 */
import { describe, expect, it } from 'vitest'

import type { EmbeddingGeneration, EmbeddingIndex } from '@/services/appSettingsService'
import {
  DEFAULT_EMBEDDING_MODEL,
  EMBEDDING_STATE_UNREADABLE,
  NOWRAP_MAX_CHARS,
  OPENAI_OFFLINE_REFUSED,
  embedderChanged,
  embedderKey,
  familiesLine,
  formatUtc,
  generationName,
  modelForProvider,
  prefixFieldValue,
  prefixInUseLine,
  providerOptions,
  providerValue,
  refusalSentence,
  statusLines,
} from './embeddingIndexModel'

const gen = (provider: string, model: string, prefix = '', tag = ''): EmbeddingGeneration => ({
  provider,
  model,
  prefix,
  key: provider === 'default' ? 'default' : `${provider}:${model}`,
  tag,
})
const MINILM = gen('default', 'all-MiniLM-L6-v2')
const NOMIC = gen('ollama', 'nomic-embed-text:v1.5', '', 'e7d5a41f5')

const index = (overrides: Partial<EmbeddingIndex> = {}): EmbeddingIndex => ({
  active: MINILM,
  configured: MINILM,
  in_sync: true,
  status: 'idle',
  target: null,
  families: [],
  processed: 0,
  total: null,
  requested_at: null,
  switched_at: null,
  last_error: null,
  configured_note: null,
  ...overrides,
})

describe('statusLines', () => {
  it('in sync: "In use: …" alone, no action', () => {
    const lines = statusLines(index())
    expect(lines.kind).toBe('idle')
    expect(lines.lead).toBe('In use: Bundled MiniLM (all-MiniLM-L6-v2).')
    expect(lines.action).toBeNull()
    expect(lines.since).toBeNull()
  })

  it('in sync after a switch: says since when, in UTC', () => {
    const lines = statusLines(index({ active: NOMIC, configured: NOMIC, switched_at: '2026-10-24T14:05:09Z' }))
    expect(lines.lead).toBe('In use: Ollama (nomic-embed-text:v1.5).')
    expect(lines.since).toBe('In use since 24 Oct 2026 14:05 UTC.')
  })

  it('configured ≠ in use: both sentences on one line and Re-embed now', () => {
    const lines = statusLines(index({ configured: NOMIC, in_sync: false }))
    expect(lines.lead).toBe(
      'In use: Bundled MiniLM (all-MiniLM-L6-v2). Configured Ollama · nomic-embed-text:v1.5 is not in use yet.',
    )
    expect(lines.action).toBe('reembed')
  })

  it('a configured prefix change names the prefix', () => {
    const lines = statusLines(index({ configured: gen('default', 'all-MiniLM-L6-v2', 'demo'), in_sync: false }))
    expect(lines.lead).toContain('Configured Bundled MiniLM · all-MiniLM-L6-v2 with prefix “demo” is not in use yet.')
  })

  it('an unsupported stored provider: no "not in use yet", no button, the server note', () => {
    const note = "The stored provider 'cohere' is not supported; Bundled MiniLM (all-MiniLM-L6-v2) stays in use."
    const lines = statusLines(index({ configured: gen('cohere', 'embed-v3'), in_sync: false, configured_note: note }))
    expect(lines.lead).toBe('In use: Bundled MiniLM (all-MiniLM-L6-v2).')
    expect(lines.action).toBeNull()
    expect(lines.note).toBe(note)
  })

  it('building: the progress sentence and one line per family, skipped caches left out', () => {
    const lines = statusLines(index({
      configured: NOMIC,
      in_sync: false,
      status: 'building',
      target: NOMIC,
      processed: 1200,
      total: 2290,
      families: [
        { family: 'test_case_search', state: 'running', processed: 1200, total: 2290 },
        { family: 'knowledge_chunks', state: 'done', processed: 340, total: 340 },
        { family: 'agent_memory_vectors', state: 'pending', processed: 0, total: null },
        { family: 'ai_analysis_cache', state: 'skipped', processed: 0, total: null },
      ],
    }))
    expect(lines.kind).toBe('building')
    expect(lines.lead).toBe(
      'Re-embedding to Ollama · nomic-embed-text:v1.5: 1,200 / 2,290. Semantic search uses Bundled MiniLM until it ' +
        'finishes; cached analyses start empty.',
    )
    expect(lines.families).toBe('Test results 1,200 / 2,290 · Knowledge chunks done · Agent memory pending')
    expect(lines.action).toBeNull()
  })

  it('a live build offers no action and no stalled sentence', () => {
    const lines = statusLines(index({ status: 'building', target: NOMIC, processed: 10, total: 20, stalled: false }))
    expect(lines.stalled).toBeNull()
    expect(lines.action).toBeNull()
    expect(statusLines(index({ status: 'building', target: NOMIC, stalled: null })).stalled).toBeNull()
  })

  it('a stalled build keeps the progress, says it stopped, and offers Re-embed now to resume', () => {
    const lines = statusLines(index({
      configured: NOMIC, in_sync: false, status: 'building', target: NOMIC, processed: 1200, total: 2290, stalled: true,
      families: [{ family: 'test_case_search', state: 'running', processed: 1200, total: 2290 }],
    }))
    expect(lines.kind).toBe('building')
    expect(lines.lead).toContain('Re-embedding to Ollama · nomic-embed-text:v1.5: 1,200 / 2,290.')
    expect(lines.families).toBe('Test results 1,200 / 2,290')
    expect(lines.stalled).toBe('Stalled — the re-embed stopped before it finished. Re-embed now to resume.')
    expect(lines.action).toBe('reembed')
  })

  it('stalled means nothing outside a build', () => {
    expect(statusLines(index({ stalled: true })).stalled).toBeNull()
    expect(statusLines(index({ status: 'failed', target: NOMIC, stalled: true })).stalled).toBeNull()
  })

  it('building with no total yet says how many so far', () => {
    const lines = statusLines(index({ status: 'building', target: NOMIC, processed: 0, total: null }))
    expect(lines.lead).toContain('Re-embedding to Ollama · nomic-embed-text:v1.5: 0 so far.')
  })

  it('failed: the reason, what stays in use, and Retry', () => {
    const lines = statusLines(index({
      configured: NOMIC,
      in_sync: false,
      status: 'failed',
      target: NOMIC,
      last_error: 'ConnectError while embedding test results',
    }))
    expect(lines.lead).toBe(
      'Re-embedding to Ollama · nomic-embed-text:v1.5 failed: ConnectError while embedding test results. ' +
        'Semantic search still uses Bundled MiniLM.',
    )
    expect(lines.action).toBe('retry')
  })

  it('failed with a reason that ends in a full stop does not double it; no reason reads plainly', () => {
    expect(statusLines(index({ status: 'failed', target: NOMIC, last_error: 'The worker stopped.' })).lead)
      .toContain('failed: The worker stopped. Semantic')
    expect(statusLines(index({ status: 'failed', target: NOMIC })).lead)
      .toBe('Re-embedding to Ollama · nomic-embed-text:v1.5 failed. Semantic search still uses Bundled MiniLM.')
  })

  it.each([null, undefined])('embedding_index %s reads "Could not read the embedding index state."', (value) => {
    const lines = statusLines(value)
    expect(lines.kind).toBe('unreadable')
    expect(lines.lead).toBe(EMBEDDING_STATE_UNREADABLE)
    expect(lines.action).toBeNull()
  })
})

describe('embedder names are one unbreakable unit in every sentence', () => {
  const units = (lines: ReturnType<typeof statusLines>) => lines.leadParts.filter((p) => p.nowrap).map((p) => p.text)
  const STATES: [string, EmbeddingIndex][] = [
    ['in sync', index()],
    ['not applied', index({ configured: NOMIC, in_sync: false })],
    ['building', index({ configured: NOMIC, in_sync: false, status: 'building', target: NOMIC, processed: 1, total: 2 })],
    ['stalled', index({ configured: NOMIC, in_sync: false, status: 'building', target: NOMIC, stalled: true })],
    ['failed', index({ configured: NOMIC, in_sync: false, status: 'failed', target: NOMIC, last_error: 'ConnectError' })],
    ['a prefix change', index({ configured: gen('default', 'all-MiniLM-L6-v2', 'demo'), in_sync: false })],
  ]

  it.each(STATES)('%s: the parts join to the lead, text unchanged', (_label, state) => {
    const lines = statusLines(state)
    expect(lines.leadParts.map((p) => p.text).join('')).toBe(lines.lead)
  })

  it('names the units: in use, configured, target and the provider still in use', () => {
    expect(units(statusLines(STATES[0][1]))).toEqual(['Bundled MiniLM (all-MiniLM-L6-v2)'])
    expect(units(statusLines(STATES[1][1]))).toEqual(['Bundled MiniLM (all-MiniLM-L6-v2)', 'Ollama · nomic-embed-text:v1.5'])
    expect(units(statusLines(STATES[2][1]))).toEqual(['Ollama · nomic-embed-text:v1.5', 'Bundled MiniLM'])
    expect(units(statusLines(STATES[3][1]))).toEqual(['Ollama · nomic-embed-text:v1.5', 'Bundled MiniLM'])
    expect(units(statusLines(STATES[4][1]))).toEqual(['Ollama · nomic-embed-text:v1.5', 'Bundled MiniLM'])
    // The prefix words wrap like any text; only the name is held together.
    expect(units(statusLines(STATES[5][1]))).toEqual(['Bundled MiniLM (all-MiniLM-L6-v2)', 'Bundled MiniLM · all-MiniLM-L6-v2'])
  })

  it(`a name longer than ${NOWRAP_MAX_CHARS} characters may wrap (one unit wider than the card would scroll)`, () => {
    const long = gen('ollama', 'hf.co/someone/a-very-long-embedding-model-GGUF:Q4_K_M')
    const lines = statusLines(index({ configured: long, in_sync: false }))
    expect(lines.lead).toContain('Ollama · hf.co/someone/a-very-long-embedding-model-GGUF:Q4_K_M')
    expect(units(lines)).toEqual(['Bundled MiniLM (all-MiniLM-L6-v2)'])
  })

  it('the unreadable sentence is one plain part', () => {
    expect(statusLines(null).leadParts).toEqual([{ text: EMBEDDING_STATE_UNREADABLE, nowrap: false }])
  })
})

describe('names, counts and times', () => {
  it('generationName names a prefix only when it differs from the one in use', () => {
    expect(generationName(NOMIC)).toBe('Ollama · nomic-embed-text:v1.5')
    expect(generationName(NOMIC, MINILM)).toBe('Ollama · nomic-embed-text:v1.5')
    expect(generationName(MINILM, gen('default', 'all-MiniLM-L6-v2', 'demo'))).toBe('Bundled MiniLM · all-MiniLM-L6-v2 with no prefix')
  })

  it('familiesLine is null when nothing is shown', () => {
    expect(familiesLine([])).toBeNull()
    expect(familiesLine(undefined)).toBeNull()
    expect(familiesLine([{ family: 'dupdetect', state: 'skipped', processed: 0, total: null }])).toBeNull()
  })

  it('formatUtc reads an ISO time in UTC and refuses garbage', () => {
    expect(formatUtc('2026-01-05T03:07:00+00:00')).toBe('5 Jan 2026 03:07 UTC')
    expect(formatUtc('not a time')).toBeNull()
    expect(formatUtc(null)).toBeNull()
  })
})

describe('the form', () => {
  it('offers the three providers; OpenAI disabled with the sentence only while offline', () => {
    const offline = providerOptions('default', true)
    expect(offline.map((o) => o.label)).toEqual(['Bundled MiniLM', 'Ollama', 'OpenAI'])
    expect(offline.map((o) => o.value)).toEqual(['default', 'ollama', 'openai'])
    expect(offline[2]).toMatchObject({ disabled: true, title: OPENAI_OFFLINE_REFUSED })
    expect(offline.slice(0, 2).every((o) => !o.disabled && o.title === undefined)).toBe(true)
    const online = providerOptions('ollama', false)
    expect(online[2]).toMatchObject({ disabled: false })
    expect(online[2].title).toBeUndefined()
  })

  it('keeps a stored unsupported provider visible as "(not supported)"', () => {
    const options = providerOptions('cohere', false)
    expect(options.map((o) => o.label)).toEqual(['Bundled MiniLM', 'Ollama', 'OpenAI', 'cohere (not supported)'])
    expect(providerValue('cohere')).toBe('cohere')
    expect(providerValue('Ollama ')).toBe('ollama')
    expect(providerValue('')).toBe('default')
  })

  it('compares embedders the way the server does', () => {
    expect(embedderKey('', 'whatever')).toBe('default')
    expect(embedderKey('Ollama', ' nomic-embed-text:v1.5 ')).toBe('ollama:nomic-embed-text:v1.5')
    expect(embedderChanged({ provider: 'default', model: 'x' }, { provider: 'default', model: 'y' })).toBe(false)
    expect(embedderChanged({ provider: 'ollama', model: 'nomic-embed-text' }, { provider: 'ollama', model: 'nomic-embed-text' })).toBe(false)
    expect(embedderChanged({ provider: 'ollama', model: 'nomic-embed-text:v1.5' }, { provider: 'ollama', model: 'nomic-embed-text' })).toBe(true)
    expect(embedderChanged({ provider: 'ollama', model: 'm' }, { provider: 'default', model: DEFAULT_EMBEDDING_MODEL })).toBe(true)
  })

  it('a provider change puts the bundled model, the saved model, or nothing in the form', () => {
    const saved = { provider: 'ollama', model: 'nomic-embed-text:v1.5' }
    expect(modelForProvider('default', saved)).toBe(DEFAULT_EMBEDDING_MODEL)
    expect(modelForProvider('ollama', saved)).toBe('nomic-embed-text:v1.5')
    expect(modelForProvider('openai', saved)).toBe('')
  })
})

describe('the Collection prefix', () => {
  it('the field shows the configured prefix, or an older server\'s raw value', () => {
    expect(prefixFieldValue({ chroma_collection: 'test_failures', collection_prefix: '' })).toBe('')
    expect(prefixFieldValue({ chroma_collection: 'demo', collection_prefix: 'demo' })).toBe('demo')
    expect(prefixFieldValue({ chroma_collection: 'test_case_search' })).toBe('test_case_search')
  })

  it('says which prefix is in use; nothing for an older server; the unreadable sentence for null', () => {
    expect(prefixInUseLine({ active_collection_prefix: '' })).toBe('In use: collections carry no prefix.')
    expect(prefixInUseLine({ active_collection_prefix: 'demo' })).toBe('In use: collections start with “demo_”.')
    expect(prefixInUseLine({})).toBeNull()
    expect(prefixInUseLine({ active_collection_prefix: null })).toBe(EMBEDDING_STATE_UNREADABLE)
  })
})

describe('refusalSentence', () => {
  const refused = (status: number, detail: unknown) => ({ response: { status, data: { detail } } })

  it('reads a 422 sentence, and other statuses only when asked', () => {
    expect(refusalSentence(refused(422, 'Name the embedding model.'))).toBe('Name the embedding model.')
    expect(refusalSentence(refused(409, 'A re-embed is already running.'))).toBeNull()
    expect(refusalSentence(refused(409, 'A re-embed is already running.'), [409, 422])).toBe('A re-embed is already running.')
  })

  it('ignores a validation array, a blank detail and a network error', () => {
    expect(refusalSentence(refused(422, [{ msg: 'bad', loc: ['body', 'x'] }]))).toBeNull()
    expect(refusalSentence(refused(422, '  '))).toBeNull()
    expect(refusalSentence(new Error('Network Error'))).toBeNull()
    expect(refusalSentence(null)).toBeNull()
  })
})
