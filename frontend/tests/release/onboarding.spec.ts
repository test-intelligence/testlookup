import { randomUUID } from 'node:crypto'
import { expect, test } from '@playwright/test'
import { checkStatus } from './http'
import { apiRequest, login } from './data-project'

test('TL-AUTH-001 first sign-in, project selection, reload and logout', {
  tag: ['@P0', '@regression', '@full', '@ephemeral-only', '@area:auth', '@journey:J01', '@role:viewer'],
}, async ({ page, request }) => {
  if (process.env.TL_TARGET !== 'ephemeral') throw Error('BLOCKED: disposable project required')
  const { api, headers } = await login(request, 'admin')
  const name = 'e2e-' + randomUUID()
  const password = 'First-' + randomUUID() + '!1a'
  const created = await apiRequest(request, 'POST', api + '/projects', { headers, data: { name, slug: name } })
  await checkStatus('POST', created, 201)
  const projectId = (await created.json()).id
  let viewerId: string | undefined
  try {
    const createdUser = await apiRequest(request, 'POST', api + '/users', {
      headers, data: { username: name, email: name + '@example.com', full_name: name, role: 'VIEWER' },
    })
    await checkStatus('POST', createdUser, 201)
    const viewer = await createdUser.json()
    viewerId = viewer.id
    const grant = await apiRequest(request, 'POST', api + '/projects/' + projectId + '/members', {
      headers, data: { user_id: viewerId, role: 'VIEWER' },
    })
    await checkStatus('POST', grant, 201)

    async function signIn(value: string) {
      await page.getByLabel('Email or Username').fill(name)
      await page.locator('#password').fill(value)
      await page.getByRole('button', { name: 'Log In', exact: true }).click()
    }
    await page.goto('/login')
    await signIn(viewer.temp_password)
    await expect(page).toHaveURL(/\/reset-password/)
    await page.locator('#new-password').fill(password)
    await page.locator('#confirm-password').fill(password)
    await page.locator('form button[type=submit]').click()
    await expect(page).toHaveURL(/\/login/)
    await signIn(password)
    const picker = page.getByRole('combobox', { name: 'Select project' })
    await picker.selectOption(projectId)
    await page.goto('/overview')
    await page.reload()
    await expect(picker).toHaveValue(projectId)
    const account = page.getByRole('button', { name: /Account menu/ })
    await expect(account).toContainText(name)
    await account.click()
    await page.getByRole('menuitem', { name: 'Sign out' }).click()
    await expect(page).toHaveURL(/\/login/)
    await page.goto('/overview')
    await expect(page.getByLabel('Email or Username')).toBeVisible()
  } finally {
    try {
      if (viewerId) {
        const disabled = await apiRequest(request, 'PATCH', api + '/users/' + viewerId + '/status', {
          headers, data: { is_active: false },
        })
        await checkStatus('PATCH', disabled, 200)
      }
    } finally {
      await checkStatus('DELETE', await apiRequest(request, 'DELETE', api + '/projects/' + projectId, { headers }), 204)
    }
  }
})
