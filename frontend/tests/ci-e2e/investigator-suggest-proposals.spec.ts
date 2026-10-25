/**
 * W15 AGT-02, hermetic (`docs/deferred-scope-2026-10-13/work/W15-contract.md` §2.2 test 6): Suggest mode is
 * distinct. On `/deep-investigate?run=<id>` the cockpit's verdict lists the Investigator's typed actions with the
 * state of the ledger row each one became:
 *
 *  - a suggest-mode verdict → `Quarantine checkout-retry — Waiting for approval` rows, each with a Review link into
 *    Inbox › Approvals › Agent actions (the executed one has none), and the suggest sentence;
 *  - a shadow-mode verdict → every row `Shadow mode — not proposed`, and the shadow sentence;
 *  - Do it myself → the EXISTING in-page Propose quarantine form under its row (no dialog, nothing submitted);
 *  - the page asks for nothing beyond the world's investigation reads (the proposals ride the detail it polls).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock; the QA lead fixture). World:
 * `tests/visual/production/fixtures-investigator-proposals.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, observed, openRollout, requestsTo } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { INVESTIGATION_ID, investigationWorld, REVIEW_HREF } from '../visual/production/fixtures-investigator-proposals'

test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const PAGE = `/deep-investigate?run=${RUN_ID}`
const cockpit = (p: Page) => p.locator('section[aria-label="Agent investigation"]')
const verdict = (p: Page) => p.getByTestId('investigation-verdict')
const rows = (p: Page) => p.getByTestId('cockpit-action-row')
const SUGGEST_LINE = 'Suggest mode — these proposals wait in Inbox › Approvals; nothing runs until a person approves it.'
const SHADOW_LINE = 'Shadow mode — no actions were taken. The agent only recorded what it would do.'

/** Nothing but reads, and the only investigation reads are the world's list and detail. */
function expectReadsOnly(api: Parameters<typeof observed>[0]) {
  expect(observed(api).filter((line) => !line.startsWith('GET '))).toEqual([])
  const detailReads = requestsTo(api, /^\/api\/v1\/investigations\//)
  expect(detailReads.length).toBeGreaterThan(0)
  expect(new Set(detailReads)).toEqual(new Set([`GET /api/v1/investigations/${INVESTIGATION_ID}`]))
  expect(requestsTo(api, /agent-actions/)).toEqual([])
  expect(requestsTo(api, /\/quarantine/)).toEqual([])
}

test('a suggest-mode verdict lists its proposals with their states and Review links', async ({ page }) => {
  const { handlers } = investigationWorld({ mode: 'suggest' })
  const { api, errors } = await openRollout(page, PAGE, { handlers, ready: verdict })

  await expect(rows(page)).toHaveCount(3)
  await expect(rows(page).nth(0)).toContainText('Quarantine checkout-retry — Waiting for approval')
  await expect(rows(page).nth(1)).toContainText('Quarantine payment-refund-sync — Waiting for approval')
  await expect(rows(page).nth(2)).toContainText('Quarantine cart-session-expiry — Executed')
  for (const index of [0, 1]) {
    await expect(rows(page).nth(index).getByRole('link', { name: 'Review' })).toHaveAttribute('href', REVIEW_HREF)
  }
  await expect(rows(page).nth(2).getByRole('link', { name: 'Review' })).toHaveCount(0)
  await expect(rows(page).nth(2).getByTestId('cockpit-action-state')).toHaveAttribute(
    'title', 'Executed · approved by QA Lead Two · quarantined.',
  )
  await expect(verdict(page)).toContainText(SUGGEST_LINE)
  await expect(verdict(page)).not.toContainText(SHADOW_LINE)

  await networkQuiet(page, api)
  expectReadsOnly(api)
  assertHermetic(api, errors)
})

test('a shadow-mode verdict proposes nothing and says so', async ({ page }) => {
  const { handlers } = investigationWorld({ mode: 'shadow' })
  const { api, errors } = await openRollout(page, PAGE, { handlers, ready: verdict })

  await expect(rows(page)).toHaveCount(3)
  for (const index of [0, 1, 2]) {
    await expect(rows(page).nth(index)).toContainText('— Shadow mode — not proposed')
  }
  await expect(cockpit(page).getByRole('link', { name: 'Review' })).toHaveCount(0)
  await expect(verdict(page)).toContainText(SHADOW_LINE)
  await expect(verdict(page)).not.toContainText(SUGGEST_LINE)

  await networkQuiet(page, api)
  expectReadsOnly(api)
  assertHermetic(api, errors)
})

test('Do it myself opens the in-page Propose quarantine form under its row — no dialog, nothing sent', async ({ page }) => {
  const { handlers } = investigationWorld({ mode: 'suggest' })
  const { api, errors } = await openRollout(page, PAGE, { handlers, ready: verdict })

  const first = rows(page).nth(0)
  await first.getByRole('button', { name: 'Do it myself' }).click()
  const form = page.getByRole('region', { name: 'Propose quarantine' })
  await expect(form).toBeVisible()
  await expect(form).toContainText('checkout-retry')
  // The form sits in the in-page row right under the action it came from.
  await expect(page.locator('li[data-in-page-row]').getByRole('region', { name: 'Propose quarantine' })).toHaveCount(1)
  await expect(first.locator('xpath=following-sibling::li[1]').getByRole('region', { name: 'Propose quarantine' })).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  // Cancel closes it; nothing was proposed.
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)

  await networkQuiet(page, api)
  expectReadsOnly(api)
  assertHermetic(api, errors)
})
