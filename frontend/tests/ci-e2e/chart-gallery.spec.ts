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
import AxeBuilder from '@axe-core/playwright'
import {
  GALLERY_CANVAS,
  GALLERY_DRAWN_CANVAS_ITEMS,
  GALLERY_DRAWN_SVG_ITEMS,
  GALLERY_HEATMAP_DATA,
  GALLERY_ITEM_IDS,
  GALLERY_STATUS_MATRIX_DATA,
  HOSTILE_LABEL,
  galleryChartHeight,
} from '../../src/pages/dev/chartGalleryFixtures'
import {
  NOT_MEASURED_REASON,
  REQUEST_IDS,
  STATE_ITEM_IDS,
  STATE_ITEMS,
} from '../../src/pages/dev/chartStatesFixtures'

const GALLERY = '/__charts'
const CHART_SVG = '.recharts-wrapper > svg.recharts-surface'
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
  const KNOWN_VIOLATIONS: { theme: string; rule: string; nodeHtmlIncludes: string }[] = [
    { theme: 'lab', rule: 'color-contrast', nodeHtmlIncludes: 'No defect data' },
  ]

  /**
   * No serious / critical axe violation except an allowlisted one — and every
   * allowlisted one must still fire (the ratchet), so a fix shrinks the list.
   */
  async function expectNoBlockingViolations(
    page: Page,
    theme: string,
    known: { theme: string; rule: string; nodeHtmlIncludes: string }[],
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
    expect(blocking, `axe (${theme})`).toEqual([])

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
  }

  // Every theme the app ships: a contrast regression can hide in any one of them.
  const ALL_THEMES = ['signal', 'console', 'slate', 'ember', 'lab', 'midnight'] as const

  for (const theme of ALL_THEMES) {
    test(`has no serious or critical automated accessibility violations (${theme})`, async ({
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
    const scroller = page.locator('[data-gallery-item="trend-line"] [role="group"]')
    await expect(scroller).toHaveAttribute('tabindex', '0')
    expect(await scroller.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true)
  })

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
    test(`the states page has no serious or critical accessibility violations (${theme})`, async ({ page }) => {
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
