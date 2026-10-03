/**
 * VIZ-601 scenario 3 — "Hostile names cannot execute", in a real browser.
 *
 * Given a test, suite, series or release named `HOSTILE_LABEL`
 * (`<img src=x onerror="window.__xss=1">`), every place a chart PRINTS that
 * name shows it as literal text and `window.__xss` stays undefined: the
 * pointer tooltip, the keyboard readout and the page announcer, the legend (or
 * the axis / the plot, where that chart prints names instead), the table view,
 * the exported SVG and the exported CSV.
 *
 * Where a chart has no such place, the item says so rather than skipping it
 * silently: a stacked bar's legend names STATUSES (its suites are on the
 * category axis), and a time series' legend and CSV hold its two plotted
 * series — a release is a marker on the plot and a row of the release table,
 * not a plotted value. The plain heatmap's hostile label is covered by
 * `chart-gallery.spec.ts` (its canvas tooltip and its CSP run); the heatmap
 * FRAME's (Wave 2.6) is the first test of the second block below.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { GALLERY_PROTOTYPE_NAMES, HOSTILE_LABEL } from '../../src/pages/dev/chartGalleryFixtures'
// Both fixtures only `import type` from `@/…`, so they resolve in Playwright's plain-Node transform.
import { HEATMAP_FRAME_LONG_NAME } from '../../src/components/charts/__fixtures__/heatmapFrame'
import { SCATTER_LONG_NAME } from '../../src/components/charts/__fixtures__/testScatter'
import {
  CHART_SVG,
  EXPORT,
  exportFile,
  galleryItem,
  keyboardTo,
  leaveCharts,
  markCentres,
  openGallery,
  parseCsv,
  parseSvg,
  pointAt,
  squash,
  watchErrors,
  xssFlag,
} from '../lib/chart-gallery-page'

/** Where each hostile item prints the name, beyond the tooltip and the table every one of them has. */
interface HostileItem {
  id: string
  /** The drawn marks to point at; the one whose tooltip names the label is used. */
  marks: string
  /** Printed IN FULL here (legend, axis or plot label). */
  printed: string
  /** The exported SVG carries the label whole (a legend), or only as the axis draws it (a truncated prefix). */
  svgText: 'whole' | 'prefix'
  /** The CSV carries the label as a cell (a category or a series name). */
  inCsv: boolean
}

const HOSTILE_ITEMS: HostileItem[] = [
  {
    id: 'donut-hostile-label',
    marks: '.recharts-pie-sector path',
    printed: '[data-chart-legend]',
    svgText: 'whole',
    inCsv: true,
  },
  {
    id: 'bar-stacked-hostile-label',
    marks: '.recharts-bar-rectangle path',
    // The legend names statuses; the SUITE is printed on the category axis.
    printed: '.recharts-yAxis-tick-labels',
    svgText: 'prefix',
    inCsv: true,
  },
  {
    id: 'timeseries-hostile-release',
    marks: '.recharts-bar-rectangle path',
    // A release is printed as the marker's label, on the plot itself.
    printed: CHART_SVG,
    svgText: 'whole',
    inCsv: false,
  },
  {
    id: 'multi-series-hostile-label',
    marks: '.recharts-cartesian-grid-horizontal line',
    printed: '[data-multi-series-legend]',
    svgText: 'whole',
    inCsv: true,
  },
]

/** Hover each mark until the tooltip names the label; returns its text. */
async function hoverUntilNamed(page: Page, item: Locator, marks: string): Promise<string> {
  await item.scrollIntoViewIfNeeded()
  const centres = await markCentres(item.locator(marks))
  expect(centres.length, `no marks under ${marks}`).toBeGreaterThan(0)
  const tooltip = item.locator('[data-chart-tooltip]')
  const read = async () => ((await tooltip.isVisible()) ? squash(await tooltip.innerText()) : '')
  for (const at of centres) {
    // From off the chart each time: a day's tooltip is pinned beside its
    // column and HOLDS while the pointer is on it, so a pointer sliding
    // sideways from one bar to the next lands on the tooltip, not the bar.
    await leaveCharts(page)
    await pointAt(page, at)
    // The box is hidden until it has placed itself, and may still show the
    // previous mark for a moment (it lingers): wait briefly for this one.
    try {
      await expect.poll(read, { timeout: 1500 }).toContain(HOSTILE_LABEL)
      return await read()
    } catch {
      // Not this mark: try the next.
    }
  }
  throw new Error('no mark showed a tooltip naming the hostile label')
}

