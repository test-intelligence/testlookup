/**
 * W11 GATE-08a on `/release-gate/:runId`, hermetic (`docs/deferred-scope-2026-10-13/work/W11-contract.md` §2.9, §4.1,
 * §4.2), at 1280 and 1440:
 *
 *  - with no release in the top bar the page is what it was: no release strip, no `GET /releases/{id}/gate`;
 *  - choosing `2026.11` in the top bar's **Filter by release** navigates to `/release-gate` and on to 2026.11's newest
 *    FINISHED run (its newest is still running): the run list is asked keyed on `release_id`, and the URL's path is
 *    that run's (the whole path compared) with the release in `?release=`;
 *  - the strip under the header reads `Release 2026.11 (top bar) · release gate: No-Go · 2 blockers · decided 2 h ago`
 *    from ONE `GET /api/v1/releases/{id}/gate`, with **Open release →** to `/releases/{id}#gate`; the run's own card
 *    keeps its heading (two verdicts, two scopes, side by side);
 *  - **All releases** goes back to the project's newest finished run and the strip is gone;
 *  - in the page (no dialog), `#main-content` never scrolls sideways; the strip's height is printed (`INPAGE`).
 *
 * `fold-release-gate.spec.ts` is UNEDITED (it selects no release). Harness: `tests/lib/rollout.ts` (fail closed, pinned
 * clock). Fixtures: `fixtures-release-planning.ts` over `releaseGateOn()`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { boxOf, expectMainFits, inPageLine } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import {
  GATE_RUN_A,
  QA_LEAD,
  RELEASE_ACTIVE_ID,
  RELEASE_RUNS,
  releaseGateFollowsWorld,
  STRIP_TEXT,
} from '../visual/production/fixtures-release-planning'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('meter', { name: 'Risk Score' })
const strip = (page: Page) => page.locator('[data-release-verdict-strip]')
const picker = (page: Page) => page.getByLabel('Filter by release')
const GATE_READ = `/api/v1/releases/${RELEASE_ACTIVE_ID}/gate`

for (const width of [1280, 1440]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 1000 } })

    test(`choosing 2026.11 in the top bar opens its newest finished run and names the release's verdict — ${width}`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = releaseGateFollowsWorld()
      const { api, errors } = await openRollout(page, `/release-gate/${GATE_RUN_A}`, { handlers: world.handlers, ready, user: QA_LEAD })
      await networkQuiet(page, api)
      await expect(strip(page), 'no release selected: no strip').toHaveCount(0)
      expect(requestsTo(api, /^\/api\/v1\/releases\/[^/]+\/gate$/), 'no release gate read').toEqual([])

      await picker(page).selectOption({ label: '2026.11' })
      await expect(page).toHaveURL((url) => url.pathname === `/release-gate/${RELEASE_RUNS.finished}`)
      // The top bar republishes its release into the new page's address (the picker's write-back).
      await expect.poll(() => new URL(page.url()).searchParams.get('release'), { message: 'the release rides the URL' }).toBe(RELEASE_ACTIVE_ID)
      await expect(ready(page)).toBeVisible()
      await expect(strip(page)).toHaveCount(1)
      await expect(strip(page)).toContainText(STRIP_TEXT)
      await expect(strip(page)).toContainText('decided 2 h ago')
      const open = strip(page).getByRole('link', { name: /Open release/ })
      const href = new URL((await open.getAttribute('href')) ?? '', page.url())
      expect(`${href.pathname}${href.hash}`).toBe(`/releases/${RELEASE_ACTIVE_ID}#gate`)
      await networkQuiet(page, api)
      expect(requestsTo(api, GATE_READ), 'the release gate, read once').toEqual([`GET ${GATE_READ}`])
      expect(
        world.runsSeen.filter((t) => new URL(t, 'http://x').searchParams.get('release_id') === RELEASE_ACTIVE_ID).length,
        'the run list is asked for the release',
      ).toBeGreaterThan(0)
      expect(requestsTo(api, `/api/v1/release-readiness/${RELEASE_RUNS.running}`), 'the running run is passed over').toEqual([])
      const box = await strip(page).boundingBox()
      const text = await boxOf(strip(page).getByRole('link', { name: /Open release/ }))
      inPageLine('/release-gate release strip', width, { height: Math.round(box?.height ?? 0), width: Math.round(box?.width ?? 0), linkRight: text.right })
      expect(box?.height ?? 0, 'one line').toBeLessThan(40)
      await expectMainFits(page, `strip @${width}`)
      await expectNoOverlay(page)

      // All releases: the project's newest finished run, and no strip.
      await picker(page).selectOption({ label: 'All releases' })
      await expect(page).toHaveURL((url) => url.pathname === `/release-gate/${GATE_RUN_A}`)
      await expect(strip(page)).toHaveCount(0)
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })
  })
}
