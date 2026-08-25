/** Rendering sweep: every documentation page renders as formatted content. */
import { test, expect, type Page } from '@playwright/test'

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
