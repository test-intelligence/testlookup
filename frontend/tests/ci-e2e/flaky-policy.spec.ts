/**
 * W09 FLK-02 (contract §2.6), hermetic: the quarantine policy has a page, `/flaky?tab=policy`.
 *
 *  - a QA lead: the card (`[data-quarantine-policy]`, in the tab, no dialog) reads the project's defaults
 *    (`Using defaults: SLA 14 days, promote after 20 passes, detection 20 % over 10 runs.`, built from the
 *    answer), `2 of 8 active now.` from the counts the page already reads; SLA 7 and auto-release on → Save →
 *    the PUT body (the whole policy, the flip rate sent as 0-1); toast; after a reload the GET answers the PUT
 *    and the card shows the saved values without the defaults line;
 *  - a value out of range (SLA 400) blocks Save with the range beside the field;
 *  - a QA engineer sees the same card read-only, with `Only a QA lead can change the quarantine policy.`;
 *  - the cap has its consumer: with the cap saved at 1, the Quarantined tab warns
 *    `2 tests are quarantined; this project's policy warns past 1.`;
 *  - the policy is read on the Policy and Quarantined tabs only (never Detected / Proposed / History).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `flakyQuarantineWorld()` over
 * `flakyHandlers` (whose policy PUT is answered back by the GET).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { flakyQuarantineWorld, QA_ENGINEER } from '../visual/production/fixtures-notify-flaky'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const POLICY_PATH = `/api/v1/projects/${P}/quarantine/policy`
const ready = (p: Page) => p.locator('[data-page-header] h1')
const card = (page: Page) => page.locator('[data-quarantine-policy]')
const tab = (page: Page, name: string) => page.getByRole('tab', { name: new RegExp(`^${name}`) })
const policyGets = (api: { seen: string[] }) => api.seen.filter((line) => line === `GET ${POLICY_PATH}`).length

const DEFAULTS_LINE = 'Using defaults: SLA 14 days, promote after 20 passes, detection 20 % over 10 runs.'

/** The card's fields, by the labels of the contract (§2.6). */
const fields = (scope: Locator) => ({
  sla: scope.getByLabel(/^SLA \(days a test may stay quarantined\)/),
  autoDefect: scope.getByLabel(/^File an internal defect when a quarantine is approved/),
  autoRelease: scope.getByRole('checkbox', { name: /^Release automatically after/ }),
  passes: scope.getByRole('spinbutton', { name: /consecutive passes/ }),
  threshold: scope.getByLabel(/^Detection threshold/),
  minRuns: scope.getByLabel(/minimum runs/i),
  cap: scope.getByLabel(/^Warn when more than/),
  save: scope.getByRole('button', { name: 'Save', exact: true }),
})

