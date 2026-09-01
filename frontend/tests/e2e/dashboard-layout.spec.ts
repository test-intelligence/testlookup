import { test, expect } from '@playwright/test'
import { performRealLogin } from './realLoginHelper'

async function readTopRowLayout(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const heading = [...document.querySelectorAll('h3')]
      .find((element) => element.textContent?.trim() === 'Quality workflow')
    const workflowCard = heading?.closest('.card')
    const grid = workflowCard?.parentElement
    if (!grid) return null

    const cards = [...grid.children].map((card) => {
      const rect = card.getBoundingClientRect()
      return { width: rect.width, top: rect.top, height: rect.height }
    })
    const lede = grid.querySelector('p.text-\\[13px\\]')?.getBoundingClientRect()

    return {
      columns: getComputedStyle(grid).gridTemplateColumns,
      cards,
      ledeWidth: lede?.width ?? 0,
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

  test('keeps readiness content readable beside the workflow ribbon at xl width', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 })
    await page.getByRole('combobox', { name: 'Select project' }).selectOption({ label: 'All Projects' })
    await page.waitForTimeout(500)

    const layout = await readTopRowLayout(page)
    expect(layout).not.toBeNull()
    if (!layout) throw new Error('Dashboard top-row layout was not found')
    const [readinessCard, workflowCard] = layout.cards
    if (!readinessCard || !workflowCard) throw new Error('Dashboard top-row cards were not found')
    expect(layout.cards).toHaveLength(2)
    expect(readinessCard.width).toBeGreaterThan(300)
    expect(workflowCard.width).toBeGreaterThan(500)
    expect(layout.ledeWidth).toBeGreaterThan(150)
    expect(layout.documentWidth).toBe(layout.viewportWidth)
  })

  test('keeps the top row single-column below the xl breakpoint', async ({ page }) => {
    await page.setViewportSize({ width: 1279, height: 720 })
    const layout = await readTopRowLayout(page)

    expect(layout).not.toBeNull()
    if (!layout) throw new Error('Dashboard top-row layout was not found')
    const [readinessCard, workflowCard] = layout.cards
    if (!readinessCard || !workflowCard) throw new Error('Dashboard top-row cards were not found')
    expect(layout.cards).toHaveLength(2)
    expect(readinessCard.width).toBeGreaterThan(900)
    expect(workflowCard.width).toBeGreaterThan(900)
    expect(workflowCard.top).toBeGreaterThan(readinessCard.top + readinessCard.height - 1)
  })
})
