import { expect, test, type Page, type Route } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { opened } from '../lib/inPage'

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
    // UX redesign P4 (D4): /reviews redirects to Inbox › Approvals, which also
    // reads the assigned failures (the other tab's count), the quarantine
    // proposals and the test-case review queue. Empty, in their real shapes.
    if (path === '/api/v1/me/assigned-failures') {
      return json(route, { items: [], total: 0, page: 1, size: 25, pages: 0, unresolved_total: 0 })
    }
    if (path === '/api/v1/quarantine') return json(route, [])
    if (path === '/api/v1/test-management/cases') {
      return json(route, { items: [], total: 0, page: 1, size: 50, pages: 0 })
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

    const skipLink = page.getByRole('link', { name: 'Skip to main content' })
    // A Tab pressed before the shell mounts lands on the document, never the
    // link (failed once in a full local run under load; passed on repeat).
    await skipLink.waitFor({ state: 'attached' })
    await page.keyboard.press('Tab')
    await expect(skipLink).toBeFocused()
    await expect(skipLink).toBeVisible()
    await page.keyboard.press('Enter')

    const main = page.getByRole('main')
    await expect(main).toBeFocused()
    await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible()
  })

  // W27b PAN-05 (§2.3): New Project is a section of the page, not a dialog. Focus moves in on Project Name; Tab is NOT
  // trapped (from the last control it leaves the section, as anywhere in the page); Escape inside closes it and focus
  // returns to New Project.
  test('the in-page New Project form takes focus without trapping Tab and restores the invoking control', async ({ page }) => {
    await page.goto('/projects')
    const newProject = page.getByRole('button', { name: 'New Project' })
    await newProject.focus()
    await newProject.press('Enter')

    const section = opened(page, 'New Project')
    await expect(section).toBeVisible()
    await expect(page.locator('[aria-modal="true"]'), 'no modal').toHaveCount(0)
    await expect(page.getByRole('dialog'), 'no dialog').toHaveCount(0)
    const name = page.getByLabel('Project Name *')
    await expect(name).toBeFocused()

    const focusable = section.locator(
      'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),[tabindex]:not([tabindex="-1"])',
    )
    const inside = () => section.evaluate((el) => el.contains(document.activeElement))
    await focusable.last().focus()
    await page.keyboard.press('Tab')
    expect(await inside(), 'Tab from the last control leaves the section (not trapped)').toBe(false)
    await focusable.first().focus()
    await page.keyboard.press('Shift+Tab')
    expect(await inside(), 'Shift+Tab from the first control leaves it too').toBe(false)

    await name.focus()
    await page.keyboard.press('Escape')
    await expect(section).toHaveCount(0)
    await expect(newProject).toBeFocused()
  })

  for (const path of ['/reviews', '/projects']) {
    test(`${path} has no serious or critical automated accessibility violations`, async ({ page }) => {
      await page.goto(path)
      await expect(page.getByRole('main')).toBeVisible()
      // P4 (D4): /reviews is Inbox › Approvals now. Wait for its queues, so the
      // scan sees the tabs, the source filter and the drawn tables, not a spinner.
      if (path === '/reviews') await expect(page.getByTestId('review-row')).toBeVisible()

      const result = await new AxeBuilder({ page }).analyze()
      const blocking = result.violations.filter(
        (violation) => violation.impact === 'serious' || violation.impact === 'critical',
      )

      expect(blocking).toEqual([])
    })
  }
})
