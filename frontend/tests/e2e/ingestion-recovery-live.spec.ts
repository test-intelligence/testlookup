import { expect, test } from '@playwright/test'

const BACKEND_URL = process.env.VITE_API_BASE_URL || 'http://localhost:8000'

test.describe('Ingestion recovery — live upload contract', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('accepted UI upload survives an immediate page refresh', async ({
    page,
    request,
  }) => {
    test.slow()
    const adminPassword = process.env.E2E_ADMIN_PASSWORD
    if (!adminPassword) throw new Error('E2E_ADMIN_PASSWORD is required')

    const marker = `exp-m03-ui-${Date.now()}`
    let token = ''
    let projectId = ''
    let primaryFailure: unknown
    const cleanupErrors: Error[] = []

    try {
      const login = await request.post(`${BACKEND_URL}/api/v1/auth/login`, {
        form: { username: 'admin', password: adminPassword },
      })
      expect(login.status(), await login.text()).toBe(200)
      const session = await login.json()
      token = session.access_token

      const createProject = await request.post(`${BACKEND_URL}/api/v1/projects`, {
        headers: { Authorization: `Bearer ${token}` },
        data: { name: `M03 UI ${marker}`, slug: marker },
      })
      expect(createProject.status(), await createProject.text()).toBe(201)
      const project = await createProject.json()
      projectId = project.id

      await page.goto('/login')
      await page.evaluate(
        ({ accessToken, refreshToken, selectedProject }) => {
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
                activeProjectId: selectedProject.id,
                activeProject: selectedProject,
              },
              version: 0,
            }),
          )
        },
        {
          accessToken: session.access_token,
          refreshToken: session.refresh_token,
          selectedProject: project,
        },
      )

      await page.goto('/runs?upload=1')
      await expect(
        page.getByRole('heading', { name: 'Upload test report' }),
      ).toBeVisible()

      const report =
        '<testsuite name="RefreshSuite"><testcase name="durable_after_refresh"/></testsuite>'
      const padding = `<!--${'x'.repeat(5 * 1024 * 1024 - report.length - 7)}-->`
      const payload = Buffer.from(report + padding, 'utf8')
      expect(payload.length).toBe(5 * 1024 * 1024)
      await page.locator('input[type="file"]').setInputFiles({
        name: 'refresh-during-parse.xml',
        mimeType: 'application/xml',
        buffer: payload,
      })
      await page.getByLabel('Skip AI analysis').check()
      await page.getByRole('button', { name: 'Optional metadata' }).click()
      await page.getByLabel('Build label').fill(`${marker}-refresh`)

      const acceptedPromise = page.waitForResponse(
        response =>
          response.request().method() === 'POST' &&
          response.url().endsWith('/api/v1/ingest/file'),
      )
      await page.getByRole('button', { name: 'Upload', exact: true }).click()
      const accepted = await acceptedPromise
      expect(accepted.status(), await accepted.text()).toBe(202)
      const receipt = await accepted.json()

      await page.reload({ waitUntil: 'domcontentloaded' })
      await expect(page).toHaveURL(/\/runs/)

      await expect
        .poll(
          async () => {
            const response = await request.get(
              `${BACKEND_URL}/api/v1/ingest/uploads/${receipt.task_id}`,
              { headers: { Authorization: `Bearer ${token}` } },
            )
            if (response.status() !== 200) return `http-${response.status()}`
            return (await response.json()).state
          },
          { timeout: 90_000 },
        )
        .toBe('succeeded')

      const run = await request.get(
        `${BACKEND_URL}/api/v1/runs/${receipt.run_id}`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      expect(run.status(), await run.text()).toBe(200)
      expect((await run.json()).total_tests).toBe(1)

      await page.goto(`/runs/${receipt.run_id}`)
      await expect(page.getByText('durable_after_refresh')).toBeVisible({
        timeout: 20_000,
      })
    } catch (error) {
      primaryFailure = error
    } finally {
      if (token && projectId) {
        try {
          const cleanup = await request.delete(
            `${BACKEND_URL}/api/v1/projects/${projectId}`,
            { headers: { Authorization: `Bearer ${token}` } },
          )
          if (![204, 404].includes(cleanup.status())) {
            cleanupErrors.push(
              new Error(`project cleanup returned ${cleanup.status()}`),
            )
          }
        } catch (error) {
          cleanupErrors.push(new Error('project cleanup failed', { cause: error }))
        }
      }
    }

    const failures = [
      ...(primaryFailure ? [primaryFailure] : []),
      ...cleanupErrors,
    ]
    if (failures.length) {
      throw new AggregateError(
        failures,
        'M03 ingestion recovery or synthetic-fixture cleanup failed',
      )
    }
  })
})
