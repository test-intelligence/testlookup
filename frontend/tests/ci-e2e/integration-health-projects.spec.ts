/**
 * W10 INT-04 (+ INT-07's paused webhooks) on `/settings/integration-health`, hermetic
 * (`docs/deferred-scope-2026-10-13/work/W10-contract.md` §2.4, §2.5, §3.1, §4), at 1280 and 1440:
 *
 *  - as ADMIN the page reads `GET /api/v1/integration-health/status?scope=all` once (the one changed read: the same
 *    path every older handler keys on), and lists under the deployment-wide cards one row per project integration:
 *    "GitHub · Payment Service" healthy on `ghe.fake.test/api/v3`, "GitHub · Checkout" auth_error, "GitLab · API
 *    Gateway" Not probed yet (never healthy);
 *  - Probe now on the GitLab row sends exactly `POST /api/v1/integration-health/probe?provider=gitlab&project_id=<API
 *    Gateway>` (the whole target) and re-reads the status, the row then healthy;
 *  - "Open GitHub settings" is a link to `/settings/github` (whole value) that first makes the row's project the active
 *    one; the paused webhook row says how many failures paused it and links to `/settings/webhooks`;
 *  - as QA LEAD (one project, not an admin) only that project's rows;
 *  - a QA ENGINEER is not offered the page: it is a management route, so `/settings/integration-health` sends them
 *    to `/overview` and no `/integration-health/*` request is made;
 *  - everything renders IN the page (no dialog) and `#main-content` never scrolls sideways (printed `INPAGE`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-integrations-runtime.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay } from '../lib/inPage'
import { expectMainFits, inPageLine, minContentSlack, ownOverflow } from '../lib/inPageGeometry'
import { assertHermetic, freezeClock, mockApi, seedSession, stillMotion, watchPageErrors } from '../lib/production-pages'
import { networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { NOW, OVERVIEW_ON, PROJECT_ID } from '../visual/production/fixtures'
import {
  ADMIN,
  API_GATEWAY,
  GITLAB_PROBED_MESSAGE,
  integrationHealthWorld,
  PAUSED_WEBHOOK_NAME,
  PAYMENT_SERVICE,
  PAYMENT_TARGET,
  QA_ENGINEER,
  QA_LEAD,
} from '../visual/production/fixtures-integrations-runtime'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const STATUS = '/api/v1/integration-health/status'
const STATUS_ALL = `GET ${STATUS}?scope=all`
const h1 = (p: Page) => p.getByRole('heading', { level: 1, name: 'Integration Health', exact: true })
/** A row card by its title ("GitHub · Payment Service"): the card the title is in. */
const row = (page: Page, title: string): Locator =>
  page.locator('[data-health-row]').filter({ has: page.getByText(title, { exact: true }) })
const activeProjectId = (page: Page) =>
  page.evaluate(() => (JSON.parse(localStorage.getItem('testlookup-active-project') ?? '{}') as { state?: { activeProjectId?: string } }).state?.activeProjectId)

/**
 * `#main-content` fits, and every row card's numbers are printed: its min-content slack and how many px of its title
 * the `truncate` hides (contract §4.1: the title truncates, its full text in `title`; > 0 means cut on these fonts).
 */
async function expectRowsFit(page: Page, width: number, what: string) {
  await expectMainFits(page, `${what} @${width}`)
  const numbers: Record<string, number> = {}
  for (const [i, card] of (await page.locator('[data-health-row]').all()).entries()) {
    numbers[`row${i}.slack`] = await minContentSlack(card)
    numbers[`row${i}.titleCut`] = await card.locator('span[title]').first().evaluate((el: HTMLElement) => el.scrollWidth - el.clientWidth)
    expect(await ownOverflow(card), `${what}: row ${i} does not overflow its card`).toBeLessThanOrEqual(0)
  }
  inPageLine(`/settings/integration-health ${what}`, width, numbers)
}

