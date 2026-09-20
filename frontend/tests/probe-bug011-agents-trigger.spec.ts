/**
 * BUG-011 live verification: choosing a run must change what the page shows.
 *
 *   # against the deployment
 *   npx playwright test --config probe-live.config.ts probe-bug011-agents-trigger
 *   # against a local dev stack (vite :3000 + backend :8000)
 *   PROBE_BASE_URL=http://localhost:3000 npx playwright test \
 *     --config probe-live.config.ts probe-bug011-agents-trigger
 *
 * Two user reports, one page, and the first fix caused the second.
 *
 * TL-2026-09-18-01-009 was "selecting a run does not refresh the page". It was
 * fixed by clearing the stale pipeline id, verified by unit test, and never
 * checked against the deployment. BUG-011 is the same sentence reported again,
 * because clearing the id left every right-hand panel on "Select a pipeline run
 * to see agent stages" — the SAME placeholder for every run. Measured here
 * before the fix: four runs, four different left-hand lists, one identical
 * empty panel. Wrong content had become no content.
 *
 * So the load-bearing assertion is that the detail panel DIFFERS between two
 * runs. Asserting merely that "something rendered" would have passed all the
 * way through the reported bug: the placeholder is something.
 *
 * Read-only. It navigates and reads. The one control it sets is the trigger
 * dropdown, whose form it never submits, so no pipeline is queued.
 */
import { expect, test, type Page, type APIRequestContext } from '@playwright/test'

const BASE = process.env.PROBE_BASE_URL ?? 'http://testlookup.local'
const USER = 'admin'
const PASS = process.env.E2E_ADMIN_PASSWORD ?? 'Admin@2026!'

/** The deployment disables dev-login, so fall back to a real form login. */
async function tokenFor(request: APIRequestContext): Promise<string> {
  const dev = await request.post(
    `${BASE}/api/v1/auth/dev-login?username=${USER}&role=ADMIN`,
    { failOnStatusCode: false },
  )
  if (dev.ok()) return (await dev.json()).access_token
  const login = await request.post(`${BASE}/api/v1/auth/login`, {
    form: { username: USER, password: PASS },
  })
  expect(login.ok(), 'neither dev-login nor form login succeeded').toBeTruthy()
  return (await login.json()).access_token
}

async function signIn(page: Page, request: APIRequestContext) {
  const token = await tokenFor(request)
  await page.addInitScript((t) => {
    localStorage.setItem('auth-storage', JSON.stringify({
      state: {
        token: t,
        refreshToken: '',
        user: { username: 'admin', role: 'ADMIN', email: 'admin@testlookup.local' },
        isAuthenticated: true,
      },
      version: 0,
    }))
  }, token)
}

const PLACEHOLDER = /Select a pipeline run to see agent stages/i

/**
 * Wait for the run options to arrive, then return them.
 *
 * The select renders immediately with only its placeholder option and fills in
 * when `useRuns` resolves. Reading it straight after `toBeVisible` raced that
 * fetch and saw one option, which a `test.skip(options < 2)` turned into a
 * PASSING run — the probe reporting "nothing to check" on a page that had
 * everything to check. Waiting, and failing when nothing arrives, keeps the
 * skip honest: it can then only mean a genuinely empty deployment.
 */
async function runOptions(page: Page): Promise<string[]> {
  const header = page.locator('#agent-field-0')
  await expect(
    header,
    'the run dropdown is not on the page — this probe is not measuring what it claims to',
  ).toBeVisible({ timeout: 30_000 })
  await expect
    .poll(() => header.locator('option').count(), {
      timeout: 30_000,
      message:
        'the run dropdown never filled with runs. Either this deployment has ' +
        'no runs, or /runs is failing — both need a look, and neither is a ' +
        'reason to report this probe as passing',
    })
    .toBeGreaterThan(1)
  return header.locator('option').evaluateAll(
    els => els.map(e => (e as HTMLOptionElement).value).filter(Boolean),
  )
}

