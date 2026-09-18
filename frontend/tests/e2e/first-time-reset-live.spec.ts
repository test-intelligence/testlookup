import { expect, test } from '@playwright/test'

const BACKEND_URL = process.env.VITE_API_BASE_URL || 'http://localhost:8000'

test.describe('First-time password reset — live revocation contract', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('reauthenticates after reset and completes empty-project onboarding', async ({ page, request }) => {
    const adminPassword = process.env.E2E_ADMIN_PASSWORD
    if (!adminPassword) throw new Error('E2E_ADMIN_PASSWORD is required')

    const marker = `exp-m01-${Date.now().toString().slice(-10)}`
    const initialPassword = 'Tmp-M01-Start-9!'
    const permanentPassword = 'Tmp-M01-Permanent-9!'
    let adminToken = ''
    let userId = ''
    let projectId = ''

    try {
      const adminLogin = await request.post(`${BACKEND_URL}/api/v1/auth/login`, {
        form: { username: 'admin', password: adminPassword },
      })
      expect(adminLogin.status()).toBe(200)
      adminToken = (await adminLogin.json()).access_token

      const registration = await request.post(`${BACKEND_URL}/api/v1/auth/register`, {
        data: {
          email: `${marker}@example.com`, username: marker,
          full_name: 'M01 Synthetic User', password: initialPassword,
        },
      })
      expect(registration.status()).toBe(201)
      userId = (await registration.json()).id

      const project = await request.post(`${BACKEND_URL}/api/v1/projects`, {
        headers: { Authorization: `Bearer ${adminToken}` },
        data: { name: `${marker} empty project`, slug: marker },
      })
      expect(project.status()).toBe(201)
      const projectBody = await project.json()
      projectId = projectBody.id

      const membership = await request.post(
        `${BACKEND_URL}/api/v1/projects/${projectId}/members`,
        {
          headers: { Authorization: `Bearer ${adminToken}` },
          data: { user_id: userId, role: 'QA_ENGINEER' },
        },
      )
      expect(membership.status()).toBe(201)

      await page.goto('/login')
      await page.locator('input[name="username"]').fill(marker)
      await page.locator('input[name="password"]').fill(initialPassword)
      await page.locator('button[type="submit"]').click()
      await expect(page).toHaveURL(/\/reset-password$/, { timeout: 20_000 })

      await page.locator('#new-password').fill(permanentPassword)
      await page.locator('#confirm-password').fill(permanentPassword)
      const postResetPaths: string[] = []
      let resetSubmitted = false
      page.on('framenavigated', frame => {
        if (resetSubmitted && frame === page.mainFrame()) {
          postResetPaths.push(new URL(frame.url()).pathname)
        }
      })
      const resetResponse = page.waitForResponse(
        response => response.url().includes('/api/v1/auth/first-time-reset'),
      )
      resetSubmitted = true
      await page.getByRole('button', { name: /set password/i }).click()
      expect((await resetResponse).status()).toBe(204)

      await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 })
      expect(postResetPaths).not.toContain('/overview')
      await expect(page.getByText('Password updated. Sign in with your new password.')).toBeVisible()
      await page.locator('input[name="username"]').fill(marker)
      await page.locator('input[name="password"]').fill(permanentPassword)
      await page.locator('button[type="submit"]').click()
      await expect(page).toHaveURL(/\/overview$/, { timeout: 20_000 })

      await page.evaluate(projectValue => {
        localStorage.setItem('testlookup-active-project', JSON.stringify({
          state: { activeProjectId: projectValue.id, activeProject: projectValue },
          version: 0,
        }))
      }, projectBody)
      await page.goto('/getting-started')
      await expect(page.getByRole('heading', { name: 'Getting Started' })).toBeVisible()

      let skipped = 0
      while (await page.locator('button[title="Skip this step"]').count()) {
        const response = page.waitForResponse(
          item => item.url().includes('/api/v1/onboarding/') && item.url().endsWith('/skip'),
        )
        await page.locator('button[title="Skip this step"]').first().click()
        expect((await response).status()).toBe(200)
        skipped += 1
        expect(skipped).toBeLessThan(10)
      }
      await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100')
      await page.reload()
      await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100')
    } finally {
      if (adminToken && projectId) {
        const cleanupProject = await request.delete(`${BACKEND_URL}/api/v1/projects/${projectId}`, {
          headers: { Authorization: `Bearer ${adminToken}` },
        })
        expect(cleanupProject.status()).toBe(204)
      }
      if (adminToken && userId) {
        const cleanupUser = await request.patch(`${BACKEND_URL}/api/v1/users/${userId}/status`, {
          headers: { Authorization: `Bearer ${adminToken}` },
          data: { is_active: false },
        })
        expect(cleanupUser.status()).toBe(200)
      }
    }
  })
})
