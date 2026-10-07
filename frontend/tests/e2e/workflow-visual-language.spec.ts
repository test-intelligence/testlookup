import { expect, test, type Page } from '@playwright/test'
import { performRealLogin } from './realLoginHelper'

/**
 * The shared WorkflowTimeline sits in a collapsed "Pipeline" disclosure at the
 * bottom of each page (UX redesign P2). The disclosure renders nothing while
 * closed, so each test opens it before asserting the timeline.
 */
async function openPipeline(page: Page) {
  const toggle = page.getByRole('button', { name: 'Pipeline', exact: true })
  await expect(toggle).toHaveAttribute('aria-expanded', 'false', { timeout: 15000 })
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
}

test.describe('Workflow visual language', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page)
  })

  test('integration health uses the shared workflow timeline', async ({ page }) => {
    await page.goto('/settings/integration-health')
    await expect(page.getByText('Integration Health')).toBeVisible({ timeout: 15000 })
    await expect(page.getByText('Health workflow')).toHaveCount(0)
    await openPipeline(page)
    await expect(page.getByText('Health workflow')).toBeVisible()
  })

  test('ai evaluation uses the shared workflow timeline', async ({ page }) => {
    await page.goto('/settings/ai-eval')
    await expect(page.getByText('AI Evaluation Dashboard')).toBeVisible({ timeout: 15000 })
    await expect(page.getByText('Evaluation workflow')).toHaveCount(0)
    await openPipeline(page)
    await expect(page.getByText('Evaluation workflow')).toBeVisible()
  })

  test('audit dashboard uses the shared workflow timeline', async ({ page }) => {
    await page.goto('/settings/audit')
    await expect(page.getByText('Audit Dashboard')).toBeVisible({ timeout: 15000 })
    await expect(page.getByText('Tenant audit flow')).toHaveCount(0)
    await openPipeline(page)
    await expect(page.getByText('Tenant audit flow')).toBeVisible()
  })
})