for (const width of [1280, 1440]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 1200 } })

    test(`as ADMIN: one scope=all read, a row per project integration, Probe now on one project — ${width}`, async ({ page }) => {
      const world = integrationHealthWorld({ viewer: 'ADMIN' })
      const { api, errors } = await openRollout(page, '/settings/integration-health', { handlers: world.handlers, ready: h1, user: ADMIN })
      const payment = row(page, 'GitHub · Payment Service')
      const checkout = row(page, 'GitHub · Checkout')
      const gateway = row(page, 'GitLab · API Gateway')
      await expect(payment).toContainText(PAYMENT_TARGET)
      await expect(payment).toContainText('healthy')
      await expect(checkout).toContainText('api.github.com')
      await expect(checkout).toContainText('auth_error')
      await expect(checkout).toContainText('HTTP 401: the token was refused')
      await expect(gateway).toContainText('Not probed yet')
      await expect(gateway, 'a row never probed is never healthy').not.toContainText('healthy')
      await networkQuiet(page, api)
      expect(requestsTo(api, STATUS), 'one read, scope=all').toEqual([STATUS_ALL])
      await expectNoOverlay(page)
      await expectRowsFit(page, width, 'admin rows')

      await gateway.getByRole('button', { name: 'Probe now' }).click()
      await expect(gateway).toContainText(GITLAB_PROBED_MESSAGE)
      await expect(gateway).not.toContainText('Not probed yet')
      await networkQuiet(page, api)
      expect(world.probes).toEqual([`/api/v1/integration-health/probe?provider=gitlab&project_id=${API_GATEWAY.id}`])
      expect(requestsTo(api, STATUS), 'the status is read again').toEqual([STATUS_ALL, STATUS_ALL])
      assertHermetic(api, errors)
    })

    test(`as ADMIN: Open GitHub settings switches to the row's project; the paused webhook links to Outbound webhooks — ${width}`, async ({ page }) => {
      const world = integrationHealthWorld({ viewer: 'ADMIN' })
      const { api, errors } = await openRollout(page, '/settings/integration-health', { handlers: world.handlers, ready: h1, user: ADMIN })
      const paused = row(page, `Webhook ${PAUSED_WEBHOOK_NAME} · Checkout`)
      await expect(paused.locator('[data-health-paused-line]')).toHaveText('Paused after 3 consecutive failures (threshold 3)')
      await expect(paused).toContainText('Last error: HTTP 500: receiver error')
      await expect(paused).toContainText('paused')
      await expect(paused.getByRole('link', { name: 'Open Outbound webhooks' })).toHaveAttribute('href', '/settings/webhooks')
      await expect(page.getByRole('region', { name: 'Paused webhooks' }).locator('[data-health-row]')).toHaveCount(1)
      // The project rows sit in one section per provider.
      await expect(page.getByRole('region', { name: 'GitHub projects' }).locator('[data-health-row]')).toHaveCount(2)
      await expect(page.getByRole('region', { name: 'GitLab projects' }).locator('[data-health-row]')).toHaveCount(1)
      await expect(row(page, 'GitLab · API Gateway').getByRole('link', { name: 'Open GitLab settings' })).toHaveAttribute('href', '/settings/gitlab')

      const open = row(page, 'GitHub · Payment Service').getByRole('link', { name: 'Open GitHub settings' })
      await expect(open).toHaveAttribute('href', '/settings/github')
      expect(await activeProjectId(page)).toBe(PROJECT_ID)
      await open.click()
      await expect(page).toHaveURL(/\/settings\/github$/)
      await expect.poll(() => activeProjectId(page), { message: 'the row\'s project is the active one' }).toBe(PAYMENT_SERVICE.id)
      await networkQuiet(page, api)
      expect(requestsTo(api, `/api/v1/projects/${PAYMENT_SERVICE.id}/github-integration`).length, 'the GitHub page reads that project').toBeGreaterThan(0)
      assertHermetic(api, errors)
    })

    test(`as QA LEAD (one project): only that project's rows — ${width}`, async ({ page }) => {
      const world = integrationHealthWorld({ viewer: 'QA_LEAD' })
      const { api, errors } = await openRollout(page, '/settings/integration-health', { handlers: world.handlers, ready: h1, user: QA_LEAD })
      await expect(row(page, 'GitHub · Checkout')).toContainText('auth_error')
      await expect(row(page, `Webhook ${PAUSED_WEBHOOK_NAME} · Checkout`)).toBeVisible()
      await expect(page.getByText('GitHub · Payment Service', { exact: true })).toHaveCount(0)
      await expect(page.getByText('GitLab · API Gateway', { exact: true })).toHaveCount(0)
      await expect(page.locator('[data-health-row]')).toHaveCount(2)
      await networkQuiet(page, api)
      expect(requestsTo(api, STATUS)).toEqual([STATUS_ALL])
      expect(world.probes).toEqual([])
      await expectRowsFit(page, width, 'qa lead rows')
      assertHermetic(api, errors)
    })
  })
}

test('a QA ENGINEER is not offered the page: /settings/integration-health sends them to /overview, nothing of it is asked', async ({ page }) => {
  const world = integrationHealthWorld({ viewer: 'QA_LEAD' })
  const errors = watchPageErrors(page)
  await seedSession(page, { theme: 'signal', user: QA_ENGINEER, projectId: PROJECT_ID })
  await freezeClock(page, NOW)
  await stillMotion(page)
  const api = await mockApi(page, QA_ENGINEER, OVERVIEW_ON)
  await page.goto('/settings/integration-health')
  await expect(page).toHaveURL(/\/overview(\?|$)/)
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible({ timeout: 20_000 })
  await networkQuiet(page, api)
  expect([...api.seen, ...api.unhandled].filter((line) => /^[A-Z]+ \/api\/v1\/integration-health\//.test(line)), 'no /integration-health request').toEqual([])
  expect(world.probes).toEqual([])
  assertHermetic(api, errors)
})
