/**
 * VIZ-606 — per-chart export, the files themselves, from a real browser with a
 * real canvas (jsdom has none, so the unit tests stop at the draw calls).
 *
 *   - PNG: decoded here (`tests/lib/png.ts`, node's zlib): the image is exactly
 *     2× the export's own layout (read from the SVG export of the same chart),
 *     the band under the footer rule has ink in it — the footer is IN the
 *     pixels — and the file is named `testlookup_<chart>_<project>_<yyyymmdd-hhmm>Z.png`.
 *     The story's budget (≤ 1 s for a standard chart) is measured from the
 *     click on "PNG image" to the browser's download event.
 *   - SVG: a well-formed document carrying the title, the legend and the
 *     footer as text, with no `var(` left anywhere in it.
 *   - CSV: the `#` provenance lines, the plotted values, and every name that
 *     begins with `=`, `+`, `-` or `@` neutralised with a leading `'` — while a
 *     negative VALUE stays a number.
 */
import { expect, test } from '@playwright/test'
import { GALLERY_CHANGE_BARS, GALLERY_FORMULA_BARS, GALLERY_TOP_FAILING } from '../../src/pages/dev/chartGalleryFixtures'
import {
  EXPORT,
  exportFile,
  exportFileName,
  galleryItem,
  openGallery,
  parseCsv,
  parseSvg,
  watchErrors,
} from '../lib/chart-gallery-page'
import { decodePng, inkInRow, pixelAt } from '../lib/png'

/**
 * A browser zone far from UTC, on every machine: the file name's stamp must be
 * UTC (`…Z`), and on a UTC CI runner a stamp taken in LOCAL time would look
 * exactly right. Here it would be 5 h 30 min off, and the scoped test fails.
 */
test.use({ timezoneId: 'Asia/Kolkata' })

/** The story's budget for a standard chart, ms. */
const EXPORT_BUDGET_MS = 1000

/**
 * The one gallery item with a real envelope `meta` (`scoped: true`): project
 * "payments", suites checkout and payments-api, 42 days, generated at
 * 2026-03-30T09:00:00Z — and opened zoomed, so its exports carry the zoom note.
 */
const SCOPED = 'timeseries-zoom-trend'

