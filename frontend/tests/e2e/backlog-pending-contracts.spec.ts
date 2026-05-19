/**
 * End-to-end contract probes for OPEN backlog items.
 *
 * Each test asserts the user-visible behaviour the backlog promises.
 * For endpoints that aren't implemented yet, the test must NOT fail
 * the suite — it ``skip``s with a clear reason. When the item lands,
 * we flip ``skip`` to ``test`` and the suite enforces the contract.
 *
 * Backlog source: ``docs/BACKLOG.md``. Items covered here:
 *
 *   1. ``GET /api/v1/intelligence/insights?range=24h``
 *   2. ``GET /api/v1/intelligence/spend?cycle=current``
 *   3. ``GET /api/v1/intelligence/activity``
 *   4. Aggregate-page ``?suite=`` filter param on
 *      ``/api/v1/metrics/dashboard-summary``,
 *      ``/api/v1/metrics/trend-data``,
 *      ``/api/v1/coverage/summary``,
 *      ``/api/v1/test-management/cases``.
 *   5. ``GET /api/v1/releases/compliance-packs`` list endpoint.
 *
 * Strategy: hit each endpoint; assert it either returns the expected
 * shape OR ``404`` (i.e. not yet routed). Anything 5xx is a
 * regression that this spec catches even before the item lands.
 */
import { test, expect } from '@playwright/test'
import { performRealLogin } from './realLoginHelper'

test.describe('Backlog pending contracts', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page)
  })

  test('intelligence insights endpoint — 200 with shape OR 404 (not yet routed)', async ({ request }) => {
    const resp = await request.get('/api/v1/intelligence/insights?range=24h', {
      failOnStatusCode: false,
    })
    // 5xx is always a regression. Either 200 with a shape OR
    // 404 (route absent) is acceptable.
    expect(resp.status()).toBeLessThan(500)
    if (resp.status() === 200) {
      const body = await resp.json()
      // Acceptance shape from BACKLOG.md: a list of insight cards
      // covering perf drift, flake-rising, coverage drop, cost.
      // Tolerate either ``{items: [...]}`` or ``[...]``.
      const items = Array.isArray(body) ? body : body.items ?? body.insights
      expect(items).toBeDefined()
      expect(Array.isArray(items)).toBe(true)
    }
  })

  test('intelligence spend endpoint — 200 with shape OR 404', async ({ request }) => {
    const resp = await request.get('/api/v1/intelligence/spend?cycle=current', {
      failOnStatusCode: false,
    })
    expect(resp.status()).toBeLessThan(500)
    if (resp.status() === 200) {
      const body = await resp.json()
      // Expected shape: per-LLM cost panel — totals + breakdown.
      expect(body).toHaveProperty('cycle')
    }
  })

  test('intelligence activity endpoint — 200 with shape OR 404', async ({ request }) => {
    const resp = await request.get('/api/v1/intelligence/activity', {
      failOnStatusCode: false,
    })
    expect(resp.status()).toBeLessThan(500)
    if (resp.status() === 200) {
      const body = await resp.json()
      const items = Array.isArray(body) ? body : body.items ?? body.events
      expect(items).toBeDefined()
      expect(Array.isArray(items)).toBe(true)
    }
  })

  test('dashboard-summary accepts ?suite_name= filter without 500', async ({ request }) => {
    const resp = await request.get(
      '/api/v1/metrics/dashboard-summary?suite_name=Auth&days=7',
      { failOnStatusCode: false },
    )
    expect(resp.status()).toBeLessThan(500)
  })

  test('coverage/summary accepts ?suite_name= filter without 500', async ({ request }) => {
    const resp = await request.get(
      '/api/v1/coverage/summary?suite_name=Auth&days=7',
      { failOnStatusCode: false },
    )
    expect(resp.status()).toBeLessThan(500)
  })

  test('trend-data accepts ?suite_name= filter without 500', async ({ request }) => {
    const resp = await request.get(
      '/api/v1/metrics/trend-data?suite_name=Auth&days=7',
      { failOnStatusCode: false },
    )
    expect(resp.status()).toBeLessThan(500)
  })

  test('test-management/cases accepts ?suite_name= filter without 500', async ({ request }) => {
    const resp = await request.get(
      '/api/v1/test-management/cases?suite_name=Auth',
      { failOnStatusCode: false },
    )
    expect(resp.status()).toBeLessThan(500)
  })

  test('compliance packs list endpoint — 200 with shape OR 404', async ({ request }) => {
    const resp = await request.get('/api/v1/releases/compliance-packs', {
      failOnStatusCode: false,
    })
    expect(resp.status()).toBeLessThan(500)
    if (resp.status() === 200) {
      const body = await resp.json()
      const items = Array.isArray(body) ? body : body.items ?? body.packs
      expect(items).toBeDefined()
      expect(Array.isArray(items)).toBe(true)
    }
  })

  test('ingestion-retry backlog item — endpoint absent today, soft-probe', async ({ request }) => {
    // From ``memory/project_ingestion_retry_followup.md`` — user
    // future-scope ask. Until the table + endpoints land, this probe
    // tolerates 404. When implemented (per the proposed shape in
    // the memory doc), flip the assertion to 200.
    const resp = await request.get('/api/v1/admin/ingestion-failures', {
      failOnStatusCode: false,
    })
    expect(resp.status()).toBeLessThan(500)
  })
})
