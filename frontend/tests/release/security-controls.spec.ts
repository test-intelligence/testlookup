import { expect, test } from '@playwright/test'
import { verifySecurityResponse } from '../../src/test/security-response-policy'
import { apiRequest, login } from './data-project'
import { checkStatus } from './http'

test('TL-SEC-001 isolated response protections and ordinary role denial', {
  tag: ['@P1', '@regression', '@full', '@ephemeral-only', '@area:security', '@journey:J01', '@role:viewer'],
}, async ({ request }, info) => {
  if (process.env.TL_TARGET !== 'ephemeral') throw Error('BLOCKED: isolated candidate required')
  const api = process.env.TL_API + '/api/v1'
  const username = process.env.TL_USER_VIEWER
  if (!username || !process.env.TL_WEB || !process.env.TL_API) throw Error('BLOCKED: isolated target and assigned viewer required')
  const response = await apiRequest(request, 'GET', process.env.TL_WEB + '/login', { maxRedirects: 0, timeout: 10_000 })
  await checkStatus('GET', response, 200)
  const controls = verifySecurityResponse(response.headers(), await response.text())

  let token = process.env.TL_TOKEN_VIEWER
  if (!token) {
    // The existing disposable launcher enables this fixture-only endpoint.
    const session = await apiRequest(request, 'POST', api + '/auth/dev-login', { params: { role: 'viewer' }, maxRedirects: 0, timeout: 10_000 })
    await checkStatus('POST', session, 200)
    const body = await session.json()
    if (typeof body.access_token !== 'string') throw Error('BLOCKED: fixture session unavailable')
    token = body.access_token
  }
  const viewerHeaders = { Authorization: 'Bearer ' + token }
  const identity = await apiRequest(request, 'GET', api + '/auth/me', { headers: viewerHeaders, maxRedirects: 0, timeout: 10_000 })
  await checkStatus('GET', identity, 200)
  const viewer = await identity.json()
  expect(viewer.role === 'VIEWER' && viewer.username === username, 'assigned viewer authenticated').toBe(true)
  const denied = await apiRequest(request, 'GET', api + '/users', { headers: viewerHeaders, maxRedirects: 0, timeout: 10_000 })
  await checkStatus('GET', denied, 403)

  const admin = await login(request, 'admin')
  const allowed = await apiRequest(request, 'GET', api + '/users', { headers: admin.headers, params: { page: 1, page_size: 1 }, maxRedirects: 0, timeout: 10_000 })
  await checkStatus('GET', allowed, 200)
  expect(Array.isArray(await allowed.json()), 'permitted admin read returns its documented list').toBe(true)
  await info.attach('security-control-evidence', { body: JSON.stringify({
    schema_version: 1, candidate_sha: process.env.TL_EXPECTED_SHA,
    profile: 'ephemeral', controls, viewer_authenticated: true, viewer_admin_read_denied: true,
    admin_read_permitted: true, transport: new URL(process.env.TL_WEB).protocol,
    hsts_browser_enforcement: 'not_measured',
  }), contentType: 'application/json' })
})
