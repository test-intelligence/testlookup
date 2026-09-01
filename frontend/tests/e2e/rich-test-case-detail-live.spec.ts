import { expect, test, type Page } from '@playwright/test'
import { performRealLogin } from './realLoginHelper'

const richRunId = process.env.E2E_RICH_RUN_ID
const richTestId = process.env.E2E_RICH_TEST_ID
const sparseRunId = process.env.E2E_RICH_SPARSE_RUN_ID
const sparseTestId = process.env.E2E_RICH_SPARSE_TEST_ID

async function expandStep(page: Page, name: string) {
  const stepName = page.getByText(name, { exact: true })
  await expect(stepName).toBeVisible()

  const row = stepName.locator('xpath=../../..')
  const toggle = row.getByRole('button', { name: 'Expand step' })
  await expect(toggle).toBeVisible()
  await toggle.click()
}

test.describe('Rich test-case detail — deployed Allure contract', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page)
  })

  test('renders source metadata, nested steps, attachments, and redacted parameters', async ({ page }) => {
    test.skip(!richRunId || !richTestId, 'Set E2E_RICH_RUN_ID and E2E_RICH_TEST_ID to a seeded Allure case')

    await page.goto(`/runs/${richRunId}/tests/${richTestId}`)

    await expect(page.getByRole('heading', { name: /rich_detail_production_validation_/ })).toBeVisible()
    const detailRegion = page.getByRole('region', { name: 'Enriched test case details' })
    await expect(detailRegion).toBeVisible()
    await expect(detailRegion.getByText('test-case-detail v1', { exact: true })).toBeVisible()
    await expect(detailRegion.getByText('Auth Service', { exact: true })).toBeVisible()
    await expect(detailRegion.getByText('RichDetailProductionTests', { exact: true })).toBeVisible()
    await expect(detailRegion.getByText('allure', { exact: true }).first()).toBeVisible()
    await expect(detailRegion.getByText('2.29.0', { exact: true })).toBeVisible()
    await expect(detailRegion.getByRole('link', { name: 'Epic contract' })).toBeVisible()

    await expect(page.getByText('Authenticate as administrator', { exact: true })).toBeVisible()
    await expect(page.getByText('Open rich test case detail', { exact: true })).toBeVisible()
    await expect(page.getByText('Verify sparse fields remain optional', { exact: true })).toBeVisible()
    await expect(page.getByText('production-evidence.txt', { exact: true })).toBeVisible()

    await expandStep(page, 'Authenticate as administrator')
    await expect(page.getByText('Masked', { exact: true })).toBeVisible()
    await expect(page.getByText('must-not-leak', { exact: true })).toHaveCount(0)

    await expandStep(page, 'Open rich test case detail')
    await expect(page.getByText('Render source metadata', { exact: true })).toBeVisible()
    await expect(page.getByText('Render optional step tree', { exact: true })).toBeVisible()
    await expect(page.getByText('detail-screenshot.png', { exact: true })).toBeVisible()
  })

  test('renders a safe empty state when the source has no steps', async ({ page }) => {
    test.skip(
      !sparseRunId || !sparseTestId,
      'Set E2E_RICH_SPARSE_RUN_ID and E2E_RICH_SPARSE_TEST_ID to a seeded sparse Allure case',
    )

    await page.goto(`/runs/${sparseRunId}/tests/${sparseTestId}`)

    await expect(page.getByRole('heading', { name: /rich_detail_sparse_validation_/ })).toBeVisible()
    await expect(page.getByText('test-case-detail v1', { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Steps', exact: true })).toBeVisible()
    await expect(page.getByText('No granular steps captured for this test.', { exact: true })).toBeVisible()
    await expect(page.getByText('Test case not found', { exact: true })).toHaveCount(0)
  })
})
