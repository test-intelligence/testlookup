/**
 * The DEV-only chart gallery (`/__charts`) really draws its charts.
 *
 * This is the functional half of the visual-regression harness: the pixel
 * baselines live in `tests/visual/`, this runs in CI today and asks the
 * questions a screenshot cannot answer on its own —
 *
 *   - the route is reachable WITHOUT a backend (it must not bounce to /login);
 *   - every expected item is on the page, and the count is asserted, so a
 *     gallery that silently renders nothing FAILS rather than passing empty;
 *   - each non-empty chart has real GEOMETRY — an svg of a credible size with
 *     drawn marks — not just text that says a chart is there;
 *   - the page loads with no console.error / pageerror, no serious or critical
 *     axe violation, and no horizontal scroll at phone width.
 *
 * Nothing is mocked: the gallery fetches nothing.
 */
import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import {
  GALLERY_CANVAS,
  GALLERY_DRAWN_ITEMS,
  GALLERY_ITEM_IDS,
} from '../../src/pages/dev/chartGalleryFixtures'

const GALLERY = '/__charts'
const CHART_SVG = '.recharts-wrapper > svg.recharts-surface'

/** Console errors and uncaught exceptions, collected from before navigation. */
function watchErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console.error: ${message.text()}`)
  })
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))
  return errors
}

async function openGallery(page: Page, search = '') {
  await page.goto(`${GALLERY}${search}`)
  // Fail, never skip: a 404 or an auth bounce lands on /overview → /login.
  expect(new URL(page.url()).pathname, 'the gallery route redirected').toBe(GALLERY)
  await expect(page.getByTestId('chart-gallery')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1, name: 'Chart gallery (dev only)' })).toBeVisible()
  // The login page is a form; the gallery has none.
  await expect(page.locator('form')).toHaveCount(0)
}

/** Drawn marks inside an svg: path/rect/circle with a real box, outside <defs>. */
async function drawnMarks(page: Page, itemId: string): Promise<number> {
  return page.locator(`[data-gallery-item="${itemId}"] ${CHART_SVG}`).first().evaluate((svg) => {
    const marks = Array.from(svg.querySelectorAll('path, rect, circle'))
    return marks.filter((mark) => {
      if (mark.closest('defs')) return false
      const box = mark.getBoundingClientRect()
      return box.width > 0 && box.height > 0
    }).length
  })
}

test.describe('chart gallery (/__charts)', () => {
  test('is reachable without a session, renders every item once, and draws each chart', async ({
    page,
  }) => {
    const errors = watchErrors(page)
    await openGallery(page)

    const sections = page.locator('[data-gallery-item]')
    const ids = await sections.evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute('data-gallery-item')),
    )
    // The count is the point: an empty gallery must fail, not vacuously pass.
    expect(GALLERY_ITEM_IDS.length).toBeGreaterThanOrEqual(8)
    expect(ids).toEqual(GALLERY_ITEM_IDS)
    await expect(sections).toHaveCount(GALLERY_ITEM_IDS.length)

    for (const item of GALLERY_DRAWN_ITEMS) {
      const section = page.locator(`[data-gallery-item="${item.id}"]`)
      await expect(section.getByRole('heading', { level: 2, name: item.title })).toBeVisible()

      const svg = section.locator(CHART_SVG).first()
      await expect(svg, `${item.id}: no chart svg`).toBeVisible()
      const box = await svg.boundingBox()
      expect(box, `${item.id}: svg has no box`).not.toBeNull()
      expect(box?.width ?? 0, `${item.id}: svg too narrow`).toBeGreaterThanOrEqual(300)
      expect(box?.height ?? 0, `${item.id}: svg too short`).toBeGreaterThanOrEqual(150)
      // Inside its canvas, never spilling out of it.
      expect(box?.width ?? Infinity).toBeLessThanOrEqual(GALLERY_CANVAS.width + 1)
      expect(box?.height ?? Infinity).toBeLessThanOrEqual(GALLERY_CANVAS.height + 1)

      // Recharts draws asynchronously after ResponsiveContainer measures; poll
      // for the marks rather than asserting a single early snapshot of the DOM.
      await expect
        .poll(() => drawnMarks(page, item.id), {
          message: `${item.id}: expected at least ${item.minMarks} drawn marks`,
        })
        .toBeGreaterThanOrEqual(Math.max(1, item.minMarks))
    }

    expect(errors).toEqual([])
  })

  test('renders the requested theme and shrugs off a bogus one', async ({ page }) => {
    const errors = watchErrors(page)
    await openGallery(page, '?theme=lab')
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'lab')
    await expect(page.getByTestId('chart-gallery')).toHaveAttribute('data-gallery-theme', 'lab')

    await openGallery(page, '?theme=%3Cscript%3E')
    await expect(page.getByTestId('chart-gallery')).toHaveAttribute('data-gallery-theme', 'default')
    await expect(page.locator('html')).not.toHaveAttribute('data-theme', '<script>')
    expect(errors).toEqual([])
  })

  /**
   * Pre-existing, and outside this harness's remit to fix: DefectDonut's
   * empty-state text is `--color-text-muted` (#767d89), which on the `lab`
   * theme's white card measures 4.14:1 — under AA's 4.5:1. The fix belongs in
   * the component (a secondary-text token) or in the lab palette, not here.
   *
   * Allowlisted by rule AND node, so nothing else can shelter behind it, and
   * ratcheted: the moment the contrast is fixed this entry stops matching and
   * the test fails until the entry is deleted. A green run therefore means
   * "no NEW violation", never "none".
   */
  const KNOWN_VIOLATIONS: { theme: string; rule: string; nodeHtmlIncludes: string }[] = [
    { theme: 'lab', rule: 'color-contrast', nodeHtmlIncludes: 'No defect data' },
  ]

  for (const theme of ['signal', 'lab']) {
    test(`has no serious or critical automated accessibility violations (${theme})`, async ({
      page,
    }) => {
      await openGallery(page, `?theme=${theme}`)
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      await expect(page.locator(CHART_SVG).first()).toBeVisible()

      const result = await new AxeBuilder({ page }).analyze()
      const known = KNOWN_VIOLATIONS.filter((entry) => entry.theme === theme)
      const isKnown = (rule: string, html: string) =>
        known.some((entry) => entry.rule === rule && html.includes(entry.nodeHtmlIncludes))

      const blocking = result.violations
        .filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
        .map((violation) => ({
          ...violation,
          nodes: violation.nodes.filter((node) => !isKnown(violation.id, node.html)),
        }))
        .filter((violation) => violation.nodes.length > 0)
      expect(blocking).toEqual([])

      // The ratchet: an allowlisted violation that no longer occurs is a fix,
      // and the allowlist must shrink with it.
      for (const entry of known) {
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
    })
  }

  test('does not scroll sideways at phone width', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await openGallery(page)
    await expect(page.locator(CHART_SVG).first()).toBeVisible()

    const overflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      bodyScrollWidth: document.body.scrollWidth,
    }))
    expect(overflow.scrollWidth, JSON.stringify(overflow)).toBeLessThanOrEqual(overflow.clientWidth)
    expect(overflow.bodyScrollWidth).toBeLessThanOrEqual(overflow.clientWidth)
    // …because each fixed-size canvas scrolls inside its own keyboard-reachable box.
    const scroller = page.locator('[data-gallery-item="trend-line"] [role="group"]')
    await expect(scroller).toHaveAttribute('tabindex', '0')
    expect(await scroller.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true)
  })
})
