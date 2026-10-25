/**
 * W15 G-C baseline (`docs/deferred-scope-2026-10-13/work/W15-contract.md` §6.5, ONE new region), signal, lab and
 * paper (`THEMES_WITH_PAPER`):
 *
 *  - `/deep-investigate?run=<id>`: `cockpit-proposed-actions` — the Agent Investigation cockpit's verdict card for a
 *    suggest-mode investigation: the text recommendations, the Proposed actions (two `Waiting for approval` rows
 *    with Review / Do it myself, one `Executed`) and the suggest sentence.
 *
 * The region is the verdict card itself, with focus blurred. Hermetic world:
 * `production/fixtures-investigator-proposals.ts`. PNGs are rendered by CI only (Linux); nobody regenerates them
 * locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED_TALL, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { openRollout } from '../lib/rollout'
import { RUN_ID } from './production/fixtures'
import { investigationWorld } from './production/fixtures-investigator-proposals'

test.use(PINNED_TALL)

const ready = (p: Page) => p.getByTestId('investigation-verdict')
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

for (const theme of THEMES_WITH_PAPER) {
  test(`the cockpit's proposed actions for a suggest verdict — ${theme}`, async ({ page }) => {
    const { handlers } = investigationWorld({ mode: 'suggest' })
    const { api, errors } = await openRollout(page, `/deep-investigate?run=${RUN_ID}`, { theme, handlers, ready })
    const card = page.getByTestId('investigation-verdict')
    const rows = card.getByTestId('cockpit-action-row')
    await expect(rows).toHaveCount(3)
    await expect(rows.nth(0)).toContainText('Quarantine checkout-retry — Waiting for approval')
    await expect(rows.nth(2)).toContainText('Executed')
    await expect(card).toContainText('Suggest mode — these proposals wait in Inbox › Approvals')
    await blur(page)
    await visualRegion(page, 'cockpit-proposed-actions', theme, card)
    assertHermetic(api, errors)
  })
}
