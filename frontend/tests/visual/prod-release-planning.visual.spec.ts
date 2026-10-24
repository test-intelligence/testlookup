/**
 * W11 "release-planning-policy" baselines (`docs/deferred-scope-2026-10-13/work/W11-contract.md` §6.5, §7.3: the
 * twelve NEW regions), three themes each (`THEMES_WITH_PAPER`: signal, lab, paper), each region the in-page container
 * itself, focus blurred and the pointer off it, at `PINNED_TALL` (1280 × 4000, nothing scrolled, as `visualRegion`
 * requires):
 *
 *  - `releases-plan-callout`: the planning release 2026.12's card, "No phases scoped yet" with Copy phases from… and
 *    Draft from a PRD… on its line (REL-05 / REL-07);
 *  - `releases-clone-form`: the `Copy phases` form in the pane, 2026.10 chosen, its four phases previewed, First phase
 *    starts 2026-10-05 (REL-05);
 *  - `releases-draft-preview`: the `Draft phases from a PRD` form after the hermetic rules draft — the badge with its
 *    reason and three typed, editable rows (REL-07);
 *  - `releases-calendar`: September 2026 in the list column — 2026.10 and 2026.11 on their days, 2026.11's three
 *    phases as bars, the UTC legend (REL-06);
 *  - `releases-portfolio-kpis`: "Release health · this week" open — the four cards with their 12-week sparklines and
 *    the two captions (REL-03);
 *  - `attribution-rules-page`: the rules page — the ladder and the three rules (REL-09);
 *  - `attribution-rule-preview`: New rule `branch release/2.5* → 2.5` after Preview — the sentences and the samples;
 *  - `policy-versions-compare`: the policy's Versions open, Compare v3 → v4 drawn (GATE-02);
 *  - `overview-verdict-policy`: Overview's "How this verdict is decided" open — Decided by Payments gate v4 and the P0
 *    cap (GATE-04);
 *  - `trends-pass-rate-policy`: the Trends hero with the project policy (GO ≥ 97%, No-Go < 92% shaded) (GATE-04);
 *  - `release-gate-release-strip`: the release strip under `/release-gate/:runId`'s header (GATE-08a);
 *  - `compliance-pack-verified`: a release's compliance-pack panel after Verify — the Signed chip and the verified
 *    result (REL-11).
 *
 * The five CHANGED regions (`trends-on-pass-rate`, `trends-on-trend-analysis`, `trends-on-pass-rate-notes`,
 * `trends-presentation-pass-rate`: the GO line 95 and the No-Go zone under the default bands; `run-header`: the chip
 * reads `Unattributed`) are NOT shot here: a baseline's path is its region name alone, so each stays with its one
 * unedited writer; the last describe asserts their new content, without a shot, for the reader of the re-shot PNGs.
 *
 * Hermetic fixtures: `production/fixtures-release-planning.ts`. PNGs are rendered by CI only (`visual-baselines.yml`,
 * Linux); nobody regenerates them locally.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, cardByHeading, landmark, PINNED_TALL, THEMES_WITH_PAPER, visualRegion, waitForCharts } from '../lib/production-pages'
import { expectDrawn, networkQuiet, openRollout, sectionFrame } from '../lib/rollout'
import { RUN_ID, TRENDS_ON } from './production/fixtures'
import { RUN_PAGE } from './production/fixtures-run'
import {
  attributionRulesWorld,
  CLONE_FIRST_START,
  DECIDED_BY,
  DRAFT_FALLBACK,
  NEW_RULE,
  overviewPolicyWorld,
  POLICY_IDS,
  policyVersionsWorld,
  PORTFOLIO_CAPTIONS,
  PRD_TEXT,
  PREVIEW_SENTENCES,
  QA_LEAD,
  RELEASE_ACTIVE_ID,
  RELEASE_RUNS,
  RELEASE_SHIPPED_ID,
  releaseGateFollowsWorld,
  releasePlanningWorld,
  STRIP_TEXT,
  trendsPolicyWorld,
  VERIFIED_SUMMARY,
} from './production/fixtures-release-planning'

test.use(PINNED_TALL)

const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const releasesReady = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const card = (page: Page, name: string) => page.getByRole('article', { name: new RegExp(`^${name.replace('.', '\\.')} `) })
const pane = (page: Page, name: string) => page.getByRole('complementary', { name, exact: true })
const disclosure = (page: Page, title: RegExp): Locator =>
  page.locator('section[data-disclosure]').filter({ has: page.getByRole('button', { name: title }) })

/** Settle the network, blur, then shoot. */
async function shoot(page: Page, api: Parameters<typeof networkQuiet>[1], name: string, theme: (typeof THEMES_WITH_PAPER)[number], region: Locator) {
  await networkQuiet(page, api)
  await blur(page)
  await visualRegion(page, name, theme, region)
}

