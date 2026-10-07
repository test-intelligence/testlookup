/**
 * Settings forms in two columns at >= 1280 px (UX redesign P5, plan item 4).
 *
 * A settings page sits beside the 220 px settings sub-nav, so its content
 * column is ~730 px at a 1280 px window and ~890 px at 1440. The forms used to
 * sit in a `max-w-2xl` / `max-w-3xl` column inside that; now the form cards
 * pair up side by side. This spec measures the real layout (JSDOM cannot):
 *
 *  - the paired cards share a top edge and split the content column in two;
 *  - a full-width card (AI keys, the storage status strip) spans the column;
 *  - the Retention page uses the whole column (its form was already a
 *    two-column field grid; the cap was the only narrowing);
 *  - nothing scrolls sideways, and every page has the compact header.
 *
 * Hermetic and fail closed (`production-pages.ts`): every request is mocked.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { assertHermetic, openProductionPage, type ApiHandlers } from '../lib/production-pages'
import { LAYOUT, NOW, PROJECT_ID, USER } from '../visual/production/fixtures'

/** The settings pages above are ADMIN-only for their controls. */
const ADMIN = { ...USER, role: 'ADMIN', username: 'admin', full_name: 'Admin' }

const AI_CONFIG = {
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
  ai_timeout_seconds: 300,
  deep_investigation_enabled: true,
  finetune_enabled: false,
  openai_key_set: false,
  google_key_set: false,
  anthropic_key_set: false,
  openrouter_key_set: true,
  analysis_mode: 'auto',
  ml_model_available: false,
  ml_model_accuracy: null,
  ml_training_sample_count: 12,
  ml_human_label_count: 0,
  ml_human_label_floor: 50,
  ml_maturity: 'not_trained',
  knowledge_rag_enabled: false,
}

const MODEL_STATUS = {
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
  checked_at: '2026-09-18T00:00:00+00:00',
}

const STORAGE = {
  storage_backend: 'minio',
  postgres_connected: true,
  mongo_connected: true,
  redis_connected: false,
  minio_endpoint: 'minio:9000',
  minio_bucket_name: 'testlookup-artifacts',
  minio_use_ssl: false,
  chroma_host: 'chromadb',
  chroma_port: 8001,
  chroma_collection: 'test_failures',
}

const MFA_POLICY = {
  require_mfa: false,
  required_for_role: null,
  lockout_enabled: true,
  lockout_threshold: 5,
  lockout_duration_minutes: 15,
}

const RETENTION_POLICY = {
  enabled: true,
  raw_events_days: 90,
  runs_days: 365,
  artifacts_days: 180,
  audit_days: 2555,
  source: 'default',
  last_purge: null,
}

const PROJECT_STORAGE = {
  project_id: PROJECT_ID,
  computed_at: '2026-09-18T11:00:00Z',
  stores: [
    { store: 'postgres', measured: true, exact: true, complete: true, bytes: 1_048_576, items: 120, estimate_basis: null, unreachable_reason: null },
  ],
  total_bytes: 1_048_576,
  total_is_estimate: false,
  fully_measured: true,
}

const DELETED_PROJECTS_STORAGE = {
  computed_at: '2026-09-18T11:00:00Z',
  projects_total: 0,
  projects_measured: 0,
  truncated: false,
  projects: [],
  total_bytes: 0,
  total_is_estimate: false,
  unreachable_by_retention: 0,
}

const HANDLERS: ApiHandlers = [
  // Before LAYOUT: the AI page reads the full config, the chrome a subset.
  ['/api/v1/settings/ai', () => AI_CONFIG],
  ['/api/v1/settings/ai/model-status', () => MODEL_STATUS],
  ['/api/v1/settings/storage', () => STORAGE],
  ['/api/v1/settings/mfa-policy', () => MFA_POLICY],
  [`/api/v1/projects/${PROJECT_ID}/retention-policy`, () => RETENTION_POLICY],
  [`/api/v1/projects/${PROJECT_ID}/storage`, () => PROJECT_STORAGE],
  ['/api/v1/admin/storage/deleted-projects', () => DELETED_PROJECTS_STORAGE],
  ['/api/v1/auth/me/dismissals', () => ({ dismissed: [] })],
  ...LAYOUT,
]

const VIEWPORTS = [
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
] as const

/** Two columns of a 16 px gap: each about half the grid. */
const GAP_PX = 16
const TOLERANCE_PX = 2

async function open(page: Page, path: string, title: string) {
  return openProductionPage(page, path, {
    theme: 'signal',
    now: NOW,
    me: ADMIN,
    user: ADMIN,
    projectId: PROJECT_ID,
    handlers: HANDLERS,
    ready: (p) => p.getByRole('heading', { level: 1, name: title }),
  })
}

async function box(locator: Locator) {
  const b = await locator.boundingBox()
  if (!b) throw new Error(`not laid out: ${locator}`)
  return b
}

/** The card (direct child of the form grid) whose heading is `name`. */
const card = (page: Page, name: string) =>
  page.locator('[data-settings-form-grid] > *').filter({ has: page.getByRole('heading', { name, exact: true }) })

