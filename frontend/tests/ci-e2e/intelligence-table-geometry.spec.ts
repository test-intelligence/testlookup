/**
 * The "Recent runs analyzed" table on `/intelligence` must show its values.
 *
 * Regression for TL-2026-09-18-01-007 / BUG-005 (user-reported 2026-09-18 with
 * a screenshot: the Build column occupies a large empty band while the Pass
 * rate percentage is cut mid-glyph — `0'` and `1(` where `0%` and `100%`
 * belong).
 *
 * `textContent` cannot see this defect: every value is present in the DOM and
 * only its *box* is wrong, so a rendering assertion passes while the user sees
 * a truncated number. These assertions read **computed geometry**, the same way
 * the 2026-08-29 dashboard-grid regression did.
 *
 * Two viewports, deliberately. The column budget that produced the defect was
 * "measured live at a 1345px viewport" — a single measurement is exactly how a
 * layout ends up correct at one width and broken at every other.
 */
import { expect, test, type Page, type Route } from '@playwright/test'

const PROJECT_ID = '00000000-0000-4000-8000-0000000000aa'
const project = { id: PROJECT_ID, name: 'Checkout Service', slug: 'checkout-service', is_active: true }

const user = {
  id: '00000000-0000-4000-8000-000000000001',
  email: 'qa@example.test',
  username: 'qa_lead',
  full_name: 'QA Lead',
  role: 'QA_LEAD',
  is_active: true,
  must_change_password: false,
  avatar_color: null,
}

/** Pass rates chosen for width: 100% is the widest label, 0% the narrowest. */
const runs = [100, 0, 57].map((pct, i) => ({
  id: `0000000${i}-0000-4000-8000-00000000000${i}`,
  project_id: PROJECT_ID,
  project_name: project.name,
  build_number: `${900 + i}`,
  run_seq: 900 + i,
  jenkins_job: 'nightly-regression',
  status: pct === 100 ? 'PASSED' : 'FAILED',
  total_tests: 100,
  passed_tests: pct,
  failed_tests: 100 - pct,
  skipped_tests: 0,
  broken_tests: 0,
  unknown_tests: 0,
  pass_rate: pct,
  duration_ms: 123456,
  primary_suite_name: 'CheckoutSuite',
  suite_names: ['CheckoutSuite'],
  branch: 'main',
  release_name: 'Unreleased',
  trigger_source: 'api',
  start_time: '2026-09-18T10:00:00Z',
  end_time: '2026-09-18T10:02:03Z',
  created_at: '2026-09-18T10:00:00Z',
  updated_at: '2026-09-18T10:02:03Z',
}))

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installApi(page: Page) {
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/auth/me') return json(route, user)
    if (path === '/api/v1/sso/status') {
      return json(route, { sso_enabled: false, has_active_config: false, enforcement_mode: 'OPTIONAL' })
    }
    if (path === '/api/v1/projects') return json(route, [project])
    if (path === '/api/v1/runs') {
      return json(route, { items: runs, total: runs.length, page: 1, size: 50, pages: 1 })
    }
    if (path === '/api/v1/saved-views') return json(route, [])
    if (path === '/api/v1/notifications/history') return json(route, [])
    if (path === '/api/v1/notifications/history/unread-count') return json(route, { unread: 0 })
    if (path === '/api/v1/analytics/trends') return json(route, [])
    if (path === '/api/v1/analytics/failure-categories') return json(route, [])
    if (path.startsWith('/api/v1/releases')) return json(route, [])
    if (path.startsWith('/api/v1/suites')) return json(route, { items: [], total: 0, page: 1, size: 100, pages: 0 })
    return json(route, {})
  })
}

async function seedAuth(page: Page) {
  await page.addInitScript(
    ([seedUser, pid, pname]) => {
      localStorage.setItem('auth-storage', JSON.stringify({
        state: { token: 'access', refreshToken: 'refresh', user: seedUser, isAuthenticated: true },
        version: 0,
      }))
      localStorage.setItem('testlookup-active-project', JSON.stringify({
        state: { activeProjectId: pid, activeProject: { id: pid, name: pname } },
        version: 0,
      }))
    },
    [user, PROJECT_ID, project.name] as const,
  )
}

type Geometry = {
  table: number
  container: number
  columns: number[]
  passRateClient: number
  passRateScroll: number
}

