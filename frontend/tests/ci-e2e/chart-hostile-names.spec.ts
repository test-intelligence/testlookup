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
 * not a plotted value. The heatmap's hostile label is covered by
 * `chart-gallery.spec.ts` (its canvas tooltip and its CSP run).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { HOSTILE_LABEL } from '../../src/pages/dev/chartGalleryFixtures'
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
