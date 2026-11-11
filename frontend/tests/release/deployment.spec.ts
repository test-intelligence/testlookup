import { expect, test } from '@playwright/test'
import { checkStatus } from './http'

const tags = ['@P0', '@smoke', '@regression', '@full', '@homelab-safe']

test('TL-DEPLOY-002 candidate identity and dependency readiness', {
  tag: [...tags, '@journey:J13', '@area:deploy', '@role:public'],
}, async ({ request }, info) => {
  const api = process.env.TL_API
  const expected = process.env.TL_EXPECTED_SHA
  expect(expected).toMatch(/^[0-9a-f]{40}$/)
  const version = await request.get(api + '/health/version', { maxRedirects: 0 })
  await checkStatus('GET', version, 200)
  const body = await version.json()
  await info.attach('observed-build', { body: JSON.stringify({
    revision: body.build?.revision, version: body.version, built_at: body.build?.built_at,
    request_id: version.headers()['x-request-id'],
  }), contentType: 'application/json' })
  expect(body.build.revision).toBe(expected)
  const ready = await request.get(api + '/health/ready', { maxRedirects: 0 })
  await checkStatus('GET', ready, 200)
  expect(await ready.json()).toMatchObject({ status: 'ready', checks: {
    postgres: { status: 'ok' }, mongo: { status: 'ok' }, redis: { status: 'ok' },
  } })
})

test('TL-AUTH-002 public login page renders working form controls', {
  tag: [...tags, '@journey:J01', '@area:auth', '@role:public'],
}, async ({ page }, info) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.name))
  page.on('console', msg => {
    if (msg.type() !== 'error') return
    const location = msg.location().url
    const path = location.startsWith('http') ? new URL(location).pathname : 'inline'
    errors.push('console ' + path + ' ' + msg.text().split('?')[0].slice(0, 160))
  })
  page.on('response', response => {
    if (response.status() >= 500 || (response.request().method() !== 'GET' && response.status() >= 400)) {
      errors.push([response.request().method(), new URL(response.url()).pathname,
        response.status(), response.headers()['x-request-id']].join(' '))
    }
  })
  try {
    const response = await page.goto('/login')
    expect(response?.status()).toBe(200)
    await expect(page.getByLabel('Email or Username')).toBeEditable()
    await expect(page.getByLabel('Password', { exact: true })).toHaveAttribute('type', 'password')
    await expect(page.getByRole('button', { name: 'Log In', exact: true })).toBeEnabled()
    expect(errors).toEqual([])
  } catch (error) {
    await info.attach('public-login-screen', { body: await page.screenshot(), contentType: 'image/png' })
    throw error
  }
})
