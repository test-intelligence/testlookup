import { expect, test, type Page, type Route } from '@playwright/test'

const PROJECT_ID = '00000000-0000-4000-8000-000000000101'
const REVIEW_ID = '00000000-0000-4000-8000-000000000201'

type ReviewState = 'pending_review' | 'accepted' | 'rejected'

const baseUser = {
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

async function seedSession(page: Page, role = 'QA_LEAD') {
  await page.addInitScript(({ user, projectId }) => {
    localStorage.setItem('auth-storage', JSON.stringify({
      state: {
        token: 'access',
        refreshToken: 'refresh',
        user,
        isAuthenticated: true,
      },
      version: 0,
    }))
    localStorage.setItem('testlookup-active-project', JSON.stringify({
      state: { activeProjectId: projectId },
      version: 0,
    }))
  }, { user: { ...baseUser, role }, projectId: PROJECT_ID })
}

function review(state: ReviewState, reasonCode: string | null = null, notes: string | null = null) {
  return {
    id: REVIEW_ID,
    project_id: PROJECT_ID,
    kind: 'report',
    subject_type: 'pipeline_run',
    subject_id: 'pipeline-1',
    pipeline_run_id: 'pipeline-1',
    test_run_id: 'run-1',
    workflow_type: 'deep',
    state,
    reviewed: state !== 'pending_review',
    reviewed_at: state === 'pending_review' ? null : '2026-09-16T04:00:00Z',
    reason_code: reasonCode,
    notes,
    evidence_bundle_sha256: null,
    superseded_by: null,
    created_at: '2026-09-16T03:00:00Z',
    requires_human_review: true,
    ai_disclaimer: 'AI-generated content. Verify before acting.',
    ai_disclaimer_version: '2026-09-12.v1',
  }
}

async function installApi(
  page: Page,
  options: {
    role?: string
    refuseSettlement?: boolean
    onSettlement?: (action: 'accept' | 'reject', body: unknown) => void
  } = {},
) {
  let state: ReviewState = 'pending_review'
  let reasonCode: string | null = null
  let notes: string | null = null
  const user = { ...baseUser, role: options.role ?? 'QA_LEAD' }

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (path === '/api/v1/auth/me') return json(route, user)
    if (path === '/api/v1/projects') {
      return json(route, [{ id: PROJECT_ID, name: 'Hermetic Project', description: '', is_active: true }])
    }
    if (path === '/api/v1/notifications/history') return json(route, [])
    if (path === '/api/v1/notifications/history/unread-count') return json(route, { unread: 0 })
    if (path === '/api/v1/saved-views') return json(route, [])

    if (path === `/api/v1/projects/${PROJECT_ID}/reviews`) {
      const filter = url.searchParams.get('state')
      const current = review(state, reasonCode, notes)
      return json(route, !filter || filter === state ? [current] : [])
    }

    const match = path.match(new RegExp(`^/api/v1/reviews/${REVIEW_ID}/(accept|reject)$`))
    if (match && request.method() === 'POST') {
      const action = match[1] as 'accept' | 'reject'
      const body = request.postDataJSON() as { reason_code?: string; notes?: string } | null
      options.onSettlement?.(action, body)
      if (options.refuseSettlement) {
        return json(route, { detail: 'The user who triggered this run cannot review its report.' }, 409)
      }
      state = action === 'accept' ? 'accepted' : 'rejected'
      reasonCode = body?.reason_code ?? null
      notes = body?.notes ?? null
      return json(route, review(state, reasonCode, notes))
    }

    return json(route, {})
  })
}

test.describe('hermetic human review gate', () => {
  test('accepts a pending report and moves it to the accepted queue', async ({ page }) => {
    const settlements: Array<{ action: string; body: unknown }> = []
    await seedSession(page)
    await installApi(page, { onSettlement: (action, body) => settlements.push({ action, body }) })

    await page.goto('/reviews')
    await expect(page.getByText('Awaiting review')).toBeVisible()
    await page.getByTestId('review-accept').click()
    await page.getByTestId('review-notes').fill('Evidence checked')
    await page.getByTestId('review-confirm').click()
    await expect(page.getByTestId('review-row')).toHaveCount(0)

    await page.getByRole('tab', { name: 'Accepted' }).click()
    await expect(page.getByRole('cell', { name: 'Accepted' })).toBeVisible()
    expect(settlements).toEqual([{ action: 'accept', body: { notes: 'Evidence checked' } }])
  })

  test('requires a rejection reason and preserves it in the rejected queue', async ({ page }) => {
    const settlements: Array<{ action: string; body: unknown }> = []
    await seedSession(page)
    await installApi(page, { onSettlement: (action, body) => settlements.push({ action, body }) })

    await page.goto('/reviews')
    await page.getByTestId('review-reject').click()
    await expect(page.getByTestId('review-confirm')).toBeDisabled()
    await page.getByTestId('review-reason').selectOption('unsupported_claim')
    await page.getByTestId('review-notes').fill('Citation does not support the claim')
    await page.getByTestId('review-confirm').click()
    await expect(page.getByTestId('review-row')).toHaveCount(0)

    await page.getByRole('tab', { name: 'Rejected' }).click()
    await expect(page.getByRole('cell', { name: 'Rejected' })).toBeVisible()
    await expect(page.getByText('Unsupported claim')).toBeVisible()
    expect(settlements).toEqual([{
      action: 'reject',
      body: { reason_code: 'unsupported_claim', notes: 'Citation does not support the claim' },
    }])
  })

  test('keeps the report pending when separation of duties refuses settlement', async ({ page }) => {
    await seedSession(page)
    await installApi(page, { refuseSettlement: true })

    await page.goto('/reviews')
    await page.getByTestId('review-accept').click()
    await page.getByTestId('review-confirm').click()

    await expect(page.getByText('The user who triggered this run cannot review its report.')).toBeVisible()
    await expect(page.getByText('Awaiting review')).toBeVisible()
    await expect(page.getByTestId('review-row')).toHaveCount(1)
  })

  test('renders the same pending queue read-only for a QA engineer', async ({ page }) => {
    await seedSession(page, 'QA_ENGINEER')
    await installApi(page, { role: 'QA_ENGINEER' })

    await page.goto('/reviews')
    await expect(page.getByTestId('review-readonly-note')).toBeVisible()
    await expect(page.getByText('Awaiting review')).toBeVisible()
    await expect(page.getByTestId('review-accept')).toHaveCount(0)
    await expect(page.getByTestId('review-reject')).toHaveCount(0)
  })
})
