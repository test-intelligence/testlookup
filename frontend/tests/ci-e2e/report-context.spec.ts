/**
 * The DEV-only report-chrome gallery (`/__report-context`, VIZ-301..305) in a
 * real browser: what jsdom cannot see.
 *
 *   - the route is reachable WITHOUT a backend and FAILS (never skips) when it
 *     is missing — a 404 or an auth bounce lands somewhere else;
 *   - axe finds no serious/critical violation in all six themes, with the
 *     suites popover closed and open (allow-list + ratchet exactly as
 *     chart-gallery.spec.ts does it);
 *   - the context header's values render at >= --text-xl and its labels at
 *     >= --text-sm by COMPUTED STYLE and BOUNDING BOX, not by class name or
 *     textContent (a class can say one thing and the cascade another);
 *   - a chip removed from the keyboard hands focus to the next chip, then to
 *     "Clear all", then to the filter bar; remove targets are >= 24x24;
 *   - the "+4" suites popover is a portal: a click inside keeps it open, a
 *     click outside closes it, Escape returns focus to its trigger.
 *
 * The gallery fetches nothing. The second describe ("real report routes")
 * mounts the chrome where production mounts it — the layout, beside the
 * sidebar — with the backend stubbed at the network edge like the other
 * ci-e2e specs (seeded auth + project, `page.route` for /api/v1) and the two
 * flags stubbed ON: the request it makes, the window it sends, that a failed
 * request never toasts, the strip's geometry at 1280 px, and that the page
 * load is not announced.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, test, type Locator, type Page, type Route } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const ROUTE = '/__report-context'
/** Every theme in `src/store/themeStore.ts`. */
const ALL_THEMES = ['signal', 'console', 'slate', 'ember', 'lab', 'midnight'] as const
const CASE_COUNT = 11

function watchErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console.error: ${message.text()}`)
  })
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))
  return errors
}

async function openGallery(page: Page, search = '') {
  await page.goto(`${ROUTE}${search}`)
  // Fail, never skip: a missing route redirects (→ /overview → /login).
  expect(new URL(page.url()).pathname, 'the report-context route redirected').toBe(ROUTE)
  await expect(page.getByTestId('report-context-gallery')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1, name: 'Report context (dev only)' })).toBeVisible()
  await expect(page.locator('[data-report-case]')).toHaveCount(CASE_COUNT)
}

const caseOf = (page: Page, id: string) => page.locator(`[data-report-case="${id}"]`)

async function boxOf(locator: Locator) {
  const box = await locator.boundingBox()
  expect(box, 'element has no box').not.toBeNull()
  return box ?? { x: 0, y: 0, width: 0, height: 0 }
}

/** A CSS length token on :root, in px. */
async function tokenPx(page: Page, name: string): Promise<number> {
  const px = await page.evaluate((token) => {
    const probe = document.createElement('div')
    probe.style.fontSize = `var(${token})`
    document.body.appendChild(probe)
    const size = parseFloat(getComputedStyle(probe).fontSize)
    probe.remove()
    return size
  }, name)
  expect(px, `${name} did not resolve`).toBeGreaterThan(0)
  return px
}

test.describe('Report context gallery (/__report-context)', () => {
  test('is reachable without a session and renders every case with no errors', async ({ page }) => {
    const errors = watchErrors(page)
    await openGallery(page)
    for (const section of await page.locator('[data-report-case]').all()) {
      await expect(section.locator('[data-report-chrome]')).toHaveCount(1)
    }
    expect(errors).toEqual([])
  })

  /**
   * Pre-existing violations this spec may not fail on, by theme, rule AND
   * node, exactly as chart-gallery.spec.ts does it: nothing else can shelter
   * behind an entry, and the ratchet below fails the moment an entry stops
   * firing, so the list only ever shrinks. "Green" means "no NEW violation".
   *
   * Empty: the two MetricCard contrast entries this list once held (title in
   * `--color-text-muted`, trend line in the status hue, `lab` theme) were
   * fixed in MetricCard itself, and the MultiSelect list's
   * scrollable-region-focusable (fix round B) in MultiSelect. Keep it empty.
   */
  const KNOWN_VIOLATIONS: { theme: string; rule: string; nodeHtmlIncludes: string }[] = [
  ]

  async function expectNoBlockingViolations(
    page: Page,
    theme: string,
    known: { theme: string; rule: string; nodeHtmlIncludes: string }[],
    what: string,
    /**
     * Hold every allowlisted entry to still firing. Only on the undisturbed
     * page: an open popover covers nodes, and axe reports a covered node as
     * "incomplete", not a violation — so the ratchet would misfire there.
     */
    ratchet: boolean,
  ) {
    const result = await new AxeBuilder({ page }).analyze()
    const isKnown = (rule: string, html: string) =>
      known.some((entry) => entry.rule === rule && html.includes(entry.nodeHtmlIncludes))

    const blocking = result.violations
      .filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
      .map((violation) => ({
        id: violation.id,
        nodes: violation.nodes.filter((node) => !isKnown(violation.id, node.html)).map((node) => node.html),
      }))
      .filter((violation) => violation.nodes.length > 0)
    expect(blocking, `axe (${theme}, ${what})`).toEqual([])

    for (const entry of ratchet ? known : []) {
      const stillPresent = result.violations.some(
        (violation) =>
          violation.id === entry.rule &&
          violation.nodes.some((node) => node.html.includes(entry.nodeHtmlIncludes)),
      )
      expect(
        stillPresent,
        `${entry.rule} on "${entry.nodeHtmlIncludes}" (${theme}) no longer fires — remove it from KNOWN_VIOLATIONS`,
      ).toBe(true)
    }
  }

  for (const theme of ALL_THEMES) {
    test(`has no serious or critical automated accessibility violations (${theme})`, async ({ page }) => {
      await openGallery(page, `?theme=${theme}`)
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      const known = KNOWN_VIOLATIONS.filter((entry) => entry.theme === theme)
      await expectNoBlockingViolations(page, theme, known, 'popover closed', true)

      await caseOf(page, 'many-suites').getByRole('button', { name: '+4 more: show all 7 suites' }).click()
      await expect(page.getByRole('dialog', { name: 'All suites (7)' })).toBeVisible()
      await expectNoBlockingViolations(page, theme, known, 'suites popover open', false)
      await page.keyboard.press('Escape')

      // Fix round B (m6): the MultiSelect popover open, low on the page, so
      // its capped height squeezes the list — axe scrollable-region-focusable
      // fired on the list here. Every impact counts for that rule.
      const release = caseOf(page, 'filtered').locator('[data-report-filter="release"]')
      await release.scrollIntoViewIfNeeded()
      await release.click()
      await expect(page.getByRole('dialog', { name: 'Release options' })).toBeVisible()
      await expectNoBlockingViolations(page, theme, known, 'release multiselect open', false)
      const popoverScan = await new AxeBuilder({ page }).include('[data-multiselect-popover]').analyze()
      expect(popoverScan.violations.map((v) => v.id)).not.toContain('scrollable-region-focusable')
    })
  }

  test('header values render at >= --text-xl and labels at >= --text-sm (computed style + box)', async ({ page }) => {
    await openGallery(page)
    const xl = await tokenPx(page, '--text-xl')
    const sm = await tokenPx(page, '--text-sm')
    const statLg = await tokenPx(page, '--text-stat-lg')

    const measure = (selector: string) =>
      page.locator(selector).evaluateAll((nodes) =>
        nodes
          .filter((node) => (node as HTMLElement).getClientRects().length > 0)
          .map((node) => {
            const style = getComputedStyle(node)
            const rect = node.getBoundingClientRect()
            return {
              text: (node.textContent ?? '').slice(0, 40),
              fontSize: parseFloat(style.fontSize),
              lineHeight: parseFloat(style.lineHeight) || parseFloat(style.fontSize),
              height: rect.height,
              width: rect.width,
            }
          }),
      )

    const values = await measure('[data-report-context-header] [data-context-value]')
    expect(values.length, 'no header values measured').toBeGreaterThan(40)
    for (const v of values) {
      expect(v.fontSize, `value "${v.text}" font-size`).toBeGreaterThanOrEqual(xl)
      // The glyph box is really that big: rendered height >= the font size.
      expect(v.height, `value "${v.text}" rendered height`).toBeGreaterThanOrEqual(xl * 0.95)
      expect(v.width, `value "${v.text}" has no width`).toBeGreaterThan(0)
    }

    const labels = await measure('[data-report-context-header] dt')
    expect(labels.length, 'no header labels measured').toBeGreaterThan(40)
    for (const l of labels) {
      expect(l.fontSize, `label "${l.text}" font-size`).toBeGreaterThanOrEqual(sm)
      expect(l.height, `label "${l.text}" rendered height`).toBeGreaterThanOrEqual(sm * 0.95)
    }

    const stats = await measure('[data-metrics-strip] [data-metric] p.text-3xl')
    expect(stats.length, 'no strip values measured').toBeGreaterThan(40)
    for (const s of stats) expect(s.fontSize, `metric "${s.text}"`).toBeGreaterThanOrEqual(statLg)
  })

  test('removing chips from the keyboard: next chip, then "Clear all", then the filter bar', async ({ page }) => {
    await openGallery(page)
    const section = caseOf(page, 'filtered')
    const remove = (name: string) => section.getByRole('button', { name })

    for (const name of [
      'Remove filter Release 2026.09',
      'Remove filter Release 2026.08',
      'Remove filter Suite payments',
      'Remove filter Suite cart',
    ]) {
      const box = await boxOf(remove(name))
      expect(box.width, `${name}: width`).toBeGreaterThanOrEqual(24)
      expect(box.height, `${name}: height`).toBeGreaterThanOrEqual(24)
    }

    await remove('Remove filter Release 2026.08').focus()
    await page.keyboard.press('Enter')
    await expect(remove('Remove filter Release 2026.08')).toHaveCount(0)
    await expect(remove('Remove filter Suite payments')).toBeFocused()

    await page.keyboard.press('Space')
    await expect(remove('Remove filter Suite cart')).toBeFocused()

    await remove('Remove filter Release 2026.09').focus()
    await page.keyboard.press('Enter')
    await expect(remove('Remove filter Suite cart')).toBeFocused()

    // The last chip: nothing after it, the 14-day window keeps "Clear all".
    await page.keyboard.press('Enter')
    await expect(section.getByRole('button', { name: 'Clear all' })).toBeFocused()
    await expect(section.getByText('No filters applied — showing all releases and suites')).toBeVisible()

    await page.keyboard.press('Enter')
    await expect(section.getByRole('button', { name: 'Clear all' })).toHaveCount(0)
    await expect(section.getByRole('group', { name: 'Report filters' })).toBeFocused()
    // The summary moved and was announced once, through the page announcer.
    await expect(page.locator('[data-filtered-summary-live]')).toHaveCount(0)
  })

  test('more than eight chips collapse into "+N more" and expand', async ({ page }) => {
    await openGallery(page)
    const section = caseOf(page, 'many-chips')
    const list = section.getByRole('list', { name: 'Active filters' })
    await expect(list.getByRole('listitem')).toHaveCount(8)
    await section.getByRole('button', { name: '+3 more', exact: true }).focus()
    await page.keyboard.press('Enter')
    await expect(list.getByRole('listitem')).toHaveCount(11)
    // Focus goes to the first chip it revealed (the 9th), not left on the toggle.
    const ninth = list.getByRole('listitem').nth(8).getByRole('button')
    await expect(ninth).toBeFocused()
  })

  test('dismissing the dropped-values notice hands focus to the Release trigger, never <body>', async ({ page }) => {
    await openGallery(page)
    const section = caseOf(page, 'filtered')
    const live = section.locator('[data-dropped-notice-live]')
    await expect(live).toHaveAttribute('role', 'status')
    await expect(live).not.toHaveText('')
    await section.getByRole('button', { name: 'Dismiss notice' }).focus()
    await page.keyboard.press('Enter')
    await expect(section.locator('[data-dropped-notice]')).toHaveCount(0)
    await expect(section.locator('[data-report-filter="release"]')).toBeFocused()
    // The region stays, now empty, ready for the next notice.
    await expect(live).toHaveText('')
  })

  test('"Skip to report content" is the first stop, visible on focus, and jumps past the chrome', async ({ page }) => {
    await openGallery(page)
    const section = caseOf(page, 'unfiltered')
    await expect(section.getByRole('heading', { level: 2, name: 'Report context' })).toBeAttached()
    const skip = section.getByRole('link', { name: 'Skip to report content' })
    await skip.focus()
    const box = await boxOf(skip)
    expect(box.width, 'the skip link is visible when focused').toBeGreaterThan(40)
    await page.keyboard.press('Enter')
    await expect(section.locator('[data-report-chrome-end]')).toBeFocused()
  })

  test('the suites popover is a portal: inside click keeps it, outside click closes it, Escape returns focus', async ({
    page,
  }) => {
    await openGallery(page)
    const section = caseOf(page, 'many-suites')
    const trigger = section.getByRole('button', { name: '+4 more: show all 7 suites' })
    await trigger.click()
    const dialog = page.getByRole('dialog', { name: 'All suites (7)' })
    await expect(dialog).toBeVisible()
    // Portalled out of the header.
    expect(await section.locator('[role="dialog"]').count()).toBe(0)
    await expect(dialog.getByRole('listitem')).toHaveText([
      'payments',
      'cart',
      'search',
      'checkout-api',
      'inventory',
      'auth',
      'notifications',
    ])

    await dialog.getByText('inventory').click()
    await expect(dialog).toBeVisible()

    await page.getByRole('heading', { level: 1 }).click()
    await expect(dialog).toBeHidden()
    await expect(trigger).toHaveAttribute('aria-expanded', 'false')

    await trigger.click()
    await expect(dialog).toBeVisible()
    await expect(page.locator('[data-context-popover]')).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
    await expect(trigger).toBeFocused()
  })

  test('All projects: the release control is disabled with a visible reason', async ({ page }) => {
    await openGallery(page)
    const section = caseOf(page, 'all-projects')
    await expect(section.locator('[data-context-entry="project"] dd')).toHaveText('All projects (12)')
    const trigger = section.getByTestId('report-filter-release').getByRole('button').first()
    await expect(trigger).toHaveAttribute('aria-disabled', 'true')
    await expect(section.getByText('Pick one project to filter by release', { exact: false })).toBeVisible()
  })

  test('no horizontal scroll at 375 px', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await openGallery(page)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(overflow).toBeLessThanOrEqual(0)
  })
})

// ── The chrome on REAL report routes (layout mount, flags stubbed) ─────────

const fixturePayload = (relative: string) =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`../../../contracts/viz/fixtures/${relative}`, import.meta.url)), 'utf8'))
    .payload as Record<string, unknown>
const ENVELOPE = fixturePayload('envelope/valid/filtered.json') as { scope: Record<string, unknown> } & Record<string, unknown>
const BLOCK = fixturePayload('report_metrics/valid/comparable.json')

const PROJECT = '11111111-1111-4111-8111-111111111111'
const R1 = '22222222-2222-4222-8222-222222222221'
const USER = {
  id: '00000000-0000-4000-8000-000000000001',
  email: 'lead@example.test',
  username: 'qa_lead',
  full_name: 'QA Lead',
  role: 'QA_LEAD',
  is_active: true,
  must_change_password: false,
  avatar_color: null,
}
const PROJECTS = [
  { id: PROJECT, name: 'Checkout', slug: 'checkout', description: '', is_active: true, created_at: '2026-09-16T03:00:00Z' },
]
const RELEASES = [{ id: R1, project_id: PROJECT, name: '2026.09', version: null, description: null, status: 'active' }]
/** A detail no real error message has: if it is ever on screen, a toast showed it. */
const TOAST_SENTINEL = 'chrome-422-sentinel'

interface RealOptions {
  multiFilters?: boolean
  reportContext?: boolean
  /** The stored global window (`testlookup-time-window`). */
  storedDays?: number
  /** Answer the chrome's summary request (the one with include=report_metrics) with this status. */
  chromeStatus?: number
}

/** Every `/metrics/summary` request, as sent. */
interface SummaryCall {
  days: string | null
  include: string | null
}

async function openReportRoute(page: Page, path: string, options: RealOptions = {}): Promise<SummaryCall[]> {
  const { multiFilters = true, reportContext = true, storedDays = 30, chromeStatus = 200 } = options
  const calls: SummaryCall[] = []
  await page.addInitScript(
    ({ user, projectId, days }) => {
      localStorage.setItem(
        'auth-storage',
        JSON.stringify({ state: { token: 'access', refreshToken: 'refresh', user, isAuthenticated: true }, version: 0 }),
      )
      localStorage.setItem('testlookup-active-project', JSON.stringify({ state: { activeProjectId: projectId }, version: 0 }))
      localStorage.setItem('testlookup-time-window', JSON.stringify({ state: { days }, version: 3 }))
    },
    { user: USER, projectId: PROJECT, days: storedDays },
  )
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (path === '/api/v1/auth/me') return json(route, USER)
    if (path === '/api/v1/projects') return json(route, PROJECTS)
    if (path === '/api/v1/feature-flags/viz_multi_filters/status') {
      return json(route, { key: 'viz_multi_filters', enabled: multiFilters })
    }
    if (path === '/api/v1/feature-flags/viz_report_context/status') {
      return json(route, { key: 'viz_report_context', enabled: reportContext })
    }
    if (path.startsWith('/api/v1/feature-flags/')) return json(route, { key: path.split('/')[4], enabled: false })
    if (path === '/api/v1/releases') return json(route, { items: RELEASES, total: 1, page: 1, size: 100 })
    if (path === '/api/v1/runs') return json(route, { items: [], total: 0, page: 1, size: 100, pages: 0 })
    if (path === '/api/v1/metrics/summary') {
      const include = url.searchParams.get('include')
      const days = url.searchParams.get('days')
      calls.push({ days, include })
      if (include === 'report_metrics' && chromeStatus !== 200) {
        return json(route, { detail: TOAST_SENTINEL }, chromeStatus)
      }
      const n = Number(days)
      const to = new Date('2026-09-19T00:00:00Z')
      const from = new Date(to.getTime() - n * 86_400_000).toISOString().slice(0, 10)
      const meta = {
        ...ENVELOPE,
        scope: { ...ENVELOPE.scope, releases: [], suites: [], window: { from, to: '2026-09-19', days: n, timezone: 'UTC' } },
      }
      return json(route, {
        total_executions_7d: { value: 3960 },
        avg_pass_rate_7d: { value: 91.2, basis: 'executions' },
        flaky_test_count: { value: 12 },
        avg_duration_ms: { value: 53_287 },
        meta,
        ...(include === 'report_metrics' ? { report_metrics: BLOCK } : {}),
      })
    }
    if (path.endsWith('/count')) return json(route, { count: 0, unread: 0 })
    if (path.includes('/history') || path.includes('saved-views') || path.includes('/suites')) return json(route, [])
    return json(route, {})
  })
  await page.goto(path)
  return calls
}

const chromeCalls = (calls: SummaryCall[]) => calls.filter((c) => c.include === 'report_metrics')

test.describe('Report chrome on real report routes (flags stubbed)', () => {
  test('both flags on: one chrome request, opting in to report_metrics; either flag off: no chrome (M1)', async ({ page }) => {
    const calls = await openReportRoute(page, '/trends')
    const header = page.locator('[data-report-context-header][data-state="ready"]')
    await expect(header).toBeVisible({ timeout: 20_000 })
    // /trends resets the shared window to 14 on mount; the chrome follows the
    // page there (it may have asked once with the stored 30 before that effect).
    await expect(header.locator('[data-context-entry="window"] dd')).toContainText('Last 14 days')
    const chrome = chromeCalls(calls)
    expect(chrome[chrome.length - 1]?.days).toBe('14')
    for (const call of chromeCalls(calls)) expect(['14', '30']).toContain(call.days)

    for (const flags of [
      { reportContext: true, multiFilters: false },
      { reportContext: false, multiFilters: true },
    ]) {
      const other = await page.context().newPage()
      const otherCalls = await openReportRoute(other, '/trends', flags)
      await expect(other.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 20_000 })
      // Give a (wrongly) flagged chrome time to mount and fetch.
      await other.waitForTimeout(1500)
      await expect(other.locator('[data-report-chrome]'), JSON.stringify(flags)).toHaveCount(0)
      expect(chromeCalls(otherCalls), JSON.stringify(flags)).toEqual([])
      await other.close()
    }
  })

  test('a stored 1-year window is sent as 90 days, and the header says so (M2)', async ({ page }) => {
    const calls = await openReportRoute(page, '/value-metrics', { storedDays: 365 })
    const header = page.locator('[data-report-context-header][data-state="ready"]')
    await expect(header).toBeVisible({ timeout: 20_000 })
    expect(chromeCalls(calls).map((c) => c.days)).not.toContain('365')
    expect(new Set(chromeCalls(calls).map((c) => c.days))).toEqual(new Set(['90']))
    await expect(header.locator('[data-context-entry="window"] dd')).toContainText('Last 90 days (max for summary)')
  })

  test('a stored "All time" (0) is never sent: the chrome uses the page\'s own 24 h (M2)', async ({ page }) => {
    const calls = await openReportRoute(page, '/overview', { storedDays: 0 })
    await expect(page.locator('[data-report-context-header][data-state="ready"]')).toBeVisible({ timeout: 20_000 })
    expect(chromeCalls(calls).map((c) => c.days)).not.toContain('0')
    expect(new Set(chromeCalls(calls).map((c) => c.days))).toEqual(new Set(['1']))
  })

  test('a failed chrome request never toasts: the chrome states the reason inline (M2)', async ({ page }) => {
    await openReportRoute(page, '/trends', { chromeStatus: 422 })
    const header = page.locator('[data-report-context-header][data-state="unavailable"]')
    await expect(header).toBeVisible({ timeout: 20_000 })
    await expect(header).toContainText('the metrics request failed (HTTP 422)')
    await expect(page.locator('[data-metric="runs"]')).toHaveAttribute('data-measured', 'false')
    // A toast would carry the response detail; give one time to appear.
    await page.waitForTimeout(1500)
    expect(await page.evaluate((s) => document.body.innerText.includes(s), TOAST_SENTINEL)).toBe(false)
  })

  test('at 1280 px beside the sidebar every tile is >= 13rem and no value wraps or overflows (a11y M7)', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 })
    await openReportRoute(page, '/trends')
    await expect(page.locator('[data-metrics-strip] [data-metric] p.text-3xl').first()).toBeVisible({ timeout: 20_000 })
    const tiles = await page.locator('[data-metrics-strip] [data-metric]').evaluateAll((nodes) =>
      nodes.map((tile) => {
        const value = tile.querySelector('p.text-3xl') as HTMLElement
        const card = tile.querySelector('.card') as HTMLElement
        const rem = parseFloat(getComputedStyle(document.documentElement).fontSize)
        const style = getComputedStyle(value)
        const valueBox = value.getBoundingClientRect()
        const cardBox = card.getBoundingClientRect()
        return {
          id: tile.getAttribute('data-metric'),
          width: tile.getBoundingClientRect().width,
          minWidth: 13 * rem,
          lines: Math.round(valueBox.height / (parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.2)),
          overflow: value.scrollWidth - value.clientWidth,
          insideCard: valueBox.right <= cardBox.right + 0.5,
        }
      }),
    )
    expect(tiles.length).toBeGreaterThanOrEqual(10)
    for (const t of tiles) {
      expect(t.width, `${t.id} tile width`).toBeGreaterThanOrEqual(t.minWidth - 0.5)
      expect(t.lines, `${t.id} value lines`).toBe(1)
      expect(t.overflow, `${t.id} value overflow`).toBeLessThanOrEqual(0)
      expect(t.insideCard, `${t.id} value inside its card`).toBe(true)
    }
    // Sanity: the sidebar is really there, so this is the squeezed layout.
    const mainLeft = await page.locator('main#main-content').evaluate((m) => m.getBoundingClientRect().left)
    expect(mainLeft).toBeGreaterThan(100)
  })

  test('the page load is not announced; the chrome is headed and skippable; no axe violation (M3, M6)', async ({ page }) => {
    await openReportRoute(page, '/trends')
    await expect(page.locator('[data-report-context-header][data-state="ready"]')).toBeVisible({ timeout: 20_000 })
    // Past the summary's debounce: a first-load "change" would be in a live region by now.
    await page.waitForTimeout(2000)
    const liveTexts = await page
      .locator('[data-report-chrome] [role="status"], [data-report-chrome] [aria-live], [data-chart-announcer]')
      .evaluateAll((nodes) => nodes.map((n) => (n.textContent ?? '').trim()).filter(Boolean))
    expect(liveTexts).toEqual([])

    await expect(page.locator('[data-report-chrome]').getByRole('heading', { level: 2, name: 'Report context' })).toBeAttached()
    const skip = page.getByRole('link', { name: 'Skip to report content' })
    await skip.focus()
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-report-chrome-end]')).toBeFocused()

    const result = await new AxeBuilder({ page }).include('[data-report-chrome]').analyze()
    const blocking = result.violations
      .filter((v) => v.impact === 'serious' || v.impact === 'critical')
      .map((v) => ({ id: v.id, nodes: v.nodes.map((n) => n.target.join(' ')) }))
    expect(blocking).toEqual([])
  })
})
