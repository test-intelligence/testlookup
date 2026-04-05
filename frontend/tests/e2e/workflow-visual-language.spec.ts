import { expect, test } from '@playwright/test'
import { performRealLogin } from './realLoginHelper'

test.describe('Workflow visual language', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page)
  })

  test('integration health uses the shared workflow timeline', async ({ page }) => {
    await page.goto('/settings/integration-health')
    await expect(page.getByText('Health workflow')).toBeVisible({ timeout: 15000 })
    await expect(page.getByText('Integration Health')).toBeVisible()
  })

  test('ai evaluation uses the shared workflow timeline', async ({ page }) => {
    await page.goto('/settings/ai-eval')
    await expect(page.getByText('Evaluation workflow')).toBeVisible({ timeout: 15000 })
    await expect(page.getByText('AI Evaluation Dashboard')).toBeVisible()
  })

  test('audit dashboard uses the shared workflow timeline', async ({ page }) => {
    await page.goto('/settings/audit')
    await expect(page.getByText('Tenant audit flow')).toBeVisible({ timeout: 15000 })
    await expect(page.getByText('Audit Dashboard')).toBeVisible()
  })
})
