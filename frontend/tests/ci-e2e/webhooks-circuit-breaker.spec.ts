/**
 * W10 INT-07 on `/settings/webhooks`, hermetic (`docs/deferred-scope-2026-10-13/work/W10-contract.md` §2.5, §4), at
 * 1280 and 1440:
 *
 *  - as QA LEAD a subscription the breaker paused (`disabled_reason: "circuit_open"`) reads PAUSED, says
 *    `Paused after 3 consecutive failures (a refused or unreachable target counts too). Last error: …` in its card, and
 *    offers Re-enable: exactly one `PATCH /api/v1/webhooks/{id}` with the FULL write and `enabled: true`, the list
 *    re-read, `Webhook re-enabled`, the paused line and chip gone;
 *  - an enabled subscription that has started failing shows its streak (`2/10 in a row`);
 *  - New webhook with Pause after 3 sends `breaker_threshold: 3`;
 *  - a QA ENGINEER has neither Re-enable nor the toggle: the page is a management route, so `/settings/webhooks` sends
 *    them to `/overview` and nothing of it is asked or written;
 *  - in the page (no dialog), `#main-content` never scrolls sideways; the header row's slack is printed (`INPAGE`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-integrations-runtime.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay } from '../lib/inPage'
import { expectMainFits, inPageLine, minContentSlack } from '../lib/inPageGeometry'
import { assertHermetic, freezeClock, mockApi, seedSession, stillMotion, watchPageErrors } from '../lib/production-pages'
import { networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { NOW, OVERVIEW_ON, PROJECT_ID } from '../visual/production/fixtures'
import { PAUSED_WEBHOOK, QA_ENGINEER, QA_LEAD, STREAK_WEBHOOK, webhooksBreakerWorld } from '../visual/production/fixtures-integrations-runtime'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const LIST = '/api/v1/webhooks'
const PAUSED_LINE = `Paused after 3 consecutive failures (a refused or unreachable target counts too). Last error: ${PAUSED_WEBHOOK.last_error}`
const h1 = (p: Page) => p.getByRole('heading', { level: 1, name: 'Outbound Webhooks', exact: true })
/** A subscription's card: the item of the `space-y-2` list that holds its Delete control (as `in-page-settings.spec.ts`). */
const hookCard = (page: Page, name: string): Locator =>
  page.locator('div.space-y-2 > div').filter({ has: page.getByRole('button', { name: `Delete ${name}` }) })
const toast = (page: Page, text: string | RegExp) => page.getByRole('status').filter({ hasText: text })

async function expectCardsFit(page: Page, width: number, what: string) {
  await expectMainFits(page, `${what} @${width}`)
  const numbers: Record<string, number> = {}
  for (const name of [PAUSED_WEBHOOK.name, STREAK_WEBHOOK.name]) {
    const card = hookCard(page, name)
    if ((await card.count()) === 0) continue
    // The header row is a flex row of the name, target, events and the actions (`ml-auto`): its slack is the room
    // left before it would wrap or overflow.
    numbers[name] = await minContentSlack(card.locator('> div').first())
  }
  inPageLine(`/settings/webhooks ${what}`, width, numbers)
}

