import { expect, test, type Page, type Route } from '@playwright/test'

const PROJECT_ID = '00000000-0000-4000-8000-000000000101'
const REVIEW_ID = '00000000-0000-4000-8000-000000000201'

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

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function seedSession(page: Page) {
  await page.addInitScript(({ seedUser, projectId }) => {
    localStorage.setItem('auth-storage', JSON.stringify({
      state: {
        token: 'access',
        refreshToken: 'refresh',
        user: seedUser,
        isAuthenticated: true,
      },
      version: 0,
    }))
    localStorage.setItem('testlookup-active-project', JSON.stringify({
      state: { activeProjectId: projectId },
      version: 0,
    }))
  }, { seedUser: user, projectId: PROJECT_ID })
}

async function installApi(page: Page) {
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/auth/me') return json(route, user)
    if (path === '/api/v1/projects') {
      return json(route, [{
        id: PROJECT_ID,
        name: 'Hermetic Project',
        slug: 'hermetic-project',
        description: '',
        is_active: true,
        created_at: '2026-09-16T03:00:00Z',
      }])
    }
    if (path === '/api/v1/notifications/history') return json(route, [])
    if (path === '/api/v1/notifications/history/unread-count') return json(route, { unread: 0 })
    if (path === '/api/v1/saved-views') return json(route, [])
    if (path === `/api/v1/projects/${PROJECT_ID}/reviews`) {
      return json(route, [{
        id: REVIEW_ID,
        project_id: PROJECT_ID,
        kind: 'report',
        subject_type: 'pipeline_run',
        subject_id: 'pipeline-1',
        pipeline_run_id: 'pipeline-1',
        test_run_id: 'run-1',
        workflow_type: 'deep',
        state: 'pending_review',
        reviewed: false,
        reviewed_at: null,
        reason_code: null,
        notes: null,
        evidence_bundle_sha256: null,
        superseded_by: null,
        created_at: '2026-09-16T03:00:00Z',
        requires_human_review: true,
        ai_disclaimer: 'AI-generated content. Verify before acting.',
        ai_disclaimer_version: '2026-09-12.v1',
      }])
    }
    return json(route, {})
  })
}

test.describe('hermetic keyboard and landmark journeys', () => {
  test.beforeEach(async ({ page }) => {
    await seedSession(page)
    await installApi(page)
  })

  test('skips repeated navigation and focuses the main landmark', async ({ page }) => {
    await page.goto('/reviews')

    await page.keyboard.press('Tab')
    const skipLink = page.getByRole('link', { name: 'Skip to main content' })
    await expect(skipLink).toBeFocused()
    await expect(skipLink).toBeVisible()
    await page.keyboard.press('Enter')

    const main = page.getByRole('main')
    await expect(main).toBeFocused()
    await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible()
  })

  test('traps focus in the project dialog and restores the invoking control', async ({ page }) => {
    await page.goto('/projects')
    const newProject = page.getByRole('button', { name: 'New Project' })
    await newProject.focus()
    await newProject.press('Enter')

    const dialog = page.getByRole('dialog', { name: 'New Project' })
    await expect(dialog).toBeVisible()
    await expect(page.getByLabel('Project Name *')).toBeFocused()

    const focusable = dialog.locator(
      'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),[tabindex]:not([tabindex="-1"])',
    )
    const first = focusable.first()
    const last = focusable.last()
    await first.focus()
    await page.keyboard.press('Shift+Tab')
    await expect(last).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(first).toBeFocused()

    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
    await expect(newProject).toBeFocused()
  })
})
