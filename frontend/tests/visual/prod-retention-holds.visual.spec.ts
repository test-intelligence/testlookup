/**
 * W22a baselines for the Retention page and the run's purged baseline (`docs/deferred-scope-2026-10-13/work/
 * W22a-contract.md` §6.3, §6.5), the five NEW regions, in signal, lab and paper (`THEMES_WITH_PAPER`):
 *
 *  - `retention-holds-card`: the Holds card with two live holds (a release hold with its link, a pack hold), ADMIN;
 *  - `retention-hold-place-inline`: the card with its Place hold form open (reason typed, an expiry day);
 *  - `retention-preview-kept`: the purge preview's kept lines (two holds, the floor, a reference warning, the
 *    not-held note);
 *  - `retention-policy-floor`: the keep-last floor field block of the policy form;
 *  - `run-baseline-purged`: the run's Changes card with `Baseline build 4471 was purged on …`.
 *
 * Each region is the component's own container; the focus ring is not part of a baseline (blurred). The 4000 px
 * viewport (`PINNED_TALL`) keeps every region on screen with nothing scrolled, as `visualRegion` requires. World:
 * `production/fixtures-retention.ts`. PNGs are rendered by CI only (`visual-baselines.yml`, Linux); nobody
 * regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED_TALL, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { RUN_ID } from './production/fixtures'
import { RUN_PAGE } from './production/fixtures-run'
import {
  HOLD_PACK_ID,
  LITIGATION_REASON,
  retentionHoldsWorld,
  worldHold,
  worldRegressionDiff,
} from './production/fixtures-retention'

test.use({ ...PINNED_TALL, timezoneId: 'UTC', locale: 'en-US' })

const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const card = (page: Page) => page.locator('[data-retention-holds-card]')

/** Two live holds (the release hold keeps 5 runs) and one released, ADMIN. */
function adminWorld() {
  const audit = worldHold({ entity_label: 'release 2026.10', placed_at: '2026-09-16T12:00:00Z' })
  const pack = worldHold({
    entity_type: 'compliance_pack', entity_id: HOLD_PACK_ID, entity_label: 'pack of 2026-09-02',
    reason: LITIGATION_REASON, placed_at: '2026-09-17T12:00:00Z', expires_at: '2026-12-31T23:59:59Z',
  })
  const released = worldHold({ reason: 'Old matter', released_at: '2026-09-10T09:00:00Z', released_by_name: 'Admin', release_reason: 'Matter closed' })
  return retentionHoldsWorld({ role: 'ADMIN', holds: [audit, pack, released], keptByHold: { [audit.id]: { runs_kept: 5 } } })
}

async function openRetention(page: Page, theme: (typeof THEMES_WITH_PAPER)[number]) {
  const world = adminWorld()
  const opened = await openRollout(page, '/settings/retention', { theme, handlers: world.handlers, ready: h1('Retention & Purge'), user: world.user })
  await expect(card(page).locator('li[data-retention-hold]')).toHaveCount(2)
  await networkQuiet(page, opened.api)
  return opened
}

for (const theme of THEMES_WITH_PAPER) {
  test(`the Holds card — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRetention(page, theme)
    await blur(page)
    await visualRegion(page, 'retention-holds-card', theme, card(page))
    assertHermetic(api, errors)
  })

  test(`Place hold, open in the card — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRetention(page, theme)
    await card(page).getByRole('button', { name: 'Place hold' }).click()
    const form = card(page).locator('[data-retention-hold-form]')
    await form.getByLabel(/Reason/).fill('Litigation sentinel')
    await form.getByLabel(/Expires/).fill('2026-12-31')
    await blur(page)
    await visualRegion(page, 'retention-hold-place-inline', theme, form)
    assertHermetic(api, errors)
  })

  test(`the preview's kept lines — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRetention(page, theme)
    await page.getByRole('button', { name: 'Preview purge' }).click()
    const kept = page.locator('[data-retention-preview-kept]')
    await expect(kept.getByText('3 runs lose their baseline link', { exact: true })).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'retention-preview-kept', theme, kept)
    assertHermetic(api, errors)
  })

  test(`the keep-last floor field — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRetention(page, theme)
    const floor = page.locator('[data-retention-floor]')
    await expect(floor.locator('#retention-keep_last_runs')).toHaveValue('10')
    await blur(page)
    await visualRegion(page, 'retention-policy-floor', theme, floor)
    assertHermetic(api, errors)
  })

  test(`the run's purged baseline on Changes — ${theme}`, async ({ page }) => {
    const world = retentionHoldsWorld({ base: RUN_PAGE, regressionDiff: (runId) => worldRegressionDiff(runId) })
    const changes = (p: Page) => p.getByRole('region', { name: 'What changed since the last good run' })
    const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=changes`, {
      theme, handlers: world.handlers, ready: (p) => changes(p).locator('[data-baseline-purged]'),
    })
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'run-baseline-purged', theme, changes(page))
    assertHermetic(api, errors)
  })
}