for (const width of [1280, 1440]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 1000 } })

    test(`a paused subscription: PAUSED, the line, Re-enable sends the full write with enabled: true — ${width}`, async ({ page }) => {
      const world = webhooksBreakerWorld()
      const { api, errors } = await openRollout(page, '/settings/webhooks', { handlers: world.handlers, ready: h1, user: QA_LEAD })
      const paused = hookCard(page, PAUSED_WEBHOOK.name)
      await expect(paused.getByText('PAUSED', { exact: true })).toBeVisible()
      await expect(paused.getByText('ENABLED', { exact: true })).toHaveCount(0)
      await expect(paused.getByText(PAUSED_LINE, { exact: true })).toBeVisible()
      const streak = hookCard(page, STREAK_WEBHOOK.name)
      await expect(streak).toContainText('✓ 4 · ✗ 2 · 2/10 in a row')
      await expect(streak.getByRole('button', { name: 'Re-enable' }), 'an enabled row offers no Re-enable').toHaveCount(0)
      await networkQuiet(page, api)
      await expectNoOverlay(page)
      await expectCardsFit(page, width, 'paused row')

      const reads = requestsTo(api, LIST).length
      await paused.getByRole('button', { name: 'Re-enable' }).click()
      await expect(toast(page, 'Webhook re-enabled')).toBeVisible()
      await expect(paused.getByText('PAUSED', { exact: true }), 'the badge is gone').toHaveCount(0)
      await expect(paused.getByText('ENABLED', { exact: true })).toBeVisible()
      await expect(paused.getByText(/^Paused after/)).toHaveCount(0)
      await expect(paused.getByRole('button', { name: 'Re-enable' })).toHaveCount(0)
      await networkQuiet(page, api)
      expect(world.writes).toEqual([
        {
          line: `PATCH /api/v1/webhooks/${PAUSED_WEBHOOK.id}`,
          body: {
            name: PAUSED_WEBHOOK.name,
            target_url: PAUSED_WEBHOOK.target_url,
            events: PAUSED_WEBHOOK.events,
            enabled: true,
            max_retries: PAUSED_WEBHOOK.max_retries,
            breaker_threshold: PAUSED_WEBHOOK.breaker_threshold,
          },
        },
      ])
      expect(requestsTo(api, LIST).length, 'the list is read again').toBeGreaterThan(reads)
      assertHermetic(api, errors)
    })

    test(`New webhook with Pause after 3 sends breaker_threshold: 3 — ${width}`, async ({ page }) => {
      const world = webhooksBreakerWorld()
      const { api, errors } = await openRollout(page, '/settings/webhooks', { handlers: world.handlers, ready: h1, user: QA_LEAD })
      await networkQuiet(page, api)
      await page.getByRole('button', { name: 'New webhook' }).click()
      const threshold = page.getByLabel('Pause after (consecutive failed deliveries)')
      await expect(threshold, 'the default').toHaveValue('10')
      await page.getByLabel('Name', { exact: true }).fill('Breaker test')
      await page.getByLabel('Target URL (https only)').fill('https://hooks.example.test/breaker')
      await page.getByRole('checkbox', { name: /run\.completed/ }).check()
      await threshold.fill('3')
      await expectMainFits(page, `create form @${width}`)
      await page.getByRole('button', { name: 'Create', exact: true }).click()
      await expect(toast(page, 'Webhook created')).toBeVisible()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([
        {
          line: `POST ${LIST}`,
          body: {
            name: 'Breaker test',
            target_url: 'https://hooks.example.test/breaker',
            events: ['run.completed'],
            enabled: true,
            max_retries: 5,
            breaker_threshold: 3,
          },
        },
      ])
      expect(requestsTo(api, LIST).filter((line) => line.startsWith('POST')), 'one create, for the project').toEqual([`POST ${LIST}?project_id=${PROJECT_ID}`])
      await expect(hookCard(page, 'Breaker test')).toBeVisible()
      assertHermetic(api, errors)
    })
  })
}

test('a QA ENGINEER has neither Re-enable nor the toggle: /settings/webhooks sends them to /overview, nothing of it is asked', async ({ page }) => {
  const world = webhooksBreakerWorld()
  const errors = watchPageErrors(page)
  await seedSession(page, { theme: 'signal', user: QA_ENGINEER, projectId: PROJECT_ID })
  await freezeClock(page, NOW)
  await stillMotion(page)
  const api = await mockApi(page, QA_ENGINEER, [...world.handlers.filter(([, , verb]) => verb && verb !== 'GET'), ...OVERVIEW_ON])
  await page.goto('/settings/webhooks')
  await expect(page).toHaveURL(/\/overview(\?|$)/)
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('button', { name: 'Re-enable' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  expect([...api.seen, ...api.unhandled].filter((line) => /^[A-Z]+ \/api\/v1\/webhooks(\/|\?|$)/.test(line)), 'no /webhooks request').toEqual([])
  assertHermetic(api, errors)
})