/** Read the header widths and the pass-rate cell's clipping, from the browser. */
async function measure(page: Page): Promise<Geometry> {
  return page.evaluate(() => {
    const headers = [...document.querySelectorAll('th')]
    const buildTh = headers.find((th) => (th.textContent || '').trim().startsWith('Build'))
    if (!buildTh) throw new Error('Recent runs analyzed table not found')
    const row = buildTh.parentElement as HTMLTableRowElement
    const table = buildTh.closest('table') as HTMLTableElement

    // The pass-rate cell in the first body row.
    const passIndex = [...row.children].findIndex((th) =>
      (th.textContent || '').toLowerCase().includes('pass rate'))
    const firstBodyRow = table.querySelector('tbody tr') as HTMLTableRowElement
    const passCell = firstBodyRow.children[passIndex] as HTMLTableCellElement
    const meter = passCell.firstElementChild as HTMLElement

    const container = table.parentElement as HTMLElement
    return {
      table: table.getBoundingClientRect().width,
      container: container.getBoundingClientRect().width,
      columns: [...row.children].map((th) => th.getBoundingClientRect().width),
      // clientWidth is the cell's visible content box; the meter's scrollWidth
      // is what it actually needs. The cell sets `overflow-hidden`, so an
      // excess here is a value the user cannot read.
      passRateClient: passCell.clientWidth,
      passRateScroll: meter ? meter.scrollWidth + 28 : 0, // + px-3.5 both sides
    }
  })
}

const VIEWPORTS = [
  { name: '1345px (the width the current budget was measured at)', width: 1345, height: 900 },
  { name: '1920px (the width it was reported broken at)', width: 1920, height: 1080 },
]

test.describe('Recent runs analyzed — column geometry', () => {
  for (const vp of VIEWPORTS) {
    test(`shows the pass rate without clipping at ${vp.name}`, async ({ page }) => {
      await installApi(page)
      await seedAuth(page)
      await page.setViewportSize({ width: vp.width, height: vp.height })
      await page.goto('/intelligence')
      await expect(page.locator('table tbody tr').first()).toBeVisible({ timeout: 20_000 })

      const geo = await measure(page)
      // eslint-disable-next-line no-console
      console.log(`GEO ${vp.width} table=${Math.round(geo.table)} container=${Math.round(geo.container)} cols=${geo.columns.map(Math.round).join(',')} passClient=${geo.passRateClient} passNeeds=${Math.round(geo.passRateScroll)}`)

      expect(
        geo.passRateScroll,
        'the pass-rate cell is narrower than the meter plus its percentage, so ' +
          'the number is cut mid-glyph behind overflow-hidden',
      ).toBeLessThanOrEqual(geo.passRateClient + 28)
    })
  }

  for (const vp of VIEWPORTS) {
    test(`the table fills its panel without overflowing it at ${vp.name}`, async ({ page }) => {
      // Both directions are real defects and both were shipped during this fix.
      //
      // OVERFLOWING gives a horizontal scrollbar — the state the original
      // column budget was written to escape.
      //
      // FALLING SHORT leaves an empty band at the right-hand edge. The first
      // version of this fix capped the table at 860px to stop Build growing,
      // and the user reported the gap that produced: the Build band had simply
      // moved to the other side of the row. Nothing here caught it, because
      // every assertion was about the COLUMNS and none about the table against
      // the space it was given.
      await installApi(page)
      await seedAuth(page)
      await page.setViewportSize({ width: vp.width, height: vp.height })
      await page.goto('/intelligence')
      await expect(page.locator('table tbody tr').first()).toBeVisible({ timeout: 20_000 })

      const geo = await measure(page)
      const ratio = geo.table / geo.container
      // eslint-disable-next-line no-console
      console.log(`GEO fill at ${vp.width} = ${(ratio * 100).toFixed(1)}% (table ${Math.round(geo.table)} / container ${Math.round(geo.container)})`)

      expect(ratio, 'the table overflows its panel — horizontal scrollbar').toBeLessThanOrEqual(1.01)
      expect(ratio, 'the table falls short of its panel — empty band at the right edge').toBeGreaterThan(0.97)
    })
  }

  test('Build does not absorb the whole row at a wide viewport', async ({ page }) => {
    await installApi(page)
    await seedAuth(page)
    await page.setViewportSize({ width: 1920, height: 1080 })
    await page.goto('/intelligence')
    await expect(page.locator('table tbody tr').first()).toBeVisible({ timeout: 20_000 })

    const geo = await measure(page)
    const buildShare = geo.columns[0] / geo.table
    // eslint-disable-next-line no-console
    console.log(`GEO build share at 1920 = ${(buildShare * 100).toFixed(1)}%`)

    // Measured: 53.8% before the fix, 44.7% after. The first version of this
    // assertion used 0.55 and therefore PASSED on the defect it was written
    // for — a threshold has to sit between the two measurements, not merely
    // above the good one.
    expect(
      buildShare,
      'Build is the only flexible column, so every pixel past the width the ' +
        'budget was measured at lands in it and the row reads as one label ' +
        'beside a large empty band',
    ).toBeLessThan(0.5)
  })
})