/** The detail column, which is what the user means by "the displayed content". */
const detailText = async (page: Page) => {
  const body = await page.locator('body').innerText()
  return body.replace(/\s+/g, ' ').trim()
}

test.describe('BUG-011: picking a run changes the page, live', () => {
  test('two different runs produce two different detail panels', async ({ page, request }) => {
    test.setTimeout(3 * 60 * 1000)
    await signIn(page, request)
    await page.setViewportSize({ width: 1600, height: 1000 })
    await page.goto(`${BASE}/agents`, { waitUntil: 'domcontentloaded' })

    const runIds = await runOptions(page)
    const header = page.locator('#agent-field-0')

    const seen: string[] = []
    const placeholders: boolean[] = []
    for (const id of runIds.slice(0, 3)) {
      await header.selectOption(id)
      // The panel fills from two chained fetches (pipelines, then stages).
      await page.waitForTimeout(3500)
      const text = await detailText(page)
      seen.push(text)
      placeholders.push(PLACEHOLDER.test(text))
    }

    // eslint-disable-next-line no-console
    console.log(`LIVE_BUG011 runs=${runIds.length} placeholders=${JSON.stringify(placeholders)} ` +
      `lens=${JSON.stringify(seen.map(s => s.length))}`)

    expect(
      placeholders.every(Boolean),
      'every run selected left the detail panel on "Select a pipeline run…" — ' +
        'this is BUG-011 exactly: the run changes and the content does not',
    ).toBeFalsy()

    expect(
      new Set(seen).size,
      'selecting different runs produced identical page content — the dropdown ' +
        'is not driving the detail panel',
    ).toBeGreaterThan(1)
  })

  test('a run opens its pipeline without a second click', async ({ page, request }) => {
    test.setTimeout(3 * 60 * 1000)
    await signIn(page, request)
    await page.setViewportSize({ width: 1600, height: 1000 })
    await page.goto(`${BASE}/agents`, { waitUntil: 'domcontentloaded' })

    const runIds = await runOptions(page)
    const header = page.locator('#agent-field-0')

    // Find a run that actually has a pipeline; a run with none correctly keeps
    // the placeholder, and skipping that case is the point of the loop.
    let opened = false
    for (const id of runIds.slice(0, 4)) {
      await header.selectOption(id)
      await page.waitForTimeout(3500)
      const text = await detailText(page)
      if (!PLACEHOLDER.test(text) && !/No agent pipelines yet/i.test(text)) {
        opened = true
        break
      }
    }

    expect(
      opened,
      'no run opened its pipeline on selection alone — the user still has to ' +
        'click a card after choosing a run, which is the reported complaint',
    ).toBeTruthy()
  })

  test('the manual-trigger dropdown still says it does not change the view', async ({ page, request }) => {
    // The mitigation shipped in 3909db8e. It is still correct and still needed:
    // a second dropdown listing the same runs sits under the "Pipeline Runs"
    // heading and only arms the trigger button, so it must say so.
    test.setTimeout(3 * 60 * 1000)
    await signIn(page, request)
    await page.setViewportSize({ width: 1600, height: 1000 })
    await page.goto(`${BASE}/agents`, { waitUntil: 'domcontentloaded' })

    // No skip here. The control is gated on QA_ENGINEER and this probe always
    // signs in as ADMIN, so "not present" means the control moved or its gate
    // changed — findings, not reasons to pass quietly. (An earlier version used
    // `isVisible({timeout})`, which does NOT wait, and skipped on a page where
    // the control was in fact present.)
    const trigger = page.locator('select[aria-label*="trigger" i]').first()
    await expect(
      trigger,
      'the manual-trigger dropdown is gone from /agents for an ADMIN — if it ' +
        'was removed on purpose, drop this test with it',
    ).toBeVisible({ timeout: 30_000 })

    const body = await page.locator('body').innerText()
    expect(body, 'the trigger dropdown lost its visible label').toMatch(/trigger a pipeline manually/i)
    expect(body, 'the "does not change the view" qualifier is gone').toMatch(/does not change the view below/i)
  })
})
