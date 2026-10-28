/**
 * W22b ACT-06 the purge window, the pause, per-field inheritance and the instance defaults on
 * `/settings/retention`, hermetic (`docs/deferred-scope-2026-10-13/work/W22b-contract.md` §4.1, §6.3), at 1280:
 *
 *  - the window select is in the PUT only when it changed (`{purge_window_start_utc: 3}`), and the status line
 *    reads the next purge in UTC;
 *  - `Pause 7 days` → `{pause_days: 7}` → `Paused until …`; `Resume` → `{resume: true}`;
 *  - a world with `field_sources` shows `180 days (instance default) · Override`, and `Reset to inherited` sends
 *    `inherit: ["runs_days"]` (never the value);
 *  - the Instance defaults card issues NO request until `Open`; `Turn on…` lists the preview's projects, `Turn on`
 *    is enabled only by the exact phrase, and sends `{confirmation, preview_hash}`;
 *  - a QA lead gets the refusal page and makes NO jobs / instance request.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock `NOW` = 2026-09-18 12:00 UTC). World:
 * `fixtures-retention-b.ts` `retentionExportWorld`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectMainFits, inPageLine } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  retentionExportWorld,
  type RetentionExportWorld,
  type RetentionExportWorldOptions,
} from '../visual/production/fixtures-retention-b'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce', viewport: { width: 1280, height: 900 } })

const P = PROJECT_ID
const POLICY = `/api/v1/projects/${P}/retention-policy`
const INSTANCE = '/api/v1/settings/retention-defaults'
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const schedule = (page: Page) => page.locator('[data-retention-schedule]')
const marker = (page: Page, field: string) => page.locator(`[data-retention-inherited="${field}"]`)
const instanceCard = (page: Page) => page.locator('[data-retention-instance-defaults]')
const policyPuts = (world: RetentionExportWorld) => world.writes.filter((w) => w.line === `PUT ${POLICY}`).map((w) => w.body)
const instanceReads = (lines: string[]) => lines.filter((line) => line.includes(INSTANCE))
const saveButton = (page: Page) => page.getByRole('button', { name: 'Save policy' })

/** A one-line flex row's free width: the row minus its children and gaps (≥ 40 px means it never wraps on DejaVu). */
async function rowSlack(row: Locator): Promise<number> {
  return row.evaluate((el: HTMLElement) => {
    const kids = Array.from(el.children) as HTMLElement[]
    const gap = parseFloat(getComputedStyle(el).columnGap) || 0
    const used = kids.reduce((sum, kid) => sum + kid.getBoundingClientRect().width, 0) + gap * (kids.length - 1)
    return Math.round(el.clientWidth - used)
  })
}

async function open(page: Page, options: RetentionExportWorldOptions = {}) {
  const world = retentionExportWorld({ role: 'ADMIN', ...options })
  const opened = await openRollout(page, '/settings/retention', { handlers: world.handlers, ready: h1('Retention & Purge'), user: world.user })
  await expect(saveButton(page)).toBeVisible()
  await networkQuiet(page, opened.api)
  return { world, ...opened }
}

