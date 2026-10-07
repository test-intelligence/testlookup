import { test, expect } from '@playwright/test'
import { performRealLogin } from './realLoginHelper'

// P2 removed the "Quality workflow" ribbon that shared the top row with the
// release-readiness verdict (its four stages were invented, re-stating the
// verdict's and the KPIs' numbers). The verdict is now the top row on its own:
// these checks keep the spec's intent — the readiness content stays readable
// and nothing pushes the page sideways — for the layout that remains.
async function readTopRowLayout(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const verdict = document.querySelector('section[aria-label="Release readiness"]')
    const row = verdict?.parentElement
    if (!verdict || !row) return null

    const rect = verdict.getBoundingClientRect()
    const rowRect = row.getBoundingClientRect()
    const lede = verdict.querySelector('p.text-\\[13px\\]')?.getBoundingClientRect()
    const ribbon = [...document.querySelectorAll('h3')]
      .some((element) => element.textContent?.trim() === 'Quality workflow')

    return {
      verdictWidth: rect.width,
      rowWidth: rowRect.width,
      ledeWidth: lede?.width ?? 0,
      ribbon,
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
    }
  })
}

test.describe('Dashboard responsive layout', () => {
  test.beforeEach(async ({ page }) => {
    // performRealLogin already finishes on a loaded /overview page. A second
    // same-URL goto can race Firefox's final auth redirect and be aborted with
    // NS_BINDING_ABORTED before the layout assertion ever runs.
    await performRealLogin(page)
    await expect(page.getByRole('heading', { name: 'Dashboard', level: 1 })).toBeVisible()
  })

  test('gives the readiness verdict the full top row at xl width, with no workflow ribbon', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 })
    await page.getByRole('combobox', { name: 'Select project' }).selectOption({ label: 'All Projects' })
    await expect(page.getByRole('region', { name: 'Release readiness' })).toBeVisible()

    const layout = await readTopRowLayout(page)
    expect(layout).not.toBeNull()
    if (!layout) throw new Error('Dashboard readiness verdict was not found')
    expect(layout.ribbon).toBe(false)
    // The verdict spans its row (it used to be the narrower of two columns).
    expect(layout.verdictWidth).toBeGreaterThan(900)
    expect(layout.verdictWidth).toBeGreaterThan(layout.rowWidth - 1)
    expect(layout.ledeWidth).toBeGreaterThan(150)
    expect(layout.documentWidth).toBe(layout.viewportWidth)
  })

  test('keeps the readiness verdict full-width below the xl breakpoint', async ({ page }) => {
    await page.setViewportSize({ width: 1279, height: 720 })
    await expect(page.getByRole('region', { name: 'Release readiness' })).toBeVisible()
    const layout = await readTopRowLayout(page)

    expect(layout).not.toBeNull()
    if (!layout) throw new Error('Dashboard readiness verdict was not found')
    expect(layout.ribbon).toBe(false)
    expect(layout.verdictWidth).toBeGreaterThan(900)
    expect(layout.verdictWidth).toBeGreaterThan(layout.rowWidth - 1)
    expect(layout.ledeWidth).toBeGreaterThan(150)
    expect(layout.documentWidth).toBe(layout.viewportWidth)
  })
})
