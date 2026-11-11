import { expect, test, type Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { apiRequest, createDataProject } from './data-project'
import { checkStatus } from './http'

type Update = {
  type: string; run_id?: string; sequence_id: string; last_test?: string; last_status?: string
  passed?: number; failed?: number; total?: number
}
type Observer = Window & { feed?: { stop: AbortController; events: Update[]; error?: string } }

async function subscribe(page: Page, project: string, token: string, cursor = '') {
  // Read the browser's real event stream. The browser fetch transport permits
  // supplying the durable cursor on reconnect and inspecting SSE event IDs.
  await page.evaluate(({ project, token, cursor }) => {
    const win = window as Observer
    win.feed?.stop.abort()
    const feed: NonNullable<Observer['feed']> = { stop: new AbortController(), events: [] }
    win.feed = feed
    void (async () => {
      const response = await fetch('/api/v1/stream/sse/' + project + '?token=' + encodeURIComponent(token), {
        signal: feed.stop.signal, headers: cursor ? { 'Last-Event-ID': cursor } : {},
      })
      if (response.status !== 200 || !response.body) throw Error('Subscription HTTP ' + response.status)
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
      let pending = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        pending += value
        let boundary
        while ((boundary = pending.indexOf('\n\n')) !== -1) {
          const fields = pending.slice(0, boundary).split('\n')
          pending = pending.slice(boundary + 2)
          const payload = fields.find(line => line.startsWith('data: '))
          if (!payload) continue
          const event = JSON.parse(payload.slice(6)) as Update
          const sequence = fields.find(line => line.startsWith('id: '))?.slice(4)
          if (sequence !== event.sequence_id) throw Error('Event body and SSE cursor disagree')
          feed.events.push(event)
        }
      }
    })().catch(() => { if (!feed.stop.signal.aborted) feed.error = 'SSE read failed' })
  }, { project, token, cursor })
}

async function waitEvent(page: Page, kind: string, run?: string, leaf?: string): Promise<Update> {
  const args = { kind, run, leaf }
  const handle = await page.waitForFunction(({ kind, run, leaf }) => {
    const feed = (window as Observer).feed
    if (feed?.error) throw Error(feed.error)
    return feed?.events.find(event => event.type === kind && (!run || event.run_id === run)
      && (!leaf || event.last_test === leaf))
  }, args, { timeout: 30_000 })
  return await handle.jsonValue() as Update
}

test('TL-STREAM-001 ordered results survive browser reconnect and producer redelivery', {
  tag: ['@P1', '@regression', '@full', '@ephemeral-only', '@area:Streaming and activity', '@journey:J12', '@role:tester'],
}, async ({ browser, request }) => {
  const data = await createDataProject(request, 'tester')
  const tabs = await Promise.all([browser.newContext(), browser.newContext()])
  try {
    const [a, b] = await Promise.all(tabs.map(context => context.newPage()))
    for (const tab of [a, b]) {
      await tab.goto(process.env.TL_WEB + '/login')
      await subscribe(tab, data.project.id, data.session.access_token)
      await waitEvent(tab, 'initial_state')
    }
    const opened = await apiRequest(request, 'POST', data.api + '/stream/sessions', { headers: data.producerHeaders,
      data: { project_id: data.project.id, client_name: 'release-stream', build_number: data.project.name, total_tests: 2 } })
    await checkStatus('POST', opened, 201)
    const session = await opened.json()
    expect(session.project_id).toBe(data.project.id)
    async function send(batch_id: string, events: object[]) {
      const response = await apiRequest(request, 'POST', data.api + '/stream/events/batch', {
        headers: { 'X-Session-Token': session.session_token },
        data: { session_id: session.session_id, run_id: session.run_id, batch_id, events },
      })
      await checkStatus('POST', response, 202)
      return await response.json()
    }
    await send(randomUUID(), [{ event_type: 'test_result', test_name: 'first', status: 'PASSED', duration_ms: 2 }])
    const first = await waitEvent(a, 'live_test_result', session.run_id, 'first')
    expect(await waitEvent(b, 'live_test_result', session.run_id, 'first')).toEqual(first)
    await b.evaluate(() => {
      const feed = (window as Observer).feed
      if (!feed) throw Error('Missing subscriber before disconnect')
      feed.stop.abort()
    })
    const second = [{ event_type: 'test_result', test_name: 'second', status: 'FAILED', duration_ms: 3, error_message: 'intentional stream fixture' }]
    const batch = randomUUID()
    await send(batch, second)
    await send(batch, second)
    const live = await waitEvent(a, 'live_test_result', session.run_id, 'second')
    await subscribe(b, data.project.id, data.session.access_token, first.sequence_id)
    expect(await waitEvent(b, 'live_test_result', session.run_id, 'second')).toEqual(live)
    expect(live.sequence_id).not.toBe(first.sequence_id)
    expect([live.last_status, live.passed, live.failed]).toEqual(['FAILED', 1, 1])
    await send(randomUUID(), [{ event_type: 'run_complete' }])
    const end = await waitEvent(a, 'live_run_complete', session.run_id)
    expect(await waitEvent(b, 'live_run_complete', session.run_id)).toEqual(end)
    expect([end.passed, end.failed, end.total]).toEqual([1, 1, 2])
    const close = await apiRequest(request, 'DELETE', data.api + '/stream/sessions/' + session.session_id, { headers: data.producerHeaders })
    await checkStatus('DELETE', close, 204)
    await expect.poll(async () => {
      const cases = await apiRequest(request, 'GET', data.api + '/runs/' + session.run_id + '/tests', { headers: data.headers })
      await checkStatus('GET', cases, 200)
      return (await cases.json()).items.map((item: { test_name: string; status: string }) => [item.test_name, item.status]).sort()
    }, { timeout: 45_000 }).toEqual([['first', 'PASSED'], ['second', 'FAILED']])
    const result = await apiRequest(request, 'GET', data.api + '/runs/' + session.run_id, { headers: data.headers })
    await checkStatus('GET', result, 200)
    const run = await result.json()
    expect([run.project_id, run.build_number, run.total_tests, run.passed_tests, run.failed_tests])
      .toEqual([data.project.id, data.project.name, 2, 1, 1])
  } finally {
    await Promise.all(tabs.map(context => context.close()))
    await data.remove()
  }
})