test('a QA lead: the defaults sentence, SLA 7 + auto-release → Save PUTs the whole policy; a reload shows it', async ({ page }) => {
  const world = flakyQuarantineWorld()
  const { api, errors } = await openRollout(page, '/flaky?tab=policy', { handlers: world.handlers, ready, flags: { flaky_auto_quarantine: true } })
  await expect(tab(page, 'Policy')).toHaveAttribute('aria-selected', 'true')
  // A tab with no count.
  await expect(tab(page, 'Policy')).toHaveText('Policy')
  const policy = card(page)
  await expect(policy).toContainText(DEFAULTS_LINE)
  await expect(page.locator('#main-content [data-quarantine-policy]'), 'the card is in the tab, not a dialog').toHaveCount(1)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  const f = fields(policy)
  await expect(f.sla).toHaveValue('14')
  await expect(f.autoDefect).not.toBeChecked()
  await expect(policy.getByText('An internal TestLookup defect. No Jira issue is created.')).toBeVisible()
  await expect(f.autoRelease).not.toBeChecked()
  await expect(f.passes).toHaveValue('20')
  await expect(f.threshold).toHaveValue('20')
  await expect(f.minRuns).toHaveValue('10')
  await expect(f.cap).toHaveValue('8')
  // The two active quarantines of the world, from the counts the page already reads.
  await expect(policy).toContainText('2 of 8 active now.')
  await networkQuiet(page, api)
  expect(policyGets(api), 'the policy, once').toBe(1)

  await f.sla.fill('7')
  await f.autoRelease.check()
  const put = page.waitForRequest((r) => r.method() === 'PUT' && new URL(r.url()).pathname === POLICY_PATH)
  await f.save.click()
  expect((await put).postDataJSON()).toEqual({
    sla_days: 7,
    auto_create_defect: false,
    auto_promote: true,
    promote_after_passes: 20,
    detection_flip_rate_threshold: 0.2,
    detection_min_runs: 10,
    max_active_quarantined: 8,
  })
  await expect(page.getByText('Quarantine policy saved')).toBeVisible()
  expect(world.policyWrites).toHaveLength(1)

  await page.reload()
  await expect(card(page)).toBeVisible()
  const saved = fields(card(page))
  await expect(saved.sla).toHaveValue('7')
  await expect(saved.autoRelease).toBeChecked()
  await expect(card(page)).not.toContainText('Using defaults')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a value out of its range blocks Save with the range beside the field', async ({ page }) => {
  const world = flakyQuarantineWorld()
  const { api, errors } = await openRollout(page, '/flaky?tab=policy', { handlers: world.handlers, ready })
  const f = fields(card(page))
  await expect(f.sla).toHaveValue('14')
  await f.sla.fill('400')
  await expect(card(page)).toContainText('1-365 days')
  if (await f.save.isEnabled()) await f.save.click()
  await networkQuiet(page, api)
  expect(world.policyWrites, 'nothing was saved').toEqual([])
  expect(api.seen.filter((line) => line.startsWith('PUT '))).toEqual([])
  assertHermetic(api, errors)
})

test('a QA engineer sees the policy read-only', async ({ page }) => {
  const world = flakyQuarantineWorld()
  const { api, errors } = await openRollout(page, '/flaky?tab=policy', { handlers: world.handlers, ready, user: QA_ENGINEER })
  const policy = card(page)
  await expect(policy).toContainText('Only a QA lead can change the quarantine policy.')
  const f = fields(policy)
  await expect(f.sla).toHaveValue('14')
  for (const input of [f.sla, f.autoDefect, f.autoRelease, f.passes, f.threshold, f.minRuns, f.cap]) await expect(input).toBeDisabled()
  await expect(f.save).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.policyWrites).toEqual([])
  assertHermetic(api, errors)
})

test('the cap has a consumer: past it the Quarantined tab warns; the policy is read on two tabs only', async ({ page }) => {
  const world = flakyQuarantineWorld()
  const { api, errors } = await openRollout(page, '/flaky', { handlers: world.handlers, ready })
  await networkQuiet(page, api)
  expect(policyGets(api), 'Detected does not read the policy').toBe(0)
  await tab(page, 'Proposed').click()
  await tab(page, 'History').click()
  await networkQuiet(page, api)
  expect(policyGets(api), 'nor do Proposed and History').toBe(0)

  // Under the cap (2 of 8): no warning.
  await tab(page, 'Quarantined').click()
  await expect(page.locator('[data-primary]').getByTestId('quarantine-row')).toHaveCount(2)
  await expect.poll(() => policyGets(api), { message: 'the Quarantined tab reads the policy' }).toBeGreaterThan(0)
  await networkQuiet(page, api)
  await expect(page.locator('[data-quarantine-over-cap]')).toHaveCount(0)

  // Save the cap at 1, then the Quarantined tab warns.
  await tab(page, 'Policy').click()
  const f = fields(card(page))
  await expect(f.cap).toHaveValue('8')
  await f.cap.fill('1')
  const put = page.waitForRequest((r) => r.method() === 'PUT' && new URL(r.url()).pathname === POLICY_PATH)
  await f.save.click()
  expect(((await put).postDataJSON() as { max_active_quarantined: number }).max_active_quarantined).toBe(1)
  await tab(page, 'Quarantined').click()
  await expect(page.locator('[data-quarantine-over-cap]')).toHaveText("2 tests are quarantined; this project's policy warns past 1.")
  await networkQuiet(page, api)
  expect(requestsTo(api, POLICY_PATH).filter((line) => line.startsWith('PUT '))).toEqual([`PUT ${POLICY_PATH}`])
  assertHermetic(api, errors)
})
