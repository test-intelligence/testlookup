/**
 * W11 REL-11 on a release's compliance-pack panel, hermetic (`docs/deferred-scope-2026-10-13/work/W11-contract.md`
 * §2.10, §4.1, §4.2), at 1280 and 1440:
 *
 *  - the released release 2026.10, opened beside the list (`?open=`), lists its signed pack with the chip
 *    `Signed · k-1a2b3c4d` (the full key id in its `title`) and no new request on load (the panel's own reads);
 *  - **Verify** sends ONE `POST /api/v1/compliance-packs/{id}/verify` (no body) and renders the result under the row
 *    (`[data-pack-verification]`): `Verified · 12 files · signed by key k-1a2b3c4d5e6f7a8b` and `12 of 12 files match`;
 *  - a tampered answer names the file: `Tampered: decision.json does not match the manifest` and
 *    `decision.json — does not match the manifest`;
 *  - **Public key** downloads `testlookup-compliance-k-1a2b3c4d5e6f7a8b.pem` through the session (the whole target
 *    compared, the token in the header, never the URL), a PUBLIC key PEM;
 *  - in the page (no dialog), `#main-content` never scrolls sideways.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-release-planning.ts`.
 */
import { readFile } from 'node:fs/promises'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { expectMainFits } from '../lib/inPageGeometry'
import { cardByHeading } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { RELEASE_PACK } from '../visual/production/fixtures-pages'
import {
  QA_LEAD,
  RELEASE_SHIPPED_ID,
  releasePlanningWorld,
  SIGNING_KEY_ID,
  TAMPERED_SUMMARY,
  VERIFIED_SUMMARY,
} from '../visual/production/fixtures-release-planning'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const VERIFY = `/api/v1/compliance-packs/${RELEASE_PACK.id}/verify`
const releasesReady = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const pane = (page: Page) => page.getByRole('complementary', { name: '2026.10', exact: true })
const packPanel = (scope: Locator): Locator => cardByHeading(scope, 'Compliance Pack')

async function openPack(page: Page, verify: 'verified' | 'tampered' = 'verified') {
  const world = releasePlanningWorld({ verify })
  const opened = await openRollout(page, `/releases?open=${RELEASE_SHIPPED_ID}`, { handlers: world.handlers, ready: releasesReady, user: QA_LEAD })
  const panel = packPanel(pane(page))
  await expect(panel).toBeVisible()
  await networkQuiet(page, opened.api)
  return { ...opened, world, panel }
}

for (const width of [1280, 1440]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 1000 } })

    test(`a signed pack: the chip, Verify posts once and says Verified · 12 files · signed by key … — ${width}`, async ({ page }) => {
      watchNativeDialogs(page)
      const { api, errors, world, panel } = await openPack(page)
      const chip = panel.getByText('Signed · k-1a2b3c4d', { exact: true })
      await expect(chip).toBeVisible()
      await expect(chip).toHaveAttribute('title', SIGNING_KEY_ID)
      expect(requestsTo(api, VERIFY), 'nothing verified until asked').toEqual([])

      await panel.getByRole('button', { name: 'Verify', exact: true }).click()
      const result = panel.locator('[data-pack-verification]')
      await expect(result).toContainText(VERIFIED_SUMMARY)
      await expect(result).toContainText('12 of 12 files match')
      await networkQuiet(page, api)
      expect(world.writes.filter((w) => w.line.endsWith('/verify'))).toEqual([{ line: `POST ${VERIFY}`, body: null }])
      await expectMainFits(page, `verified @${width}`)
      await expectNoOverlay(page)
      assertHermetic(api, errors)
    })
  })
}

test('a tampered pack: the summary and the file that does not match', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 })
  const { api, errors, panel } = await openPack(page, 'tampered')
  await panel.getByRole('button', { name: 'Verify', exact: true }).click()
  const result = panel.locator('[data-pack-verification]')
  await expect(result).toContainText(TAMPERED_SUMMARY)
  await expect(result).toContainText('decision.json — does not match the manifest')
  await expect(result).not.toContainText('12 of 12 files match')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Public key downloads the PEM through the session', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const { api, errors, world, panel } = await openPack(page)
  const [download] = await Promise.all([page.waitForEvent('download'), panel.getByRole('button', { name: 'Public key', exact: true }).click()])
  expect(download.suggestedFilename()).toBe(`testlookup-compliance-${SIGNING_KEY_ID}.pem`)
  const pem = await readFile(await download.path(), 'utf8')
  expect(pem.startsWith('-----BEGIN PUBLIC KEY-----\n')).toBe(true)
  expect(pem.includes('PRIVATE')).toBe(false)
  expect(world.downloads).toEqual([
    { target: `/api/v1/compliance-packs/signing-keys/${SIGNING_KEY_ID}/pem`, authorization: 'Bearer access' },
  ])
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