test.describe('VIZ-601 scenario 3 — a hostile name is literal text everywhere a chart prints it', () => {
  for (const hostile of HOSTILE_ITEMS) {
    test(`${hostile.id}: tooltip, keyboard, ${hostile.printed.includes('legend') ? 'legend' : 'plot'}, table, SVG and CSV`, async ({
      page,
    }) => {
      const errors = watchErrors(page)
      await openGallery(page)
      const item = galleryItem(page, hostile.id)
      await expect(item.locator(CHART_SVG)).toBeVisible()

      // The pointer tooltip: the label as text, and no element made from it.
      const tip = await hoverUntilNamed(page, item, hostile.marks)
      expect(tip).toContain(HOSTILE_LABEL)
      await expect(item.locator('[data-chart-tooltip] img')).toHaveCount(0)

      // The keyboard: the readout and the ONE page announcer say it too.
      const readout = await keyboardTo(page, item, (text) => text.includes(HOSTILE_LABEL))
      expect(readout).toContain(HOSTILE_LABEL)
      await expect(page.locator('[data-chart-announcer="assertive"]')).toContainText(HOSTILE_LABEL)
      await page.keyboard.press('Escape')

      // Where the chart prints names: whole, as text.
      const printed = item.locator(hostile.printed).first()
      if (hostile.svgText === 'prefix') await expect(printed).toContainText('<img src=x')
      else await expect(printed).toContainText(HOSTILE_LABEL)

      // The table view.
      await item.getByRole('button', { name: 'View as table' }).click()
      const tables = item.getByRole('table')
      await expect(tables.first()).toBeVisible()
      expect(squash((await tables.allInnerTexts()).join(' '))).toContain(HOSTILE_LABEL)

      // Nothing, anywhere in the item, became an element; nothing ran.
      await expect(item.locator('img')).toHaveCount(0)
      expect(await xssFlag(page)).toBeUndefined()

      // The exported SVG: a well-formed document whose text carries the name,
      // with no element or handler made from it.
      const svg = await exportFile(page, item, EXPORT.svg)
      const parsed = await parseSvg(page, svg.bytes.toString('utf-8'))
      expect(parsed.ok, 'the exported SVG does not parse').toBe(true)
      const svgText = parsed.texts.join('\n')
      expect(svgText).toContain(hostile.svgText === 'whole' ? HOSTILE_LABEL : '<img src=x')
      expect(parsed.foreign).toEqual([])
      expect(parsed.handlers).toEqual([])

      // The exported CSV: the name is one cell, exactly — `<` is not a formula
      // character, so it is not prefixed, only quoted (it holds `"`).
      const csv = await exportFile(page, item, EXPORT.csv)
      const cells = parseCsv(csv.bytes.toString('utf-8')).flat()
      if (hostile.inCsv) expect(cells).toContain(HOSTILE_LABEL)
      else expect(cells.some((cell) => cell.includes('<img'))).toBe(false)

      expect(await xssFlag(page)).toBeUndefined()
      expect(errors).toEqual([])
    })
  }
})

/**
 * Wave 2.6 (VIZ-408). The heatmap frame the Trends catalogue draws: a suite
 * name is printed on the canvas (where markup cannot run) and as TEXT in the
 * tooltip, the keyboard announcement, the table view and the CSV. And ranked
 * bars whose suites are named like `Object.prototype` members: before the
 * own-property lookups every place that resolved a label read the inherited
 * member instead ("function Object() { [native code] }").
 */
