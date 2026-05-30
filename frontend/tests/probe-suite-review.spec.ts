/**
 * Diagnostic probe for the "Review later" 500.
 *
 * Strategy: sign in via dev-login, find a real (test_run_id, suite_name)
 * pair via GET /runs + GET /suites, then PUT the suite-review endpoint
 * with state=review_later and capture the response body.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'

test('suite-review probe — capture the 500 body', async ({ page, request }) => {
  // Use the API client directly (bypass UI) with the dev-login JWT.
  const loginResp = await request.post(`${BASE}/api/v1/auth/dev-login?role=admin`)
  if (!loginResp.ok()) {
    console.log('dev-login failed:', loginResp.status(), await loginResp.text())
    expect(false).toBe(true)
    return
  }
  const { access_token } = await loginResp.json() as { access_token: string }
  const auth = { Authorization: `Bearer ${access_token}` }

  // Find a recent run with a suite to target.
  const runsResp = await request.get(`${BASE}/api/v1/runs?page=1&size=10`, { headers: auth })
  console.log('runs status:', runsResp.status())
  const runsBody = runsResp.ok() ? await runsResp.json() : { items: [] }
  const runs = (runsBody as { items?: unknown[] }).items ?? []
  console.log('runs found:', runs.length)
  if (runs.length === 0) {
    console.log('no runs to target — skipping')
    expect(true).toBe(true)
    return
  }
  const firstRun = runs[0] as { id: string; project_id?: string }
  console.log('first run id:', firstRun.id)

  // Try PUT review with several common suite names.
  for (const suiteName of ['checkout-api', 'notifications', 'auth-api', 'smoke', 'unknown']) {
    const putResp = await request.put(
      `${BASE}/api/v1/test-management/suite-reviews/by-run/${firstRun.id}/${encodeURIComponent(suiteName)}`,
      {
        headers: { ...auth, 'Content-Type': 'application/json' },
        data: { state: 'review_later', note: 'probe' },
      },
    )
    const body = await putResp.text()
    console.log(`\n--- PUT ${suiteName} ---`)
    console.log('  status:', putResp.status())
    console.log('  body:', body.slice(0, 1500))
  }

  expect(true).toBe(true)
})
