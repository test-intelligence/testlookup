/**
 * CI check: the user guide's diagrams draw, and their labels are not clipped.
 *
 * Runs against a harness page served by `vite dev` — no backend, no
 * deployment, no auth. The equivalent live probe (`probe-docs-render.spec.ts`)
 * still checks the real deployment; this one is what runs on every push.
 *
 *   npx playwright test --config playwright.docs.config.ts
 *
 * Deliberately NOT named `probe-*`: `probe-live.config.ts` matches that prefix
 * and would run this against the homelab, where the harness does not exist.
 */
import { test, expect } from '@playwright/test'
import * as fs from 'fs'
import * as path from 'path'
import { fileURLToPath } from 'url'

import { measureDiagramLabels } from './lib/diagram-geometry'

const HARNESS = '/tests/diagram-harness/index.html'

/** The font the app really uses, read from the stylesheet that defines it. */
function fontFromStylesheet(): string {
  // The spec is loaded as ESM, where __dirname does not exist.
  const here = path.dirname(fileURLToPath(import.meta.url))
  const css = fs.readFileSync(path.join(here, '..', 'src', 'index.css'), 'utf-8')
  const themeBlock = css.slice(css.indexOf('[data-theme="signal"]'))
  const match = /--font-sans:\s*([^;]+);/.exec(themeBlock)
  if (!match) throw new Error('--font-sans is not defined for the default theme')
  return match[1].trim()
}

const normalise = (value: string) => value.replace(/\s+/g, ' ').replace(/"/g, "'").trim()

test.describe('user guide diagrams', () => {
  test('every diagram draws as a picture, and no label is clipped', async ({ page }) => {
    const pageErrors: string[] = []
    page.on('pageerror', (e) => pageErrors.push(String(e)))

    await page.goto(HARNESS)

    const handle = await page.waitForFunction(() => window.__HARNESS__, undefined, {
      timeout: 60_000,
    })
    const info = await handle.jsonValue()

    // Non-vacuous: the guide has diagrams, and this is looking at all of them.
    expect(info.expected, 'the harness collected no diagrams to render').toBeGreaterThan(10)
    expect(info.pages.length, 'diagrams should span several pages').toBeGreaterThan(5)

    // The palette must have resolved from the real stylesheet. If the harness
    // lost its data-theme, readDiagramPalette() would quietly return hardcoded
    // fallbacks and this check would be measuring a font the app never uses.
    expect(
      normalise(info.palette.font),
      'the harness is not using the app font — check data-theme on the harness page',
    ).toBe(normalise(fontFromStylesheet()))

    // Every diagram drew.
    await expect
      .poll(() => page.locator('article figure svg').count(), {
        timeout: 60_000,
        message: 'not every diagram produced an SVG',
      })
      .toBe(info.expected)

    // None of them fell back to showing source.
    await expect(
      page.getByText(/could not be drawn/i),
      'a diagram failed to parse and fell back to its source',
    ).toHaveCount(0)

    // And the labels fit. This is the assertion no text check can make: a
    // clipped label still reports its full text through textContent.
    const geometry = await page.evaluate(measureDiagramLabels, 'article figure')
    expect(geometry.nodes, 'no node boxes were measured — the check is vacuous').toBeGreaterThan(20)
    expect(geometry.clipped, 'these labels are cut off by their own node box').toEqual([])

    expect(pageErrors, 'the harness logged errors while rendering').toEqual([])
  })
})
