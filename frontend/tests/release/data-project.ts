import { randomUUID } from 'node:crypto'
import { expect, type APIRequestContext, type Page } from '@playwright/test'
import { checkStatus } from './http'

type Role = 'admin' | 'qa_lead' | 'qa_engineer' | 'tester'

// Playwright's transport errors include every request header in the call log.
// Report the operation and URL path only. Retry connection resets on reads.
export async function apiRequest(request: APIRequestContext, method: string, url: string,
                                 options: NonNullable<Parameters<APIRequestContext['fetch']>[1]> = {}) {
  try {
    return await request.fetch(url, {
      ...options, method, headers: { ...options.headers, Connection: 'close' },
      maxRetries: method === 'GET' ? 2 : 0,
    })
  } catch {
    throw Error(method + ' ' + new URL(url).pathname + ' transport failed')
  }
}

export async function login(request: APIRequestContext, role: Role) {
  const api = process.env.TL_API + '/api/v1'
  const token = process.env['TL_TOKEN_' + role.toUpperCase()]
  let session: { access_token: string; refresh_token?: string }
  if (token) {
    session = { access_token: token }
  } else {
    const response = await apiRequest(request, 'POST', api + '/auth/dev-login', { params: { role }, maxRedirects: 0 })
    await checkStatus('POST', response, 200)
    session = await response.json()
  }
  const headers = { Authorization: 'Bearer ' + session.access_token }
  const who = await apiRequest(request, 'GET', api + '/auth/me', { headers })
  await checkStatus('GET', who, 200)
  const user = await who.json()
  expect(user.role).toBe(role.toUpperCase())
  expect(user.username).toBe(process.env['TL_USER_' + role.toUpperCase()])
  return { api, session, headers, user }
}

export async function createDataProject(request: APIRequestContext, role: 'qa_engineer' | 'tester') {
  if (process.env.TL_TARGET !== 'ephemeral') throw Error('BLOCKED: disposable project required')
  const lead = await login(request, 'qa_lead')
  const actor = await login(request, role)
  const name = 'e2e-' + randomUUID()
  const created = await apiRequest(request, 'POST', lead.api + '/projects', { headers: lead.headers, data: { name, slug: name } })
  await checkStatus('POST', created, 201)
  const project = await created.json()
  async function remove() {
    const response = await apiRequest(request, 'DELETE', lead.api + '/projects/' + project.id, { headers: lead.headers })
    await checkStatus('DELETE', response, 204)
  }
  try {
    const member = await apiRequest(request, 'POST', lead.api + '/projects/' + project.id + '/members', {
      headers: lead.headers, data: { user_id: actor.user.id, role: actor.user.role },
    })
    await checkStatus('POST', member, 201)
    expect((await member.json()).user_id).toBe(actor.user.id)
    return { ...actor, project, producerHeaders: lead.headers, remove }
  } catch (error) {
    await remove()
    throw error
  }
}

export async function authenticatePage(page: Page, data: Awaited<ReturnType<typeof createDataProject>>) {
  const { project, session, user } = data
  await page.goto('/login')
  await page.evaluate(({ project, session, user }) => {
    localStorage.setItem('auth-storage', JSON.stringify({
      version: 0, state: { token: session.access_token, refreshToken: session.refresh_token, user },
    }))
    localStorage.setItem('testlookup-active-project', JSON.stringify({
      version: 0, state: { activeProjectId: project.id, activeProject: project },
    }))
  }, { project, session, user })
}
