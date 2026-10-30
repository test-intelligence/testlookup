/**
 * W23c baselines (`docs/deferred-scope-2026-10-13/work/W23c-contract.md` §6.3, §6.5), the six NEW regions, in
 * signal, lab and paper (`THEMES_WITH_PAPER`):
 *
 *  - `review-gate-audit-only`: Settings › AI › Review gate's state card — `Audit only`, the sentence with the
 *    30-day counts, the by-channel list (an ADMIN);
 *  - `review-gate-scheduled`: the page's cards with a date four days ahead (`Audit only until …`, the date card
 *    with the stored date and `Clear date`);
 *  - `review-gate-enforced`: the cards with a date two days back (`Enforced since …`, the gated channels card);
 *  - `review-gate-past-date-confirm`: the date card with a past time typed and the inline warning under the field;
 *  - `agents-try-panel-inline`: `/agents` › Try an agent open on `agent.summary.v1` (a QA engineer): agent, anchor
 *    line, radios, the pre-filled editor and its byte counter;
 *  - `agents-invocation-awaiting-review`: the invocation detail — header, the awaiting-review line, the output and
 *    the server's redacted stored input.
 *
 * Each region is the component's own container; the focus ring is not part of a baseline (blurred). The 2400 px
 * viewport (`PINNED`) keeps every region on screen with nothing scrolled. World: `fixtures-review-gate-inline.ts`
 * (G-A). PNGs are rendered by CI only (`visual-baselines.yml`, Linux); nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { RUN_ID } from './production/fixtures'
import { AI_REPORT_SUMMARY } from './production/fixtures-run'
import {
  WORLD_INVOCATION_ID,
  reviewGateWorld,
  tryAgentWorld,
  worldGateDate,
  worldGateScheduled,
  worldInvocationAwaitingReview,
} from './production/fixtures-review-gate-inline'

test.use({ ...PINNED })

type Theme = (typeof THEMES_WITH_PAPER)[number]

const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const gateReady = (p: Page) => p.locator('[data-review-gate-state]')
const agentsReady = (p: Page) => p.getByRole('region', { name: 'AI report' }).getByText(AI_REPORT_SUMMARY)

async function openGate(page: Page, theme: Theme, world: ReturnType<typeof reviewGateWorld>) {
  const opened = await openRollout(page, '/settings/review-gate', { theme, handlers: world.handlers, ready: gateReady, user: world.user })
  await networkQuiet(page, opened.api)
  return opened
}

for (const theme of THEMES_WITH_PAPER) {
  test(`review gate, audit only — ${theme}`, async ({ page }) => {
    const { api, errors } = await openGate(page, theme, reviewGateWorld())
    const card = page.locator('[data-review-gate-state]')
    await expect(card.locator('[data-review-gate-by-channel] [data-channel]')).toHaveCount(3)
    await visualRegion(page, 'review-gate-audit-only', theme, card)
    assertHermetic(api, errors)
  })

  test(`review gate, scheduled — ${theme}`, async ({ page }) => {
    const { api, errors } = await openGate(page, theme, reviewGateWorld({ gate: worldGateScheduled() }))
    await expect(page.locator('[data-review-gate-state]')).toContainText('Audit only until Sep 22, 2026, 12:00 (UTC+00:00)')
    await expect(page.getByRole('button', { name: 'Clear date' })).toBeVisible()
    await visualRegion(page, 'review-gate-scheduled', theme, page.locator('[data-review-gate]'))
    assertHermetic(api, errors)
  })

  test(`review gate, enforced — ${theme}`, async ({ page }) => {
    const { api, errors } = await openGate(page, theme, reviewGateWorld({ gate: worldGateDate() }))
    await expect(page.locator('[data-review-gate-state]')).toContainText('Enforced since Sep 16, 2026, 12:00 (UTC+00:00)')
    await expect(page.locator('[data-review-gate-channels] li')).toHaveCount(13)
    await visualRegion(page, 'review-gate-enforced', theme, page.locator('[data-review-gate]'))
    assertHermetic(api, errors)
  })

  test(`review gate, the past-date warning — ${theme}`, async ({ page }) => {
    const world = reviewGateWorld()
    const { api, errors } = await openGate(page, theme, world)
    const card = page.locator('[data-review-gate-date]')
    await card.getByLabel('Enforce from').fill('2026-09-17T08:30')
    await card.getByRole('button', { name: 'Set date' }).click()
    await expect(card.locator('[data-inline-confirm]')).toBeVisible()
    expect(world.writes, 'nothing is sent before Enforce now').toEqual([])
    await blur(page)
    await visualRegion(page, 'review-gate-past-date-confirm', theme, card)
    assertHermetic(api, errors)
  })

  test(`agents, the Try-an-agent panel on inline input — ${theme}`, async ({ page }) => {
    const world = tryAgentWorld()
    const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}`, { theme, handlers: world.handlers, ready: agentsReady, user: world.user })
    await networkQuiet(page, api)
    await page.getByRole('button', { name: 'Try an agent' }).click()
    const panel = page.locator('[data-try-agent]')
    await panel.getByLabel('Agent').selectOption('agent.summary.v1')
    await expect(panel.getByRole('textbox', { name: 'Inline input (JSON)' })).toBeVisible()
    await expect(panel.locator('[data-try-agent-bytes]')).toBeVisible()
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'agents-try-panel-inline', theme, panel)
    assertHermetic(api, errors)
  })

  test(`agents, an invocation awaiting review — ${theme}`, async ({ page }) => {
    const world = tryAgentWorld({ invocationSequence: [worldInvocationAwaitingReview()] })
    const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}&invocation=${WORLD_INVOCATION_ID}`, {
      theme, handlers: world.handlers, ready: agentsReady, user: world.user,
    })
    const detail = page.locator('[data-invocation-detail]')
    await expect(detail).toHaveAttribute('data-invocation-status', 'completed')
    await expect(detail.locator('[data-invocation-stored-input]')).toBeVisible()
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'agents-invocation-awaiting-review', theme, detail)
    assertHermetic(api, errors)
  })
}
