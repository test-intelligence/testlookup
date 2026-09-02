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

  test('the clipping check can actually detect a clipped label', async ({ page }) => {
    // A self-test, and it earns its place.
    //
    // The structural cause of the original defect is gone: the diagram no
    // longer sits inside a <pre>, so it no longer inherits monospace, and
    // reintroducing the old Mermaid config no longer clips anything. That is
    // the right outcome for the product and an awkward one for this check —
    // it leaves the measurement above with no failing case in the codebase,
    // and a guard nobody can demonstrate failing is the kind that turns out to
    // have been inert for months.
    //
    // So: take a diagram that drew correctly, narrow one label's box, and
    // require the measurement to notice. This asserts the detector works,
    // independently of whether the app currently has anything to detect.
    await page.goto(HARNESS)

    // Wait for EVERY diagram, not just the first one to appear.
    //
    // Mermaid renders each diagram independently and asynchronously, so the
    // first `article figure svg` to exist says nothing about the rest — and
    // this test needs a `g.node foreignObject`, which a freshly-inserted <svg>
    // does not necessarily carry yet. Gating on one <svg> made the narrowing
    // step find nothing and fail with "no node label was available to narrow",
    // intermittently and only under CI timing. Measuring mid-render can also
    // report a half-laid-out label as clipped, tripping the precondition below.
    // Same gate the measurement test uses.
    const handle = await page.waitForFunction(() => window.__HARNESS__, undefined, {
      timeout: 60_000,
    })
    const info = await handle.jsonValue()
    await expect
      .poll(() => page.locator('article figure svg').count(), {
        timeout: 60_000,
        message: 'not every diagram produced an SVG',
      })
      .toBe(info.expected)

    // Then wait for the exact element this test narrows.
    await page
      .locator('article figure g.node foreignObject')
      .first()
      .waitFor({ timeout: 60_000 })

    const before = await page.evaluate(measureDiagramLabels, 'article figure')
    expect(before.clipped, 'precondition: nothing should be clipped yet').toEqual([])

    const victim = await page.evaluate(() => {
      const fo = document.querySelector('article figure g.node foreignObject')
      if (!fo) return null
      const label = fo.firstElementChild?.textContent?.trim() ?? ''
      // Narrow the allocated box without touching the text inside it — exactly
      // the shape of the real defect, where Mermaid allocated too little room.
      fo.setAttribute('width', String(Math.max(1, (fo as SVGGraphicsElement).getBBox().width - 30)))
      return label
    })
    expect(victim, 'no node label was available to narrow').toBeTruthy()

    const after = await page.evaluate(measureDiagramLabels, 'article figure')
    expect(
      after.clipped.join(' '),
      'the measurement did not notice a label that no longer fits — it is inert',
    ).toContain(victim as string)
  })

  test('a diagram that cannot be drawn leaves nothing behind on the page', async ({ page }) => {
    // The reported defect: /docs/troubleshooting showed "Syntax error in text /
    // mermaid version 11.17.2" three times at the bottom — a page with no
    // diagrams of its own.
    //
    // Mermaid's default on a render failure is to draw that graphic into the
    // scratch element it created under <body> and NOT remove it (errorRenderer
    // .draw followed by a bare throw, in its render()). In a single-page app the
    // element then survives every navigation, and each further attempt adds
    // another copy — which is why the count need not match the number of broken
    // diagrams.
    //
    // The reader should get the failure where the diagram is, and nowhere else.
    await page.goto(`${HARNESS}?broken=1`)
    await page.locator('[data-broken] ').first().waitFor({ timeout: 60_000 })
    await expect
      .poll(() => page.getByText(/could not be drawn/i).count(), { timeout: 60_000 })
      .toBeGreaterThan(0)
    // Give any stray graphic time to be appended before asserting its absence.
    await page.waitForTimeout(1_000)

    const stray = await page.evaluate(() => ({
      graphics: (document.body.innerText.match(/Syntax error in text/g) || []).length,
      nodes: document.querySelectorAll(
        'body > div[id^="dmermaid"], body > div[id^="mermaid"], body > svg',
      ).length,
    }))
    expect(stray.graphics, "mermaid's error graphic is stranded on the page").toBe(0)
    expect(stray.nodes, 'mermaid left a scratch element under <body>').toBe(0)

    // And the failure is still reported where the reader can act on it.
    await expect(page.locator('[data-broken]').getByText(/could not be drawn/i)).toBeVisible()

    // The good diagrams are unaffected — a fix that stopped rendering
    // everything would also satisfy the assertions above.
    const handle = await page.waitForFunction(() => window.__HARNESS__, undefined, {
      timeout: 60_000,
    })
    const info = await handle.jsonValue()
    await expect
      .poll(() => page.locator('article figure svg').count(), { timeout: 60_000 })
      .toBe(info.expected)
  })
})
