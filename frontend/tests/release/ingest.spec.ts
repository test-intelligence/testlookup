import { expect, test } from '@playwright/test'
import { checkStatus } from './http'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

test('TL-INGEST-002 JUnit leaves agree with run counts and rendered failure', {
  tag: ['@P0', '@smoke', '@regression', '@full', '@ephemeral-only', '@journey:J02', '@area:ingest', '@role:ADMIN'],
}, async ({ request, page }) => {
  if (process.env.TL_TARGET !== 'ephemeral') throw new Error('BLOCKED: requires disposable project')
  const api = process.env.TL_API
  const fixture = fileURLToPath(new URL('../../../e2e-runner/fixtures/pytest-junit.xml', import.meta.url))
  const oracle: Record<string, string> = JSON.parse(execFileSync(process.env.TL_PYTHON || 'python',
    [fileURLToPath(new URL('../../../e2e-runner/fixture_counts.py', import.meta.url)), fixture], { encoding: 'utf8' }))
  const mark = 'e2e-' + randomUUID()
  const login = await request.post(api + '/api/v1/auth/dev-login', { params: { role: 'admin' }, maxRedirects: 0 })
  await checkStatus('POST', login, 200)
  const session = await login.json()
  const headers = { Authorization: 'Bearer ' + session.access_token }
  const me = await request.get(api + '/api/v1/auth/me', { headers, maxRedirects: 0 })
  await checkStatus('GET', me, 200)
  const user = await me.json()
  expect(user.role).toBe('ADMIN')
  expect(user.username).toBe(process.env.TL_USER_ADMIN)
  const create = await request.post(api + '/api/v1/projects', { headers, data: { name: mark, slug: mark } })
  await checkStatus('POST', create, 201)
  const project = await create.json()
  try {
    const accepted = await request.post(api + '/api/v1/ingest/file', { headers, multipart: {
      file: { name: 'pytest-junit.xml', mimeType: 'application/xml', buffer: readFileSync(fixture) },
      project_id: project.id, build_number: mark, run_ai: 'false', format: 'junit',
    } })
    await checkStatus('POST', accepted, 202)
    const receipt = await accepted.json()
    await expect.poll(async () => {
      const status = await request.get(api + '/api/v1/ingest/uploads/' + receipt.task_id, { headers })
      await checkStatus('GET', status, 200)
      return (await status.json()).state
    }, { timeout: 90_000, message: 'Upload task must reach succeeded' }).toBe('succeeded')
    const response = await request.get(api + '/api/v1/runs/' + receipt.run_id, { headers })
    await checkStatus('GET', response, 200)
    const run = await response.json()
    expect(run.project_id).toBe(project.id)
    expect(run.total_tests).toBe(Object.keys(oracle).length)
    for (const status of ['PASSED', 'FAILED', 'BROKEN', 'SKIPPED']) {
      expect(run[status.toLowerCase() + '_tests']).toBe(Object.values(oracle).filter(s => s === status).length)
    }
    await page.goto('/login')
    await page.evaluate(({ session, user, project }) => {
      localStorage.setItem('auth-storage', JSON.stringify({
        state: { token: session.access_token, refreshToken: session.refresh_token, user }, version: 0,
      }))
      localStorage.setItem('testlookup-active-project', JSON.stringify({
        state: { activeProjectId: project.id, activeProject: project }, version: 0,
      }))
    }, { session, user, project })
    await page.goto('/runs/' + receipt.run_id)
    const failedName = Object.keys(oracle).find(name => oracle[name] === 'FAILED')!
    await expect(page.getByText(failedName, { exact: true }).first()).toBeVisible({ timeout: 20_000 })
  } finally {
    const cleanup = await request.delete(api + '/api/v1/projects/' + project.id, { headers })
    await checkStatus('DELETE', cleanup, 204)
  }
})
