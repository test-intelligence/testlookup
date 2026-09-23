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
import { expect, test, type Locator, type Page } from '@playwright/test'
// The axe gate (tags, themes, ratcheted allowlist) is shared with the VIZ-405 spec.
import { ALL_THEMES, expectNoBlockingViolations, type KnownViolation } from '../lib/axe-gate'
import { textEscapes } from '../lib/chart-text-escapes'
import {
  GALLERY_CANVAS,
  GALLERY_CHANGE_BARS,
  GALLERY_DRAWN_CANVAS_ITEMS,
  GALLERY_DRAWN_DOM_ITEMS,
  GALLERY_DRAWN_ITEMS,
  GALLERY_DRAWN_SVG_ITEMS,
  GALLERY_HEATMAP_DATA,
  GALLERY_ITEM_IDS,
  GALLERY_ITEMS,
  GALLERY_LONG_NAMES,
  GALLERY_STATUS_MATRIX_DATA,
  GALLERY_SUITE_STATUS,
  GALLERY_TOP_FAILING,
  HOSTILE_LABEL,
  galleryCanvasSize,
  galleryChartHeight,
  GALLERY_FLUID_CANVAS_PARAM,
  GALLERY_GAPPY_SUITES,
  GALLERY_NOT_COMPARABLE_REASON,
  GALLERY_THREE_SUITES,
} from '../../src/pages/dev/chartGalleryFixtures'
// VIZ-404: the model only `import type`s from `@/…`, so it resolves in plain Node too.
import { ALIGNED_X_TITLE, HIDDEN_SUFFIX } from '../../src/components/charts/multiSeriesModel'
// Imported rather than retyped, so a reworded indicator fails here instead of
// quietly passing. `timeSeriesModel` only `import type`s from `@/…`, so it
// resolves in Playwright's plain-Node transform with no alias.
import { AXIS_NOT_ZERO_LABEL, UTC_AXIS_CAPTION } from '../../src/components/charts/timeSeriesModel'
import {
  NOT_MEASURED_REASON,
  REQUEST_IDS,
  STATE_ITEM_IDS,
  STATE_ITEMS,
} from '../../src/pages/dev/chartStatesFixtures'

const GALLERY = '/__charts'
const CHART_SVG = '.recharts-wrapper > svg.recharts-surface'
/** Recharts 3 draws the category (y) axis tick labels in a layer of their own. */
const CATEGORY_TICKS = '.recharts-yAxis-tick-labels'
/** …and the value (x) axis ones in theirs. `.recharts-xAxis` holds none of them. */
const VALUE_TICKS = '.recharts-xAxis-tick-labels'
const ECHARTS_CANVAS = '[data-chart-engine="echarts"] canvas'
/** Any request for ECharts or zrender code — a dev-server dep or a built chunk. */
const ENGINE_REQUEST = /echarts|zrender/i

/**
 * Collect `securitypolicyviolation` events from before any page script runs.
 * The dev server serves the same index.html as production, CSP <meta> and all,
 * so a chart engine that needs `eval`, a `blob:` worker or a remote resource
 * shows up here.
 */
async function watchCsp(page: Page) {
  await page.addInitScript(() => {
    const seen: string[] = []
    ;(window as unknown as { __cspViolations: string[] }).__cspViolations = seen
    document.addEventListener('securitypolicyviolation', (event) => {
      seen.push(`${event.violatedDirective} <- ${event.blockedURI || 'inline'}`)
    })
  })
}

const cspViolations = (page: Page) =>
  page.evaluate(() => (window as unknown as { __cspViolations?: string[] }).__cspViolations ?? null)

/**
 * Distinct opaque colours (sampled) in the most-painted canvas of a chart: a
 * blank chart has 0 or 1. Every canvas is measured, not the first — ECharts
 * adds layer canvases (e.g. when a series uses decals) that are blank.
 */
async function paintedColoursIn(chart: Locator): Promise<number> {
  return chart.evaluate((root) =>
    Math.max(
      0,
      ...Array.from(root.querySelectorAll('canvas'), (canvas) => {
        const ctx = canvas.getContext('2d')
        if (!ctx || canvas.width === 0 || canvas.height === 0) return 0
        const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height)
        const colours = new Set<string>()
        for (let i = 0; i < data.length; i += 4 * 97) {
          if (data[i + 3] > 200) colours.add(`${data[i]},${data[i + 1]},${data[i + 2]}`)
        }
        return colours.size
      }),
    ),
  )
}