for (const theme of THEMES_WITH_PAPER) {
  // ── /releases (REL-05, REL-07, REL-06, REL-03, REL-11) ──
  test(`the planning callout — ${theme}`, async ({ page }) => {
    const world = releasePlanningWorld()
    const { api, errors } = await openRollout(page, '/releases', { theme, handlers: world.handlers, ready: releasesReady, user: QA_LEAD })
    const planned = card(page, '2026.12')
    await expect(planned.getByRole('button', { name: 'Copy phases from…', exact: true })).toBeVisible()
    await expect(planned.getByRole('button', { name: 'Draft from a PRD…', exact: true })).toBeVisible()
    await shoot(page, api, 'releases-plan-callout', theme, planned)
    assertHermetic(api, errors)
  })

  test(`the Copy phases form — ${theme}`, async ({ page }) => {
    const world = releasePlanningWorld()
    const { api, errors } = await openRollout(page, '/releases', { theme, handlers: world.handlers, ready: releasesReady, user: QA_LEAD })
    await card(page, '2026.12').getByRole('button', { name: 'Copy phases from…', exact: true }).click()
    const form = pane(page, '2026.12').getByRole('region', { name: 'Copy phases', exact: true })
    await form.getByLabel('From release').selectOption({ label: '2026.10 · 4 phases' })
    await form.getByLabel('First phase starts').fill(CLONE_FIRST_START)
    await expect(form.getByTitle('Production cutover', { exact: true })).toBeVisible()
    await shoot(page, api, 'releases-clone-form', theme, form)
    assertHermetic(api, errors)
  })

  test(`the PRD draft preview — ${theme}`, async ({ page }) => {
    const world = releasePlanningWorld()
    const { api, errors } = await openRollout(page, '/releases', { theme, handlers: world.handlers, ready: releasesReady, user: QA_LEAD })
    await card(page, '2026.12').getByRole('button', { name: 'Draft from a PRD…', exact: true }).click()
    const form = pane(page, '2026.12').getByRole('region', { name: 'Draft phases from a PRD', exact: true })
    await form.getByLabel('PRD text').fill(PRD_TEXT)
    await form.getByRole('button', { name: 'Draft phases', exact: true }).click()
    await expect(form.getByText(`Drafted by rules — ${DRAFT_FALLBACK.text}`, { exact: true })).toBeVisible()
    await expect(form.getByRole('button', { name: 'Create 3 phases', exact: true })).toBeVisible()
    await shoot(page, api, 'releases-draft-preview', theme, form)
    assertHermetic(api, errors)
  })

  test(`the release calendar — ${theme}`, async ({ page }) => {
    const world = releasePlanningWorld()
    const { api, errors } = await openRollout(page, '/releases?view=calendar', { theme, handlers: world.handlers, ready: releasesReady, user: QA_LEAD })
    const calendar = page.locator('[data-primary]')
    await expect(calendar.getByText('September 2026')).toBeVisible()
    await expect(calendar.getByRole('button', { name: /Payments soak/ })).toBeVisible()
    await expect(calendar.getByText('Dates are shown as UTC days.', { exact: true })).toBeVisible()
    await shoot(page, api, 'releases-calendar', theme, calendar)
    assertHermetic(api, errors)
  })

  test(`the release KPI sparklines — ${theme}`, async ({ page }) => {
    const world = releasePlanningWorld()
    const { api, errors } = await openRollout(page, '/releases', { theme, handlers: world.handlers, ready: releasesReady, user: QA_LEAD })
    await page.getByRole('button', { name: /^Release health · this week/ }).click()
    const health = disclosure(page, /^Release health · this week/)
    await expect(health.getByRole('img')).toHaveCount(4)
    await expect(health.getByText(PORTFOLIO_CAPTIONS.ready, { exact: true })).toBeVisible()
    await expect(health.getByText(PORTFOLIO_CAPTIONS.pack, { exact: true })).toBeVisible()
    await shoot(page, api, 'releases-portfolio-kpis', theme, health)
    assertHermetic(api, errors)
  })

  test(`the verified compliance pack — ${theme}`, async ({ page }) => {
    const world = releasePlanningWorld()
    const { api, errors } = await openRollout(page, `/releases?open=${RELEASE_SHIPPED_ID}`, { theme, handlers: world.handlers, ready: releasesReady, user: QA_LEAD })
    const panel = cardByHeading(pane(page, '2026.10'), 'Compliance Pack')
    await panel.getByRole('button', { name: 'Verify', exact: true }).click()
    await expect(panel.locator('[data-pack-verification]')).toContainText(VERIFIED_SUMMARY)
    await shoot(page, api, 'compliance-pack-verified', theme, panel)
    assertHermetic(api, errors)
  })

  // ── /releases/attribution-rules (REL-09) ──
  test(`the attribution rules page — ${theme}`, async ({ page }) => {
    const world = attributionRulesWorld()
    const h1 = (p: Page) => p.getByRole('heading', { level: 1, name: 'Attribution rules', exact: true })
    const { api, errors } = await openRollout(page, '/releases/attribution-rules', { theme, handlers: world.handlers, ready: h1, user: QA_LEAD })
    // The page's body: the header, the ladder, the rules table and the footer (the parent of `[data-primary]`).
    const body = page.locator('[data-primary]').locator('xpath=..')
    await expect(body.getByRole('row').filter({ hasText: 'Nightly builds' })).toBeVisible()
    await expect(body.getByRole('region', { name: 'How a run gets its release' })).toBeVisible()
    await shoot(page, api, 'attribution-rules-page', theme, body)
    assertHermetic(api, errors)
  })

  test(`an attribution rule's preview — ${theme}`, async ({ page }) => {
    const world = attributionRulesWorld()
    const h1 = (p: Page) => p.getByRole('heading', { level: 1, name: 'Attribution rules', exact: true })
    const { api, errors } = await openRollout(page, '/releases/attribution-rules', { theme, handlers: world.handlers, ready: h1, user: QA_LEAD })
    await page.getByRole('button', { name: 'New rule', exact: true }).click()
    const form = opened(page, 'New attribution rule')
    await form.getByLabel('Name', { exact: true }).fill(NEW_RULE.name)
    await form.getByLabel('Field', { exact: true }).selectOption(NEW_RULE.match_field)
    await form.getByLabel('Pattern', { exact: true }).fill(NEW_RULE.match_pattern)
    await form.getByLabel('Release', { exact: true }).fill(NEW_RULE.target_release_name)
    await form.getByRole('button', { name: 'Preview', exact: true }).click()
    const sentence = page.getByText(PREVIEW_SENTENCES.matches, { exact: true })
    await expect(sentence).toBeVisible()
    const preview = form.locator('[data-attribution-preview]')
    await expect(preview).toContainText(PREVIEW_SENTENCES.missingTarget)
    await shoot(page, api, 'attribution-rule-preview', theme, preview)
    assertHermetic(api, errors)
  })

  // ── /policies/:id (GATE-02) ──
  test(`a policy's versions compared — ${theme}`, async ({ page }) => {
    const world = policyVersionsWorld()
    const view = (p: Page) => p.getByRole('heading', { name: 'View: Payments gate', level: 1 })
    const { api, errors } = await openRollout(page, `/policies/${POLICY_IDS.v4}`, { theme, handlers: world.handlers, ready: view, user: QA_LEAD })
    await page.getByRole('button', { name: /^Versions/ }).click()
    const versions = disclosure(page, /^Versions/)
    await versions.locator('li, [data-policy-version]').filter({ hasText: 'v3 · Published' }).first().getByRole('checkbox').check()
    await versions.locator('li, [data-policy-version]').filter({ hasText: 'v4 · Active' }).first().getByRole('checkbox').check()
    await versions.getByRole('button', { name: 'Compare v3 → v4', exact: true }).click()
    await expect(versions.getByText('Pass-rate band yellow_min 95 → 97', { exact: true })).toBeVisible()
    await page.mouse.move(0, 0)
    await shoot(page, api, 'policy-versions-compare', theme, versions)
    assertHermetic(api, errors)
  })

  // ── /overview and /trends (GATE-04) ──
  test(`the Overview verdict's policy — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/overview', {
      theme,
      handlers: overviewPolicyWorld(),
      ready: (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }),
      user: QA_LEAD,
    })
    await page.getByRole('button', { name: /^How this verdict is decided/ }).click()
    const details = disclosure(page, /^How this verdict is decided/)
    await expect(details.getByText(DECIDED_BY, { exact: true })).toBeVisible()
    await shoot(page, api, 'overview-verdict-policy', theme, details)
    assertHermetic(api, errors)
  })

  test(`the Trends hero with a project policy — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/trends', { theme, handlers: trendsPolicyWorld(), ready: (p) => landmark(p, 'Trend metrics'), user: QA_LEAD })
    const hero = sectionFrame(page, 'trends-pass-rate', 'Pass rate trend')
    await expectDrawn(hero, 'pass rate')
    await waitForCharts(hero)
    await expect(hero).toContainText('GO ≥ 97%')
    await expect(hero).toContainText('No-Go < 92%')
    await shoot(page, api, 'trends-pass-rate-policy', theme, hero)
    assertHermetic(api, errors)
  })

  // ── /release-gate/:runId (GATE-08a) ──
  test(`the release-gate release strip — ${theme}`, async ({ page }) => {
    const world = releaseGateFollowsWorld()
    const { api, errors } = await openRollout(page, `/release-gate/${RUN_ID}`, {
      theme,
      handlers: world.handlers,
      ready: (p) => p.getByRole('meter', { name: 'Risk Score' }),
      user: QA_LEAD,
    })
    await page.getByLabel('Filter by release').selectOption({ label: '2026.11' })
    await expect(page).toHaveURL((url) => url.pathname === `/release-gate/${RELEASE_RUNS.finished}`)
    const strip = page.locator('[data-release-verdict-strip]')
    await expect(strip).toContainText(STRIP_TEXT)
    await expect(page.getByRole('meter', { name: 'Risk Score' })).toBeVisible()
    expect(new URL(page.url()).searchParams.get('release')).toBe(RELEASE_ACTIVE_ID)
    await shoot(page, api, 'release-gate-release-strip', theme, strip)
    assertHermetic(api, errors)
  })
}

