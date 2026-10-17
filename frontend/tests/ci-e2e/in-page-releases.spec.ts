/**
 * W27a PAN-02 IN-PAGE SHAPE (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §2.2 S1-S6, §4.1), hermetic, on
 * the CONVERTED `/releases` and `/release-gate/:runId`. It fails on the modal tree by design (`expectNoOverlay`);
 * the flows themselves are `characterize-releases.spec.ts`, which runs unedited on both.
 *
 *  - S1 at 1280 and 1440: a card → `?open=<id>`, no overlay, the release in a pane BESIDE the cards (it starts at
 *    or right of the cards column), the pane ≥ 440 px, the cards column ≥ 480 px at 1280, the rail folded, nothing
 *    scrolling sideways (`#main-content`, every card), the five detail reads exactly once; `INPAGE /releases`;
 *  - S2 another card switches the pane (the URL replaced: Back lands on the list); a refresh keeps it; Back closes;
 *  - S3 `?open=../../admin` and an unknown id → the not-found card with no stray request; Close drops the key;
 *  - S4 a typed edit inside the pane asks before another card replaces it (Keep editing / Discard changes); Save
 *    sends R3's PUT and re-reads both;
 *  - S5 Delete (admin): the inline strip in the pane footer, nothing sent until its own Delete;
 *  - S6 `/release-gate/:runId` Share report: a section under the header, no overlay.
 *
 * The pane's URL key is `?open=` (the contract's `?release=` was amended: `?release=` is the top bar's global release
 * filter, `ReleasePicker`'s `RELEASE_PARAM`, read on every page).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `releasesInPageWorld()`, `releaseGateOn()`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { boxOf, expectMainFits, inPageLine, minContentSlack, ownOverflow, precedes } from '../lib/inPageGeometry'
import { respond } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { GATE_CLUSTERS, releaseGateOn, RUN_ID } from '../visual/production/fixtures'
import { releasesInPageWorld } from '../visual/production/fixtures-in-page'
import { ADMIN, RELEASE } from '../visual/production/fixtures-releases'

const ready = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const card = (page: Page, name: string) => page.getByRole('article', { name: new RegExp(`^${escape(name)} `) })
const pane = (page: Page) => page.locator('aside[data-in-page="detail"]')
const release = (page: Page, name: string) => page.getByRole('complementary', { name, exact: true })
/** The list wrapper of the body's MasterDetail row (the cards column). */
const cardsColumn = (page: Page) => page.locator('[data-master-detail] > div').first()
const NOT_FOUND = 'This release could not be found, or you cannot open it.'
const UNKNOWN_ID = 'aaaaaaaa-0000-4000-8000-00000000dead'

// A release's reads: its detail and packs, its recorded gate, its baseline and its phases' live gates.
const detailRequests = (seen: readonly string[]) =>
  seen.filter((line) => /^GET \/api\/v1\/releases\/[^/?]+(\/compliance-packs|\/gate|\/gate\/baseline|\/phases\/gate)?$/.test(line))
const fiveReads = (id: string) =>
  [
    `GET /api/v1/releases/${id}`,
    `GET /api/v1/releases/${id}/compliance-packs`,
    `GET /api/v1/releases/${id}/gate`,
    `GET /api/v1/releases/${id}/gate/baseline`,
    `GET /api/v1/releases/${id}/phases/gate`,
  ].sort()

/** The whole URL is `<origin><pathAndQuery>`. */
async function expectUrl(page: Page, pathAndQuery: string) {
  const origin = new URL(page.url()).origin
  await expect(page).toHaveURL(`${origin}${pathAndQuery}`)
}

async function openList(page: Page, user?: typeof ADMIN) {
  watchNativeDialogs(page)
  const world = releasesInPageWorld()
  const opened = await openRollout(page, '/releases', { handlers: world.handlers, ready, user })
  return { ...world, ...opened }
}