const paintedColours = (page: Page, itemId: string) =>
  paintedColoursIn(page.locator(`[data-gallery-item="${itemId}"] [data-chart-engine="echarts"]`).first())

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

    for (const item of GALLERY_DRAWN_SVG_ITEMS) {
      const section = page.locator(`[data-gallery-item="${item.id}"]`)
      await expect(section.getByRole('heading', { level: 2, name: item.title })).toBeVisible()

      const svg = section.locator(CHART_SVG).first()
      await expect(svg, `${item.id}: no chart svg`).toBeVisible()
      const box = await svg.boundingBox()
      expect(box, `${item.id}: svg has no box`).not.toBeNull()
      expect(box?.width ?? 0, `${item.id}: svg too narrow`).toBeGreaterThanOrEqual(300)
      expect(box?.height ?? 0, `${item.id}: svg too short`).toBeGreaterThanOrEqual(150)
      // Inside its canvas, never spilling out of it. A Wave-2 item is a whole
      // ChartFrame, so its box is taller than the plain-plot one.
      const canvas = galleryCanvasSize(item)
      expect(box?.width ?? Infinity).toBeLessThanOrEqual(canvas.width + 1)
      expect(box?.height ?? Infinity).toBeLessThanOrEqual(canvas.height + 1)

      // Recharts draws asynchronously after ResponsiveContainer measures; poll
      // for the marks rather than asserting a single early snapshot of the DOM.
      await expect
        .poll(() => drawnMarks(page, item.id), {
          message: `${item.id}: expected at least ${item.minMarks} drawn marks`,
        })
        .toBeGreaterThanOrEqual(Math.max(1, item.minMarks))
    }

    // ECharts items draw on a canvas: no DOM marks to count, so ask the pixels.
    expect(GALLERY_DRAWN_CANVAS_ITEMS.length).toBeGreaterThanOrEqual(2)
    for (const item of GALLERY_DRAWN_CANVAS_ITEMS) {
      const section = page.locator(`[data-gallery-item="${item.id}"]`)
      await expect(section.getByRole('heading', { level: 2, name: item.title })).toBeVisible()
      await expect(section.locator('[data-chart-engine="echarts"]')).toHaveAttribute('data-chart-status', 'ready')
      const canvas = section.locator(ECHARTS_CANVAS).first()
      await expect(canvas, `${item.id}: no chart canvas`).toBeVisible()
      const box = await canvas.boundingBox()
      expect(Math.round(box?.width ?? 0), `${item.id}: canvas width`).toBe(GALLERY_CANVAS.width)
      expect(Math.round(box?.height ?? 0), `${item.id}: canvas height`).toBe(galleryChartHeight(item))
      // Card + axis text + at least a few cell colours — a blank canvas has one colour.
      await expect
        .poll(() => paintedColours(page, item.id), { message: `${item.id}: canvas is blank` })
        .toBeGreaterThanOrEqual(4)
    }

    // `dom` items (VIZ-406's slowest-tests) draw no engine at all: a ranked
    // list whose bars are elements with a percentage width. There is no svg to
    // measure and no canvas to sample, so the geometry IS those widths —
    // `textContent` cannot see whether a bar was actually drawn.
    expect(GALLERY_DRAWN_DOM_ITEMS.length).toBeGreaterThanOrEqual(1)
    for (const item of GALLERY_DRAWN_DOM_ITEMS) {
      const section = page.locator(`[data-gallery-item="${item.id}"]`)
      await expect(section.getByRole('heading', { level: 2, name: item.title })).toBeVisible()
      await expect(section.locator(CHART_SVG), `${item.id}: expected no svg engine`).toHaveCount(0)
      const widths = await section
        .locator('[data-testid="ranked-bar"]')
        .evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().width))
      expect(widths.length, `${item.id}: no ranked bars`).toBeGreaterThan(0)
      expect(Math.max(...widths), `${item.id}: every bar has zero width`).toBeGreaterThan(0)
    }

    // The three loops above must PARTITION the drawn items. Without this, a new
    // `galleryEngine` value would put an item in none of them and it would be
    // asserted on by nothing at all, while this test stayed green.
    expect(
      GALLERY_DRAWN_SVG_ITEMS.length + GALLERY_DRAWN_CANVAS_ITEMS.length + GALLERY_DRAWN_DOM_ITEMS.length,
      'a drawn gallery item belongs to no engine loop',
    ).toBe(GALLERY_DRAWN_ITEMS.length)

    expect(errors).toEqual([])
  })

  test('the ECharts engine runs under the production CSP with zero violations', async ({ page }) => {
    await watchCsp(page)
    const errors = watchErrors(page)
    const engineRequests: string[] = []
    page.on('request', (request) => {
      if (ENGINE_REQUEST.test(request.url())) engineRequests.push(request.url())
    })
    await openGallery(page)
    // The meta tag the dev server serves is the production policy.
    const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content')
    expect(csp).toContain("script-src 'self'")
    expect(csp).not.toContain('unsafe-eval')
    for (const item of GALLERY_DRAWN_CANVAS_ITEMS) {
      await expect(page.locator(`[data-gallery-item="${item.id}"] [data-chart-engine="echarts"]`)).toHaveAttribute(
        'data-chart-status',
        'ready',
      )
    }
    // Positive control for the network assertion below: here the engine WAS fetched.
    expect(engineRequests.length).toBeGreaterThan(0)
    expect(await cspViolations(page)).toEqual([])
    expect(errors).toEqual([])
  })

  test('a page without a heatmap never requests the ECharts engine', async ({ page }) => {
    await watchCsp(page)
    const engineRequests: string[] = []
    page.on('request', (request) => {
      if (ENGINE_REQUEST.test(request.url())) engineRequests.push(request.url())
    })
    await page.goto('/login')
    await expect(page.locator('form')).toBeVisible()
    await page.waitForLoadState('networkidle')
    expect(engineRequests).toEqual([])
    expect(await cspViolations(page)).toEqual([])
  })

  test('a hostile label in a heatmap tooltip is literal text and never executes', async ({ page }) => {
    await watchCsp(page)
    const errors = watchErrors(page)
    await openGallery(page)
    const section = page.locator('[data-gallery-item="heatmap-hostile-label"]')
    await expect(section.locator('[data-chart-engine="echarts"]')).toHaveAttribute('data-chart-status', 'ready')
    await expect.poll(() => paintedColours(page, 'heatmap-hostile-label')).toBeGreaterThanOrEqual(3)
    const canvas = section.locator(ECHARTS_CANVAS).first()
    await canvas.scrollIntoViewIfNeeded()
    const box = await canvas.boundingBox()
    if (!box) throw new Error('hostile heatmap canvas has no box')

    // The plot area is the canvas minus the grid insets (heatmapOption.ts:
    // left 120, right 16, top 8, bottom 56); the hostile cell is its left half.
    const plot = { left: 120, right: GALLERY_CANVAS.width - 16, top: 8, bottom: GALLERY_CANVAS.height - 56 }
    await page.mouse.move(box.x + plot.left + (plot.right - plot.left) / 4, box.y + (plot.top + plot.bottom) / 2)

    const tooltip = section.locator('[data-chart-tooltip]')
    await expect(tooltip).toBeVisible()
    await expect(tooltip).toContainText(HOSTILE_LABEL)
    await expect(tooltip).toContainText('<b>suite</b>')
    // Rendered as text: no element was created from the label, nothing ran.
    await expect(section.locator('img')).toHaveCount(0)
    await expect(section.locator('b')).toHaveCount(0)
    expect(await page.evaluate(() => (window as unknown as { __xss?: unknown }).__xss)).toBeUndefined()
    expect(await cspViolations(page)).toEqual([])
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
  const KNOWN_VIOLATIONS: KnownViolation[] = [
    { theme: 'lab', rule: 'color-contrast', nodeHtmlIncludes: 'No defect data' },
  ]

  // The gate itself (every impact, the full WCAG + best-practice tag set, the
  // ratchet) and the six themes live in `tests/lib/axe-gate.ts`.

  for (const theme of ALL_THEMES) {
    test(`has no automated accessibility violations at any impact (${theme})`, async ({
      page,
    }) => {
      await openGallery(page, `?theme=${theme}`)
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      await expect(page.locator(CHART_SVG).first()).toBeVisible()
      // Audit the canvas charts too, not a still-loading placeholder.
      for (const item of GALLERY_DRAWN_CANVAS_ITEMS) {
        await expect(page.locator(`[data-gallery-item="${item.id}"] [data-chart-engine="echarts"]`)).toHaveAttribute(
          'data-chart-status',
          'ready',
        )
      }
      // …and the VIZ-406 list, which has no engine to report ready. Auditing
      // before it paints would audit an empty box and pass for the wrong reason.
      for (const item of GALLERY_DRAWN_DOM_ITEMS) {
        await expect(
          page.locator(`[data-gallery-item="${item.id}"] [data-testid="ranked-bar"]`).first(),
        ).toBeVisible()
      }
      await expectNoBlockingViolations(
        page,
        theme,
        KNOWN_VIOLATIONS.filter((entry) => entry.theme === theme),
      )
    })
  }

  test('status marks carry their pattern, and each legend swatch carries the same one', async ({ page }) => {
    await openGallery(page)
    const bar = page.locator('[data-gallery-item="trend-bar"]')
    await expect(bar.locator(`${CHART_SVG} path`).first()).toBeVisible()
    const found = await bar.evaluate((section) => {
      const fillOf = (el: Element | null) => el?.getAttribute('fill') ?? ''
      const patternFor = (fill: string) => {
        const id = /^url\(#(.+)\)$/.exec(fill)?.[1]
        const node = id ? document.getElementById(id) : null
        return node?.tagName.toLowerCase() === 'pattern' ? node.id : null
      }
      const svg = section.querySelector('.recharts-wrapper > svg.recharts-surface')
      // Recharts draws each bar segment as a path with the Bar's fill.
      const markFills = [...new Set(Array.from(svg?.querySelectorAll('.recharts-bar-rectangle path') ?? [], fillOf))]
      const legend = Array.from(section.querySelectorAll('[data-legend-status]'), (entry) => ({
        status: entry.getAttribute('data-legend-status'),
        fill: fillOf(entry.querySelector('[data-legend-swatch]')),
      }))
      return {
        marks: markFills.map((fill) => ({ fill, pattern: patternFor(fill) })),
        legend: legend.map((entry) => ({ ...entry, pattern: patternFor(entry.fill) })),
      }
    })
    expect(found.marks).toHaveLength(4)
    for (const mark of found.marks) expect(mark.pattern, mark.fill).not.toBeNull()
    expect(found.legend.map((entry) => entry.status)).toEqual(['passed', 'failed', 'skipped', 'broken'])
    for (const entry of found.legend) {
      expect(entry.pattern, `${entry.status} legend swatch`).toMatch(new RegExp(`chart-pattern-${entry.status}$`))
      expect(found.marks.map((mark) => mark.fill)).toContain(entry.fill)
    }

    // The status matrix (canvas) has a legend drawn with the same patterns.
    const status = page.locator('[data-gallery-item="heatmap-status"]')
    await expect(status.locator('[data-chart-engine="echarts"]')).toHaveAttribute('data-chart-status', 'ready')
    const present = [...new Set(GALLERY_STATUS_MATRIX_DATA.cells.map((c) => c.value).filter((v) => v !== null))]
    await expect(status.locator('[data-legend-status]')).toHaveCount(present.length)
    await expect.poll(() => paintedColours(page, 'heatmap-status')).toBeGreaterThanOrEqual(6)
  })

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
    const scroller = page.locator('[data-gallery-scroller="trend-line"]')
    await expect(scroller).toHaveAttribute('tabindex', '0')
    expect(await scroller.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true)
    // It carries NO role and no name of its own: a framed chart is already a
    // named `role="group"` (the frame body), and a second group around it
    // made every chart "group, group, <title>, <title>".
    await expect(scroller).not.toHaveAttribute('role', /.+/)
    for (const framed of ['donut-status', 'bar-stacked']) {
      const section = page.locator(`[data-gallery-item="${framed}"]`)
      // One heading per item, not the gallery's h2 AND the frame's h3.
      await expect(section.getByRole('heading', { name: await section.getAttribute('aria-label') ?? '' })).toHaveCount(1)
      await expect(section.locator(`[data-gallery-scroller="${framed}"][role]`)).toHaveCount(0)
    }
  })

  // ── Wave 2 · the donut (VIZ-401) and the bars (VIZ-402) ──────────────────────

  /** Bounding boxes of the drawn bar rectangles of one gallery item, top to bottom. */
  async function barBoxes(page: Page, itemId: string) {
    const boxes = await page
      .locator(`[data-gallery-item="${itemId}"] .recharts-bar-rectangle path`)
      .evaluateAll((nodes) =>
        nodes.map((node) => {
          const box = node.getBoundingClientRect()
          return { x: box.x, right: box.right, width: box.width, y: box.y }
        }),
      )
    return boxes.sort((a, b) => a.y - b.y || a.x - b.x)
  }

  test('the status donut: fixed order, a centre total, count-and-percent labels summing to 100.0', async ({
    page,
  }) => {
    const errors = watchErrors(page)
    await openGallery(page)

    const donut = page.locator('[data-gallery-item="donut-status"]')
    await expect(donut.locator('[data-donut]')).toHaveAttribute('data-donut-total', '1000')
    await expect(donut.locator('[data-donut-centre]')).toContainText('1,000')
    await expect(donut.locator('[data-donut-centre]')).toContainText('executions')

    // Count AND percent, in the FIXED status order (passed, failed, broken, skipped).
    await expect.poll(() => donut.locator('[data-donut-slice-label]').count()).toBe(4)
    const labels = await donut.locator('[data-donut-slice-label]').allTextContents()
    expect(labels).toEqual(['880 (88.0%)', '60 (6.0%)', '20 (2.0%)', '40 (4.0%)'])
    const percents = labels.map((label) => Number(/\(([\d.]+)%\)/.exec(label)?.[1]))
    expect(percents.reduce((sum, value) => sum + value, 0)).toBeCloseTo(100, 6)

    // `unknown` is the fifth slice; flaky is never one.
    const withUnknown = page.locator('[data-gallery-item="donut-status-unknown"] [data-donut]')
    await expect(withUnknown).toHaveAttribute('data-donut-slices', '5')
    await expect(page.locator('[data-gallery-item="donut-status-unknown"] [data-chart-legend]')).not.toContainText(
      'Flaky',
    )

    // One status only: a full ring, still labelled.
    const single = page.locator('[data-gallery-item="donut-single-status"]')
    await expect(single.locator('[data-donut]')).toHaveAttribute('data-donut-full-ring', 'true')
    await expect(single.locator('[data-donut-slice-label]')).toHaveText('412 (100.0%)')

    // Under 2%: no label on the arc, the label (with the TRUE value) in the
    // legend, and the arc still drawn.
    const tiny = page.locator('[data-gallery-item="donut-tiny-slice"]')
    await expect(tiny.locator('[data-donut-slice-label]')).toHaveCount(1)
    await expect(tiny.locator('[data-donut]')).toHaveAttribute('data-donut-legend-only', '1')
    await expect(tiny.locator('[data-chart-legend]')).toContainText('Failed 50 (0.5%)')
    await expect.poll(() => drawnMarks(page, 'donut-tiny-slice')).toBeGreaterThanOrEqual(2)

    // All zero is not a ring of nothing: the frame says the filters match nothing.
    const zero = page.locator('[data-gallery-item="donut-all-zero"] [data-chart-frame]')
    await expect(zero).toHaveAttribute('data-chart-state', 'filtered-empty')
    await expect(zero).toContainText('No data matches the current filters')
    await expect(zero.locator('[data-donut]')).toHaveCount(0)

    expect(errors).toEqual([])
  })

  test('ranked bars are really sorted, really start at zero, and really are proportional', async ({ page }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="bar-ranked"]')
    const biggest = Math.max(...GALLERY_TOP_FAILING.map(([, value]) => value))
    // From zero, and past the biggest bar — to a NICE end, not to the data max.
    const [start, end] = ((await item.locator('[data-bar-chart="ranked"]').getAttribute('data-bar-domain')) ?? '')
      .split(',')
      .map(Number)
    expect(start).toBe(0)
    expect(end).toBeGreaterThanOrEqual(biggest)

    await expect.poll(() => barBoxes(page, 'bar-ranked').then((boxes) => boxes.length)).toBe(
      GALLERY_TOP_FAILING.length,
    )
    const boxes = await barBoxes(page, 'bar-ranked')

    // Sorted: each bar is no longer than the one above it.
    const widths = boxes.map((box) => box.width)
    expect(widths).toEqual([...widths].sort((a, b) => b - a))

    // Zero baseline, twice over: every bar starts at the same x…
    const lefts = new Set(boxes.map((box) => Math.round(box.x)))
    expect(lefts.size, `bars start at ${[...lefts].join(', ')}`).toBe(1)
    // …and length is proportional to value, which is only true from zero. (A
    // baseline at, say, 4 would draw the 41 as ~9× the 9, not 4.6×.)
    const values = [...GALLERY_TOP_FAILING].map(([, value]) => value).sort((a, b) => b - a)
    const ratios = boxes.map((box, index) => box.width / values[index])
    expect(Math.max(...ratios) / Math.min(...ratios)).toBeLessThan(1.1)
  })

  test('a change chart diverges around a zero baseline', async ({ page }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="bar-diverging"]')
    const extreme = Math.max(...GALLERY_CHANGE_BARS.map(([, value]) => Math.abs(value)))
    const plot = item.locator('[data-bar-chart="ranked"]')
    await expect(plot).toHaveAttribute('data-bar-diverging', 'true')
    // Symmetric about zero, and wide enough for the most extreme change.
    const [low, high] = ((await plot.getAttribute('data-bar-domain')) ?? '').split(',').map(Number)
    expect(low).toBe(-high)
    expect(high).toBeGreaterThanOrEqual(extreme)
    await expect(item).toContainText('diverge from a zero baseline')

    await expect.poll(() => barBoxes(page, 'bar-diverging').then((b) => b.length)).toBe(GALLERY_CHANGE_BARS.length)
    const boxes = await barBoxes(page, 'bar-diverging')
    const positives = GALLERY_CHANGE_BARS.filter(([, value]) => value > 0).length
    // The rises start where the falls end: one shared zero, in the middle.
    const rises = boxes.slice(0, positives)
    const falls = boxes.slice(positives)
    const zero = Math.round(rises[0].x)
    for (const rise of rises) expect(Math.round(rise.x)).toBe(zero)
    for (const fall of falls) expect(Math.round(fall.right)).toBe(zero)
  })

  test('the bars state their ties and their page, and the page turns', async ({ page }) => {
    await openGallery(page)

    const ties = page.locator('[data-gallery-item="bar-ranked-ties"]')
    await expect(ties.locator('[data-chart-footer]')).toContainText('tied with the 10th')
    await expect(ties.locator('[data-bar-chart]')).toHaveAttribute('data-bar-total', '13')

    const paged = page.locator('[data-gallery-item="bar-paginated"]')
    await expect(paged.locator('[data-chart-footer]')).toContainText('Page 1 of 2')
    await expect(paged.locator('[data-chart-footer]')).toContainText('60 bars in all')
    await expect.poll(() => barBoxes(page, 'bar-paginated').then((b) => b.length)).toBe(50)

    await paged.getByRole('button', { name: 'Next bars' }).click()
    await expect(paged.locator('[data-chart-footer]')).toContainText('Page 2 of 2')
    await expect(paged.locator('[data-bar-chart]')).toHaveAttribute('data-bar-page', '1')
    await expect.poll(() => barBoxes(page, 'bar-paginated').then((b) => b.length)).toBe(10)
  })

  test('a long test name is middle-truncated on the axis and whole in the table', async ({ page }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="bar-long-names"]')
    // Recharts 3 hoists the tick LABELS out of the axis layer into their own.
    const tickLabels = item.locator(`${CATEGORY_TICKS} .recharts-cartesian-axis-tick-value`)
    await expect.poll(() => tickLabels.count()).toBe(GALLERY_LONG_NAMES.length)
    const ticks = await tickLabels.allTextContents()
    const longest = GALLERY_LONG_NAMES[0][0]
    expect(ticks.some((tick) => tick.includes('…'))).toBe(true)
    expect(ticks).not.toContain(longest)
    // Middle, not end: the tick keeps the head AND the tail.
    const truncated = ticks.find((tick) => tick.includes('…')) as string
    expect(longest.startsWith(truncated.split('…')[0])).toBe(true)
    expect(longest.endsWith(truncated.split('…')[1])).toBe(true)

    await item.getByRole('button', { name: 'View as table' }).click()
    await expect(item.getByRole('table')).toContainText(longest)
  })

  test('a hostile test name is drawn as literal text and never executes', async ({ page }) => {
    const errors = watchErrors(page)
    await openGallery(page)
    const item = page.locator('[data-gallery-item="bar-hostile-label"]')
    await expect(item.locator('[data-bar-chart]')).toBeVisible()
    // Rendered as text: no element was created from the label.
    await expect(item.locator('img')).toHaveCount(0)
    await expect(item.locator(CATEGORY_TICKS)).toContainText('<img src=x')
    await item.getByRole('button', { name: 'View as table' }).click()
    await expect(item.getByRole('table')).toContainText(HOSTILE_LABEL)
    await expect(item.getByRole('table').locator('img')).toHaveCount(0)
    expect(await page.evaluate(() => (window as unknown as { __xss?: unknown }).__xss)).toBeUndefined()
    expect(errors).toEqual([])
  })

  test('stacked bars keep the fixed status order, and 100% mode really fills the axis', async ({ page }) => {
    await openGallery(page)
    const stacked = page.locator('[data-gallery-item="bar-stacked"]')
    await expect(stacked.locator('[data-bar-chart]')).toHaveAttribute('data-bar-statuses', 'passed,failed,broken,skipped')
    await expect(stacked.locator('[data-bar-chart]')).toHaveAttribute('data-bar-mode', 'absolute')
    await expect(stacked.locator('[data-chart-legend] [data-legend-status]')).toHaveCount(4)
    const legend = await stacked.locator('[data-legend-status]').evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute('data-legend-status')),
    )
    expect(legend).toEqual(['passed', 'failed', 'broken', 'skipped'])

    // Absolute: the bars differ in length, because the suites differ in size.
    const spans = async (id: string) =>
      page.locator(`[data-gallery-item="${id}"] .recharts-bar-rectangle path`).evaluateAll((nodes) => {
        const byRow = new Map<number, { left: number; right: number }>()
        for (const node of nodes) {
          const box = node.getBoundingClientRect()
          const row = Math.round(box.y)
          const current = byRow.get(row)
          byRow.set(row, {
            left: Math.min(current?.left ?? box.left, box.left),
            right: Math.max(current?.right ?? box.right, box.right),
          })
        }
        return [...byRow.values()].map((span) => Math.round(span.right - span.left))
      })

    await expect.poll(() => spans('bar-stacked').then((rows) => rows.length)).toBe(GALLERY_SUITE_STATUS.length)
    const absolute = await spans('bar-stacked')
    expect(new Set(absolute).size).toBeGreaterThan(1)

    // 100%: every bar is the same length, because every bar is 100%.
    const full = await spans('bar-stacked-100')
    expect(full.length).toBe(GALLERY_SUITE_STATUS.length)
    expect(Math.max(...full) - Math.min(...full)).toBeLessThanOrEqual(2)
    await expect(page.locator('[data-gallery-item="bar-stacked-100"] [data-bar-chart]')).toHaveAttribute(
      'data-bar-domain',
      '0,100',
    )

    // …and the toggle switches the absolute one into that same shape.
    await stacked.getByRole('button', { name: 'Show 100%' }).click()
    await expect(stacked.locator('[data-bar-chart]')).toHaveAttribute('data-bar-mode', 'percent')
    await expect.poll(async () => {
      const toggled = await spans('bar-stacked')
      return Math.max(...toggled) - Math.min(...toggled)
    }).toBeLessThanOrEqual(2)

    // Grouped is the same data, unstacked.
    await expect(page.locator('[data-gallery-item="bar-grouped"] [data-bar-chart]')).toHaveAttribute(
      'data-bar-chart',
      'grouped',
    )
  })

  // -- Wave 2 - fix round A: what a keyboard and a narrow window can read ------

  /**
   * Every Wave-2 Recharts chart used to take BOTH of Recharts' accessibility
   * affordances away: a custom tooltip `content` deletes its `role="status"`
   * live region, and `accessibilityLayer` puts an UNNAMED `role="application"`
   * on the surface, which suppresses the virtual cursor. A keyboard reader
   * could then reach no point value on any of them.
   */
  test('a keyboard reader can read every point, through the ONE page announcer', async ({ page }) => {
    await openGallery(page)
    const announcer = page.locator('[data-chart-announcer="assertive"]')

    for (const [item, plot, expected] of [
      ['donut-status', '[data-donut]', /Passed 880/],
      ['bar-ranked', '[data-bar-chart="ranked"]', /:\s/],
      ['bar-stacked', '[data-bar-chart="stacked"]', /Passed/],
      // …and fix round B's charts: the time series and the two SVG duration ones.
      ['timeseries-trend-releases', '[data-time-series-plot]', /\(UTC\)/],
      ['timeseries-zoomed-axis', '[data-time-series-plot]', /Pass rate/],
      ['duration-histogram', '[data-duration-histogram-plot]', /executions?/],
      ['duration-band', '[data-duration-trend-plot]', /p95/],
    ] as const) {
      const surface = page.locator(`[data-gallery-item="${item}"] ${plot}`)
      // Named after the chart, focusable, and NOT an application.
      await expect(surface).toHaveAttribute('tabindex', '0')
      await expect(surface).toHaveAttribute('role', 'group')
      await expect(surface).toHaveAttribute('aria-label', /Use the arrow keys/)
      await surface.focus()
      await expect(surface).toBeFocused()
      await page.keyboard.press('ArrowRight')
      await expect(surface).toHaveAttribute('data-chart-cursor', 'active')
      await expect(announcer).toContainText(expected)
      // A sighted keyboard user sees the same words.
      await expect(page.locator(`[data-gallery-item="${item}"] [data-chart-readout]`)).toBeVisible()

      // SC 1.4.13: dismissible, and focus stays where the reader put it.
      await page.keyboard.press('Escape')
      await expect(surface).toHaveAttribute('data-chart-cursor', 'idle')
      await expect(page.locator(`[data-gallery-item="${item}"] [data-chart-readout]`)).toHaveCount(0)
      await expect(surface).toBeFocused()
    }

    // …and still exactly one announcer for the whole page — one polite, one
    // assertive — with no frame declaring a live region of its own.
    expect(await page.locator('[data-chart-announcer]').count()).toBe(2)
    expect(await page.locator('[data-chart-frame] [data-chart-announcer]').count()).toBe(0)

    /**
     * NO chart in the Wave-2 catalogue is an unnamed `role="application"`.
     *
     * Round A could only claim this for four items; round B finished the rest,
     * so the assertion now walks EVERY gallery item. The only charts excused
     * are the two Wave-1 components that still pass Recharts'
     * `accessibilityLayer` and belong to neither round's scope — `TrendChart`
     * (`chart: 'trend'`) and `PassRateGauge` (`chart: 'gauge'`). They are named
     * by COMPONENT rather than by item id, so adding another fixture of the
     * same chart does not need a new exception; a new chart of any other kind
     * that arrives with the default layer fails here.
     */
    const LEGACY_APPLICATION_LAYER = ['trend', 'gauge']
    const backlog = new Set(
      GALLERY_ITEMS.filter((item) => LEGACY_APPLICATION_LAYER.includes(item.chart)).map((item) => item.id),
    )
    let backlogStillUnfixed = 0
    for (const item of GALLERY_ITEM_IDS) {
      const found = await page.locator(`[data-gallery-item="${item}"] [role="application"]`).count()
      if (backlog.has(item)) backlogStillUnfixed += found
      else expect(found, `${item} has an unnamed role="application"`).toBe(0)
    }
    // The ratchet: once TrendChart and PassRateGauge are fixed too, this test
    // fails until the exception above is deleted.
    expect(
      backlogStillUnfixed,
      'no excused chart has role="application" any more — delete LEGACY_APPLICATION_LAYER',
    ).toBeGreaterThan(0)
  })

  /**
   * `slowest-tests` is the one Wave-2 chart with no drawing surface: it is 20
   * real `<li>` rows, which a screen reader walks with its own virtual cursor.
   * What it owes a keyboard and a 320 px window is the full test NAME, not a
   * nine-character truncation with the rest in a mouse-only `title`.
   */
  test('the slowest-tests list reads as rows, and shows the whole name when narrow', async ({ page }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="slowest-tests"]')
    await expect(item.locator('[data-testid="ranked-bar"]').first()).toBeVisible()
    expect(await item.locator('[role="application"]').count()).toBe(0)
    // Wide: one line per row, the name truncated with the full text in `title`.
    await expect(item.locator('[data-slowest-list]')).toHaveAttribute('data-slowest-stacked', 'false')

    // Narrow, for real: the fluid canvas at a 320 px viewport.
    await page.setViewportSize({ width: 320, height: 800 })
    await openGallery(page, `?canvas=${GALLERY_FLUID_CANVAS_PARAM}`)
    const narrow = page.locator('[data-gallery-item="slowest-tests"]')
    await expect(narrow.locator('[data-slowest-list]')).toHaveAttribute('data-slowest-stacked', 'true')
    const names = await narrow.locator('[data-slowest-name]').allTextContents()
    expect(names.length).toBeGreaterThan(1)
    // Every row says something DIFFERENT: the whole point of the stack.
    expect(new Set(names).size).toBe(names.length)
    // …and the name is not clipped to a sliver of its box.
    const widths = await narrow.locator('[data-slowest-name]').evaluateAll((nodes) =>
      nodes.map((node) => ({ scroll: node.scrollWidth, client: node.clientWidth })),
    )
    for (const box of widths) expect(box.scroll, JSON.stringify(box)).toBeLessThanOrEqual(box.client + 1)
  })

  test('a hover tooltip is hoverable and dismissible (SC 1.4.13)', async ({ page }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="bar-stacked"]')
    await expect(item.locator('.recharts-bar-rectangle path').first()).toBeVisible()
    // The gallery is far taller than the viewport, and `mouse.move` takes
    // VIEWPORT coordinates: without this the pointer goes nowhere near the bar.
    await item.scrollIntoViewIfNeeded()
    // The WIDEST drawn segment: a status with a zero count is a path of no
    // width at all, and its "centre" is a point beside the bar, not on it.
    const centre = await item.locator('.recharts-bar-rectangle path').evaluateAll((nodes) => {
      const boxes = nodes.map((node) => node.getBoundingClientRect()).filter((box) => box.width > 2)
      const widest = boxes.sort((a, b) => b.width - a.width)[0]
      return { x: widest.x + widest.width / 2, y: widest.y + widest.height / 2 }
    })
    const overTheBar = () => page.mouse.move(centre.x, centre.y)
    await page.mouse.move(centre.x - 30, centre.y - 30)
    await overTheBar()
    const tooltip = item.locator('[data-chart-tooltip]')
    await expect(tooltip).toBeVisible()

    // Hoverable (SC 1.4.13), the part a chart can guarantee: the tooltip TAKES
    // pointer events. Recharts' wrapper is `pointer-events: none`, which makes
    // hovering the content impossible by construction — the pointer passes
    // straight through it, so the criterion fails before the pointer has
    // moved. (Reaching it is a second question: this tooltip is still placed
    // relative to the cursor, so it moves as the pointer approaches. Pinning
    // it is the remaining half, and it is NOT done here.)
    const wrapper = item.locator('.recharts-tooltip-wrapper').first()
    expect(await wrapper.evaluate((el) => getComputedStyle(el).pointerEvents)).toBe('auto')
    // …and the tooltip itself is a real hit target, not a 0x0 box.
    const tip = (await tooltip.boundingBox()) as { width: number; height: number }
    expect(tip.width).toBeGreaterThan(20)
    expect(tip.height).toBeGreaterThan(10)

    // Dismissible: Escape, with the pointer still over the chart and no
    // keyboard focus anywhere near it.
    await page.keyboard.press('Escape')
    await expect(tooltip).toHaveCount(0)
    // …and pointing at the chart again asks for it back: a dismissal is for
    // THIS tooltip, not a setting the reader has to undo.
    await page.mouse.move(10, 10)
    await page.mouse.move(centre.x - 30, centre.y - 30)
    await overTheBar()
    await expect(item.locator('[data-chart-tooltip]')).toBeVisible()
  })

  test('both bar axes are titled, and the value axis follows the 100% toggle', async ({ page }) => {
    await openGallery(page)
    const axisText = (id: string) =>
      page
        .locator(`[data-gallery-item="${id}"] ${CHART_SVG} text`)
        .allTextContents()
        .then((texts) => texts.map((text) => text.trim()))

    // A bar chart that names neither axis says only "big" and "small".
    const ranked = await axisText('bar-ranked')
    expect(ranked, 'ranked: no value-axis title').toContain('Count')
    expect(ranked, 'ranked: no category-axis title').toContain('Test')

    const stacked = page.locator('[data-gallery-item="bar-stacked"]')
    const stackedAxes = await axisText('bar-stacked')
    expect(stackedAxes).toContain('Count')
    expect(stackedAxes).toContain('Suite')
    const absoluteTicks = await stacked.locator(`${VALUE_TICKS} .recharts-cartesian-axis-tick-value`).allTextContents()
    expect(absoluteTicks.some((tick) => tick.includes('%'))).toBe(false)

    await stacked.getByRole('button', { name: 'Show 100%' }).click()
    await expect(stacked.locator('[data-bar-chart]')).toHaveAttribute('data-bar-mode', 'percent')
    // The axis now carries the unit…
    await expect
      .poll(async () => {
        const ticks = await stacked.locator(`${VALUE_TICKS} .recharts-cartesian-axis-tick-value`).allTextContents()
        return ticks.length > 0 && ticks.every((tick) => tick.endsWith('%'))
      })
      .toBe(true)
    // …its title says what the length means…
    expect(await axisText('bar-stacked')).toContain('Share of bar (%)')
    // …and the chart says, in words, which side of the toggle is live.
    await expect(stacked.locator('[data-chart-footer]')).toContainText('Each bar is drawn as 100%')

    // The table follows too: shares, not counts.
    await stacked.getByRole('button', { name: 'View as table' }).click()
    await expect(stacked.getByRole('table')).toContainText('%')
  })

  test('"Next bars" onto the last page keeps the reader where they were', async ({ page }) => {
    await openGallery(page)
    const paged = page.locator('[data-gallery-item="bar-paginated"]')
    const next = paged.getByRole('button', { name: 'Next bars' })
    const status = paged.locator('[data-bar-page-status]')
    await expect(status).toHaveText('Page 1 of 2')
    // The page state describes the buttons it belongs to.
    const statusId = await status.getAttribute('id')
    await expect(next).toHaveAttribute('aria-describedby', statusId as string)
    await expect(paged.getByRole('button', { name: 'Previous bars' })).toHaveAttribute(
      'aria-describedby',
      statusId as string,
    )

    await next.focus()
    await page.keyboard.press('Enter')
    await expect(status).toHaveText('Page 2 of 2')
    // `disabled` used to destroy the focused control and drop focus to <body>.
    await expect(next).toBeFocused()
    await expect(next).toHaveAttribute('aria-disabled', 'true')
    expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false)
    // …and pressing it again does nothing rather than paging past the end.
    await page.keyboard.press('Enter')
    await expect(status).toHaveText('Page 2 of 2')
  })

  test('the page controls are the same size as the frame\'s own buttons', async ({ page }) => {
    await openGallery(page)
    const heightOf = (locator: Locator) => locator.evaluate((el) => Math.round(el.getBoundingClientRect().height))
    const paged = page.locator('[data-gallery-item="bar-paginated"]')
    const frameButton = await heightOf(paged.getByRole('button', { name: 'View as table' }))
    for (const name of ['Previous bars', 'Next bars']) {
      expect(await heightOf(paged.getByRole('button', { name })), `${name} height`).toBe(frameButton)
    }
    expect(frameButton).toBeGreaterThanOrEqual(26)
  })

  /**
   * The gallery pins every canvas to 640 px, which is exactly what hides SC
   * 1.4.10: at a 320 px viewport the canvas stays 640 px and scrolls inside
   * its box, so nothing reflows and a chart that loses its whole value axis
   * looks perfect. `?canvas=fluid` releases the pin so the SAME charts are
   * narrow for real.
   */
  test('the numbers survive a real 320px frame', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 900 })
    await openGallery(page, `?canvas=${GALLERY_FLUID_CANVAS_PARAM}`)
    await expect(page.getByTestId('chart-gallery')).toHaveAttribute('data-gallery-canvas-mode', 'fluid')

    // Still no two-dimensional scrolling.
    const overflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }))
    expect(overflow.scrollWidth, JSON.stringify(overflow)).toBeLessThanOrEqual(overflow.clientWidth)

    for (const id of ['bar-ranked', 'bar-stacked']) {
      const item = page.locator(`[data-gallery-item="${id}"]`)
      // The chart KNOWS it is narrow…
      await expect(item.locator('[data-bar-chart]')).toHaveAttribute('data-bar-compact', 'true')
      // …the value axis is still drawn…
      await expect
        .poll(() => item.locator(`${VALUE_TICKS} .recharts-cartesian-axis-tick-value`).count(), {
          message: `${id}: the value axis vanished at 320px`,
        })
        .toBeGreaterThanOrEqual(2)
      // …and every category still has its label.
      await expect
        .poll(() => item.locator(`${CATEGORY_TICKS} .recharts-cartesian-axis-tick-value`).count())
        .toBeGreaterThanOrEqual(2)
    }

    // The ranked chart keeps a value label on every bar it draws.
    const ranked = page.locator('[data-gallery-item="bar-ranked"]')
    const bars = await ranked.locator('.recharts-bar-rectangle path').count()
    expect(bars).toBeGreaterThan(0)
    await expect.poll(() => ranked.locator('.recharts-label-list text').count()).toBe(bars)
  })

  test('a tiny donut slice leaves no empty text node behind', async ({ page }) => {
    await openGallery(page)
    const empties = await page
      .locator('[data-gallery-item="donut-tiny-slice"] svg text')
      .evaluateAll((nodes) => nodes.filter((node) => (node.textContent ?? '').trim() === '').length)
    expect(empties, 'an empty <text> is a node a reader can land on that says nothing').toBe(0)
  })

  test('the REGISTRY, not the caller, decides between a donut and a ranked bar', async ({ page }) => {
    await openGallery(page)

    const four = page.locator('[data-gallery-item="breakdown-four-categories"]')
    await expect(four.locator('[data-chart-choice]')).toHaveAttribute('data-chart-choice', 'donut')
    await expect(four.locator('[data-chart-choice]')).toHaveAttribute('data-chart-offers-pie', 'true')
    await expect(four.locator('[data-donut]')).toHaveCount(1)

    // Six categories, and the caller asked for a donut: it gets bars, and no pie.
    const six = page.locator('[data-gallery-item="breakdown-six-categories"]')
    await expect(six.locator('[data-chart-choice]')).toHaveAttribute('data-chart-choice', 'ranked-bar')
    await expect(six.locator('[data-chart-choice]')).toHaveAttribute('data-chart-offers-pie', 'false')
    await expect(six.locator('[data-donut]')).toHaveCount(0)
    await expect(six.locator('[data-bar-chart="ranked"]')).toHaveCount(1)
  })

  test('the table view of a Wave-2 chart opens from the keyboard and lands on the caption', async ({ page }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="donut-status"]')
    const button = item.getByRole('button', { name: 'View as table' })
    await button.focus()
    await expect(button).toBeFocused()
    await page.keyboard.press('Enter')

    const table = item.getByRole('table')
    await expect(table).toBeVisible()
    await expect(table.locator('caption')).toBeFocused()
    const rows = await table
      .locator('tbody tr')
      .evaluateAll((trs) => trs.map((tr) => Array.from(tr.children, (cell) => cell.textContent ?? '')))
    // Exactly the plotted values, in the drawn order — count AND share, the
    // two things every arc is labelled with.
    expect(rows).toEqual([
      ['Passed', '880', '88.0%'],
      ['Failed', '60', '6.0%'],
      ['Broken', '20', '2.0%'],
      ['Skipped', '40', '4.0%'],
    ])
    // …and the number in the centre of the ring, which no row could carry.
    await expect(item.locator('[data-donut-table-total]')).toHaveText('Total 1,000 executions')
    // Hiding it again is a keyboard round trip too.
    await item.getByRole('button', { name: 'Hide table' }).focus()
    await page.keyboard.press('Enter')
    await expect(item.getByRole('table')).toHaveCount(0)
  })

  // ── Wave 2 · the time series (VIZ-403) and the duration charts (VIZ-406) ─────

  const TIME_SERIES = '[data-gallery-item="timeseries-trend-releases"]'

  /** The fills and dash patterns of one item's drawn bar segments. */
  async function barEncoding(page: Page, itemId: string) {
    return page.locator(`[data-gallery-item="${itemId}"] .recharts-bar-rectangle path`).evaluateAll((nodes) =>
      nodes
        .filter((node) => {
          const box = node.getBoundingClientRect()
          return box.width > 0 && box.height > 0
        })
        .map((node) => ({
          fill: node.getAttribute('fill') ?? '',
          dash: node.getAttribute('stroke-dasharray'),
        })),
    )
  }

  test('the time series marks its partial UTC day with a pattern, not just a colour', async ({ page }) => {
    const errors = watchErrors(page)
    await openGallery(page)
    const item = page.locator(TIME_SERIES)
    const figure = item.locator('[data-chart="time-series"]')

    // `meta.partial_day` names 2026-03-10, and 2 runs are still executing.
    await expect(figure).toHaveAttribute('data-partial-day', '2026-03-10')
    await expect(item.locator('[data-chart-partial-note]')).toContainText('2026-03-10 is still filling')
    await expect(item.locator('[data-chart-partial-note]')).toContainText('2 in progress')

    // Exactly one of the drawn execution bars carries a pattern fill, and that
    // one is ALSO dashed. A colour-only difference would vanish in a monochrome
    // print and for a colour-blind reader, so both encodings are asserted.
    const bars = await barEncoding(page, 'timeseries-trend-releases')
    // 10 UTC days, 2 of which had no runs at all: 8 bars.
    expect(bars.length, JSON.stringify(bars)).toBe(8)
    const patterned = bars.filter((bar) => /^url\(#/.test(bar.fill))
    expect(patterned).toHaveLength(1)
    expect(patterned[0].dash, 'the partial day is hatched but not dashed').toBeTruthy()

    // The two days nobody ran anything are a GAP, stated as such — never a 0%.
    await expect(item.locator('[data-chart-gap-note]')).toContainText('2 days have no pass rate')
    await expect(item.locator('[data-chart-axis-caption]')).toHaveText(UTC_AXIS_CAPTION)
    expect(errors).toEqual([])
  })

  test('the release markers a time series draws are in its table view too', async ({ page }) => {
    await openGallery(page)
    const item = page.locator(TIME_SERIES)

    // Drawn on the plot first: one reference line per marked bucket.
    await expect(item.locator('.recharts-reference-line')).toHaveCount(2)

    await item.getByRole('button', { name: 'View as table' }).click()
    const releases = item.locator('[data-chart-release-table]')
    await expect(releases).toBeVisible()
    const rows = await releases
      .locator('tbody tr')
      .evaluateAll((trs) => trs.map((tr) => Array.from(tr.children, (cell) => cell.textContent ?? '')))
    // 1.4.0 is stamped 2026-03-03T00:00:00Z: midnight OPENS that bucket rather
    // than closing the 2nd's. 1.4.1 is 2026-03-08T16:30:00Z, inside the 8th.
    expect(rows).toEqual([
      ['2026-03-03', '1.4.0'],
      ['2026-03-08', '1.4.1'],
    ])
    // 1.3.0 is dated 2026-02-24, before the window: dropped from the plot, but
    // COUNTED — in the chart's own note and again for the table reader. The
    // sentence also covers a release whose date could not be parsed at all,
    // which used to be dropped without being counted anywhere.
    const notShown = '1 release is not shown: dated outside this window, or carrying a date that could not be read.'
    await expect(releases).toContainText(notShown)
    await expect(item.locator('[data-chart-markers-outside]')).toContainText(notShown)
    // A table reader also gets the plotted values themselves.
    await expect(item.getByRole('table').first()).toContainText('2026-03-10')
  })

  test('a zoomed rate axis says so, and an axis that starts at 0 does not', async ({ page }) => {
    await openGallery(page)
    const zoomed = page.locator('[data-gallery-item="timeseries-zoomed-axis"]')
    await expect(zoomed.locator('[data-chart-axis-zero-indicator]')).toHaveText(AXIS_NOT_ZERO_LABEL)
    // The negative control, and the reason the indicator means anything: the
    // other two time series are on a 0–100 axis and must NOT carry it.
    for (const id of ['timeseries-trend-releases', 'timeseries-single-point']) {
      await expect(
        page.locator(`[data-gallery-item="${id}"] [data-chart-axis-zero-indicator]`),
        `${id} claims a zoomed axis`,
      ).toHaveCount(0)
    }
  })

  test('a one-day series is drawn as a dot, because a line renderer would draw nothing', async ({ page }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="timeseries-single-point"]')
    await expect(item.locator('[data-chart="time-series"]')).toHaveAttribute('data-single-point', 'true')
    // The dot is the whole point: with `dot={false}` this chart would hold data
    // and render an empty plot.
    await expect(item.locator('.recharts-line-dot')).toHaveCount(1)
  })

  test('the duration histogram counts what it could not place, and hatches its overflow bucket', async ({
    page,
  }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="duration-histogram"]')

    // The fixture holds 214 executions with NO duration and 2 recorded as
    // exactly 0 ms — 216 excluded of 687 — and the zeroes are broken out
    // because they are excluded for a different reason (a log axis has no
    // place for 0), so a reporting bug that writes every duration as 0 cannot
    // read as a missing-data bug.
    await expect(item.locator('[data-chart-excluded]')).toHaveText(
      '216 executions without duration, including 2 recorded as 0ms',
    )
    await expect(item.locator('[data-chart-counted]')).toHaveText('471 of 687 executions placed.')

    // The 1-2-5 ladder over 0.4 ms … 42 min gives 12 finite buckets plus the
    // overflow bucket, and this fixture puts at least one execution in every
    // one of them.
    const bars = await barEncoding(page, 'duration-histogram')
    expect(bars.length).toBe(13)
    // The overflow bucket counts a DIFFERENT thing (everything above the axis),
    // so it is hatched rather than merely another colour — exactly one bar.
    expect(bars.filter((bar) => /^url\(#/.test(bar.fill))).toHaveLength(1)
    // …and it is the LAST bar, not one in the middle.
    expect(/^url\(#/.test(bars[bars.length - 1].fill)).toBe(true)
  })

  test('a histogram with nothing timed says so instead of drawing an empty axis', async ({ page }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="duration-histogram-empty"]')
    await expect(item.locator('[data-chart-empty]')).toBeVisible()
    await expect(item.locator(CHART_SVG)).toHaveCount(0)
    // Three executions, none of them placeable: 2 missing and 1 recorded as 0.
    await expect(item.locator('[data-chart-excluded]')).toHaveText(
      '3 executions without duration, including 1 recorded as 0ms',
    )
    // Nothing was placed, so the "N of M placed" line is absent rather than 0.
    await expect(item.locator('[data-chart-counted]')).toHaveCount(0)
  })

  test('the p50/p95 band reports a disagreement instead of silently reordering it', async ({ page }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="duration-band"]')
    // 2026-03-08 reports p95 180 ms under p50 410 ms — one inverted day.
    await expect(item.locator('[data-chart="duration-trend"]')).toHaveAttribute('data-inverted', '1')
    await expect(item.locator('[data-chart-band-notice]')).toContainText(
      'p95 was below p50 on 1 day; both are drawn as reported.',
    )
    // The unmeasured day is a gap in the TABLE too, never a 0 ms median.
    await item.getByRole('button', { name: 'View as table' }).click()
    const table = item.getByRole('table')
    await expect(table).toContainText('2026-03-06')
    await expect(table.locator('td', { hasText: /^—$/ }).first()).toBeVisible()
  })

  test('the slowest tests are ranked, capped, and keep their run counts', async ({ page }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="slowest-tests"]')
    const rows = item.locator('[data-chart="slowest-tests"] li')
    // 24 tests in the fixture; the frame shows the top 20 (SLOWEST_TESTS_LIMIT)
    // and states that there were more.
    await expect(rows).toHaveCount(20)
    await expect(item.locator('[data-chart-truncation]')).toContainText('Showing the slowest 20 of 24 tests')

    // The run count is beside every bar: a p95 over 12 runs is not the same
    // claim as a p95 over 200, and ranking without it invites acting on noise.
    const runs = await rows.evaluateAll((nodes) =>
      nodes.map((node) => /(\d[\d,]*) runs?/.exec(node.textContent ?? '')?.[1] ?? null),
    )
    expect(runs.filter((value) => value === null), JSON.stringify(runs)).toEqual([])

    // Ranked: each bar is no longer than the one above it, and the longest
    // measured p95 sets the scale.
    const widths = await item
      .locator('[data-testid="ranked-bar"]')
      .evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().width))
    expect(widths).toHaveLength(20)
    expect(widths).toEqual([...widths].sort((a, b) => b - a))
    expect(widths[0]).toBeGreaterThan(0)
    // The 42-minute outlier dwarfs the rest, so the second bar is a sliver —
    // but a drawn one: a measured value never renders as nothing.
    expect(widths[19]).toBeGreaterThan(0)

    // The test whose p95 was never measured sorts LAST, so it is off the top 20
    // rather than ranking as the fastest test in the project.
    await expect(item).not.toContainText('search returns nothing for an unknown sku')
  })

  // ── What the first Linux baselines showed and no test had caught ────────────
  //
  // Every defect below was found by LOOKING at the CI screenshots. Each one
  // passed the text assertions above, because it is a question of where
  // things are drawn, not of what they say: so each is asserted here on
  // GEOMETRY — boxes, positions, clipping.

  /** The drawn box of each element's TEXT (a range, not the element's layout box). */
  async function textBoxes(locator: Locator) {
    return locator.evaluateAll((nodes) =>
      nodes.map((node) => {
        const range = document.createRange()
        range.selectNodeContents(node)
        const box = range.getBoundingClientRect()
        return { text: (node.textContent ?? '').trim(), top: box.top, bottom: box.bottom, left: box.left, right: box.right }
      }),
    )
  }

  type TextBox = Awaited<ReturnType<typeof textBoxes>>[number]

  /** Every pair of boxes that overlap by more than a hair (0.5 px, for anti-aliased edges). */
  function overlaps(boxes: TextBox[]): string[] {
    const hits: string[] = []
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i]
        const b = boxes[j]
        const x = Math.min(a.right, b.right) - Math.max(a.left, b.left)
        const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
        if (x > 0.5 && y > 0.5) hits.push(`"${a.text}" overlaps "${b.text}"`)
      }
    }
    return hits
  }

  /** A ladder step: 1, 2, 2.5 or 5 times a power of ten. */
  const isNiceStep = (step: number) => {
    if (!(step > 0)) return false
    const mantissa = step / 10 ** Math.floor(Math.log10(step))
    return [1, 2, 2.5, 5, 10].some((nice) => Math.abs(mantissa - nice) < 1e-9)
  }

  const tickNumber = (text: string) => Number(text.replace(/[,%\s]/g, ''))

  test('a 50-bar page gives every category label and every value label a row of its own', async ({ page }) => {
    await openGallery(page)
    for (const [id, bars] of [
      ['bar-paginated', 50],
      ['bar-ranked-ties', 13],
      ['bar-ranked', GALLERY_TOP_FAILING.length],
    ] as const) {
      const item = page.locator(`[data-gallery-item="${id}"]`)
      const categories = item.locator(`${CATEGORY_TICKS} .recharts-cartesian-axis-tick-value`)
      const values = item.locator('.recharts-label-list text')
      await expect.poll(() => categories.count(), { message: `${id}: category labels` }).toBe(bars)
      await expect.poll(() => values.count(), { message: `${id}: value labels` }).toBe(bars)
      expect(overlaps(await textBoxes(categories)), `${id}: category labels overlap`).toEqual([])
      expect(overlaps(await textBoxes(values)), `${id}: value labels overlap`).toEqual([])
    }
    // …and a chart that grew to fit its rows still fits in its gallery box: a
    // frame that spills over the next item corrupts that item's screenshot.
    for (const id of ['bar-paginated', 'bar-ranked-ties', 'bar-grouped']) {
      const canvas = await page.locator(`[data-gallery-canvas="${id}"]`).boundingBox()
      const frame = await page.locator(`[data-gallery-item="${id}"] [data-chart-frame]`).boundingBox()
      expect(frame && canvas, id).toBeTruthy()
      expect((frame?.y ?? 0) + (frame?.height ?? 0), `${id}: the frame spills out of its canvas`).toBeLessThanOrEqual(
        (canvas?.y ?? 0) + (canvas?.height ?? 0) + 1,
      )
    }
  })

  test('grouped bars are thick enough to carry their status pattern', async ({ page }) => {
    await openGallery(page)
    const heights = await page
      .locator('[data-gallery-item="bar-grouped"] .recharts-bar-rectangle path')
      .evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().height).filter((h) => h > 0))
    expect(heights.length).toBe(GALLERY_SUITE_STATUS.length * 4)
    // The status patterns tile at 6-8 px: a thinner bar shows a colour and a
    // stray pixel of pattern, so status falls back to colour alone.
    expect(Math.min(...heights), JSON.stringify(heights)).toBeGreaterThanOrEqual(8)
  })

  test('no text in a donut is clipped: every label is whole and inside its frame', async ({ page }) => {
    await openGallery(page)
    for (const id of ['donut-status', 'donut-status-unknown', 'donut-tiny-slice', 'donut-single-status', 'breakdown-four-categories']) {
      const frame = page.locator(`[data-gallery-item="${id}"] [data-chart-frame]`)
      await expect(frame.locator('[data-donut]'), id).toBeVisible()
      await expect.poll(() => drawnMarks(page, id)).toBeGreaterThanOrEqual(1)
      const clipped = await frame.evaluate(textEscapes)
      expect(clipped, id).toEqual([])
    }

    // …and the centre total is IN the centre: inside the ring's hole, not at
    // the svg's origin where the first baselines found it.
    const donut = page.locator('[data-gallery-item="donut-status"]')
    const ring = await donut.locator('.recharts-pie-sector').evaluateAll((nodes) => {
      const boxes = nodes.map((node) => node.getBoundingClientRect())
      const left = Math.min(...boxes.map((b) => b.left))
      const right = Math.max(...boxes.map((b) => b.right))
      const top = Math.min(...boxes.map((b) => b.top))
      const bottom = Math.max(...boxes.map((b) => b.bottom))
      return { x: (left + right) / 2, y: (top + bottom) / 2, radius: (right - left) / 2 }
    })
    const centre = await textBoxes(donut.locator('[data-donut-centre]'))
    expect(centre).toHaveLength(1)
    const middle = { x: (centre[0].left + centre[0].right) / 2, y: (centre[0].top + centre[0].bottom) / 2 }
    expect(Math.abs(middle.x - ring.x), 'the centre total is off-centre horizontally').toBeLessThan(3)
    expect(Math.abs(middle.y - ring.y), 'the centre total is off-centre vertically').toBeLessThan(6)
    expect(centre[0].right - centre[0].left).toBeLessThan(ring.radius)
  })

  test('a full-ring donut draws no seam: closed all the way round, with no edge stroked across it', async ({
    page,
  }) => {
    await openGallery(page)
    const single = page.locator('[data-gallery-item="donut-single-status"]')
    await expect(single.locator('[data-donut]')).toHaveAttribute('data-donut-full-ring', 'true')
    await expect.poll(() => drawnMarks(page, 'donut-single-status')).toBeGreaterThanOrEqual(1)
    const ring = await single.evaluate((section) => {
      const paths = Array.from(section.querySelectorAll<SVGPathElement>('.recharts-pie-sector path'))
      if (paths.length !== 1) return { sectors: paths.length, gaps: [] as number[], stroke: '', strokeWidth: '' }
      const path = paths[0]
      const box = path.getBBox()
      const cx = box.x + box.width / 2
      const cy = box.y + box.height / 2
      const outer = box.width / 2
      const filled = (x: number, y: number) => path.isPointInFill(new DOMPoint(x, y))
      // The hole's radius: walk out from the centre (straight up) to the first filled point.
      let inner = 0
      while (inner < outer && !filled(cx, cy - inner)) inner += 0.5
      const mid = (inner + outer) / 2
      // Every quarter degree round the ring's middle (off the exact 0 deg
      // where the sector's two edges meet): a padding gap is a run of
      // unfilled angles.
      const gaps: number[] = []
      for (let degrees = 0.125; degrees < 360; degrees += 0.25) {
        const radians = (degrees * Math.PI) / 180
        if (!filled(cx + mid * Math.cos(radians), cy - mid * Math.sin(radians))) gaps.push(degrees)
      }
      const style = getComputedStyle(path)
      return { sectors: 1, gaps, stroke: style.stroke, strokeWidth: style.strokeWidth }
    })
    expect(ring.sectors, 'one status is one sector').toBe(1)
    expect(ring.gaps, 'the ring has a gap at these angles').toEqual([])
    // A stroke outlines the sector's two radial edges, which meet at 3 o'clock:
    // on a ring with no neighbour to separate, that is the seam.
    expect(ring.stroke === 'none' || parseFloat(ring.strokeWidth) === 0, `stroke ${ring.stroke} ${ring.strokeWidth}`).toBe(
      true,
    )
  })

  test('the p50 and p95 lines are drawn like the band\'s edges, and never leave the band', async ({ page }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="duration-band"]')
    await expect(item.locator('[data-chart="duration-trend"]')).toHaveAttribute('data-inverted', '1')
    await expect(item.locator('.recharts-line-curve')).toHaveCount(2)
    // ONE interpolation, as drawn: the band's outline and both lines are
    // straight segments between the days (a path of M/L only). Curved lines
    // over a straight-edged band came apart from its edges around the
    // inverted day — and a curve is not bounded by the band at all.
    const curveCommands = await item.evaluate((section) =>
      Array.from(section.querySelectorAll<SVGPathElement>('.recharts-area-area, .recharts-area-curve, .recharts-line-curve'), (path) => ({
        mark: path.getAttribute('class') ?? '',
        curves: (path.getAttribute('d') ?? '').match(/[CcSsQqTtAa]/g)?.length ?? 0,
      })),
    )
    expect(curveCommands.length).toBeGreaterThanOrEqual(3)
    for (const { mark, curves } of curveCommands) expect(curves, `${mark} is curved`).toBe(0)
    const found = await item.evaluate((section) => {
      const band = section.querySelector<SVGPathElement>('.recharts-area-area')
      const lines = Array.from(section.querySelectorAll<SVGPathElement>('.recharts-line-curve'))
      if (!band) return { band: false, samples: 0, escapes: [] as string[] }
      // Within a stroke's width of the band counts as on it: at each day the
      // line IS the band's edge.
      const near = (x: number, y: number) =>
        [0, -1.5, 1.5].some((dy) => band.isPointInFill(new DOMPoint(x, y + dy)))
      const escapes: string[] = []
      let samples = 0
      lines.forEach((line, index) => {
        const total = line.getTotalLength()
        for (let at = 0; at <= total; at += 2) {
          const point = line.getPointAtLength(at)
          samples += 1
          if (!near(point.x, point.y)) escapes.push(`line ${index} at (${point.x.toFixed(1)}, ${point.y.toFixed(1)})`)
        }
      })
      return { band: true, samples, escapes }
    })
    expect(found.band, 'no band drawn').toBe(true)
    expect(found.samples).toBeGreaterThan(100)
    expect(found.escapes.slice(0, 10), `${found.escapes.length} points outside the band`).toEqual([])
  })

  test('the p95-below-p50 notice is said once', async ({ page }) => {
    await openGallery(page)
    const item = page.locator('[data-gallery-item="duration-band"]')
    await expect(item.getByText('p95 was below p50 on 1 day; both are drawn as reported.', { exact: true })).toHaveCount(1)
  })

  test('a histogram explains its buckets only when it draws them', async ({ page }) => {
    await openGallery(page)
    const caption = /Buckets are log-spaced/
    await expect(page.locator('[data-gallery-item="duration-histogram"]').getByText(caption)).toHaveCount(1)
    await expect(page.locator('[data-gallery-item="duration-histogram-empty"]').getByText(caption)).toHaveCount(0)
  })

  test('every zoomed rate-axis label names the value its tick is drawn at', async ({ page }) => {
    await openGallery(page)
    for (const id of ['timeseries-zoomed-axis', 'timeseries-trend-releases']) {
      const item = page.locator(`[data-gallery-item="${id}"]`)
      const svg = await item.locator(CHART_SVG).first().boundingBox()
      const all = await textBoxes(item.locator('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value'))
      // Two y axes share the class: the rate axis is the LEFT one.
      const rate = all
        .filter((tick) => tick.right < (svg?.x ?? 0) + (svg?.width ?? 0) / 2)
        .map((tick) => ({ label: tick.text, value: tickNumber(tick.text), y: (tick.top + tick.bottom) / 2 }))
        .sort((a, b) => b.y - a.y)
      expect(rate.length, `${id}: rate ticks`).toBeGreaterThanOrEqual(3)
      const first = rate[0]
      const last = rate[rate.length - 1]
      for (const tick of rate) {
        // Where the tick IS, in axis units, from the two end ticks…
        const drawnAt = first.value + ((first.y - tick.y) / (first.y - last.y)) * (last.value - first.value)
        // …must be what the label SAYS, to the precision the label is printed at.
        const places = tick.label.split('.')[1]?.length ?? 0
        expect(Math.abs(drawnAt - tick.value), `${id}: "${tick.label}" is drawn at ${drawnAt.toFixed(3)}`).toBeLessThanOrEqual(
          0.5 * 10 ** -places + 0.05,
        )
      }
      const steps = rate.slice(1).map((tick, i) => tick.value - rate[i].value)
      expect(new Set(steps.map((step) => step.toFixed(6))).size, `${id}: uneven rate ticks ${JSON.stringify(steps)}`).toBe(1)
    }
  })

  test('the executions axis reaches the tallest execution bar', async ({ page }) => {
    await openGallery(page)
    for (const id of ['timeseries-zoomed-axis', 'timeseries-trend-releases', 'timeseries-single-point']) {
      const item = page.locator(`[data-gallery-item="${id}"]`)
      const plot = item.locator('[data-time-series-plot]')
      const largest = Number(await plot.getAttribute('data-executions-max'))
      expect(largest, `${id}: data-executions-max`).toBeGreaterThan(0)
      const [, axisMax] = ((await plot.getAttribute('data-executions-domain')) ?? '').split(',').map(Number)
      expect(axisMax, `${id}: the executions axis ends below the largest value`).toBeGreaterThanOrEqual(largest)
      // The drawn axis agrees: its top tick label is that max…
      const svg = await item.locator(CHART_SVG).first().boundingBox()
      const right = (await textBoxes(item.locator('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value')))
        .filter((tick) => tick.left > (svg?.x ?? 0) + (svg?.width ?? 0) / 2)
        .map((tick) => tickNumber(tick.text))
      expect(Math.max(...right), `${id}: right-axis ticks ${JSON.stringify(right)}`).toBe(axisMax)
      // …and no bar is cut off at the top of the plot.
      const gridTop = await item
        .locator('.recharts-cartesian-grid-horizontal line')
        .evaluateAll((lines) => Math.min(...lines.map((line) => line.getBoundingClientRect().top)))
      const barTops = await item
        .locator('.recharts-bar-rectangle path')
        .evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().top))
      expect(Math.min(...barTops), `${id}: a bar is clipped at the top`).toBeGreaterThanOrEqual(gridTop - 1)
    }
  })

  test('a time series draws a grid line at every tick, and both axes tick on it', async ({ page }) => {
    await openGallery(page)
    for (const id of ['timeseries-zoomed-axis', 'timeseries-trend-releases', 'timeseries-single-point']) {
      const item = page.locator(`[data-gallery-item="${id}"]`)
      const lines = await item
        .locator('.recharts-cartesian-grid-horizontal line')
        .evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().top))
      const ticks = await textBoxes(item.locator('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value'))
      // Five rate ticks and five executions ticks, one grid line under each pair.
      expect(lines.length, `${id}: grid lines ${JSON.stringify(lines)}`).toBe(ticks.length / 2)
      for (const tick of ticks) {
        const centre = (tick.top + tick.bottom) / 2
        const nearest = Math.min(...lines.map((line) => Math.abs(line - centre)))
        expect(nearest, `${id}: tick "${tick.text}" sits off the grid`).toBeLessThan(1.5)
      }
    }
  })

  test('bar value axes end on a nice number past the data, with evenly spaced ticks', async ({ page }) => {
    await openGallery(page)
    const totals = GALLERY_SUITE_STATUS.map(([, counts]) => Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0))
    const segments = GALLERY_SUITE_STATUS.flatMap(([, counts]) => Object.values(counts).map((n) => n ?? 0))
    const cases: [string, number][] = [
      ['bar-ranked', Math.max(...GALLERY_TOP_FAILING.map(([, v]) => v))],
      ['bar-ranked-ties', Math.max(...GALLERY_TOP_FAILING.map(([, v]) => v))],
      ['bar-long-names', Math.max(...GALLERY_LONG_NAMES.map(([, v]) => v))],
      ['bar-hostile-label', 9],
      ['bar-paginated', 300],
      ['breakdown-six-categories', 46],
      ['bar-stacked', Math.max(...totals)],
      ['bar-grouped', Math.max(...segments)],
      ['bar-stacked-100', 100],
    ]
    for (const [id, dataMax] of cases) {
      const item = page.locator(`[data-gallery-item="${id}"]`)
      const ticks = item.locator(`${VALUE_TICKS} .recharts-cartesian-axis-tick-value`)
      await expect.poll(() => ticks.count(), { message: id }).toBeGreaterThanOrEqual(3)
      const boxes = (await textBoxes(ticks)).sort((a, b) => a.left - b.left)
      const values = boxes.map((box) => tickNumber(box.text))
      const steps = values.slice(1).map((value, i) => value - values[i])
      expect(values[0], `${id}: ${JSON.stringify(values)}`).toBe(0)
      expect(values[values.length - 1], `${id}: the axis ends below the data`).toBeGreaterThanOrEqual(dataMax)
      expect(new Set(steps).size, `${id}: uneven ticks ${JSON.stringify(values)}`).toBe(1)
      expect(isNiceStep(steps[0]), `${id}: step ${steps[0]}`).toBe(true)
      // Evenly spaced on screen too, not only in value.
      const centres = boxes.map((box) => (box.left + box.right) / 2)
      const gaps = centres.slice(1).map((centre, i) => centre - centres[i])
      expect(Math.max(...gaps) - Math.min(...gaps), `${id}: tick gaps ${JSON.stringify(gaps)}`).toBeLessThan(1.5)
    }

    // The change chart stays symmetric about zero, and nice on both sides.
    const diverging = page.locator('[data-gallery-item="bar-diverging"]')
    const values = (await textBoxes(diverging.locator(`${VALUE_TICKS} .recharts-cartesian-axis-tick-value`)))
      .sort((a, b) => a.left - b.left)
      .map((box) => tickNumber(box.text))
    expect(values[0]).toBe(-values[values.length - 1])
    expect(values).toContain(0)
    expect(values[values.length - 1]).toBeGreaterThanOrEqual(Math.max(...GALLERY_CHANGE_BARS.map(([, v]) => Math.abs(v))))
    const steps = values.slice(1).map((value, i) => value - values[i])
    expect(new Set(steps).size, JSON.stringify(values)).toBe(1)
    expect(isNiceStep(steps[0])).toBe(true)
  })

  // ── Wave 2 · the multi-series comparison (VIZ-404) ───────────────────────────

  const MULTI_SERIES_ITEMS = GALLERY_ITEMS.filter((item) => item.chart === 'multi-series')
  const multiItem = (page: Page, id: string) => page.locator(`[data-gallery-item="${id}"]`)
  /** The drawn line paths of one item (Recharts: one `.recharts-line-curve` per line). */
  const linePaths = (page: Page, id: string) => multiItem(page, id).locator(`${CHART_SVG} path.recharts-line-curve`)

  test('VIZ-404: every edge case is in the gallery, and each draws its lines', async ({ page }) => {
    expect(MULTI_SERIES_ITEMS.map((item) => item.id)).toEqual([
      'multi-series-three-suites',
      'multi-series-folded',
      'multi-series-gaps',
      'multi-series-not-comparable',
      'multi-series-release-aligned',
      'multi-series-hidden',
    ])
    await openGallery(page)
    for (const item of MULTI_SERIES_ITEMS) {
      await expect(linePaths(page, item.id), item.id).toHaveCount(item.minMarks)
      // Told apart by DASH as well as colour: no two drawn lines share a pattern.
      const dashes = await linePaths(page, item.id).evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute('stroke-dasharray') ?? 'solid'),
      )
      expect(new Set(dashes).size, `${item.id}: ${dashes.join(' | ')}`).toBe(dashes.length)
    }
    // 12 suites: 7 kept + "Other", with a notice naming what was folded.
    const folded = multiItem(page, 'multi-series-folded')
    await expect(folded.locator('[data-legend-series]')).toHaveCount(8)
    await expect(folded.locator('[data-legend-series="__other__"]')).toHaveText('Other')
    await expect(folded.locator('[data-chart-fold-notice]')).toHaveText(
      '12 suites: the 7 with the most executions are drawn, and the other 5 are folded into "Other".',
    )
    // comparable:false → a banner with the reason, and the comparison still drawn.
    const branches = multiItem(page, 'multi-series-not-comparable')
    await expect(branches.locator('[data-chart-comparable-banner]')).toContainText(GALLERY_NOT_COMPARABLE_REASON)
    await expect(linePaths(page, 'multi-series-not-comparable')).toHaveCount(2)
    // Gaps, not zeros: counted in words, and the isolated measured days are dots.
    const gaps = multiItem(page, 'multi-series-gaps')
    await expect(gaps.locator('[data-chart-gap-note]')).toContainText('never as 0')
    await expect.poll(() => gaps.locator(`${CHART_SVG} .recharts-line-dots circle`).count()).toBeGreaterThan(0)
  })

  test('VIZ-404: direct labels never overlap, and each sits inside its svg', async ({ page }) => {
    await openGallery(page)
    for (const item of MULTI_SERIES_ITEMS) {
      const section = multiItem(page, item.id)
      const labels = section.locator('[data-direct-label] text')
      // One label per drawn line: a hidden series' label goes with it.
      await expect(labels, item.id).toHaveCount(item.minMarks)
      const boxes = await textBoxes(labels)
      expect(overlaps(boxes), `${item.id}: direct labels overlap`).toEqual([])
      const svg = await section.locator(CHART_SVG).first().boundingBox()
      if (!svg) throw new Error(`${item.id}: no svg`)
      for (const box of boxes) {
        expect(box.right, `${item.id}: "${box.text}" is cut off on the right`).toBeLessThanOrEqual(svg.x + svg.width + 0.5)
        expect(box.top, `${item.id}: "${box.text}" is above the svg`).toBeGreaterThanOrEqual(svg.y - 0.5)
        expect(box.bottom, `${item.id}: "${box.text}" is below the svg`).toBeLessThanOrEqual(svg.y + svg.height + 0.5)
      }
    }
    // The three-suites fixture ENDS payments and cart half a point apart: their
    // labels were nudged, not stacked, and the hidden item draws no cart label.
    await expect(multiItem(page, 'multi-series-hidden').locator('[data-direct-label="cart"]')).toHaveCount(0)
  })

  test('VIZ-404: the shared tooltip lists every series for the day, sorted descending, unmeasured last', async ({ page }) => {
    await openGallery(page)
    const section = multiItem(page, 'multi-series-gaps')
    await section.scrollIntoViewIfNeeded()
    // The plot's box, from its horizontal grid lines (the first is the 0 line, at the BOTTOM).
    const grid = await section.locator('.recharts-cartesian-grid-horizontal line').evaluateAll((nodes) => {
      const boxes = nodes.map((node) => node.getBoundingClientRect())
      return { x: boxes[0].x, width: boxes[0].width, top: Math.min(...boxes.map((b) => b.y)), bottom: Math.max(...boxes.map((b) => b.y)) }
    })
    // Day 3 of 14: search was not measured that day. Two moves: Recharts reacts
    // to movement INTO the plot, not to a pointer that is simply placed there.
    const at = { x: grid.x + (grid.width * 3) / 13, y: (grid.top + grid.bottom) / 2 }
    await page.mouse.move(at.x - 20, at.y - 20)
    await page.mouse.move(at.x, at.y)
    const tip = section.locator('[data-chart-tooltip]')
    await expect(tip).toBeVisible()
    const day = (await tip.locator('.font-semibold').first().textContent())?.trim() ?? ''
    const rows = await tip.locator('[data-tip-series]').evaluateAll((nodes) =>
      nodes.map((node) => ({
        key: node.getAttribute('data-tip-series') ?? '',
        value: node.querySelector('[data-tip-value]')?.textContent ?? '',
      })),
    )
    // The expected order is computed from the fixture, not copied from the page.
    const expected = GALLERY_GAPPY_SUITES.map((series) => ({
      key: series.key,
      y: series.points.find((point) => point.x === day)?.y ?? null,
    }))
    const measured = expected.filter((e) => e.y !== null).sort((a, b) => (b.y as number) - (a.y as number))
    const unmeasured = expected.filter((e) => e.y === null)
    expect(unmeasured.length, `day ${day} should have an unmeasured series`).toBeGreaterThan(0)
    expect(rows.map((row) => row.key)).toEqual([...measured, ...unmeasured].map((e) => e.key))
    expect(rows[rows.length - 1].value).toBe('—')
    await expect(tip.locator('[data-tip-reason-visible="search"]')).toContainText('every test was skipped')
  })

  test('VIZ-404: the keyboard reads each day through the ONE announcer, sorted the same way', async ({ page }) => {
    await openGallery(page)
    const surface = multiItem(page, 'multi-series-three-suites').locator('[data-multi-series-plot]')
    await expect(surface).toHaveAttribute('role', 'group')
    await expect(surface).toHaveAttribute('aria-label', /Use the arrow keys/)
    await surface.focus()
    await page.keyboard.press('End')
    const last = GALLERY_THREE_SUITES.map((s) => ({ key: s.key, y: s.points[s.points.length - 1].y as number })).sort(
      (a, b) => b.y - a.y,
    )
    const announcer = page.locator('[data-chart-announcer="assertive"]')
    const lastDay = GALLERY_THREE_SUITES[0].points[GALLERY_THREE_SUITES[0].points.length - 1].x
    await expect(announcer).toContainText(lastDay)
    const text = (await announcer.textContent()) ?? ''
    const positions = last.map((s) => text.indexOf(`${s.key} `))
    expect(positions.every((p) => p >= 0), text).toBe(true)
    expect([...positions].sort((a, b) => a - b), `announced out of order: ${text}`).toEqual(positions)
    await expect(multiItem(page, 'multi-series-three-suites').locator('[data-chart-readout]')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(multiItem(page, 'multi-series-three-suites').locator('[data-chart-readout]')).toHaveCount(0)
    await expect(surface).toBeFocused()
  })

  test('VIZ-404: the legend toggles by keyboard, the change is announced, and the table reflects it', async ({ page }) => {
    await openGallery(page)
    const id = 'multi-series-three-suites'
    const title = GALLERY_ITEMS.find((item) => item.id === id)?.title ?? ''
    const section = multiItem(page, id)
    const polite = page.locator('[data-chart-announcer="polite"]')

    const cart = section.locator('[data-legend-series="cart"]')
    await expect(cart).toHaveAttribute('aria-pressed', 'true')
    await cart.focus()
    await page.keyboard.press('Enter')
    await expect(cart).toHaveAttribute('aria-pressed', 'false')
    await expect(linePaths(page, id)).toHaveCount(2)
    await expect(section.locator('[data-direct-label="cart"]')).toHaveCount(0)
    await expect(polite).toHaveText(`${title} chart: cart hidden, 2 of 3 series shown`)

    // The table KEEPS cart, marked hidden.
    await section.getByRole('button', { name: 'View as table' }).click()
    const table = section.getByRole('table')
    await expect(table.getByRole('columnheader', { name: `cart${HIDDEN_SUFFIX}` })).toBeVisible()
    await expect(table.getByRole('columnheader')).toHaveCount(4)

    // Shift+Enter shows one series alone; the SAME gesture undoes it.
    const search = section.locator('[data-legend-series="search"]')
    await search.focus()
    await page.keyboard.press('Shift+Enter')
    await expect(linePaths(page, id)).toHaveCount(1)
    await expect(polite).toHaveText(`${title} chart: only search shown, 1 of 3 series shown`)
    await expect(table.getByRole('columnheader', { name: `payments${HIDDEN_SUFFIX}` })).toBeVisible()
    await page.keyboard.press('Shift+Enter')
    await expect(linePaths(page, id)).toHaveCount(3)
    await expect(polite).toHaveText(`${title} chart: all 3 series shown`)
  })

  test('VIZ-404: release over release starts both lines at day 0 on a relative axis', async ({ page }) => {
    await openGallery(page)
    const section = multiItem(page, 'multi-series-release-aligned')
    // Recharts 3 draws axis titles in a z-index layer of their own, outside `.recharts-xAxis`.
    await expect(section.locator(`${CHART_SVG} .recharts-label`, { hasText: ALIGNED_X_TITLE })).toBeVisible()
    await expect(section.locator(`${VALUE_TICKS} .recharts-cartesian-axis-tick-value`).first()).toHaveText('0')
    // Both paths begin at the same x: day 0 of each release.
    const starts = await linePaths(page, 'multi-series-release-aligned').evaluateAll((nodes) =>
      nodes.map((node) => Number(/^M\s*([\d.]+)/.exec(node.getAttribute('d') ?? '')?.[1])),
    )
    expect(starts).toHaveLength(2)
    expect(Math.abs(starts[0] - starts[1])).toBeLessThan(0.5)
    // The table names the relative day AND each release's absolute date.
    await section.getByRole('button', { name: 'View as table' }).click()
    await expect(section.getByRole('rowheader').first()).toHaveText('Day 0 (R1 2026-02-02; R2 2026-02-20)')
  })

  test('VIZ-404: no text escapes its frame, and every gallery box contains its frame', async ({ page }) => {
    await openGallery(page)
    for (const item of MULTI_SERIES_ITEMS) {
      const frame = multiItem(page, item.id).locator('[data-chart-frame]')
      await expect.poll(() => linePaths(page, item.id).count(), { message: item.id }).toBe(item.minMarks)
      const problems = await frame.evaluate(textEscapes)
      expect(problems, item.id).toEqual([])
      const canvas = await page.locator(`[data-gallery-canvas="${item.id}"]`).boundingBox()
      const box = await frame.boundingBox()
      expect(canvas && box, item.id).toBeTruthy()
      expect((box?.y ?? 0) + (box?.height ?? 0), `${item.id}: the frame spills out of its gallery box`).toBeLessThanOrEqual(
        (canvas?.y ?? 0) + (canvas?.height ?? 0) + 1,
      )
    }
  })

  // ── VIZ-404 fix round A: the review's accessibility findings, in geometry ────

  /** The plot area of one item: the box of its horizontal grid lines. */
  async function plotBox(section: Locator) {
    return section.locator('.recharts-cartesian-grid-horizontal line').evaluateAll((nodes) => {
      const boxes = nodes.map((node) => node.getBoundingClientRect())
      const left = Math.min(...boxes.map((b) => b.left))
      const right = Math.max(...boxes.map((b) => b.right))
      return { left, right, width: right - left, top: Math.min(...boxes.map((b) => b.top)), bottom: Math.max(...boxes.map((b) => b.bottom)) }
    })
  }

  /**
   * Every screen point the focused day draws: where each line crosses the
   * cursor's reference line, and any dot on it. Sampled off the real paths, so
   * a readout laid over the plot shows up as a point inside its box.
   */
  async function focusedDayPoints(section: Locator) {
    return section.evaluate((root) => {
      const focus = root.querySelector('.recharts-reference-line line')
      if (!focus) return { x: null as number | null, points: [] as { x: number; y: number }[] }
      const fx = focus.getBoundingClientRect().left
      const points: { x: number; y: number }[] = []
      for (const path of Array.from(root.querySelectorAll<SVGPathElement>('path.recharts-line-curve'))) {
        const m = path.getScreenCTM()
        if (!m) continue
        const length = path.getTotalLength()
        for (let d = 0; d <= length; d += 0.5) {
          const p = path.getPointAtLength(d)
          const x = m.a * p.x + m.c * p.y + m.e
          const y = m.b * p.x + m.d * p.y + m.f
          if (Math.abs(x - fx) < 1) points.push({ x, y })
        }
      }
      for (const dot of Array.from(root.querySelectorAll('.recharts-line-dots circle'))) {
        const box = dot.getBoundingClientRect()
        if (Math.abs(box.left + box.width / 2 - fx) < 1) points.push({ x: box.left + box.width / 2, y: box.top + box.height / 2 })
      }
      return { x: fx, points }
    })
  }

  /** Text that does not fit its box: the element scrolls, or an ellipsis cuts it. */
  const clippedText = (root: Element) =>
    [root, ...Array.from(root.querySelectorAll('*'))]
      .filter((node) => {
        const style = getComputedStyle(node)
        const scrolls = node.scrollWidth > node.clientWidth + 1 && node.clientWidth > 0
        return scrolls || (style.textOverflow === 'ellipsis' && style.overflow !== 'visible')
      })
      .map((node) => `${node.tagName.toLowerCase()} "${(node.textContent ?? '').slice(0, 40)}" ${node.scrollWidth}>${node.clientWidth}`)

  for (const [label, viewport, search] of [
    ['640 px', { width: 1280, height: 900 }, ''],
    ['320 px', { width: 320, height: 900 }, `?canvas=${GALLERY_FLUID_CANVAS_PARAM}`],
  ] as const) {
    test(`VIZ-404 fix A (M2): the keyboard readout wraps whole, OUTSIDE the plot, at ${label}`, async ({ page }) => {
      await page.setViewportSize(viewport)
      await openGallery(page, search)
      for (const [id, steps] of [
        // Day 3: search is unmeasured, so the readout carries its reason too.
        ['multi-series-gaps', 3],
        // Eight rows: the tallest readout.
        ['multi-series-folded', 6],
      ] as const) {
        const section = multiItem(page, id)
        await expect.poll(() => linePaths(page, id).count()).toBeGreaterThan(0)
        const surface = section.locator('[data-multi-series-plot]')
        await surface.focus()
        await page.keyboard.press('Home')
        for (let i = 0; i < steps; i++) await page.keyboard.press('ArrowRight')
        const readout = section.locator('[data-chart-readout]')
        await expect(readout).toBeVisible()
        expect(await readout.evaluate(clippedText), `${id}: readout text is cut off`).toEqual([])
        const box = await readout.boundingBox()
        if (!box) throw new Error(`${id}: no readout box`)
        const frame = await section.locator('[data-chart-frame]').boundingBox()
        expect(box.x + box.width, `${id}: the readout runs past its frame`).toBeLessThanOrEqual((frame?.x ?? 0) + (frame?.width ?? 0) + 0.5)
        // It hides none of the day it describes…
        const { x, points } = await focusedDayPoints(section)
        expect(x, `${id}: no focus line`).not.toBeNull()
        expect(points.length, `${id}: the focused day drew no points`).toBeGreaterThan(0)
        const covered = points.filter((p) => p.x >= box.x && p.x <= box.x + box.width && p.y >= box.y && p.y <= box.y + box.height)
        expect(covered, `${id}: the readout covers the focused day's points`).toEqual([])
        // …because it is not over the plot at all.
        const plot = await plotBox(section)
        expect(box.y, `${id}: the readout overlaps the plot`).toBeGreaterThanOrEqual(plot.bottom)
        await page.keyboard.press('Escape')
      }

      // The shared `useChartCursor` readout (every other Wave-2 chart) wraps too:
      // the time series' last day carries its local-time equivalent.
      const series = page.locator('[data-gallery-item="timeseries-trend-releases"]')
      await series.locator('[data-time-series-plot]').focus()
      await page.keyboard.press('End')
      const shared = series.locator('[data-chart-readout]')
      await expect(shared).toBeVisible()
      expect(await shared.evaluate(clippedText), 'the shared readout is cut off').toEqual([])
    })
  }

  test('VIZ-404 fix A (M8): the pointer tooltip is pinned to its DAY — moving onto it, it stays put and open', async ({ page }) => {
    await openGallery(page)
    const id = 'multi-series-three-suites'
    const section = multiItem(page, id)
    await section.scrollIntoViewIfNeeded()
    const plot = await plotBox(section)
    const days = GALLERY_THREE_SUITES[0].points.length
    const step = plot.width / (days - 1)
    const dayX = plot.left + step * 5
    const tip = section.locator('[data-chart-tooltip]')

    // Onto day 5 from below: the tooltip sits at the TOP of the plot, and a
    // pointer that arrives on it holds whatever day it names.
    await page.mouse.move(dayX - 4, plot.bottom - 10)
    await page.mouse.move(dayX, plot.bottom - 10)
    await expect(tip).toBeVisible()
    const pinned = await tip.boundingBox()
    if (!pinned) throw new Error('no tooltip box')
    const same = (box: { x: number; y: number } | null) =>
      box !== null && Math.abs(box.x - pinned.x) < 0.5 && Math.abs(box.y - pinned.y) < 0.5
    // A fixed offset from the day's x, beside it — not over it.
    expect(pinned.x > dayX || pinned.x + pinned.width < dayX, 'the tooltip covers its own day').toBe(true)

    // Anywhere on the same day, the pointer's own position never moves it.
    for (const up of [40, 90, 140, 180]) {
      await page.mouse.move(dayX + 2, plot.bottom - up)
      expect(same(await tip.boundingBox()), `the tooltip moved with the pointer, ${up}px up`).toBe(true)
    }

    // Now walk straight onto it, a pixel at a time, at its own height.
    const y = pinned.y + Math.min(pinned.height / 2, 20)
    await page.mouse.move(dayX, y)
    const right = pinned.x > dayX
    const target = right ? pinned.x + 16 : pinned.x + pinned.width - 16
    for (let x = dayX; right ? x <= target : x >= target; x += right ? 1 : -1) {
      await page.mouse.move(x, y)
      expect(await tip.isVisible(), `the tooltip closed at x=${x}`).toBe(true)
      expect(same(await tip.boundingBox()), `the tooltip moved at x=${x}`).toBe(true)
    }
    const onTip = await page.evaluate(({ x, y }) => !!document.elementFromPoint(x, y)?.closest('[data-chart-tooltip]'), { x: target, y })
    expect(onTip, 'the pointer is on the tooltip').toBe(true)
    // …and moving about ON it keeps it — the day it names does not change under the pointer.
    const title = await tip.locator('.font-semibold').first().textContent()
    await page.mouse.move(target + (right ? 30 : -30), y + 4)
    expect(same(await tip.boundingBox())).toBe(true)
    await expect(tip.locator('.font-semibold').first()).toHaveText(title ?? '')
  })

  test('VIZ-404 fix A (M6): at 320 px the line-end labels give way to the legend, and the plot gets the room', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 900 })
    await openGallery(page, `?canvas=${GALLERY_FLUID_CANVAS_PARAM}`)
    for (const item of MULTI_SERIES_ITEMS) {
      const section = multiItem(page, item.id)
      await expect.poll(() => linePaths(page, item.id).count(), { message: item.id }).toBe(item.minMarks)
      await expect(section.locator('[data-chart-labels-note]'), item.id).toBeVisible()
      await expect(section.locator('[data-direct-label]'), item.id).toHaveCount(0)
      const svg = await section.locator(CHART_SVG).first().boundingBox()
      const plot = await plotBox(section)
      if (!svg) throw new Error(`${item.id}: no svg`)
      // No 104 px gutter held open for labels that are not drawn.
      expect(svg.x + svg.width - plot.right, `${item.id}: gutter`).toBeLessThanOrEqual(17)
      expect(plot.width, `${item.id}: plot width`).toBeGreaterThan(120)
    }
    // …while the 640 px canvas keeps them.
    await page.setViewportSize({ width: 1280, height: 900 })
    await openGallery(page)
    await expect(multiItem(page, 'multi-series-three-suites').locator('[data-direct-label]')).toHaveCount(3)
  })

  test('VIZ-404 fix A (M7, m4): "Show all series" hands focus to the legend; toggles are 24 px and the group is named', async ({ page }) => {
    await openGallery(page)
    const id = 'multi-series-three-suites'
    const title = GALLERY_ITEMS.find((item) => item.id === id)?.title ?? ''
    const section = multiItem(page, id)
    // The legend group says WHICH chart's series these are.
    await expect(section.getByRole('group', { name: `Series shown on ${title}`, exact: true })).toHaveAttribute(
      'data-multi-series-legend',
      '',
    )
    for (const box of await section.locator('[data-legend-series]').evaluateAll((nodes) => nodes.map((n) => n.getBoundingClientRect().height))) {
      expect(box).toBeGreaterThanOrEqual(24)
    }
    for (const length of await section.locator('[data-multi-series-legend] [data-series-swatch]').evaluateAll((nodes) =>
      nodes.map((n) => n.getBoundingClientRect().width),
    )) {
      expect(length).toBeGreaterThanOrEqual(32)
    }
    await section.locator('[data-legend-series="cart"]').click()
    const showAll = section.locator('[data-legend-show-all]')
    await showAll.focus()
    await page.keyboard.press('Enter')
    await expect(showAll).toHaveCount(0)
    await expect(section.locator('[data-legend-series="payments"]')).toBeFocused()
  })

  /** WCAG contrast of two computed `rgb()` colours. */
  const contrastOf = (a: string, b: string) => {
    const lum = (rgb: string) => {
      const [r, g, bl] = (rgb.match(/[\d.]+/g) ?? []).slice(0, 3).map((v) => {
        const s = Number(v) / 255
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
      })
      return 0.2126 * r + 0.7152 * g + 0.0722 * bl
    }
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
    return (hi + 0.05) / (lo + 0.05)
  }

  for (const theme of ALL_THEMES) {
    test(`VIZ-404 fix A (M1): every multi-series table scrolls by keyboard, and axe passes with them all open (${theme})`, async ({
      page,
    }) => {
      await openGallery(page, `?theme=${theme}`)
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      let scrolled = 0
      for (const item of MULTI_SERIES_ITEMS) {
        const section = multiItem(page, item.id)
        await expect.poll(() => linePaths(page, item.id).count(), { message: item.id }).toBe(item.minMarks)
        await section.getByRole('button', { name: 'View as table' }).click()
        const region = section.getByRole('region', { name: `${item.title} — data table` })
        await expect(region).toHaveAttribute('tabindex', '0')
        await region.focus()
        await expect(region).toBeFocused()
        const scrolls = await region.evaluate((el) => el.scrollHeight > el.clientHeight + 1)
        if (scrolls) {
          scrolled += 1
          await page.keyboard.press('End')
          await expect
            .poll(() => region.evaluate((el) => el.scrollTop + el.clientHeight >= el.scrollHeight - 1), { message: item.id })
            .toBe(true)
          // The latest day — the row the review found hidden — is on screen.
          const rows = await region.evaluate((el) => {
            const box = el.getBoundingClientRect()
            const last = el.querySelector('tbody tr:last-child')?.getBoundingClientRect()
            return last ? { lastBottom: last.bottom, boxBottom: box.bottom } : null
          })
          expect(rows && rows.lastBottom <= rows.boxBottom + 1, `${item.id}: last row still hidden`).toBe(true)
        }
      }
      expect(scrolled, 'no multi-series table is long enough to scroll: the test proves nothing').toBeGreaterThan(0)

      // m3: the not-comparable banner's border is a real boundary (3:1) against both sides.
      const banner = multiItem(page, 'multi-series-not-comparable').locator('[data-chart-comparable-banner]')
      await expect(banner).toHaveAttribute('role', 'note')
      const colours = await banner.evaluate((el) => {
        const frame = el.closest('[data-chart-frame]') as HTMLElement
        return { border: getComputedStyle(el).borderLeftColor, inside: getComputedStyle(el).backgroundColor, outside: getComputedStyle(frame).backgroundColor }
      })
      expect(contrastOf(colours.border, colours.inside), `banner border vs its fill (${theme})`).toBeGreaterThanOrEqual(3)
      expect(contrastOf(colours.border, colours.outside), `banner border vs the card (${theme})`).toBeGreaterThanOrEqual(3)

      // No allowlist: with every table open, nothing fires.
      await expectNoBlockingViolations(
        page,
        theme,
        [],
        MULTI_SERIES_ITEMS.map((item) => `[data-gallery-item="${item.id}"]`),
      )
    })
  }

  // ── Chart frame states (VIZ-107) and the accessible-chart baseline (VIZ-105) ──

  const STATES = '?view=states'
  const stateFrame = (page: Page, id: string) => page.locator(`[data-state-item="${id}"] [data-chart-frame]`)

  const paintedIn = (page: Page, id: string) =>
    paintedColoursIn(stateFrame(page, id).locator('[data-chart-engine="echarts"]').first())

  async function openStates(page: Page, theme?: string) {
    await openGallery(page, theme ? `${STATES}&theme=${theme}` : STATES)
    await expect(page.getByTestId('chart-gallery')).toHaveAttribute('data-gallery-view', 'states')
    // The drawn frames are what axe and the keyboard test depend on: wait for real engines.
    for (const id of ['ready', 'truncated']) {
      await expect(stateFrame(page, id).locator('[data-chart-engine="echarts"]')).toHaveAttribute(
        'data-chart-status',
        'ready',
      )
    }
  }

  test('every chart state renders its own message, and only drawn states draw', async ({ page }) => {
    await openStates(page)
    const ids = await page
      .locator('[data-state-item]')
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-state-item')))
    // The count is the point: a states page that renders nothing must fail.
    expect(STATE_ITEM_IDS.length).toBeGreaterThanOrEqual(8)
    expect(ids).toEqual(STATE_ITEM_IDS)

    for (const item of STATE_ITEMS) {
      const frame = stateFrame(page, item.id)
      await expect(frame, item.id).toHaveAttribute('data-chart-state', item.state)
      // The title is a real heading, and it names the chart body.
      const heading = frame.getByRole('heading', { level: 2, name: item.title })
      await expect(heading).toBeVisible()
      const body = frame.locator('[data-chart-body]')
      await expect(body).toHaveAttribute('role', 'group')
      expect(await body.getAttribute('aria-labelledby')).toBe(await heading.getAttribute('id'))
      for (const text of item.expectText) await expect(frame, `${item.id}: "${text}"`).toContainText(text)
      await expect(frame.locator('[data-chart-engine="echarts"]'), `${item.id}: drawn?`).toHaveCount(item.draws ? 1 : 0)
    }

    // loading holds the chart's space with a chart-shaped skeleton.
    const skeleton = stateFrame(page, 'loading').locator('[data-skeleton="chart"]')
    await expect(skeleton).toBeVisible()
    expect((await skeleton.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(270)

    // not-measured is "—" plus the server's reason — never a zero.
    const notMeasured = stateFrame(page, 'not-measured')
    await expect(notMeasured.locator('[data-chart-value]')).toHaveText('—')
    await expect(notMeasured.locator('[data-chart-reason]')).toHaveText(NOT_MEASURED_REASON)
    expect(await notMeasured.innerText()).not.toMatch(/(^|\s)0(\.0)?%?(\s|$)/)

    // filtered-empty's action reaches the caller's callback.
    await stateFrame(page, 'filtered-empty').getByRole('button', { name: 'Clear filters' }).click()
    await expect(page.locator('[data-states-cleared]')).toHaveAttribute('data-states-cleared', '1')
  })

  test('error states show the request id; a bad payload and a throwing renderer stay in their frames', async ({
    page,
  }) => {
    await openStates(page)
    for (const [id, requestId] of [
      ['error', REQUEST_IDS.error],
      ['invalid-payload', REQUEST_IDS.invalidPayload],
      ['invalid-param', REQUEST_IDS.invalidParam],
    ] as const) {
      const frame = stateFrame(page, id)
      // Static text in the frame (no alert role: ChartAnnouncer announces changes).
      await expect(frame.locator('[data-chart-state-message="error"]')).toContainText('Could not load this chart')
      await expect(frame.locator('[data-chart-request-id]')).toHaveText(requestId)
    }
    // The payload that failed the contract was never drawn.
    await expect(stateFrame(page, 'invalid-payload').locator('canvas')).toHaveCount(0)
    await expect(stateFrame(page, 'invalid-param')).toContainText('"release_id"')

    // One renderer throws: its own frame shows the draw error…
    await expect(stateFrame(page, 'draw-failure').locator('[data-chart-draw-error]')).toContainText(
      'This chart could not be drawn.',
    )
    // …and the other frames are still drawn — real pixels, not a placeholder.
    for (const id of ['ready', 'truncated']) {
      await expect.poll(() => paintedIn(page, id), { message: `${id}: canvas is blank` }).toBeGreaterThanOrEqual(4)
    }
    await expect(page.getByRole('heading', { level: 1, name: 'Chart gallery (dev only)' })).toBeVisible()
  })

  test('"View as table" shows exactly the plotted values and moves focus to the caption', async ({ page }) => {
    await openStates(page)
    const frame = stateFrame(page, 'ready')
    // The body is described by the takeaway and a generated summary with min / max / latest.
    const describedBy = (await frame.locator('[data-chart-body]').getAttribute('aria-describedby')) ?? ''
    expect(describedBy.split(' ')).toHaveLength(2)
    const summary = frame.locator('[data-chart-summary]')
    await expect(summary).toContainText('Heatmap.')
    await expect(summary).toContainText('X axis: Day (7 values)')
    await expect(summary).toContainText('Min 42.0% (admin, 09-03), max 100.0% (auth, 09-04)')
    await expect(summary).toContainText('Latest (09-07)')

    await frame.getByRole('button', { name: 'View as table' }).click()
    const table = frame.getByRole('table')
    await expect(table).toBeVisible()
    await expect(table.locator('caption')).toBeFocused()
    await expect(table.locator('caption')).toHaveText('Ready — data table')

    const headers = await table.locator('thead th').allTextContents()
    expect(headers).toEqual(['Suite', ...GALLERY_HEATMAP_DATA.x_labels])
    const rows = await table
      .locator('tbody tr')
      .evaluateAll((trs) => trs.map((tr) => Array.from(tr.children, (cell) => cell.textContent ?? '')))
    // Expected cells computed independently from the fixture: rate → one-decimal %,
    // a cell sent as null → "No data" (drawn hatched), a cell never sent → "—".
    const expected = GALLERY_HEATMAP_DATA.y_labels.map((label, y) => [
      label,
      ...GALLERY_HEATMAP_DATA.x_labels.map((_, x) => {
        const cell = GALLERY_HEATMAP_DATA.cells.find((c) => c.x === x && c.y === y)
        if (!cell) return '—'
        return cell.value === null ? 'No data' : `${(cell.value * 100).toFixed(1)}%`
      }),
    ])
    expect(rows).toEqual(expected)
    await expect(table.locator('tbody th[scope="row"]')).toHaveCount(GALLERY_HEATMAP_DATA.y_labels.length)
  })

  test('arrow keys move the highlighted heatmap cell with the mouse tooltip; Escape returns focus', async ({
    page,
  }) => {
    await openStates(page)
    const frame = stateFrame(page, 'ready')
    const chart = frame.locator('[data-chart-keyboard]')
    const announcement = frame.locator('[data-chart-announcement]')
    const tooltip = frame.locator('[data-chart-tooltip]')
    // Named once — ours; ECharts' own aria label is off.
    await expect(chart).toHaveAttribute('aria-label', 'Pass rate by suite and day.')
    await expect(frame.locator('[data-chart-engine="echarts"]')).not.toHaveAttribute('aria-label', /.+/)
    const hint = frame.locator('[data-chart-keyboard-hint]')
    await expect(hint).toBeHidden()
    // Keyboard focus (Tab from the heading-adjacent control) shows the hint.
    await chart.focus()
    await page.keyboard.press('Shift+Tab')
    await page.keyboard.press('Tab')
    await expect(chart).toBeFocused()
    await expect(hint).toBeVisible()
    await expect(hint).toHaveText('Arrow keys move, Escape clears, Tab leaves')
    await expect(chart).toHaveAttribute('data-active-index', '')

    const cellAt = (x: number, y: number) => GALLERY_HEATMAP_DATA.cells.findIndex((c) => c.x === x && c.y === y)
    // y grows upward on the axis, so the top row is the last label (admin).
    const top = GALLERY_HEATMAP_DATA.y_labels.length - 1

    await page.keyboard.press('ArrowRight') // nothing highlighted yet → the top-left cell
    await expect(chart).toHaveAttribute('data-active-index', String(cellAt(0, top)))
    await expect(announcement).toContainText('admin')
    await expect(announcement).toContainText('09-01: 60.0%')
    // The tooltip the keyboard opens is the one the mouse gets.
    await expect(tooltip).toBeVisible()
    await expect(tooltip).toContainText('admin')
    await expect(tooltip).toContainText('60.0%')

    await page.keyboard.press('ArrowRight')
    await expect(chart).toHaveAttribute('data-active-index', String(cellAt(1, top)))
    await expect(announcement).toContainText('09-02: 55.0%')
    await expect(tooltip).toContainText('55.0%')

    await page.keyboard.press('ArrowDown')
    await expect(chart).toHaveAttribute('data-active-index', String(cellAt(1, top - 1)))
    await expect(announcement).toContainText('reports')

    await page.keyboard.press('End')
    await expect(chart).toHaveAttribute(
      'data-active-index',
      String(cellAt(GALLERY_HEATMAP_DATA.x_labels.length - 1, top - 1)),
    )

    await page.keyboard.press('Escape')
    await expect(chart).toHaveAttribute('data-active-index', '')
    await expect(chart).toBeFocused()
    await expect(announcement).toHaveText('')
  })

  for (const theme of ALL_THEMES) {
    test(`the states page has no accessibility violations at any impact (${theme})`, async ({ page }) => {
      await openStates(page, theme)
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      // The states page carries no allowlisted violation in any theme.
      await expectNoBlockingViolations(page, theme, [])
    })
  }

  test('the states page has ONE page-level announcer and no per-frame live regions', async ({ page }) => {
    await openStates(page)
    const counts = await page.evaluate(() => ({
      alerts: document.querySelectorAll('[role="alert"]').length,
      statuses: document.querySelectorAll('[role="status"]').length,
      assertive: document.querySelectorAll('[aria-live="assertive"]').length,
      inFrames: document.querySelectorAll('[data-chart-frame] [role="status"], [data-chart-frame] [role="alert"]').length,
      politeText: document.querySelector('[data-chart-announcer="polite"]')?.textContent ?? null,
    }))
    // Nothing is announced on load.
    expect(counts).toEqual({ alerts: 0, statuses: 1, assertive: 1, inFrames: 0, politeText: '' })
  })
})