test.describe('VIZ-606 export', () => {
  test('PNG: 2× the layout, the footer painted into the pixels, the story\'s file name, within 1 s', async ({
    page,
  }, testInfo) => {
    const errors = watchErrors(page)
    await openGallery(page)
    const timings: Record<string, number> = {}
    const footerInk: Record<string, number> = {}
    // An unscoped chart (its footer is the one line "Scope unavailable.") and
    // the scoped, zoomed trend (project, release, suites, window + totals,
    // generated-at and the zoom note: five paragraphs).
    for (const id of ['bar-ranked', SCOPED]) {
      const item = galleryItem(page, id)
      await item.scrollIntoViewIfNeeded()
      await expect(item.locator('.recharts-wrapper > svg.recharts-surface')).toBeVisible()

      // The layout's own size, from the SVG export of the same chart.
      const svg = await exportFile(page, item, EXPORT.svg)
      const layout = await parseSvg(page, svg.bytes.toString('utf-8'))
      expect(layout.ok).toBe(true)

      const png = await exportFile(page, item, EXPORT.png)
      timings[id] = png.ms
      expect(png.name, id).toMatch(exportFileName('png'))
      const image = decodePng(new Uint8Array(png.bytes))
      // 2× pixel ratio, for slides: exactly twice the layout in each direction.
      expect(image.width, `${id}: width`).toBe(Math.round(layout.width * 2))
      expect(image.height, `${id}: height`).toBe(Math.round(layout.height * 2))

      // The footer band: everything under the rule the export draws above the
      // footer — the lowest row that differs from the background across most of
      // the width. The rule is a hairline in the theme's border colour, a few
      // levels off the card, so it is found with a tight tolerance.
      const background = pixelAt(image, 1, 1)
      let rule = -1
      for (let y = image.height - 1; y > image.height / 2; y--) {
        if (inkInRow(image, y, background, 3) > 0.8) {
          rule = y
          break
        }
      }
      expect(rule, `${id}: no footer rule found in the lower half`).toBeGreaterThan(0)
      let inkRows = 0
      for (let y = rule + 3; y < image.height; y++) if (inkInRow(image, y, background) > 0.002) inkRows += 1
      // Rows of text, not a stray pixel.
      expect(inkRows, `${id}: the footer band under the rule is blank`).toBeGreaterThan(8)
      footerInk[id] = inkRows

      // Off the render path and quick: the story's budget for a standard chart.
      expect(png.ms, `${id}: PNG export took ${png.ms} ms`).toBeLessThanOrEqual(EXPORT_BUDGET_MS)
    }
    // The scoped footer is five paragraphs to the unscoped one's one: its band
    // holds several times the ink, so every paragraph reached the pixels.
    expect(footerInk[SCOPED], JSON.stringify(footerInk)).toBeGreaterThan(3 * footerInk['bar-ranked'])
    testInfo.annotations.push({ type: 'png-export-ms', description: JSON.stringify(timings) })
    console.log(`PNG export timings (click → download, ms): ${JSON.stringify(timings)}`)
    expect(errors).toEqual([])
  })

  test('a scoped, zoomed chart stamps its scope and its zoom on every format, and names the file from them', async ({
    page,
  }) => {
    await openGallery(page)
    const item = galleryItem(page, SCOPED)
    await item.scrollIntoViewIfNeeded()
    const stamp = /^testlookup_[^_]+_payments_20260330-0900Z\.(png|svg|csv)$/
    const svg = await exportFile(page, item, EXPORT.svg)
    expect(svg.name).toMatch(stamp)
    const parsed = await parseSvg(page, svg.bytes.toString('utf-8'))
    const footer = parsed.texts.join(' ')
    for (const part of [
      'Project: payments',
      'Release: All releases',
      'Test Suite: checkout, payments-api',
      'Window: 2026-02-17',
      '(42 days)',
      'runs',
      'Generated 2026-03-30',
      'Note: Zoomed to',
      // Zoomed: the run totals are the WHOLE window's, and say so (review F7).
      'Window totals:',
    ]) {
      expect(footer, part).toContain(part)
    }
    const csv = parseCsv((await exportFile(page, item, EXPORT.csv)).bytes.toString('utf-8'))
    const comments = new Map(csv.filter((row) => row[0].startsWith('#')).map(([label, value]) => [label, value]))
    expect(comments.get('# Project')).toBe('payments')
    expect(comments.get('# Test Suite')).toBe('checkout, payments-api')
    expect(comments.get('# Note')).toMatch(/^Zoomed to/)
    expect(comments.get('# Window totals')).toMatch(/ runs · /)
    expect(comments.has('# Totals')).toBe(false)
    // The CSV is what the chart PLOTS — the fourteen zoomed days, not the window's 42.
    const days = csv.filter((row) => /^\d{4}-\d{2}-\d{2}$/.test(row[0])).map((row) => row[0])
    expect(days[0]).toBe('2026-03-15')
    expect(days[days.length - 1]).toBe('2026-03-28')
    expect(days).toHaveLength(14)
  })

  test('SVG: well-formed, with the title, the legend and the footer as text, and no var( left', async ({ page }) => {
    await openGallery(page)
    for (const [id, legendText] of [
      ['bar-stacked', 'Passed'],
      ['multi-series-three-suites', 'payments'],
    ] as const) {
      const item = galleryItem(page, id)
      await item.scrollIntoViewIfNeeded()
      const title = (await item.getByRole('heading', { level: 2 }).innerText()).trim()
      const file = await exportFile(page, item, EXPORT.svg)
      expect(file.name, id).toMatch(exportFileName('svg'))
      const source = file.bytes.toString('utf-8')
      // A standalone file: no custom property can resolve outside the app.
      expect(source.includes('var('), `${id}: an unresolved var( is left in the SVG`).toBe(false)
      const parsed = await parseSvg(page, source)
      expect(parsed.ok, id).toBe(true)
      expect(parsed.roles).toEqual(expect.arrayContaining(['title', 'legend', 'footer']))
      const text = parsed.texts.join('\n')
      expect(text).toContain(title)
      expect(text).toContain(legendText)
      // The footer states the scope: these items have none (`meta: null`), and
      // the footer says so rather than inventing one. The scoped item's full
      // footer is asserted in the test above.
      expect(text).toContain('Scope unavailable.')
    }
  })

  test('CSV: # provenance lines, the plotted values, and formula-like names neutralised', async ({ page }) => {
    await openGallery(page)

    // Names a spreadsheet would evaluate: each one handed over with a leading '.
    const formula = await exportFile(page, galleryItem(page, 'bar-formula-names'), EXPORT.csv)
    expect(formula.name).toMatch(exportFileName('csv'))
    const text = formula.bytes.toString('utf-8')
    expect(text.charCodeAt(0), 'UTF-8 BOM, so Excel reads the names as UTF-8').toBe(0xfeff)
    const rows = parseCsv(text)
    const comments = rows.filter((row) => row[0].startsWith('#'))
    expect(comments.length, 'no # provenance lines').toBeGreaterThanOrEqual(2)
    expect(comments[0][0]).toBe('# TestLookup chart export')
    const data = rows.filter((row) => !row[0].startsWith('#'))
    const [header, ...values] = data
    expect(header.length).toBe(2)
    const byName = new Map(values.map(([name, value]) => [name, value]))
    for (const [name, value] of GALLERY_FORMULA_BARS) {
      expect(byName.get(`'${name}`), `${name} was not neutralised`).toBe(String(value))
      expect(byName.has(name), `${name} is in the CSV as a live formula`).toBe(false)
    }
    // No data cell at all starts with a formula character.
    for (const row of values) expect(row[0], row.join(',')).not.toMatch(/^[=+\-@]/)

    // …while a negative VALUE stays a number, exactly as plotted.
    const change = parseCsv((await exportFile(page, galleryItem(page, 'bar-diverging'), EXPORT.csv)).bytes.toString('utf-8'))
    const changeValues = new Map(change.filter((row) => !row[0].startsWith('#')).slice(1).map(([name, value]) => [name, value]))
    for (const [name, value] of GALLERY_CHANGE_BARS) expect(changeValues.get(name), name).toBe(String(value))

    // …and names that get past a FIRST-character check stay inert where Excel
    // splits on `;` or TAB too (review A6/F9, A11). Excel honours a quote only
    // at the very start of a cell, so each line is split the way a `;`-locale
    // or TAB-separated open splits it, quotes and all: no piece may begin with
    // a formula character, full-width forms included.
    const locale = await exportFile(page, galleryItem(page, 'bar-csv-locale-names'), EXPORT.csv)
    const localeText = locale.bytes.toString('utf-8').replace(/^﻿/, '')
    const localeRows = parseCsv(localeText).filter((row) => !row[0].startsWith('#'))
    const localeNames = localeRows.slice(1).map(([name]) => name)
    expect(localeNames).toEqual(["x;'=1+2", "'＝SUM(A1:A2)"])
    for (const line of localeText.split(/\r?\n/).filter((l) => l && !l.startsWith('#'))) {
      for (const piece of line.split(/[;,\t]/)) {
        expect(piece.replace(/^"/, ''), `${line} has a live formula cell when split`).not.toMatch(/^[=+\-@＝＋－＠]/)
      }
    }

    // …and a plain chart's CSV holds exactly its plotted values.
    const ranked = parseCsv((await exportFile(page, galleryItem(page, 'bar-ranked'), EXPORT.csv)).bytes.toString('utf-8'))
    const rankedValues = ranked.filter((row) => !row[0].startsWith('#')).slice(1)
    expect(rankedValues.map(([name, value]) => [name, Number(value)])).toEqual(
      [...GALLERY_TOP_FAILING].map(([name, value]) => [name, value]),
    )
  })

  /**
   * Review A9: a name carrying a character XML cannot hold — an ANSI colour
   * code (ESC, U+001B) is common in CI output — made the SVG malformed and the
   * PNG fail ("The chart image could not be drawn."). The gallery has no such
   * name, so one is written into the drawn chart (an axis label) and its
   * legend, exactly where an ingested name lands, before exporting.
   */
  test('a name with a control character (ANSI colour) still exports a valid SVG and a PNG', async ({ page }) => {
    const errors = watchErrors(page)
    await openGallery(page)
    const item = galleryItem(page, 'bar-stacked')
    await item.scrollIntoViewIfNeeded()
    await expect(item.locator('.recharts-wrapper > svg.recharts-surface')).toBeVisible()
    await item.evaluate((el) => {
      const ansi = 'suite\u001b[31mred\u001b[0m'
      const tick = el.querySelector('.recharts-wrapper > svg.recharts-surface text')
      if (tick) tick.textContent = ansi
      const legend = el.querySelector('[data-chart-legend] li span')
      if (legend) legend.textContent = ansi
    })
    const svg = await exportFile(page, item, EXPORT.svg)
    const source = svg.bytes.toString('utf-8')
    expect(source).not.toContain('\u001b')
    const parsed = await parseSvg(page, source)
    expect(parsed.ok, 'the SVG does not parse').toBe(true)
    expect(parsed.texts.join('\n')).toContain('suite\uFFFD[31mred')

    const png = await exportFile(page, item, EXPORT.png)
    expect(png.name).toMatch(exportFileName('png'))
    const image = decodePng(new Uint8Array(png.bytes))
    expect(image.width).toBe(Math.round(parsed.width * 2))
    await expect(item.locator('[data-chart-export-error]')).toHaveCount(0)
    expect(errors).toEqual([])
  })

  test('the export menu works from the keyboard and inside full screen', async ({ page }) => {
    await openGallery(page)
    const item = galleryItem(page, 'bar-ranked')
    await item.scrollIntoViewIfNeeded()
    await item.getByRole('button', { name: 'Full screen' }).click()
    const frame = item.locator('[data-chart-frame]')
    await expect(frame).toHaveAttribute('data-chart-fullscreen', /api|overlay/, { timeout: 5000 })
    const trigger = frame.getByRole('button', { name: EXPORT.trigger, exact: true })
    await trigger.focus()
    await page.keyboard.press('ArrowDown')
    const menu = frame.getByRole('menu')
    await expect(menu).toBeVisible()
    await expect(frame.getByRole('menuitem', { name: EXPORT.png })).toBeFocused()
    // Escape closes the MENU only; the frame stays full screen.
    await page.keyboard.press('Escape')
    await expect(menu).toHaveCount(0)
    await expect(trigger).toBeFocused()
    await expect(frame).toHaveAttribute('data-chart-fullscreen', /api|overlay/)
    // An export from inside full screen.
    const file = await exportFile(page, frame, EXPORT.csv)
    expect(file.name).toMatch(exportFileName('csv'))
  })
})
