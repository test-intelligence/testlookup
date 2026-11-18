import { randomUUID } from 'node:crypto'
import { expect, test, type Page } from '@playwright/test'
import { checkStatus } from './http'
import { apiRequest, login } from './data-project'

/**
 * TL-AUTH-003 (D-0019, GPT-6): one account per browser profile. Tab A holds the harness admin's session; tab B of
 * the same profile signs in as a disposable viewer. Tab A must reload onto the viewer with a note, a third tab must
 * open as the viewer, and the viewer's sign-out in B must sign A out. Before 0.1.1, A kept the admin's session and
 * could write it back over B's for the next tab or reload.
 *
 * The admin's token is never revoked here: only the viewer signs out. The viewer is created by the admin, sets its
 * password in a separate profile first (its first sign-in asks for one), and is disabled at the end.
 */
const account = (page: Page) => page.getByRole('button', { name: /Account menu/ })
/** The other tab follows: a reload and a first paint on the live stack. */
const FOLLOWS_MS = 15_000

async function signIn(page: Page, username: string, password: string) {
  await page.getByLabel('Email or Username').fill(username)
  await page.locator('#password').fill(password)
  await page.getByRole('button', { name: 'Log In', exact: true }).click()
}

test('TL-AUTH-003 a sign-in or sign-out in one tab applies to every tab of the browser', {
  tag: ['@P1', '@regression', '@full', '@ephemeral-only', '@area:auth', '@journey:J01', '@role:viewer'],
}, async ({ browser, context, request }) => {
  if (process.env.TL_TARGET !== 'ephemeral') throw Error('BLOCKED: disposable account required')
  const admin = await login(request, 'admin')
  const name = 'e2e-' + randomUUID()
  const password = 'Tabs-' + randomUUID() + '!1a'
  const created = await apiRequest(request, 'POST', admin.api + '/users', {
    headers: admin.headers, data: { username: name, email: name + '@example.com', full_name: name, role: 'VIEWER' },
  })
  await checkStatus('POST', created, 201)
  const viewer = await created.json()
  // `context` is the profile under test; the viewer sets its password in a separate one (a new context does not
  // inherit the config's baseURL, so it is passed).
  const setup = await browser.newContext({ baseURL: process.env.TL_WEB })
  const profile = context
  try {
    // The viewer's first sign-in sets its password, in a profile of its own.
    const first = await setup.newPage()
    await first.goto('/login')
    await signIn(first, name, viewer.temp_password)
    await expect(first).toHaveURL(/\/reset-password/)
    await first.locator('#new-password').fill(password)
    await first.locator('#confirm-password').fill(password)
    await first.locator('form button[type=submit]').click()
    await expect(first).toHaveURL(/\/login/)
    await setup.close()

    // Tab A: the admin's session, on Overview.
    const a = await profile.newPage()
    await a.goto('/login')
    await a.evaluate(({ session, user }) => {
      localStorage.setItem('auth-storage', JSON.stringify({
        version: 0, state: { token: session.access_token, refreshToken: session.refresh_token ?? null, user, isAuthenticated: true },
      }))
    }, { session: admin.session, user: admin.user })
    await a.goto('/overview')
    await expect(account(a)).toContainText(admin.user.full_name || admin.user.username)

    // Tab B, the same profile: the viewer signs in.
    const b = await profile.newPage()
    await b.goto('/login')
    await signIn(b, name, password)
    await expect(account(b)).toContainText(name)

    // A follows, says why, and a new tab opens as the viewer.
    await expect(account(a)).toContainText(name, { timeout: FOLLOWS_MS })
    await expect(a.getByText(`Another tab of this browser signed in as ${name}, so this tab reloaded.`)).toBeVisible()
    const c = await profile.newPage()
    await c.goto('/overview')
    await expect(account(c)).toContainText(name)

    // The viewer signs out in B: A follows to the sign-in page.
    await account(b).click()
    await b.getByRole('menuitem', { name: 'Sign out' }).click()
    await expect(b).toHaveURL(/\/login/)
    await expect(a).toHaveURL(/\/login/, { timeout: FOLLOWS_MS })
  } finally {
    await setup.close().catch(() => undefined)
    const disabled = await apiRequest(request, 'PATCH', admin.api + '/users/' + viewer.id + '/status', {
      headers: admin.headers, data: { is_active: false },
    })
    await checkStatus('PATCH', disabled, 200)
  }
})