test('the window is in the PUT only when it changed; the next purge reads in UTC', async ({ page }) => {
  const { world, api, errors } = await open(page, { policy: { purge_window_start_utc: 2 } })
  const select = page.locator('#retention-purge_window_start_utc')
  await expect(select).toBeEnabled()
  await expect(schedule(page).getByText('Next scheduled purge Sep 19, 2026, 02:00 UTC', { exact: true })).toBeVisible()

  await page.locator('#retention-raw_events_days').fill('60')
  await saveButton(page).click()
  await expect(saveButton(page)).toBeDisabled()
  await select.selectOption('3')
  await saveButton(page).click()
  await expect(schedule(page).getByText('Next scheduled purge Sep 19, 2026, 03:00 UTC', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(policyPuts(world)).toEqual([{ raw_events_days: 60 }, { purge_window_start_utc: 3 }])
  assertHermetic(api, errors)
})

test('Pause 7 days → Paused until …; Resume', async ({ page }) => {
  const { world, api, errors } = await open(page)
  const pause = schedule(page).getByRole('button', { name: 'Pause 7 days' })
  await page.locator('#retention-raw_events_days').fill('60')
  await expect(pause, 'waits for Save while the form has changes').toBeDisabled()
  await expect(pause).toHaveAttribute('title', 'Save or discard your changes first')
  await page.locator('#retention-raw_events_days').fill('90')
  await expect(pause).toBeEnabled()

  await pause.click()
  await expect(schedule(page).getByText('Paused until Sep 25, 2026, 12:00', { exact: true })).toBeVisible()
  await expect(schedule(page).locator('[data-retention-paused]')).toHaveText('Paused')
  const slack = await rowSlack(schedule(page).locator('[data-retention-schedule-status]'))
  console.log(inPageLine('schedule status row', 1280, { slack }))
  expect(slack).toBeGreaterThanOrEqual(40)

  await schedule(page).getByRole('button', { name: 'Resume' }).click()
  await expect(schedule(page).getByText(/^Next scheduled purge /)).toBeVisible()
  await networkQuiet(page, api)
  expect(policyPuts(world)).toEqual([{ pause_days: 7 }, { resume: true }])
  assertHermetic(api, errors)
})

test('inherited markers: Override / Reset to inherited sends inherit, never the value', async ({ page }) => {
  // runs_days is the project's own (400); artifacts inherit the instance's 180; the instance runs window is 180.
  const { world, api, errors } = await open(page, {
    policy: { runs_days: 400, artifacts_days: null },
    instance: { runs_days: 180, artifacts_days: 180 },
  })
  await expect(marker(page, 'artifacts_days')).toHaveText('180 days (instance default)·Override')
  await expect(page.locator('#retention-artifacts_days')).toBeDisabled()
  await expect(page.locator('#retention-artifacts_days')).toHaveValue('180')
  await expect(marker(page, 'runs_days')).toHaveText('Project value·Reset to inherited')

  await marker(page, 'runs_days').getByRole('button', { name: 'Reset to inherited' }).click()
  await expect(page.locator('#retention-runs_days')).toHaveValue('180')
  await expect(page.locator('#retention-runs_days')).toBeDisabled()
  await saveButton(page).click()
  await expect(marker(page, 'runs_days')).toHaveText('180 days (instance default)·Override')
  await networkQuiet(page, api)
  expect(policyPuts(world)).toEqual([{ inherit: ['runs_days'] }])
  await expectMainFits(page, 'retention markers @1280')
  assertHermetic(api, errors)
})

test('the Instance defaults card reads nothing until Open; Turn on needs the exact phrase', async ({ page }) => {
  const { world, api, errors } = await open(page)
  const card = instanceCard(page)
  await expect(card).toBeVisible()
  expect(instanceReads(api.seen), 'no instance request while collapsed').toEqual([])

  await card.getByRole('button', { name: 'Open', exact: true }).click()
  await expect(card.getByRole('button', { name: 'Turn on for every project without its own setting…' })).toBeVisible()
  await networkQuiet(page, api)
  expect(instanceReads(api.seen)).toEqual([`GET ${INSTANCE}`])

  const previewAnswer = page.waitForResponse((r) => new URL(r.url()).pathname === `${INSTANCE}/enable-preview`)
  await card.getByRole('button', { name: 'Turn on for every project without its own setting…' }).click()
  const previewHash = ((await (await previewAnswer).json()) as { preview_hash: string }).preview_hash
  const enable = card.locator('[data-retention-instance-enable]')
  const rows = enable.locator('[data-retention-instance-preview-row]')
  await expect(rows).toHaveCount(3)
  await expect(rows.nth(0)).toContainText('Payments sentinel')
  await expect(enable.getByText('Counts are before the keep-last floor and holds.', { exact: true })).toBeVisible()
  const phrase = 'enable retention for 3 projects'
  await expect(enable.getByText(`Type "${phrase}" to confirm`, { exact: true })).toBeVisible()
  const turnOn = enable.getByRole('button', { name: 'Turn on', exact: true })
  await expect(turnOn).toBeDisabled()
  const input = enable.getByLabel(`Type "${phrase}" to confirm`)
  await input.fill('enable retention for 3 project')
  await expect(turnOn, 'a near miss keeps it disabled').toBeDisabled()
  await input.fill(phrase)
  await expect(turnOn).toBeEnabled()
  await turnOn.click()
  await expect(card.getByRole('button', { name: 'Turn off', exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes.filter((w) => w.line.startsWith(`POST ${INSTANCE}`))).toEqual([
    { line: `POST ${INSTANCE}/enable`, body: { confirmation: phrase, preview_hash: previewHash } },
  ])
  await expectMainFits(page, 'instance defaults @1280')
  assertHermetic(api, errors)
})

test('a QA lead gets the refusal page and makes no jobs or instance request', async ({ page }) => {
  const world = retentionExportWorld({ role: 'QA_LEAD' })
  const { api, errors } = await openRollout(page, '/settings/retention', {
    handlers: world.handlers,
    ready: (p) => p.getByText('Admin access required'),
    user: world.user,
  })
  await networkQuiet(page, api)
  await expect(instanceCard(page)).toHaveCount(0)
  await expect(page.locator('[data-deletion-jobs]')).toHaveCount(0)
  const seen = [...api.seen, ...api.unhandled]
  expect(seen.filter((line) => line.includes(`/projects/${P}/deletion/jobs`))).toEqual([])
  expect(instanceReads(seen)).toEqual([])
  assertHermetic(api, errors)
})
