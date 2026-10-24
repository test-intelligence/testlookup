/**
 * W12 SRCH-02 the embedder and the Collection prefix (`docs/deferred-scope-2026-10-13/work/W12-contract.md` §2.4,
 * §0.18, §3, §4.1, §4.2, D-11), hermetic, on `/settings/ai` (the **Embedding** card) and `/settings/storage` (the
 * **ChromaDB (Vector Store)** card):
 *
 *  - the page load asks NOTHING new (`embedding_index` rides `GET /settings/ai`; `GET …/embedding-index` only while a
 *    build runs);
 *  - the `data-embedding-index` block: in sync → `In use: Bundled MiniLM (all-MiniLM-L6-v2).`; configured ≠ in use →
 *    `… Configured Ollama · nomic-embed-text:v1.5 is not in use yet.` + **Re-embed now** (ADMIN only); building →
 *    `Re-embedding to Ollama · nomic-embed-text:v1.5: 1,200 / 2,290. …` + one line per family, polled every 5 s until
 *    the switch, then nothing; failed → the sentence + **Retry**; `embedding_index: null` → `Could not read the
 *    embedding index state.`;
 *  - **Provider** is a select of Bundled MiniLM · Ollama · OpenAI (OpenAI disabled with the offline sentence while
 *    offline); a form that differs from the saved embedder warns before Save; Save → the PUT with the new embedder
 *    and the build shown;
 *  - the Collection prefix field, its help, `In use: collections carry no prefix.` / `… start with “demo_”.`, the
 *    stored-value note, the PUT body and the 422 sentence for an invalid prefix;
 *  - both card headings stay verbatim (the pairing of `settings-two-column-forms.spec.ts` holds); nothing scrolls
 *    sideways at 1280 and 1440.
 *
 * Harness: `tests/lib/production-pages.ts` (fail closed, pinned clock). Fixtures: `fixtures-search.ts`
 * (`embeddingWorld`, over `fixtures-ai-config.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { boxOf, inPageLine, minContentSlack } from '../lib/inPageGeometry'
import { assertHermetic, openProductionPage } from '../lib/production-pages'
import { expectNoHorizontalOverflow, networkQuiet, observed, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID } from '../visual/production/fixtures'
import {
  ADMIN,
  AI_SETTINGS_API,
  EMBEDDING_INDEX_API,
  EMBEDDING_REBUILD_API,
  EMBEDDING_TEXT,
  embeddingWorld,
  INDEX_BUILDING,
  INDEX_FAILED,
  INDEX_MINILM_IN_SYNC,
  INDEX_NOMIC_NOT_APPLIED,
  QA_LEAD,
  STORAGE_API,
  type EmbeddingWorldOptions,
} from '../visual/production/fixtures-search'

test.use({ viewport: { width: 1280, height: 1000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const LINE = {
  inSyncMiniLM: 'In use: Bundled MiniLM (all-MiniLM-L6-v2).',
  notApplied: 'Configured Ollama · nomic-embed-text:v1.5 is not in use yet.',
  building: 'Re-embedding to Ollama · nomic-embed-text:v1.5: 1,200 / 2,290. Semantic search uses Bundled MiniLM until it finishes; cached analyses start empty.',
  families: 'Test results 1,200 / 2,290 · Knowledge chunks done · Agent memory pending',
  failed: 'Re-embedding to Ollama · nomic-embed-text:v1.5 failed: ConnectError while embedding test results. Semantic search still uses Bundled MiniLM.',
  switched: 'In use: Ollama (nomic-embed-text:v1.5).',
  noPrefix: 'In use: collections carry no prefix.',
  demoPrefix: 'In use: collections start with “demo_”.',
} as const

const card = (page: Page, name: string) =>
  page.locator('[data-settings-form-grid] > *').filter({ has: page.getByRole('heading', { name, exact: true }) })
const embedding = (page: Page) => card(page, 'Embedding')
const block = (page: Page) => embedding(page).locator('[data-embedding-index]')
const chroma = (page: Page) => card(page, 'ChromaDB (Vector Store)')
const aiReady = (p: Page) => p.getByRole('heading', { level: 1, name: 'AI Configuration' })
const storageReady = (p: Page) => p.getByRole('heading', { level: 1, name: 'Data & Storage' })
const writes = (api: Parameters<typeof observed>[0]) => observed(api).filter((line) => !line.startsWith('GET '))

async function open(page: Page, path: string, options: EmbeddingWorldOptions & { user?: typeof ADMIN } = {}) {
  watchNativeDialogs(page)
  const world = embeddingWorld(options)
  const user = options.user ?? ADMIN
  const opened = await openProductionPage(page, path, {
    theme: 'signal',
    now: NOW,
    me: user,
    user,
    projectId: PROJECT_ID,
    handlers: world.handlers,
    ready: path.startsWith('/settings/ai') ? aiReady : storageReady,
  })
  return { ...opened, world }
}

// ── Settings › AI › Embedding ──────────────────────────────────────────────

test('in sync: In use: Bundled MiniLM; no Re-embed now; the load asked nothing new', async ({ page }) => {
  const { api, errors } = await open(page, '/settings/ai', { index: INDEX_MINILM_IN_SYNC })
  await expect(block(page)).toContainText(LINE.inSyncMiniLM)
  await expect(block(page)).not.toContainText('not in use yet')
  await expect(embedding(page).getByRole('button', { name: 'Re-embed now' })).toHaveCount(0)
  const provider = embedding(page).getByLabel('Provider')
  await expect(provider).toHaveValue('default')
  await expect(embedding(page).getByLabel('Model')).toHaveValue('all-MiniLM-L6-v2')
  await networkQuiet(page, api)
  expect(requestsTo(api, EMBEDDING_INDEX_API), 'no embedding-index read on load (idle)').toEqual([])
  // The page reads its config in a useEffect (twice under the dev server's StrictMode, as before W12): the load
  // asks /settings/ai and sends nothing.
  expect(requestsTo(api, AI_SETTINGS_API).length).toBeGreaterThanOrEqual(1)
  expect(writes(api)).toEqual([])
  assertHermetic(api, errors)
})

test('configured ≠ in use (the nomic default): "… is not in use yet." and, for an admin, Re-embed now → the rebuild POST, the build, the 5 s poll to the switch', async ({ page }) => {
  const { api, errors, world } = await open(page, '/settings/ai', { index: INDEX_NOMIC_NOT_APPLIED, pollsUntilDone: 1 })
  await expect(block(page)).toContainText(`${LINE.inSyncMiniLM} ${LINE.notApplied}`)
  await networkQuiet(page, api)
  expect(requestsTo(api, EMBEDDING_INDEX_API)).toEqual([])

  await embedding(page).getByRole('button', { name: 'Re-embed now' }).click()
  await expect(block(page)).toContainText(LINE.building)
  await expect(block(page)).toContainText(LINE.families)
  expect(world.writes).toEqual([{ line: `POST ${EMBEDDING_REBUILD_API}`, body: null }])
  await expectNoOverlay(page)

  // `useEmbeddingIndex` polls every 5 s while building; the fixture switches on the second poll.
  await expect(block(page)).toContainText(LINE.switched, { timeout: 20_000 })
  await expect(block(page)).not.toContainText('Re-embedding to')
  const polls = requestsTo(api, EMBEDDING_INDEX_API).length
  expect(polls, 'the build was polled').toBeGreaterThanOrEqual(2)
  await page.waitForTimeout(6_000)
  expect(requestsTo(api, EMBEDDING_INDEX_API).length, 'no poll once idle').toBe(polls)
  assertHermetic(api, errors)
})

test('a QA lead reads the state and has no Re-embed now or Retry', async ({ page }) => {
  const { api, errors } = await open(page, '/settings/ai', { index: INDEX_FAILED, user: QA_LEAD })
  await expect(block(page)).toContainText(LINE.failed)
  await expect(embedding(page).getByRole('button', { name: /^(Re-embed now|Retry)$/ })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(writes(api)).toEqual([])
  assertHermetic(api, errors)
})

test('a build in progress shows its progress per family and polls', async ({ page }) => {
  const building = await open(page, '/settings/ai', { index: INDEX_BUILDING, pollsUntilDone: 50 })
  await expect(block(page)).toContainText(LINE.building)
  await expect(block(page)).toContainText(LINE.families)
  await expect.poll(() => requestsTo(building.api, EMBEDDING_INDEX_API).length, { timeout: 12_000, message: 'polled while building' }).toBeGreaterThanOrEqual(1)
  assertHermetic(building.api, building.errors)
})

test('a failed build: the sentence, Retry → the rebuild POST and the build', async ({ page }) => {
  const { api, errors, world } = await open(page, '/settings/ai', { index: INDEX_FAILED, pollsUntilDone: 50 })
  await expect(block(page)).toContainText(LINE.failed)
  await embedding(page).getByRole('button', { name: 'Retry' }).click()
  await expect(block(page)).toContainText(LINE.building)
  expect(world.writes).toEqual([{ line: `POST ${EMBEDDING_REBUILD_API}`, body: null }])
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('embedding_index: null reads "Could not read the embedding index state."', async ({ page }) => {
  const { api, errors } = await open(page, '/settings/ai', { index: null })
  await expect(embedding(page).getByText(EMBEDDING_TEXT.unreadable, { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Provider is a select; OpenAI is disabled offline with the sentence; a changed form warns, Save sends the new embedder and shows the build', async ({ page }) => {
  const { api, errors, world } = await open(page, '/settings/ai', { index: INDEX_MINILM_IN_SYNC, embeddingProvider: 'default', embeddingModel: 'all-MiniLM-L6-v2', pollsUntilDone: 50 })
  const provider = embedding(page).getByLabel('Provider')
  await expect(provider.locator('option')).toHaveText(['Bundled MiniLM', 'Ollama', 'OpenAI'])
  const openai = provider.locator('option', { hasText: 'OpenAI' })
  await expect(openai).toBeDisabled()
  await expect(openai).toHaveAttribute('title', EMBEDDING_TEXT.openaiOffline)
  await expect(embedding(page).getByText(EMBEDDING_TEXT.saveWarning, { exact: true })).toHaveCount(0)

  await provider.selectOption('ollama')
  await embedding(page).getByLabel('Model').fill('nomic-embed-text:v1.5')
  await expect(embedding(page).getByText(EMBEDDING_TEXT.saveWarning, { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  await page.getByRole('button', { name: 'Save Configuration' }).click()
  await expect(block(page)).toContainText('Re-embedding to Ollama · nomic-embed-text:v1.5')
  const puts = world.writes.filter((w) => w.line === `PUT ${AI_SETTINGS_API}`)
  expect(puts).toHaveLength(1)
  expect(puts[0].body).toMatchObject({ embedding_provider: 'ollama', embedding_model: 'nomic-embed-text:v1.5' })
  await expectNoOverlay(page)
  assertHermetic(api, errors)
})

// ── Settings › Data & storage › ChromaDB ───────────────────────────────────

test('the Collection prefix: its label, help and "no prefix" line; the heading verbatim', async ({ page }) => {
  const { api, errors } = await open(page, '/settings/storage', { index: INDEX_MINILM_IN_SYNC, storedPrefix: '' })
  await expect(chroma(page)).toHaveCount(1)
  const field = chroma(page).getByLabel('Collection prefix')
  await expect(field).toHaveValue('')
  await expect(field).toHaveAttribute('placeholder', 'none')
  await expect(chroma(page)).toContainText(EMBEDDING_TEXT.prefixHelp)
  await expect(chroma(page)).toContainText(LINE.noPrefix)
  await networkQuiet(page, api)
  expect(requestsTo(api, STORAGE_API).length, 'the storage config is read (twice under StrictMode, as before)').toBeGreaterThanOrEqual(1)
  expect(writes(api)).toEqual([])
  assertHermetic(api, errors)
})

test('a stored invalid value reads as no prefix, with the note', async ({ page }) => {
  const invalid = await open(page, '/settings/storage', { index: INDEX_MINILM_IN_SYNC, storedPrefix: 'test_failures' })
  await expect(chroma(page)).toContainText("The stored value 'test_failures' is not a valid prefix and is ignored: collections carry no prefix.")
  await expect(chroma(page).getByLabel('Collection prefix')).toHaveValue('')
  assertHermetic(invalid.api, invalid.errors)
})

test('stored "demo": In use: collections start with “demo_”.', async ({ page }) => {
  const demo = { ...INDEX_MINILM_IN_SYNC, active: { ...INDEX_MINILM_IN_SYNC.active, prefix: 'demo' }, configured: { ...INDEX_MINILM_IN_SYNC.configured, prefix: 'demo' } }
  const { api, errors } = await open(page, '/settings/storage', { index: demo, storedPrefix: 'demo' })
  await expect(chroma(page).getByLabel('Collection prefix')).toHaveValue('demo')
  await expect(chroma(page)).toContainText(LINE.demoPrefix)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Save with a new prefix sends it (one PUT); an invalid one is refused with the sentence', async ({ page }) => {
  const { api, errors, world } = await open(page, '/settings/storage', { index: INDEX_MINILM_IN_SYNC, storedPrefix: '' })
  const field = chroma(page).getByLabel('Collection prefix')
  await field.fill('Demo_1')
  await page.getByRole('button', { name: 'Save Configuration' }).click()
  await expect(page.getByText(EMBEDDING_TEXT.prefixInvalid, { exact: true }).first()).toBeVisible()
  await field.fill('demo')
  await page.getByRole('button', { name: 'Save Configuration' }).click()
  await networkQuiet(page, api)
  const puts = world.writes.filter((w) => w.line === `PUT ${STORAGE_API}`)
  expect(puts.map((w) => (w.body as { chroma_collection?: string }).chroma_collection)).toEqual(['Demo_1', 'demo'])
  expect(world.state.storedPrefix).toBe('demo')
  assertHermetic(api, errors)
})

for (const width of [1280, 1440]) {
  test(`@${width}: the Embedding card (building, the widest state) still pairs with LLM Provider and nothing scrolls sideways`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    const ai = await open(page, '/settings/ai', { index: INDEX_BUILDING, pollsUntilDone: 50 })
    await expect(block(page)).toContainText(LINE.families)
    const [llm, emb] = [await boxOf(card(page, 'LLM Provider')), await boxOf(embedding(page))]
    expect(Math.abs(llm.top - emb.top), 'same top edge').toBeLessThanOrEqual(2)
    expect(llm.left).toBeLessThan(emb.left)
    await expectNoHorizontalOverflow(page, `/settings/ai embedding @${width}`)
    const slack = await minContentSlack(embedding(page))
    inPageLine('/settings/ai embedding-card', width, { card: emb.width, slack })
    expect(slack, 'the Embedding card is not narrower than its content').toBeGreaterThanOrEqual(0)
    assertHermetic(ai.api, ai.errors)
  })
}
