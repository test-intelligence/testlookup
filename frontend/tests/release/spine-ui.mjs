import { chromium, expect as playwrightExpect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import process from 'node:process'
import { URL } from 'node:url'

// The Python journey supplies only its run-owned browser state through stdin.
const { project, user, token, run, release, build, failure } = JSON.parse(readFileSync(0, 'utf8'))
const browser = await chromium.launch()
const expect = playwrightExpect.configure({ timeout: 45000 })
let hop = 'sign-in'
let listStatus = 'unmeasured'
let listProject = false
let listContainsRun = false
try {
  const page = await browser.newPage({ baseURL: process.env.TL_WEB })
  page.on('response', async response => {
    const url = new URL(response.url())
    if (url.pathname.replace(/\/$/, '') !== '/api/v1/runs') return
    listStatus = response.status()
    listProject = url.searchParams.get('project_id') === project.id
    try {
      const data = await response.json()
      listContainsRun = Array.isArray(data.items) && data.items.some(row => row.id === run)
    } catch { /* An unavailable response remains visible in the status. */ }
  })
  const navigate = path => page.goto(path, { waitUntil: 'domcontentloaded', timeout: 45000 })
  await navigate('/login')
  await page.evaluate(({ project, user, token }) => {
    globalThis.localStorage.setItem('auth-storage', JSON.stringify({ state: { token, user }, version: 0 }))
    globalThis.localStorage.setItem('testlookup-active-project', JSON.stringify({
      state: { activeProjectId: project.id, activeProject: project }, version: 0,
    }))
  }, { project, user, token })
  hop = 'run list'
  await navigate('/runs')
  await expect(page.getByRole('checkbox', { name: 'Select #' + build })).toBeVisible()
  hop = 'run detail'
  await navigate('/runs/' + run)
  await expect(page.getByText(failure, { exact: true }).first()).toBeVisible()
  hop = 'analysis'
  const analysis = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/runs/' + run + '/intelligence')
  await navigate('/runs/' + run + '?tab=analysis')
  const result = await analysis
  const document = await result.json()
  if (result.status() !== 200 || document.run.id !== run || document.run.failed_tests !== 1)
    throw new Error('Analysis response returned the wrong evidence')
  const failures = page.getByRole('region', { name: 'What failed' })
  // The upload intentionally omits AI analysis. Its supported drill-down must
  // still lead to the precise case whose failure the analysis page counts.
  await expect(failures).toContainText('1 failure')
  const failedTests = failures.getByRole('link', { name: /Open the failed tests/ })
  await expect(failedTests).toHaveAttribute('href', '/runs/' + run + '?tab=tests&status=FAILED')
  await failedTests.click()
  await expect(page.getByText(failure, { exact: true }).first()).toBeVisible()
  hop = 'project summary'
  await navigate('/reports/summary')
  await expect(page.locator('body')).toContainText(failure)
  hop = 'release gate blockers'
  await navigate('/releases/' + release)
  await expect(page.getByRole('list', { name: 'Blocking' })).toContainText(failure)
} catch {
  process.stdout.write(`Failed to present the seeded evidence on ${hop}. List HTTP ${listStatus}, project matched ${listProject}, run present ${listContainsRun}.\n`)
  process.exitCode = 1
} finally {
  await browser.close()
}
