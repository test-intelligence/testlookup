/**
 * W09 "notify-flaky" baselines (contract §4.6, §7.3), both themes, a QA lead, clock pinned at
 * 2026-09-18 12:00 UTC, fixtures `fixtures-notify-flaky.ts` / `flakyHandlers`:
 *
 *   notifications-policy-card  Email (SMTP) & channels › Project notification policy for Checkout, never saved:
 *                              "Using defaults: state changes ON, every run OFF.", the two switches, the seven
 *                              state-change boxes, the threshold and Save (NTF-02);
 *   my-notifications-events    My notifications › Email › "Notify me when" with the default policy: the per-run
 *                              hints under Run failed / Run passed / High failure rate and their link (NTF-02),
 *                              no "Flaky test detected", "Quality gate failed" offered (NTF-01);
 *   digests-subscriptions      Digest Subscriptions: the form (Weekly), a Weekly row with its view, a Per Release
 *                              row labelled 2026.11, a legacy unscoped row "Not delivering — choose a release"
 *                              with its in-row fix, a row whose view was deleted (NTF-03 / NTF-04);
 *   digests-saved-views        Saved Views: a shared report view with its scope, a legacy empty row (NTF-04);
 *   digests-preview-scope      Preview Digest with "Checkout regressions": the "Filtered by view" block and its
 *                              two notes (NTF-04);
 *   flaky-header-tabs          /flaky's header: no Experimental badge, five tabs (Policy without a count) (FLK-01/02);
 *   flaky-detected-score       the Detected table with the Flakiness score column: scored chips in three bands,
 *                              insufficient, not scored yet, — (FLK-03);
 *   flaky-policy-card          /flaky?tab=policy: the policy card with the defaults sentence (FLK-02).
 *
 * New with W09: their baselines come from CI (never generated locally). Harness and fail-closed rules:
 * `tests/lib/production-pages.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import {
  DELETED_VIEW_SUB,
  digestsWorld,
  flakyQuarantineWorld,
  LEGACY_VIEW,
  notificationsWorld,
  PER_RELEASE_SUB,
  preference,
  REGRESSIONS_VIEW,
  SUB_IDS,
  UNSCOPED_PER_RELEASE_SUB,
  VIEW_IDS,
  WEEKLY_WITH_VIEW,
} from './production/fixtures-notify-flaky'

test.use(PINNED)

const ready = (p: Page) => p.locator('[data-page-header] h1')

/** The innermost block holding "Notify me when" in a channel card: the label, the options and their hints. */
const eventsBlock = (page: Page, channel: string) =>
  page
    .locator(`[data-channel-card="${channel}"] div`)
    .filter({ has: page.getByText('Notify me when', { exact: true }) })
    .last()

for (const theme of THEMES) {
  test(`notification regions — ${theme}`, async ({ page }) => {
    const world = notificationsWorld({
      preferences: [preference('00000000-0000-4000-8000-000000000a21', 'email', ['run_failed', 'quality_gate_failed', 'test.newly_failing'])],
    })
    const { api, errors } = await openRollout(page, '/settings/notifications', { theme, handlers: world.handlers, ready })
    const card = page.locator('section[data-notification-policy]')
    await expect(card.locator('[data-policy-defaults]')).toHaveText('Using defaults: state changes ON, every run OFF.')
    await networkQuiet(page, api)
    await visualRegion(page, 'notifications-policy-card', theme, card)

    await page.goto('/settings/my-notifications')
    const events = eventsBlock(page, 'email')
    await expect(events.locator('[data-event-hint]').first()).toContainText('Not sent in Checkout: per-run notifications are off')
    await networkQuiet(page, api)
    await visualRegion(page, 'my-notifications-events', theme, events)
    assertHermetic(api, errors)
  })

  test(`digest regions — ${theme}`, async ({ page }) => {
    const world = digestsWorld({
      subscriptions: [WEEKLY_WITH_VIEW, PER_RELEASE_SUB, UNSCOPED_PER_RELEASE_SUB, DELETED_VIEW_SUB],
      views: [REGRESSIONS_VIEW, LEGACY_VIEW],
    })
    const { api, errors } = await openRollout(page, '/settings/digests', { theme, handlers: world.handlers, ready })
    await expect(page.locator(`[data-subscription-row="${SUB_IDS.unscoped}"] [data-scope-problem]`)).toHaveText(
      'Not delivering — choose a release',
    )
    await networkQuiet(page, api)
    // The tab's body: the form (Weekly, its "Choose a saved view" button) and the four rows.
    await visualRegion(page, 'digests-subscriptions', theme, page.locator('[data-subscription-form]').locator('xpath=..'))

    await page.getByRole('tab', { name: 'Saved Views' }).click()
    await expect(page.locator(`[data-digest-view-row="${VIEW_IDS.regressions}"]`)).toContainText('Saved on Trends')
    await networkQuiet(page, api)
    await visualRegion(page, 'digests-saved-views', theme, page.locator(`[data-digest-view-row="${VIEW_IDS.regressions}"]`).locator('xpath=..'))

    await page.getByRole('tab', { name: 'Preview Digest' }).click()
    await page.getByLabel('Saved view', { exact: true }).selectOption({ label: 'Checkout regressions' })
    await page.getByRole('button', { name: 'Generate Preview' }).click()
    const scope = page.locator('[data-digest-scope="saved_view"]')
    await expect(scope).toContainText('Filtered by view: Checkout regressions')
    await networkQuiet(page, api)
    // The preview card: its header, the scope block and the numbers under it.
    await visualRegion(page, 'digests-preview-scope', theme, scope.locator('xpath=..'))
    assertHermetic(api, errors)
  })

  test(`flaky regions — ${theme}`, async ({ page }) => {
    const world = flakyQuarantineWorld()
    const { api, errors } = await openRollout(page, '/flaky', { theme, handlers: world.handlers, ready, flags: { flaky_auto_quarantine: true } })
    const table = page.locator('[data-primary]').getByRole('table', { name: 'Flaky tests' })
    await expect(table.locator('tr[data-flaky-row="fp-02"]')).toContainText('0.81')
    await expect(page.getByRole('tab', { name: /^Policy/ })).toBeVisible()
    await networkQuiet(page, api)
    await visualRegion(page, 'flaky-header-tabs', theme, page.locator('[data-page-header]'))
    await visualRegion(page, 'flaky-detected-score', theme, table)

    await page.getByRole('tab', { name: /^Policy/ }).click()
    const policy = page.locator('[data-quarantine-policy]')
    await expect(policy).toContainText('Using defaults: SLA 14 days, promote after 20 passes, detection 20 % over 10 runs.')
    await networkQuiet(page, api)
    await visualRegion(page, 'flaky-policy-card', theme, policy)
    assertHermetic(api, errors)
  })
}