/** `left` and `right` sit side by side, each half of the grid. */
async function expectPair(page: Page, left: string, right: string) {
  const grid = await box(page.locator('[data-settings-form-grid]'))
  const a = await box(card(page, left))
  const b = await box(card(page, right))
  const half = (grid.width - GAP_PX) / 2
  expect(Math.abs(a.y - b.y), `${left} and ${right} share a top edge`).toBeLessThanOrEqual(TOLERANCE_PX)
  expect(a.x, `${left} is the left column`).toBeLessThan(b.x)
  expect(Math.abs(a.width - half), `${left} is half the grid`).toBeLessThanOrEqual(TOLERANCE_PX)
  expect(Math.abs(b.width - half), `${right} is half the grid`).toBeLessThanOrEqual(TOLERANCE_PX)
}

/** `name` spans the grid. */
async function expectFullWidth(page: Page, name: string) {
  const grid = await box(page.locator('[data-settings-form-grid]'))
  const c = await box(card(page, name))
  expect(Math.abs(c.width - grid.width), `${name} spans both columns`).toBeLessThanOrEqual(TOLERANCE_PX)
}

/** The form grid fills the settings content column (no max-w cap left around it). */
async function expectFillsContentColumn(page: Page, region: Locator) {
  const content = await box(page.locator('[data-settings-content]'))
  const r = await box(region)
  expect(Math.abs(r.width - content.width), 'the form fills the content column').toBeLessThanOrEqual(TOLERANCE_PX)
}

async function expectChrome(page: Page) {
  await expect(page.locator('[data-page-header]')).toHaveAttribute('data-compact', 'true')
  const main = page.locator('#main-content')
  const overflow = await main.evaluate((el) => el.scrollWidth - el.clientWidth)
  expect(overflow, 'nothing scrolls sideways').toBeLessThanOrEqual(0)
}

for (const viewport of VIEWPORTS) {
  test.describe(`settings forms at ${viewport.width} x ${viewport.height}`, () => {
    test.use({ viewport })

    test('AI configuration pairs its cards; the API keys span both columns', async ({ page }) => {
      const { api, errors } = await open(page, '/settings/ai', 'AI Configuration')
      await expect(page.getByText('Model Availability & Fallback Chain')).toBeVisible()

      await expectFillsContentColumn(page, page.locator('[data-settings-form-grid]'))
      await expectPair(page, 'Analysis Engine', 'Model Availability & Fallback Chain')
      await expectPair(page, 'LLM Provider', 'Embedding')
      await expectPair(page, 'Pipeline Settings', 'Knowledge-Grounded Generation')
      await expectFullWidth(page, 'Cloud API Keys')

      // The wrapped "Confidence Threshold (0-100)" label must not push its input
      // below its neighbour's (items-end on that field grid).
      const threshold = await box(page.getByLabel('Confidence Threshold (0-100)'))
      const timeout = await box(page.getByLabel('Timeout (seconds)'))
      expect(Math.abs(threshold.y - timeout.y), 'the two pipeline inputs are level').toBeLessThanOrEqual(TOLERANCE_PX)

      // Save sits under the grid.
      const grid = await box(page.locator('[data-settings-form-grid]'))
      const save = await box(page.getByRole('button', { name: 'Save Configuration' }))
      expect(save.y).toBeGreaterThanOrEqual(grid.y + grid.height)

      await expectChrome(page)
      assertHermetic(api, errors)
    })

    test('Data & storage: the status strip across, the two stores side by side', async ({ page }) => {
      const { api, errors } = await open(page, '/settings/storage', 'Data & Storage')
      await expect(page.getByLabel('Endpoint')).toHaveValue('minio:9000')

      await expectFillsContentColumn(page, page.locator('[data-settings-form-grid]'))
      await expectFullWidth(page, 'Infrastructure')
      await expectPair(page, 'Object Storage (MinIO / S3)', 'ChromaDB (Vector Store)')

      await expectChrome(page)
      assertHermetic(api, errors)
    })

    test('MFA & lockout: the requirement and the lockout side by side', async ({ page }) => {
      const { api, errors } = await open(page, '/settings/mfa-policy', 'MFA & Lockout Policy')
      await expect(page.getByLabel('Failed attempts before lockout')).toHaveValue('5')

      await expectFillsContentColumn(page, page.locator('[data-settings-form-grid]'))
      await expectPair(page, 'Require two-factor authentication', 'Failed sign-in lockout')

      await expectChrome(page)
      assertHermetic(api, errors)
    })

    test('Retention & purge uses the whole content column; its fields stay two per row', async ({ page }) => {
      const { api, errors } = await open(page, '/settings/retention', 'Retention & Purge')
      const raw = page.locator('#retention-raw_events_days')
      await expect(raw).toHaveValue('90')

      const policy = page
        .locator('section.card')
        .filter({ has: page.getByRole('heading', { name: 'Retention policy', exact: true }) })
      await expectFillsContentColumn(page, policy)
      const runs = await box(page.locator('#retention-runs_days'))
      const rawBox = await box(raw)
      expect(Math.abs(rawBox.y - runs.y), 'two day fields per row').toBeLessThanOrEqual(TOLERANCE_PX)
      expect(rawBox.x).toBeLessThan(runs.x)

      await expectChrome(page)
      assertHermetic(api, errors)
    })
  })
}
