/**
 * W05 "release-gate" baselines (`docs/deferred-scope-2026-10-13/work/W05-contract.md` §4.7), `/releases`:
 *
 *  - `releases-card-go`: a released release's recorded GO (tiles, pass/fail bar, "Evaluated 10d ago");
 *  - `releases-card-no-go`: the in-progress NO_GO, its own phases, three readable server blockers, "+23 more";
 *  - `releases-banner-ready`: the banner counting an in-progress GO ("Ready to ship 1");
 *  - `releases-gate-panel`: a release's own page, the gate panel (scorecard, 25 reasons, the baseline) and the
 *    phase rows with their live gate chips;
 *  - `releases-rail`: the rail with the real pack (`412.3 KB · sha256:ab12cd34`) and the ledger's rows;
 *  - `releases-phase-reason-dialog`: a refused completion's dialog quoting the server;
 *  - `releases-sync-offline`: the sync dialog with the offline 503 and the Settings link.
 *
 * Hermetic fixtures: `production/fixtures-releases.ts`. PNGs are rendered by CI only (`visual-baselines.yml`,
 * Linux).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, TALL_VIEWPORT, THEMES, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { HOTFIX_GO, RELEASE, releasesHandlers, SYNC_OFFLINE } from './production/fixtures-releases'

test.use(PINNED)

const ready = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const card = (page: Page, name: string) => page.getByRole('article', { name: new RegExp(`^${name.replace(/\./g, '\\.')} `) })

/** The list with the hotfix already GO: one in-progress release ready to ship. */
function listWithReadyHotfix() {
  const opened = releasesHandlers({ hotfix: true })
  const hotfix = opened.world.rows.find((r) => r.id === RELEASE.hotfix)
  if (!hotfix) throw new Error('the hotfix release is missing from the fixture')
  hotfix.gate = { ...HOTFIX_GO }
  return opened
}

for (const theme of THEMES) {
  test(`releases list: GO card, No-Go card, banner and rail — ${theme}`, async ({ page }) => {
    const { handlers } = listWithReadyHotfix()
    const { api, errors } = await openRollout(page, '/releases', { theme, handlers, ready })
    await expect(card(page, '2026.11').locator('[data-blockers-more]')).toHaveText('+23 more · open the release for the full list')
    await expect(page.locator('[data-release-health] [data-banner-fact]').filter({ hasText: 'Ready to ship' })).toHaveText('Ready to ship 1')
    const rail = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Compliance packs', level: 4 }) }).locator('xpath=..')
    await expect(rail.locator('[data-rail-pack]')).toContainText('412.3 KB · sha256:ab12cd34')
    await expect(rail.locator('[data-rail-activity-event]')).toHaveCount(3)
    await networkQuiet(page, api)
    await visualRegion(page, 'releases-card-go', theme, card(page, '2026.10'))
    await visualRegion(page, 'releases-card-no-go', theme, card(page, '2026.11'))
    await visualRegion(page, 'releases-banner-ready', theme, page.locator('[data-release-health]'))
    await visualRegion(page, 'releases-rail', theme, rail)
    assertHermetic(api, errors)
  })

  test(`the phase reason dialog — ${theme}`, async ({ page }) => {
    const { handlers } = releasesHandlers()
    const { api, errors } = await openRollout(page, `/releases/${RELEASE.active}`, {
      theme,
      handlers,
      ready: (p) => p.getByRole('heading', { name: 'Release Phases' }),
    })
    await page.getByLabel('Status of phase Payments soak').selectOption('completed')
    const dialog = page.locator('[data-phase-reason-dialog="complete"]')
    await expect(dialog.locator('[data-phase-gate-detail]')).toContainText('test_pay (checkout-e2e) is FAILED')
    await networkQuiet(page, api)
    await visualRegion(page, 'releases-phase-reason-dialog', theme, dialog.locator(':scope > div'))
    assertHermetic(api, errors)
  })

  test(`the sync dialog offline — ${theme}`, async ({ page }) => {
    const { handlers } = releasesHandlers()
    const { api, errors } = await openRollout(page, '/releases', { theme, handlers, ready })
    await page.locator('[data-page-header]').getByRole('button', { name: 'Sync releases' }).click()
    const dialog = page.locator('[data-sync-releases-dialog]')
    await dialog.getByRole('button', { name: 'Sync', exact: true }).click()
    await expect(dialog.locator('[data-sync-error]')).toContainText(SYNC_OFFLINE)
    await networkQuiet(page, api)
    await visualRegion(page, 'releases-sync-offline', theme, dialog.locator(':scope > div'))
    assertHermetic(api, errors)
  })

  // The release's own page is taller than the 2400 px viewport (the detail runs to ~2,540 px): the tall
  // viewport keeps the region unscrolled, as `visualRegion` requires.
  test.describe(`a release's own page — ${theme}`, () => {
    test.use({ viewport: { ...TALL_VIEWPORT } })

    test(`a release's gate panel and phase gates — ${theme}`, async ({ page }) => {
      const { handlers } = releasesHandlers()
      const { api, errors } = await openRollout(page, `/releases/${RELEASE.active}`, {
        theme,
        handlers,
        ready: (p) => p.getByRole('heading', { name: 'Release Phases' }),
      })
      await expect(page.getByTestId('phase-gate-chip')).toHaveCount(3)
      await expect(page.locator('#gate').getByRole('list', { name: 'Blocking' }).getByRole('listitem')).toHaveCount(25)
      await networkQuiet(page, api)
      // The detail's body: the metrics, the gate panel, the pack panel, the outcome, the phases with their chips.
      const detail = page.locator('div.space-y-5').filter({ has: page.locator('#gate') }).last()
      await visualRegion(page, 'releases-gate-panel', theme, detail)
      assertHermetic(api, errors)
    })
  })
}
