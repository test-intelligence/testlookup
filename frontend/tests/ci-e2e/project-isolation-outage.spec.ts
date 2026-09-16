import { expect, test, type Page, type Route } from '@playwright/test'

const PROJECT_A = '00000000-0000-4000-8000-000000000101'
const PROJECT_B = '00000000-0000-4000-8000-000000000102'

const user = {
  id: '00000000-0000-4000-8000-000000000001',
  email: 'lead@example.test',
  username: 'qa_lead',
  full_name: 'QA Lead',
  role: 'QA_LEAD',
  is_active: true,
  must_change_password: false,
  avatar_color: null,
}

const projects = [
  { id: PROJECT_A, name: 'Alpha Project', slug: 'alpha-project', description: '', is_active: true, created_at: '2026-09-16T03:00:00Z' },
  { id: PROJECT_B, name: 'Beta Project', slug: 'beta-project', description: '', is_active: true, created_at: '2026-09-16T03:00:00Z' },
]

function run(id: string, projectId: string, projectName: string, buildNumber: string) {
  return {
    id,
    project_id: projectId,
    project_name: projectName,
    build_number: buildNumber,
    branch: projectId === PROJECT_A ? 'alpha-branch' : 'beta-branch',
    status: 'PASSED',
    passed_tests: 10,
    failed_tests: 0,
    skipped_tests: 0,
    broken_tests: 0,
    total_tests: 10,
    pass_rate: 100,
    duration_ms: 1000,
    created_at: '2026-09-16T03:00:00Z',
    start_time: '2026-09-16T03:00:00Z',
    end_time: '2026-09-16T03:00:01Z',
    ingestion_source: 'upload',
    primary_suite_name: projectId === PROJECT_A ? 'alpha-suite' : 'beta-suite',
    suite_names: [projectId === PROJECT_A ? 'alpha-suite' : 'beta-suite'],
    run_seq: 1,
  }
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

test('a project switch cannot reveal stale data and recovers from a scoped outage', async ({ page }) => {
  const runProjectIds: Array<string | null> = []
  let betaAttempts = 0
  let betaRecovered = false

  await page.addInitScript(({ seedUser, projectId }) => {
    localStorage.setItem('auth-storage', JSON.stringify({
      state: { token: 'access', refreshToken: 'refresh', user: seedUser, isAuthenticated: true },
      version: 0,
    }))
    localStorage.setItem('testlookup-active-project', JSON.stringify({
      state: { activeProjectId: projectId },
      version: 0,
    }))
  }, { seedUser: user, projectId: PROJECT_A })

  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (path === '/api/v1/auth/me') return json(route, user)
    if (path === '/api/v1/projects') return json(route, projects)
    if (path === '/api/v1/notifications/history') return json(route, [])
    if (path === '/api/v1/notifications/history/unread-count') return json(route, { unread: 0 })
    if (path === '/api/v1/saved-views') return json(route, [])
    if (path === '/api/v1/test-management/suites') return json(route, [])
    if (path === '/api/v1/releases') return json(route, { items: [], total: 0, page: 1, size: 100 })
    if (path === '/api/v1/me/assigned-failures/count') return json(route, { count: 0 })
    if (path === '/api/v1/runs') {
      const projectId = url.searchParams.get('project_id')
      runProjectIds.push(projectId)
      if (projectId === PROJECT_A) {
        return json(route, { items: [run('run-a', PROJECT_A, 'Alpha Project', 'A-101')], total: 1, page: 1, size: 500, pages: 1 })
      }
      if (projectId === PROJECT_B) {
        betaAttempts += 1
        if (!betaRecovered) {
          await new Promise(resolve => setTimeout(resolve, 150))
          return json(route, { detail: 'temporary outage' }, 503)
        }
        return json(route, { items: [run('run-b', PROJECT_B, 'Beta Project', 'B-202')], total: 1, page: 1, size: 500, pages: 1 })
      }
      return json(route, { detail: 'project_id required' }, 400)
    }
    return json(route, {})
  })

  await page.goto('/runs')
  const picker = page.getByRole('combobox', { name: 'Select project' })
  await expect(picker).toHaveValue(PROJECT_A)
  await expect(page.getByRole('checkbox', { name: 'Select #A-101' })).toBeVisible()

  await picker.selectOption(PROJECT_B)
  await expect(page.getByTestId('runs-data-unavailable')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: 'Select #A-101' })).toHaveCount(0)

  betaRecovered = true
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByRole('checkbox', { name: 'Select #B-202' })).toBeVisible()

  await page.goto('/runs?m02-history=forward')
  await expect(page.getByRole('checkbox', { name: 'Select #B-202' })).toBeVisible()
  await expect(page.getByRole('checkbox', { name: 'Select #A-101' })).toHaveCount(0)

  await page.goBack()
  await expect(page).toHaveURL(/\/runs$/)
  await expect(page.getByRole('checkbox', { name: 'Select #B-202' })).toBeVisible()
  await expect(page.getByRole('checkbox', { name: 'Select #A-101' })).toHaveCount(0)

  await page.goForward()
  await expect(page).toHaveURL(/m02-history=forward/)
  await expect(page.getByRole('checkbox', { name: 'Select #B-202' })).toBeVisible()
  await expect(page.getByRole('checkbox', { name: 'Select #A-101' })).toHaveCount(0)

  expect(runProjectIds).toContain(PROJECT_A)
  expect(betaAttempts).toBeGreaterThanOrEqual(2)
  expect(runProjectIds.every((id) => id === PROJECT_A || id === PROJECT_B)).toBe(true)
})
