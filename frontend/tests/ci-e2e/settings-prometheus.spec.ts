/**
 * W23d AGT-15 — Settings › Integrations › the Prometheus card (`docs/deferred-scope-2026-10-13/work/W23d-contract.md`
 * §3.2, §3.4, §4.1, §6.3), hermetic, at 1280 and 1440:
 *
 *  - as ADMIN: the card shows `Defaults in use` and four template rows; set the URL + the token + edit a label → Save
 *    → the ONE PUT body carries ONLY the changed `prometheus_*` keys (the token as typed, once);
 *  - a scripted 422 on the templates → its sentence under the list, no toast, never "Request failed …";
 *  - `Use the defaults` → the PUT body `{"prometheus_metric_templates": []}`;
 *  - `Test connection` → `POST /api/v1/integration-health/probe?provider=prometheus` → `Test connection: healthy (42 ms)`;
 *  - as QA LEAD: every card input disabled, no Save, and `Test connection` still works;
 *  - the full-row card fits with ≥ 40 px of slack in its template row (printed), nothing scrolls sideways.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-payload-metrics.ts` (G-A).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { inPageLine, minContentSlack } from '../lib/inPageGeometry'
import { expectNoHorizontalOverflow, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { ADMIN, QA_LEAD } from '../visual/production/fixtures-integrations-runtime'
import { integrationsPrometheusWorld } from '../visual/production/fixtures-payload-metrics'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const READ = '/api/v1/settings/integrations'
const PROBE = '/api/v1/integration-health/probe'
const PLACEHOLDER_SENTENCE = 'Each template must contain {service} exactly where the service name goes.'
const URL = 'http://10.20.30.40:9090'
const TOKEN = 'prom-w23d-local-sample-value'

const h1 = (p: Page) => p.getByRole('heading', { level: 1, name: 'Integrations', exact: true })
const card = (page: Page): Locator =>
  page.locator('[data-prometheus-card]').filter({ has: page.getByRole('heading', { level: 3, name: 'Prometheus', exact: true }) })
const save = (page: Page) => page.getByRole('button', { name: 'Save Integrations' })
const puts = (writes: Array<{ line: string; body: unknown }>) => writes.filter((w) => w.line === `PUT ${READ}`)

for (const width of [1280, 1440]) {
  test(`ADMIN @${width}: defaults in use; URL + token + a label → ONE PUT with only those prometheus_* keys`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const world = integrationsPrometheusWorld({ role: ADMIN })
    const { api, errors } = await openRollout(page, '/settings/integrations', { handlers: world.handlers, ready: h1, user: ADMIN })
    const prom = card(page)
    await expect(prom.getByText('Defaults in use', { exact: true })).toBeVisible()
    await expect(prom.locator('[data-prometheus-template]')).toHaveCount(4)
    await expect(prom.getByText('Not configured', { exact: true })).toBeVisible()

    await prom.getByLabel('Prometheus URL', { exact: true }).fill(URL)
    await prom.getByLabel(/^Bearer token/).fill(TOKEN)
    await prom.getByLabel('Template 1 label', { exact: true }).fill('p95 latency (checkout)')
    await save(page).click()
    await expect.poll(() => puts(world.writes).length).toBe(1)
    const body = puts(world.writes)[0].body as Record<string, unknown>
    expect(Object.keys(body).sort()).toEqual(['prometheus_base_url', 'prometheus_bearer_token', 'prometheus_metric_templates'])
    expect(body.prometheus_base_url).toBe(URL)
    expect(body.prometheus_bearer_token).toBe(TOKEN)
    const templates = body.prometheus_metric_templates as Array<Record<string, string>>
    expect(templates).toHaveLength(4)
    expect(templates[0].label).toBe('p95 latency (checkout)')

    await expectNoHorizontalOverflow(page, `prometheus card @${width}`)
    const firstRow = prom.locator('[data-prometheus-template]').first()
    const slack = await minContentSlack(firstRow)
    inPageLine('/settings/integrations prometheus card', width, { card: await minContentSlack(prom), templateRow: slack })
    expect(slack, 'the template row keeps ≥ 40 px of slack (DejaVu plan)').toBeGreaterThanOrEqual(40)
    await networkQuiet(page, api)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
}

test('a 422 on the templates reads as its sentence under the list, no toast', async ({ page }) => {
  const world = integrationsPrometheusWorld({ role: ADMIN, putAnswers: ['templates_422'] })
  const { api, errors } = await openRollout(page, '/settings/integrations', { handlers: world.handlers, ready: h1, user: ADMIN })
  const prom = card(page)
  await prom.getByLabel('Template 1 PromQL', { exact: true }).fill('up')
  await save(page).click()
  const error = prom.locator('[data-prometheus-templates] [data-field-error="prometheus_metric_templates"]')
  await expect(error).toHaveText(PLACEHOLDER_SENTENCE)
  await expect(page.getByText(/status code/)).toHaveCount(0)
  await expect(page.getByRole('status').filter({ hasText: 'Could not save integrations.' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Use the defaults sends prometheus_metric_templates: []', async ({ page }) => {
  const world = integrationsPrometheusWorld({ role: ADMIN })
  const { api, errors } = await openRollout(page, '/settings/integrations', { handlers: world.handlers, ready: h1, user: ADMIN })
  const prom = card(page)
  await prom.getByRole('button', { name: 'Use the defaults' }).click()
  await expect(prom.locator('[data-prometheus-template]')).toHaveCount(0)
  await save(page).click()
  await expect.poll(() => puts(world.writes).length).toBe(1)
  expect(puts(world.writes)[0].body).toEqual({ prometheus_metric_templates: [] })
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Test connection: the existing probe with provider=prometheus, the answer inline', async ({ page }) => {
  const world = integrationsPrometheusWorld({ role: ADMIN, probeAnswer: 'healthy' })
  const { api, errors } = await openRollout(page, '/settings/integrations', { handlers: world.handlers, ready: h1, user: ADMIN })
  const prom = card(page)
  await prom.getByRole('button', { name: 'Test connection' }).click()
  await expect(prom.getByText('Test connection: healthy (42 ms)', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  // Whole targets, never a substring: exactly one probe, of exactly this provider.
  expect(world.probes).toEqual([`${PROBE}?provider=prometheus`])
  expect(requestsTo(api, PROBE)).toHaveLength(1)
  expect(puts(world.writes)).toEqual([])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('QA LEAD: every card input disabled, no Save, and Test connection still answers', async ({ page }) => {
  const world = integrationsPrometheusWorld({ role: QA_LEAD, probeAnswer: 'healthy' })
  const { api, errors } = await openRollout(page, '/settings/integrations', { handlers: world.handlers, ready: h1, user: QA_LEAD })
  const prom = card(page)
  const controls = prom.locator('input, textarea, select')
  await expect(controls).not.toHaveCount(0)
  for (const control of await controls.all()) await expect(control).toBeDisabled()
  await expect(save(page)).toHaveCount(0)
  await prom.getByRole('button', { name: 'Test connection' }).click()
  await expect(prom.getByText('Test connection: healthy (42 ms)', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(puts(world.writes)).toEqual([])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