// The CHANGED regions are shot by their own unedited specs; here only their new content is asserted, so the reader of
// the re-shot PNGs knows what each must show.
test.describe('the CHANGED regions (shot by prod-trends-on / prod-presentation / prod-run) hold the W11 change', () => {
  test('trends-on-pass-rate: the GO line at 95 and the No-Go zone under the default bands, the takeaway names them', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/trends', { handlers: TRENDS_ON, ready: (p) => landmark(p, 'Trend metrics') })
    const hero = sectionFrame(page, 'trends-pass-rate', 'Pass rate trend')
    await expectDrawn(hero, 'pass rate')
    await expect(hero).toContainText('GO ≥ 95%')
    await expect(hero).toContainText('No-Go < 90%')
    await expect(hero).toContainText('(95%, default bands)')
    await expect(hero).not.toContainText('Target 90%')
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('run-header: a run with no release reads Unattributed', async ({ page }) => {
    const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, { handlers: RUN_PAGE, ready: (p) => p.locator('[data-primary] table') })
    const header = page.locator('[data-page-header]')
    await expect(header.getByRole('button', { name: 'Set release (Unattributed)' })).toBeVisible()
    await expect(header.getByText('Unattributed', { exact: true })).toBeVisible()
    await expect(header.getByText('Set release', { exact: true })).toHaveCount(0)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
})