for (const width of [1280, 1440]) {
  test.describe(`${width} x 900`, () => {
    test.use({ viewport: { width, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

    test(`S1 @${width}: a release opens beside the cards, the rail folds, nothing scrolls sideways, five reads once`, async ({ page }) => {
      const { api, errors } = await openList(page)
      await expect(page.getByRole('heading', { name: 'Shipping this week' })).toBeVisible()
      await networkQuiet(page, api)
      expect(detailRequests(api.seen)).toEqual([])

      await card(page, '2026.11').click()
      await expectUrl(page, `/releases?open=${RELEASE.active}`)
      const opened = release(page, '2026.11')
      await expect(opened).toBeVisible()
      await expect(opened.getByRole('heading', { name: 'Linked Test Runs (2)' })).toBeVisible()
      await expect(pane(page)).toHaveCount(1)
      await expectNoOverlay(page)
      await expect(page.getByRole('heading', { name: 'Shipping this week' }), 'the rail folds while a release is open').toHaveCount(0)

      const [paneBox, cardsBox] = [await boxOf(opened), await boxOf(cardsColumn(page))]
      expect(paneBox.left, 'the pane starts at or right of the cards column').toBeGreaterThanOrEqual(cardsBox.right)
      expect(paneBox.width, 'the pane is at least 440 px').toBeGreaterThanOrEqual(440)
      if (width === 1280) expect(cardsBox.width, 'the cards keep at least 480 px at 1280').toBeGreaterThanOrEqual(480)
      await expectMainFits(page, `/releases @${width}`)
      const cards = page.getByRole('article')
      const slacks: number[] = []
      for (let i = 0; i < (await cards.count()); i++) {
        expect(await ownOverflow(cards.nth(i)), `card ${i} scrolls sideways`).toBeLessThanOrEqual(1)
        slacks.push(await minContentSlack(cards.nth(i)))
      }
      expect(await ownOverflow(opened), 'the pane scrolls sideways').toBeLessThanOrEqual(1)
      inPageLine('/releases', width, { pane: paneBox.width, cards: cardsBox.width, slack: Math.min(...slacks) })

      await networkQuiet(page, api)
      expect([...detailRequests(api.seen)].sort(), 'the five reads of an opened release, once each').toEqual(fiveReads(RELEASE.active))
      assertHermetic(api, errors)
    })
  })
}

test.describe('1440 x 900', () => {
  test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('S2 another card switches (replaced: Back lands on the list); a refresh keeps the release; Back closes it', async ({ page }) => {
    const { api, errors } = await openList(page)

    // Open, refresh, Back.
    await card(page, '2026.11').click()
    await expectUrl(page, `/releases?open=${RELEASE.active}`)
    await expect(release(page, '2026.11')).toBeVisible()
    await page.reload()
    await expect(release(page, '2026.11'), 'a refresh reopens it').toBeVisible()
    await page.goBack()
    await expectUrl(page, '/releases')
    await expect(pane(page)).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'Shipping this week' }), 'the rail is back').toBeVisible()

    // Open, switch (replace), Back.
    await card(page, '2026.11').click()
    await expect(release(page, '2026.11')).toBeVisible()
    await card(page, '2026.10').click()
    await expectUrl(page, `/releases?open=${RELEASE.shipped}`)
    await expect(release(page, '2026.10')).toBeVisible()
    await expect(release(page, '2026.11')).toHaveCount(0)
    // Its own card again changes nothing.
    await card(page, '2026.10').click()
    await expect(release(page, '2026.10')).toBeVisible()
    await page.goBack()
    await expectUrl(page, '/releases')
    await expect(pane(page), 'the switch replaced the entry: Back closes, it does not go back to 2026.11').toHaveCount(0)

    // Close pops the pushed entry.
    await card(page, '2026.12').click()
    await expect(release(page, '2026.12')).toBeVisible()
    await release(page, '2026.12').getByRole('button', { name: 'Close release' }).click()
    await expectUrl(page, '/releases')
    await expect(pane(page)).toHaveCount(0)
    await expectNoOverlay(page)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('S3 a malformed or unknown ?open= shows the not-found card, asks for nothing stray, and Close drops it', async ({ page }) => {
    watchNativeDialogs(page)
    const world = releasesInPageWorld()
    const { api, errors } = await openRollout(page, '/releases?open=../../admin', { handlers: world.handlers, ready })
    await expect(pane(page)).toBeVisible()
    await expect(pane(page)).toContainText(NOT_FOUND)
    await expect(card(page, '2026.11'), 'the cards render around it').toBeVisible()
    await networkQuiet(page, api)
    expect(requestsTo(api, /^\/api\/v1\/releases\/[^/]+/), 'a malformed id never reaches a request path').toEqual([])
    expect(requestsTo(api, /^\/api\/v1\/admin/)).toEqual([])
    await pane(page).getByRole('button', { name: 'Close release' }).click()
    await expect(pane(page)).toHaveCount(0)
    await expectUrl(page, '/releases')
    assertHermetic(api, errors)

    // A well-formed id the list does not have: read once (the guarded detail route answers 404), the card.
    await page.goto(`/releases?open=${UNKNOWN_ID}`)
    await expect(pane(page)).toContainText(NOT_FOUND)
    await expect(card(page, '2026.11')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Releases', level: 1 })).toBeVisible()
    await networkQuiet(page, api)
    expect(requestsTo(api, `/api/v1/releases/${UNKNOWN_ID}`)).toEqual([`GET /api/v1/releases/${UNKNOWN_ID}`])
    await expectNoOverlay(page)
    assertHermetic(api, errors)
  })

  test('S4 a typed edit asks before another card replaces it; Keep editing keeps the text; Save sends R3', async ({ page }) => {
    const { api, errors, writes } = await openList(page)
    await card(page, '2026.11').click()
    const opened = release(page, '2026.11')
    await opened.getByRole('button', { name: 'Edit', exact: true }).click()
    let editor = opened.getByRole('region', { name: 'Edit Release' })
    await expect(editor, 'the editor opens inside the pane').toBeVisible()
    await expectNoOverlay(page)
    await editor.getByLabel('Description').fill('Checkout revamp, typed')

    // Another card: the question, in the form.
    await card(page, '2026.10').click()
    const question = editor.locator('[data-inline-confirm]')
    await expect(question).toBeVisible()
    await expect(question.locator('p')).toHaveText('Discard your changes to "Edit Release"?')
    await question.getByRole('button', { name: 'Keep editing' }).click()
    await expect(question).toHaveCount(0)
    await expect(editor.getByLabel('Description')).toHaveValue('Checkout revamp, typed')
    await expect(opened).toBeVisible()
    await expectUrl(page, `/releases?open=${RELEASE.active}`)

    await networkQuiet(page, api)
    const lists = requestsTo(api, '/api/v1/releases').filter((l) => l.startsWith('GET ')).length
    const details = requestsTo(api, `/api/v1/releases/${RELEASE.active}`).filter((l) => l.startsWith('GET ')).length
    await editor.getByRole('button', { name: 'Save Changes' }).click()
    await expect(editor).toHaveCount(0)
    await expect(opened.getByText('Checkout revamp, typed', { exact: true })).toBeVisible()
    await networkQuiet(page, api)
    expect(writes).toEqual([
      {
        line: `PUT /api/v1/releases/${RELEASE.active}`,
        body: { name: '2026.11', version: '2026.11', description: 'Checkout revamp, typed', status: 'in_progress', planned_date: '2026-09-23T00:00:00.000Z' },
      },
    ])
    expect(requestsTo(api, '/api/v1/releases').filter((l) => l.startsWith('GET ')).length).toBeGreaterThan(lists)
    expect(requestsTo(api, `/api/v1/releases/${RELEASE.active}`).filter((l) => l.startsWith('GET ')).length).toBe(details + 1)

    // Discard changes: the pane switches.
    await opened.getByRole('button', { name: 'Edit', exact: true }).click()
    editor = opened.getByRole('region', { name: 'Edit Release' })
    await editor.getByLabel('Description').fill('never saved')
    await card(page, '2026.10').click()
    await editor.locator('[data-inline-confirm]').getByRole('button', { name: 'Discard changes' }).click()
    await expect(release(page, '2026.10')).toBeVisible()
    await expectUrl(page, `/releases?open=${RELEASE.shipped}`)
    await networkQuiet(page, api)
    expect(writes).toHaveLength(1)
    assertHermetic(api, errors)
  })

  test('S5 Delete (admin): the strip in the pane footer, nothing sent until its own Delete', async ({ page }) => {
    const { api, errors, writes } = await openList(page, ADMIN)
    await card(page, '2026.12').click()
    const opened = release(page, '2026.12')
    const del = opened.getByRole('button', { name: 'Delete', exact: true })
    await del.click()
    const strip = opened.locator('[data-inline-confirm]')
    await expect(strip, 'the question is asked in the pane').toBeVisible()
    await expect(strip.locator('p')).toHaveText('Delete this release and all its phases? This cannot be undone.')
    expect(await precedes(opened.locator('[data-release-panel]'), strip), 'the strip is in the footer, under the body').toBe(true)
    await expectNoOverlay(page)
    await networkQuiet(page, api)
    expect(writes, 'the first click sends nothing').toEqual([])

    await expect(strip.locator('[data-inline-confirm-action]')).toHaveText('Delete')
    await strip.locator('[data-inline-confirm-action]').click()
    await expect(page.getByText('Release deleted')).toBeVisible()
    await expect(opened).toHaveCount(0)
    await expect(card(page, '2026.12')).toHaveCount(0)
    await expectUrl(page, '/releases')
    await networkQuiet(page, api)
    expect(writes).toEqual([{ line: `DELETE /api/v1/releases/${RELEASE.planning}`, body: null }])
    assertHermetic(api, errors)
  })
})

test.describe('the release gate', () => {
  test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('S6 Share report opens as a section under the header, with no overlay', async ({ page }) => {
    watchNativeDialogs(page)
    const { api, errors } = await openRollout(page, `/release-gate/${RUN_ID}`, {
      handlers: [
        [`/api/v1/reports/runs/${RUN_ID}/share-links`, () => []],
        [/^\/api\/v1\/reports\/share-links\/[^/]+$/, () => respond(204), 'DELETE'],
        ...releaseGateOn({ clusters: GATE_CLUSTERS }),
      ],
      ready: (p) => p.getByRole('meter', { name: 'Risk Score' }),
    })
    await page.getByRole('button', { name: 'More actions' }).click()
    await page.getByRole('menuitem', { name: /Share report/ }).click()
    const share = page.getByRole('region', { name: 'Share report' })
    await expect(share).toBeVisible()
    await expect(share.getByText('No share links for this run yet.')).toBeVisible()
    await expectNoOverlay(page)
    expect(await precedes(page.locator('[data-page-header]'), share), 'under the page header').toBe(true)
    expect(await precedes(share, page.locator('[data-primary]')), 'above the verdict card').toBe(true)
    await expectMainFits(page, '/release-gate @1440 with Share report open')
    await share.getByRole('button', { name: 'Close share report' }).click()
    await expect(share).toHaveCount(0)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
})