test.describe('Wave 2.6 — the heatmap frame and prototype-member names', () => {
  test('heatmap-frame-hostile: keyboard tooltip and announcement, table and CSV print the name as text', async ({ page }) => {
    const errors = watchErrors(page)
    await openGallery(page)
    const item = galleryItem(page, 'heatmap-frame-hostile')
    await item.scrollIntoViewIfNeeded()
    await expect(item.locator('[data-chart-engine="echarts"]')).toHaveAttribute('data-chart-status', 'ready')

    // Worst first, drawn top-down: the hostile suite (the lowest rate) is the top
    // row, and the first cell the keyboard reaches is its first day.
    await item.locator('[data-chart-keyboard]').focus()
    await page.keyboard.press('ArrowRight')
    const tooltip = item.locator('[data-chart-tooltip]')
    await expect(tooltip).toBeVisible()
    await expect(tooltip).toContainText(HOSTILE_LABEL)
    // Through the page's one announcer; the chart has no live region of its own.
    await expect(page.locator('[data-chart-announcer="assertive"]')).toContainText(HOSTILE_LABEL)
    await expect(item.locator('[data-chart-announcement]')).toHaveCount(0)
    await expect(item.locator('[data-chart-tooltip] img')).toHaveCount(0)
    await page.keyboard.press('Escape')

    await item.getByRole('button', { name: 'View as table' }).click()
    const table = item.getByRole('table')
    await expect(table).toBeVisible()
    await expect(table.locator('tbody th[scope="row"]').first()).toHaveText(HOSTILE_LABEL)

    const csv = await exportFile(page, item, EXPORT.csv)
    expect(parseCsv(csv.bytes.toString('utf-8')).flat()).toContain(HOSTILE_LABEL)

    await expect(item.locator('img')).toHaveCount(0)
    expect(await xssFlag(page)).toBeUndefined()
    expect(errors).toEqual([])
  })

  test('bar-ranked-prototype-names: axis, tooltip, table and CSV name each bar by its literal name', async ({ page }) => {
    const errors = watchErrors(page)
    await openGallery(page)
    const item = galleryItem(page, 'bar-ranked-prototype-names')
    await item.scrollIntoViewIfNeeded()
    await expect(item.locator(CHART_SVG)).toBeVisible()
    const inherited = /function|native code|\[object Object\]/

    // The category axis prints every name, whole (they are short).
    const ticks = item.locator('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value')
    await expect.poll(() => ticks.allTextContents()).toEqual([...GALLERY_PROTOTYPE_NAMES])

    // The pointer tooltip of the first (longest) bar names it.
    const [first] = await markCentres(item.locator('.recharts-bar-rectangle path'))
    await leaveCharts(page)
    await pointAt(page, first)
    const tooltip = item.locator('[data-chart-tooltip]')
    await expect(tooltip).toContainText(GALLERY_PROTOTYPE_NAMES[0])
    expect(squash(await tooltip.innerText())).not.toMatch(inherited)
    await leaveCharts(page)

    // The table view and the generated summary.
    await item.getByRole('button', { name: 'View as table' }).click()
    const table = item.getByRole('table')
    await expect(table).toBeVisible()
    expect(await table.locator('tbody th[scope="row"]').allTextContents()).toEqual([...GALLERY_PROTOTYPE_NAMES])
    expect(squash(await item.innerText())).not.toMatch(inherited)

    const cells = parseCsv((await exportFile(page, item, EXPORT.csv)).bytes.toString('utf-8')).flat()
    for (const name of GALLERY_PROTOTYPE_NAMES) expect(cells).toContain(name)
    expect(cells.join('\n')).not.toMatch(inherited)
    expect(errors).toEqual([])
  })
})

/**
 * Wave 3 (PR-B). Markup, `Object.prototype` member and 250-character names in
 * the new charts: the heatmap frame's edge cases (FK1), the coverage treemap
 * (FK2: names drawn INSIDE the rectangles, on the canvas), the test scatter
 * (FK4: no name on the canvas at all) and failure groups (FK3: names on SVG
 * circles and in the ranked table). Each name reaches the keyboard
 * announcement, the table view and the CSV as itself, never as markup and
 * never as an inherited member; and drawing them leaves `Object.prototype`
 * exactly as it was (a canvas text cache keyed by plain-object lookups once
 * wrote `prev` / `next` onto it for the name `__proto__`).
 */
interface Wave3Hostile {
  id: string
  /** The chart's one focus stop. */
  surface: string
  /** Where the focused mark's words are shown on screen (the canvas tooltip, or the group readout). */
  shown: string
  /** The key that walks from one named mark to the next, and how many presses reach them all. */
  key: 'ArrowRight' | 'ArrowDown'
  steps: number
  /** The first mark the keyboard reaches is named this. */
  first: string
  names: readonly string[]
}

