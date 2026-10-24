/**
 * W11 GATE-08b on `/settings/digests` › Preview Digest, hermetic (`docs/deferred-scope-2026-10-13/work/W11-contract.md`
 * §2.8, D15): the preview carries the RELEASE-level verdict of the digest's release, beside (never merged with) the
 * run-level decision lines.
 *
 *  - **Generate Preview** (Weekly) sends ONE `GET /api/v1/digests/preview?project_id=…&period=weekly` (the page's own
 *    read, unchanged) and the line `Release 2026.11 — No-Go (2 blockers) · decided … · the project's active release`
 *    renders above the preview's body, as text;
 *  - the page asks nothing new on load (the line rides the preview's answer);
 *  - at 1280 and 1440 `#main-content` never scrolls sideways.
 *
 * `digests-per-release.spec.ts` is NOT edited (its world has no `release_verdict`). Harness: `tests/lib/rollout.ts`
 * (fail closed, pinned clock). Fixtures: `fixtures-release-planning.ts` over `digestsWorld()`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay } from '../lib/inPage'
import { expectMainFits, precedes } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { digestsReleaseVerdictWorld, QA_LEAD, RELEASE_VERDICT_LINE } from '../visual/production/fixtures-release-planning'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const PREVIEW = '/api/v1/digests/preview'
const ready = (p: Page) => p.locator('[data-page-header] h1')

for (const width of [1280, 1440]) {
  test(`Generate Preview: the release line from the preview's answer, as text — ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    const world = digestsReleaseVerdictWorld()
    const { api, errors } = await openRollout(page, '/settings/digests', { handlers: world.handlers, ready, user: QA_LEAD })
    await networkQuiet(page, api)
    expect(requestsTo(api, PREVIEW), 'no preview on load').toEqual([])

    await page.getByRole('tab', { name: 'Preview Digest' }).click()
    await page.getByRole('button', { name: 'Generate Preview' }).click()
    const heading = page.getByRole('heading', { name: 'Weekly Digest — Checkout' })
    await expect(heading).toBeVisible()
    // The whole line, as text (decided 2 h before the pinned clock).
    const line = page.locator('[data-digest-release-verdict]')
    await expect(line).toHaveCount(1)
    await expect(line).toHaveText(`${RELEASE_VERDICT_LINE} · decided 2 h ago · the project's active release`)
    await expect(line).toBeVisible()
    expect(await precedes(line, page.getByText('Avg Pass Rate').first()), 'above the preview\'s body').toBe(true)
    await networkQuiet(page, api)
    expect(world.previews).toEqual([`${PREVIEW}?project_id=${P}&period=weekly`])
    await expectMainFits(page, `digest preview @${width}`)
    await expectNoOverlay(page)
    assertHermetic(api, errors)
  })
}
