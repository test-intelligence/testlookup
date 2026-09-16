import { expect, test } from '@playwright/test'

const BACKEND_URL = process.env.VITE_API_BASE_URL || 'http://localhost:8000'

function required(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error(`${name} is required`)
  return value
}

test.describe('Intelligence to release lineage — live read contract', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('preserves run, cluster, defect, and release facts across UI reloads', async ({
    page,
    request,
  }) => {
    const adminPassword = required('E2E_ADMIN_PASSWORD')
    const projectId = required('E2E_M04_PROJECT_ID')
    const releaseId = required('E2E_M04_RELEASE_ID')
    const releaseName = required('E2E_M04_RELEASE_NAME')
    const runId = required('E2E_M04_RUN_ID')
    const buildNumber = required('E2E_M04_BUILD_NUMBER')
    const clusterLabel = required('E2E_M04_CLUSTER_LABEL')
    const defectId = required('E2E_M04_DEFECT_ID')
    const decisionId = required('E2E_M04_DECISION_ID')

    const login = await request.post(`${BACKEND_URL}/api/v1/auth/login`, {
      form: { username: 'admin', password: adminPassword },
    })
    expect(login.status(), await login.text()).toBe(200)
    const session = await login.json()
    const headers = { Authorization: `Bearer ${session.access_token}` }

    const projectResponse = await request.get(
      `${BACKEND_URL}/api/v1/projects/${projectId}`,
      { headers },
    )
    expect(projectResponse.status(), await projectResponse.text()).toBe(200)
    const project = await projectResponse.json()

    await page.goto('/login')
    await page.evaluate(
      ({ accessToken, refreshToken, activeProject }) => {
        localStorage.setItem(
          'auth-storage',
          JSON.stringify({
            state: { token: accessToken, refreshToken },
            version: 0,
          }),
        )
        localStorage.setItem(
          'testlookup-active-project',
          JSON.stringify({
            state: {
              activeProjectId: activeProject.id,
              activeProject,
            },
            version: 0,
          }),
        )
      },
      {
        accessToken: session.access_token,
        refreshToken: session.refresh_token,
        activeProject: project,
      },
    )

    await page.goto(`/runs/${runId}/intelligence`)
    await expect(page.getByText(buildNumber, { exact: true })).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.getByText(clusterLabel, { exact: true }).first()).toBeVisible()
    await expect(page.getByText(/Human review required/i).first()).toBeVisible()

    await page.reload({ waitUntil: 'domcontentloaded' })
    await expect(page.getByText(buildNumber, { exact: true })).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.getByText(clusterLabel, { exact: true }).first()).toBeVisible()

    await page.goto('/defects')
    await expect(page.getByText('payment_authorize', { exact: true })).toBeVisible({
      timeout: 30_000,
    })

    await page.goto(`/releases/${releaseId}`)
    await expect(page.getByText(releaseName, { exact: true }).first()).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.getByText('Linked Test Runs (1)', { exact: true })).toBeVisible()
    await expect(page.getByText(buildNumber, { exact: true })).toBeVisible()

    const runResponse = await request.get(`${BACKEND_URL}/api/v1/runs/${runId}`, {
      headers,
    })
    expect(runResponse.status(), await runResponse.text()).toBe(200)
    const run = await runResponse.json()
    expect(run.project_id).toBe(projectId)
    expect(run.release_id).toBe(releaseId)
    expect(run.total_tests).toBe(5)
    expect(run.failed_tests).toBe(1)

    const defectsResponse = await request.get(`${BACKEND_URL}/api/v1/analytics/defects`, {
      headers,
      params: { project_id: projectId, size: 100 },
    })
    expect(defectsResponse.status(), await defectsResponse.text()).toBe(200)
    const defects = await defectsResponse.json()
    expect(defects.items.map((item: { id: string }) => item.id)).toContain(defectId)

    const gateResponse = await request.get(
      `${BACKEND_URL}/api/v1/releases/${releaseId}/gate`,
      { headers },
    )
    expect(gateResponse.status(), await gateResponse.text()).toBe(200)
    const gate = await gateResponse.json()
    expect(gate.verdict).toBe('NO_GO')
    expect(gate.from_snapshot).toBe(true)
    expect(gate.scorecard.denominator).toBe(5)
    expect(gate.scorecard.evidence_count).toBe(5)

    // The evaluation POST owns the append-only decision id. Keep it in the
    // live fixture manifest even though the standing GET deliberately exposes
    // the snapshot without its audit-row id.
    expect(decisionId).toMatch(/^[0-9a-f-]{36}$/)
  })
})
