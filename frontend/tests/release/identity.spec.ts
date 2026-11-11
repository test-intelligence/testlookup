import { expect, test } from '@playwright/test'
import { checkStatus } from './http'

for (const role of ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']) {
  test('TL-ROLE-002 ' + role + ' session resolves to its configured principal', {
    tag: ['@P0', '@smoke', '@regression', '@full', '@homelab-safe', '@journey:J01', '@area:auth', '@role:' + role],
  }, async ({ request }) => {
    const api = process.env.TL_API
    let token = process.env['TL_TOKEN_' + role]
    const username = process.env['TL_USER_' + role]
    if (!username) throw new Error('BLOCKED: expected test principal missing for ' + role)
    if (!token && process.env.TL_TARGET === 'ephemeral') {
      const login = await request.post(api + '/api/v1/auth/dev-login', {
        params: { role: role.toLowerCase() }, maxRedirects: 0,
      })
      await checkStatus('POST', login, 200)
      token = (await login.json()).access_token
    }
    if (!token) throw new Error('BLOCKED: short-lived test credential missing for ' + role)
    const me = await request.get(api + '/api/v1/auth/me', {
      headers: { Authorization: 'Bearer ' + token }, maxRedirects: 0,
    })
    await checkStatus('GET', me, 200)
    const user = await me.json()
    expect(user.username).toBe(username)
    expect(user.role).toBe(role)
    expect(user.id).toMatch(/^[0-9a-f-]{36}$/i)
  })
}
