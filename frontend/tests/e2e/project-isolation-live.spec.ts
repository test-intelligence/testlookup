import { expect, test, type APIRequestContext, type APIResponse } from '@playwright/test'

const BACKEND_URL = process.env.VITE_API_BASE_URL || 'http://localhost:8000'

type CreatedUser = {
  id: string
  username: string
  temp_password: string
}

type Project = {
  id: string
  name: string
  slug: string
}

function bearer(token: string) {
  return { Authorization: `Bearer ${token}` }
}

async function body(response: APIResponse, status: number) {
  const url = new URL(response.url())
  expect(
    response.status(),
    `${url.pathname} returned ${response.status()}`,
  ).toBe(status)
  return response.json()
}

async function login(request: APIRequestContext, username: string, password: string) {
  const response = await request.post(`${BACKEND_URL}/api/v1/auth/login`, {
    form: { username, password },
  })
  return body(response, 200)
}

test.describe('Project isolation — live A/B contract', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('rejects B through JWT and scoped-key APIs and heals a persisted B scope', async ({
    page,
    request,
  }) => {
    test.slow()
    const adminPassword = process.env.E2E_ADMIN_PASSWORD
    if (!adminPassword) throw new Error('E2E_ADMIN_PASSWORD is required')

    const marker = `exp-m02-${Date.now()}`
    const projects: string[] = []
    const users: string[] = []
    let adminToken = ''
    let scopedKey = ''
    let primaryFailure: unknown
    const cleanupErrors: Error[] = []

    try {
      adminToken = (await login(request, 'admin', adminPassword)).access_token

      const user = (await body(
        await request.post(`${BACKEND_URL}/api/v1/users`, {
          headers: bearer(adminToken),
          data: {
            email: `${marker}-a@example.com`,
            username: `${marker}-a`,
            full_name: 'M02 A-only QA lead',
            role: 'QA_LEAD',
          },
        }),
        201,
      )) as CreatedUser
      users.push(user.id)

      const created: Record<'a' | 'b', Project> = {} as Record<'a' | 'b', Project>
      for (const side of ['a', 'b'] as const) {
        const project = (await body(
          await request.post(`${BACKEND_URL}/api/v1/projects`, {
            headers: bearer(adminToken),
            data: {
              name: `${marker}-${side}-sentinel`,
              slug: `${marker}-${side}`,
            },
          }),
          201,
        )) as Project
        created[side] = project
        projects.push(project.id)
      }

      await body(
        await request.post(
          `${BACKEND_URL}/api/v1/projects/${created.a.id}/members`,
          {
            headers: bearer(adminToken),
            data: { user_id: user.id, role: 'QA_LEAD' },
          },
        ),
        201,
      )

      const bootstrapLogin = await login(request, user.username, user.temp_password)
      expect(bootstrapLogin.must_change_password).toBe(true)
      const permanentPassword = 'M02-Live-Permanent-9!'
      const reset = await request.post(`${BACKEND_URL}/api/v1/auth/first-time-reset`, {
        headers: bearer(bootstrapLogin.access_token),
        data: {
          new_password: permanentPassword,
          confirm_password: permanentPassword,
        },
      })
      expect(reset.status(), await reset.text()).toBe(204)
      const revokedBootstrap = await request.get(`${BACKEND_URL}/api/v1/projects`, {
        headers: bearer(bootstrapLogin.access_token),
      })
      expect(revokedBootstrap.status()).toBe(401)

      const aLogin = await login(request, user.username, permanentPassword)
      expect(aLogin.must_change_password).toBe(false)
      const aToken = aLogin.access_token as string

      for (const [side, token] of [
        ['a', aToken],
        ['b', adminToken],
      ] as const) {
        const project = created[side]
        await body(
          await request.post(`${BACKEND_URL}/api/v1/ingest`, {
            headers: bearer(token),
            data: {
              project_id: project.id,
              build_number: project.name,
              results: [
                {
                  test_name: `${project.name}-case`,
                  suite_name: `${project.name}-suite`,
                  status: side === 'a' ? 'PASSED' : 'FAILED',
                  duration_ms: 17,
                },
              ],
            },
          }),
          202,
        )
      }

      const headers = bearer(aToken)
      for (const side of ['a', 'b'] as const) {
        await expect
          .poll(
            async () => {
              const response = await request.get(`${BACKEND_URL}/api/v1/runs`, {
                headers: bearer(adminToken),
                params: { project_id: created[side].id, days: 0 },
              })
              return response.status() === 200
                ? (await response.text()).includes(created[side].name)
                : false
            },
            { timeout: 60_000 },
          )
          .toBe(true)
      }

      const projectList = await body(
        await request.get(`${BACKEND_URL}/api/v1/projects`, { headers }),
        200,
      )
      expect(projectList.map((item: Project) => item.id)).toEqual([created.a.id])

      const allRuns = await body(
        await request.get(`${BACKEND_URL}/api/v1/runs`, {
          headers,
          params: { days: 0 },
        }),
        200,
      )
      expect(JSON.stringify(allRuns)).toContain(created.a.name)
      expect(JSON.stringify(allRuns)).not.toContain(created.b.name)

      const denied: Array<[string, () => Promise<APIResponse>]> = [
        [
          'project detail',
          () => request.get(`${BACKEND_URL}/api/v1/projects/${created.b.id}`, { headers }),
        ],
        [
          'runs',
          () => request.get(`${BACKEND_URL}/api/v1/runs`, {
            headers,
            params: { project_id: created.b.id, days: 0 },
          }),
        ],
        [
          'search',
          () => request.get(`${BACKEND_URL}/api/v1/search`, {
            headers,
            params: { q: created.b.name, project_id: created.b.id },
          }),
        ],
        [
          'suites',
          () => request.get(`${BACKEND_URL}/api/v1/suites`, {
            headers,
            params: { project_id: created.b.id },
          }),
        ],
        [
          'activity',
          () => request.get(`${BACKEND_URL}/api/v1/projects/${created.b.id}/activity`, {
            headers,
          }),
        ],
        [
          'activity export',
          () => request.get(
            `${BACKEND_URL}/api/v1/projects/${created.b.id}/activity/export`,
            { headers, params: { format: 'ndjson' } },
          ),
        ],
        [
          'ingest',
          () => request.post(`${BACKEND_URL}/api/v1/ingest`, {
            headers,
            data: {
              project_id: created.b.id,
              build_number: `${marker}-denied`,
              results: [
                { test_name: 'denied', suite_name: 'denied', status: 'PASSED' },
              ],
            },
          }),
        ],
      ]
      for (const [name, send] of denied) {
        const response = await send()
        const text = await response.text()
        expect([403, 404], `${name} returned ${response.status()}`).toContain(
          response.status(),
        )
        expect(text).not.toContain(created.b.name)
      }

      const key = await body(
        await request.post(`${BACKEND_URL}/api/v1/keys`, {
          headers: bearer(adminToken),
          data: {
            name: `${marker}-a-key`,
            project_id: created.a.id,
            target_user_id: user.id,
            scopes: [],
            expires_days: 1,
          },
        }),
        201,
      )
      scopedKey = key.raw_key
      const keyDenied = await request.get(`${BACKEND_URL}/api/v1/runs`, {
        headers: { 'X-API-Key': scopedKey },
        params: { project_id: created.b.id, days: 0 },
      })
      expect(keyDenied.status(), await keyDenied.text()).toBe(403)

      await page.goto('/login')
      await page.evaluate(
        ({ accessToken, refreshToken }) => {
          localStorage.setItem(
            'auth-storage',
            JSON.stringify({
              state: { token: accessToken, refreshToken },
              version: 0,
            }),
          )
        },
        {
          accessToken: aLogin.access_token as string,
          refreshToken: aLogin.refresh_token as string,
        },
      )
      await page.goto('/overview')
      await expect(page).toHaveURL(/\/overview$/, { timeout: 20_000 })

      await page.evaluate(project => {
        localStorage.setItem(
          'testlookup-active-project',
          JSON.stringify({
            state: { activeProjectId: project.id, activeProject: project },
            version: 0,
          }),
        )
      }, created.b)

      const bResponses: Array<{ status: number; url: string }> = []
      page.on('response', response => {
        if (response.url().includes(created.b.id)) {
          bResponses.push({ status: response.status(), url: response.url() })
        }
      })
      await page.reload()

      const selector = page.getByRole('combobox', { name: 'Select project' })
      await expect(selector).toHaveValue(created.a.id, { timeout: 20_000 })
      await expect(selector.locator(`option[value="${created.b.id}"]`)).toHaveCount(0)
      await expect(page.getByText(created.b.name)).toHaveCount(0)
      expect(bResponses.filter(response => response.status < 400)).toEqual([])

      const deleted = await request.delete(
        `${BACKEND_URL}/api/v1/projects/${created.b.id}`,
        { headers: bearer(adminToken) },
      )
      expect(deleted.status()).toBe(204)
      projects.splice(projects.indexOf(created.b.id), 1)

      const stale = await request.get(
        `${BACKEND_URL}/api/v1/projects/${created.b.id}`,
        { headers },
      )
      expect([403, 404]).toContain(stale.status())
      expect(await stale.text()).not.toContain(created.b.name)
    } catch (error) {
      primaryFailure = error
    } finally {
      if (adminToken) {
        for (const projectId of projects.reverse()) {
          try {
            const response = await request.delete(
              `${BACKEND_URL}/api/v1/projects/${projectId}`,
              { headers: bearer(adminToken) },
            )
            if (![204, 404].includes(response.status())) {
              cleanupErrors.push(
                new Error(`project ${projectId} cleanup returned ${response.status()}`),
              )
            }
          } catch (error) {
            cleanupErrors.push(
              new Error(`project ${projectId} cleanup failed`, { cause: error }),
            )
          }
        }
        for (const userId of users.reverse()) {
          try {
            const response = await request.patch(
              `${BACKEND_URL}/api/v1/users/${userId}/status`,
              { headers: bearer(adminToken), data: { is_active: false } },
            )
            if (![200, 404].includes(response.status())) {
              cleanupErrors.push(
                new Error(`user ${userId} cleanup returned ${response.status()}`),
              )
            }
          } catch (error) {
            cleanupErrors.push(
              new Error(`user ${userId} cleanup failed`, { cause: error }),
            )
          }
        }
        if (scopedKey) {
          try {
            const response = await request.get(`${BACKEND_URL}/api/v1/runs`, {
              headers: { 'X-API-Key': scopedKey },
              params: { days: 0 },
            })
            if (![401, 403].includes(response.status())) {
              cleanupErrors.push(
                new Error(`deleted-project key remained usable: ${response.status()}`),
              )
            }
          } catch (error) {
            cleanupErrors.push(new Error('deleted-project key check failed', { cause: error }))
          }
        }
      }
    }
    const failures = [
      ...(primaryFailure ? [primaryFailure] : []),
      ...cleanupErrors,
    ]
    if (failures.length) {
      throw new AggregateError(failures, 'M02 journey or synthetic-fixture cleanup failed')
    }
  })
})
