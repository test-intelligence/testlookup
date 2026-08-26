/** Rendering sweep: every documentation page renders as formatted content. */
import { test, expect, type Page } from '@playwright/test'

import { measureDiagramLabels } from './lib/diagram-geometry'

const BASE = 'http://testlookup.local'
const IDS = [
  'introduction','getting-started','concepts','ingestion','failure-analysis','flaky',
  'search','dashboards','test-management','releases','decisions','ai-agents','reports',
  'architecture','workflows','integrations','administration','security','troubleshooting',
]
let page: Page
test.describe.configure({ mode: 'serial' })

test.beforeAll(async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 } })
  page = await ctx.newPage()
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.fill('input[type="text"], input[name="username"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 30_000 })
})

for (const id of IDS) {
  test(`/docs/${id} renders as formatted content`, async () => {
    await page.goto(`${BASE}/docs/${id}`, { waitUntil: 'domcontentloaded' })
    const article = page.locator('article')
    await expect.poll(async () => (await article.innerText()).length, { timeout: 30_000 })
      .toBeGreaterThan(300)

    // Prose only. Code blocks legitimately contain '#' shell comments and '|'
    // table pipes in examples; including them made this check flag a page that
    // renders perfectly.
    const text = await article.evaluate((el) => {
      const clone = el.cloneNode(true) as HTMLElement
      clone.querySelectorAll('pre, code, figure').forEach((n) => n.remove())
      return clone.innerText ?? clone.textContent ?? ''
    })
    // Raw markdown leaking through means a construct did not render.
    expect(text, `${id}: raw table separator visible`).not.toMatch(/\|\s*-{3}/)
    expect(text, `${id}: raw bold markers visible`).not.toMatch(/\*\*\w/)
    expect(text, `${id}: raw heading markers visible`).not.toMatch(/^#{1,3}\s/m)
    expect(await article.locator('h2, h3').count(), `${id}: no headings rendered`)
      .toBeGreaterThan(0)
  })
}

test('pages that write tables render them as tables', async () => {
  let total = 0
  for (const id of ['flaky', 'decisions', 'ai-agents', 'reports', 'introduction']) {
    await page.goto(`${BASE}/docs/${id}`, { waitUntil: 'domcontentloaded' })
    await expect.poll(async () => page.locator('article table').count(), { timeout: 30_000 })
      .toBeGreaterThan(0)
    total += await page.locator('article table').count()
  }
  expect(total).toBeGreaterThan(8)
})

// Every guide page that writes a ```mermaid block. Kept in step with the
// content by `backend/tests/regression/test_docs_match_the_engine.py`, which
// fails if a page grows a diagram that is not listed here — so a new
// diagram cannot quietly go unprobed.
const DIAGRAM_PAGES = ['ai-agents', 'architecture', 'concepts', 'decisions', 'failure-analysis', 'getting-started', 'ingestion', 'introduction']

test('diagrams render as SVG pictures, not source text', async () => {
  // Pages that carry a mermaid block. Each must draw a real <svg>, and the
  // written description must still be there — the picture is an aid, and a
  // screen reader never gets one.
  for (const id of DIAGRAM_PAGES) {
    await page.goto(`${BASE}/docs/${id}`, { waitUntil: 'domcontentloaded' })

    await expect
      .poll(async () => page.locator('article figure svg').count(), {
        timeout: 30_000,
        message: `${id}: no diagram was drawn`,
      })
      .toBeGreaterThan(0)

    // The fallback panel must NOT be showing.
    const text = await page.locator('article').innerText()
    expect(text, `${id}: a diagram fell back to source`).not.toContain('could not be drawn')
    expect(text, `${id}: lost its written description`).toContain('In words:')
  }
})

test('a drawn diagram carries real node labels', async () => {
  // An <svg> that rendered nothing is still an <svg>. Check the content.
  await page.goto(`${BASE}/docs/architecture`, { waitUntil: 'domcontentloaded' })
  await expect
    .poll(async () => page.locator('article figure svg').count(), { timeout: 30_000 })
    .toBeGreaterThan(0)

  // textContent, not innerText: innerText is an HTMLElement property and is
  // undefined on an SVGElement, so it reports empty for a diagram that drew
  // perfectly — the assertion would fail on working code.
  const svgText = (await page.locator('article figure svg').first().textContent()) ?? ''
  expect(svgText.length, 'the diagram drew no labels').toBeGreaterThan(20)
  // Node labels from architecture.md's own flowchart, so this cannot pass on
  // some other page's diagram. Deliberately the guide's vendor-neutral names —
  // the documentation does not name the internal infrastructure.
  for (const label of ['MCP server', 'Background workers', 'Data stores']) {
    expect(svgText, `the diagram did not draw the node "${label}"`).toContain(label)
  }
})

test('diagram labels fit inside their boxes', async () => {
  // A diagram can draw perfectly and still be broken: mermaid sizes each node
  // box by MEASURING its label, and if the font it measures in differs from
  // the font the SVG finally renders in, every box comes out too narrow and
  // the labels are visually cut off ("Backend AP", "MCP serve").
  //
  // textContent still holds the full label when that happens, so the other
  // probes here pass on a diagram no reader can read. This compares the
  // painted geometry instead.
  const allClipped: string[] = []

  for (const id of DIAGRAM_PAGES) {
    await page.goto(`${BASE}/docs/${id}`, { waitUntil: 'domcontentloaded' })
    await page.locator('article figure svg').first().waitFor({ timeout: 30_000 })
    await page.waitForTimeout(500) // let fonts settle before measuring

    const { clipped, nodes } = await page.evaluate(measureDiagramLabels, 'article figure')

    // Guard against a vacuous pass: if the selector matched no nodes at all,
    // this test would report success without measuring anything.
    if (id === 'architecture') {
      expect(nodes, 'no diagram nodes were measured — the check is vacuous').toBeGreaterThan(3)
    }

    for (const o of clipped) allClipped.push(`${id}: ${o}`)
  }

  expect(allClipped, 'these labels are cut off by their own node box').toEqual([])
})
