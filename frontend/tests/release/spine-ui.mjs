import { chromium, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'

// The Python journey supplies only its run-owned browser state through stdin.
const { project, user, token, run, release, build, failure } = JSON.parse(readFileSync(0, 'utf8'))
const browser = await chromium.launch()
let hop = 'sign-in'
try {
  const page = await browser.newPage({ baseURL: process.env.TL_WEB })
  await page.goto('/login')
  await page.evaluate(({ project, user, token }) => {
    localStorage.setItem('auth-storage', JSON.stringify({ state: { token, user }, version: 0 }))
    localStorage.setItem('testlookup-active-project', JSON.stringify({
      state: { activeProjectId: project.id, activeProject: project }, version: 0,
    }))
  }, { project, user, token })
  hop = 'run list'
  await page.goto('/runs')
  await expect(page.getByRole('checkbox', { name: 'Select #' + build })).toBeVisible({ timeout: 15000 })
  hop = 'run detail'
  await page.goto('/runs/' + run)
  await expect(page.getByText(failure, { exact: true }).first()).toBeVisible({ timeout: 15000 })
  hop = 'analysis'
  const analysis = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/runs/' + run + '/intelligence')
  await page.goto('/runs/' + run + '?tab=analysis')
  const result = await analysis
  const document = await result.json()
  if (result.status() !== 200 || document.run.id !== run || document.run.failed_tests !== 1)
    throw new Error('Analysis response returned the wrong evidence')
  const failures = page.getByRole('region', { name: 'What failed' })
  // The upload intentionally omits AI analysis. Its supported drill-down must
  // still lead to the precise case whose failure the analysis page counts.
  await expect(failures).toContainText('1 failure', { timeout: 15000 })
  const failedTests = failures.getByRole('link', { name: /Open the failed tests/ })
  await expect(failedTests).toHaveAttribute('href', '/runs/' + run + '?tab=tests&status=FAILED')
  await failedTests.click()
  await expect(page.getByText(failure, { exact: true }).first()).toBeVisible()
  hop = 'project summary'
  await page.goto('/reports/summary')
  await expect(page.locator('body')).toContainText(failure, { timeout: 15000 })
  hop = 'release gate blockers'
  await page.goto('/releases/' + release)
  await expect(page.getByRole('list', { name: 'Blocking' })).toContainText(failure, { timeout: 15000 })
} catch {
  console.log('Failed to present the seeded evidence on ' + hop)
  process.exitCode = 1
} finally {
  await browser.close()
}