const WAVE_3_HOSTILE: Wave3Hostile[] = [
  {
    id: 'heatmap-frame-edges',
    surface: '[data-chart-keyboard]',
    shown: '[data-chart-tooltip]',
    // Rows worst first: __proto__, constructor, the long name, the all-null row.
    key: 'ArrowDown',
    steps: 4,
    first: '__proto__',
    names: ['__proto__', 'constructor', HEATMAP_FRAME_LONG_NAME],
  },
  {
    id: 'coverage-map-hostile',
    surface: '[data-chart-keyboard="treemap"]',
    shown: '[data-chart-tooltip]',
    // Largest first: the markup-named suite is the biggest rectangle.
    key: 'ArrowRight',
    steps: 6,
    first: HOSTILE_LABEL,
    names: [HOSTILE_LABEL, 'constructor', '__proto__', HEATMAP_FRAME_LONG_NAME, 'toString', '<b>suite</b>'],
  },
  {
    id: 'scatter-hostile',
    surface: '[data-chart-keyboard="scatter"]',
    shown: '[data-chart-tooltip]',
    // Fastest first: the markup-named test is the fastest.
    key: 'ArrowRight',
    steps: 10,
    first: HOSTILE_LABEL,
    names: [HOSTILE_LABEL, 'constructor', '__proto__', 'toString', SCATTER_LONG_NAME, '<b>suite</b>'],
  },
  {
    id: 'failure-groups-hostile',
    surface: '[data-group-plot]',
    shown: '[data-group-readout]',
    // Largest first.
    key: 'ArrowRight',
    steps: 8,
    first: `${HOSTILE_LABEL}AssertionError`,
    names: [`${HOSTILE_LABEL}AssertionError`, '__proto__', 'constructor'],
  },
]

/** What an inherited `Object.prototype` member prints as, where a name should have been. */
const INHERITED = /function |native code|\[object Object\]/

test.describe('Wave 3 — hostile and Object-member names in the new charts', () => {
  for (const hostile of WAVE_3_HOSTILE) {
    test(`${hostile.id}: keyboard, table and CSV print every name as itself`, async ({ page }) => {
      const errors = watchErrors(page)
      await openGallery(page)
      const item = galleryItem(page, hostile.id)
      await item.scrollIntoViewIfNeeded()
      const engine = item.locator('[data-chart-engine="echarts"]')
      if ((await engine.count()) > 0) await expect(engine).toHaveAttribute('data-chart-status', 'ready')

      // The keyboard: the first mark's words on screen, then every name through the ONE announcer.
      const announcer = page.locator('[data-chart-announcer="assertive"]')
      await item.locator(hostile.surface).focus()
      await page.keyboard.press(hostile.key)
      await expect(item.locator(hostile.shown)).toContainText(hostile.first)
      const announced: string[] = [squash(await announcer.innerText())]
      for (let step = 1; step < hostile.steps; step++) {
        await page.keyboard.press(hostile.key)
        await expect.poll(async () => squash(await announcer.innerText())).not.toBe(announced[announced.length - 1])
        announced.push(squash(await announcer.innerText()))
      }
      for (const name of hostile.names) expect(announced.some((text) => text.includes(name)), name).toBe(true)
      expect(announced.join('\n')).not.toMatch(INHERITED)
      await expect(item.locator(`${hostile.shown} img`)).toHaveCount(0)
      await page.keyboard.press('Escape')

      // The table view.
      await item.getByRole('button', { name: 'View as table' }).click()
      const tables = item.getByRole('table')
      await expect(tables.first()).toBeVisible()
      const tabled = squash((await tables.allInnerTexts()).join(' '))
      for (const name of hostile.names) expect(tabled, name).toContain(name)
      expect(tabled).not.toMatch(INHERITED)

      // The CSV: each name one cell, exactly.
      const cells = parseCsv((await exportFile(page, item, EXPORT.csv)).bytes.toString('utf-8')).flat()
      for (const name of hostile.names) expect(cells, name).toContain(name)
      expect(cells.join('\n')).not.toMatch(INHERITED)

      await expect(item.locator('img')).toHaveCount(0)
      expect(await xssFlag(page)).toBeUndefined()
      // Drawing the names left no key on Object.prototype (every `for...in` on the page would see it).
      expect(await page.evaluate(() => Object.keys(Object.getOwnPropertyDescriptors(Object.prototype)).filter((key) => Object.prototype.propertyIsEnumerable.call(Object.prototype, key)))).toEqual([])
      expect(errors).toEqual([])
    })
  }
})
