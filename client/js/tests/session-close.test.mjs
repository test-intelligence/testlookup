// Closing a live session must not race the results it already sent.
import assert from 'node:assert/strict'
import test from 'node:test'

import { LiveSession, TestLookupReporter } from '../dist/testlookup-reporter.js'

const tick = (ms = 1) => new Promise((resolve) => setTimeout(resolve, ms))

function reply(status, body, retryAfter = null) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (name) => (name.toLowerCase() === 'retry-after' ? retryAfter : null) },
    json: async () => body,
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
  }
}

test('close() waits for a batch the timer already sent before it returns', async () => {
  const original = globalThis.fetch
  const order = []
  let release
  const held = new Promise((resolve) => { release = resolve })
  globalThis.fetch = async (_url, init = {}) => {
    const events = JSON.parse(init.body).events
    order.push(`post:${events.length}`)
    await held
    order.push('posted')
    return reply(202, { accepted: events.length })
  }
  // A large batch size keeps record() from flushing; only the 5 ms timer sends.
  const session = new LiveSession({
    sessionId: 's-1', sessionToken: 'st-1', runId: 'r-1', baseUrl: 'http://test.invalid',
    batchSize: 50, batchIntervalMs: 5, timeoutMs: 1_000,
  })
  try {
    await session.record('release_pass', 'PASSED', 1)
    await session.record('release_fail', 'FAILED', 1)
    while (!order.length) await tick()
    const closing = session.close().then(() => order.push('closed'))
    await tick(30)
    assert.deepEqual(order, ['post:2'], 'close() returned while the timer batch was in flight')
    release()
    await closing
    assert.deepEqual(order, ['post:2', 'posted', 'closed'])
    assert.deepEqual(session.stats, { sent: 2, failed: 0 })
  } finally {
    release()
    globalThis.fetch = original
  }
})

test('close() still flushes what the timer had not taken', async () => {
  const original = globalThis.fetch
  const sizes = []
  globalThis.fetch = async (_url, init = {}) => {
    const events = JSON.parse(init.body).events
    sizes.push(events.length)
    await tick(10)
    return reply(202, { accepted: events.length })
  }
  const session = new LiveSession({
    sessionId: 's-1', sessionToken: 'st-1', runId: 'r-1', baseUrl: 'http://test.invalid',
    batchSize: 50, batchIntervalMs: 5, timeoutMs: 1_000,
  })
  try {
    await session.record('first', 'PASSED', 1)
    while (!sizes.length) await tick()
    await session.record('second', 'FAILED', 1)
    await session.close()
    assert.equal(sizes.reduce((a, b) => a + b, 0), 2)
    assert.deepEqual(session.stats, { sent: 2, failed: 0 })
  } finally {
    globalThis.fetch = original
  }
})

for (const [label, answers, deletes, warned] of [
  ['retries the 409 sent while a batch is being admitted', [409, 204], 2, false],
  ['stops retrying a 409 after a bounded number of attempts', Array(12).fill(409), 10, true],
  ['does not retry a refusal other than 409', [404, 204], 1, true],
]) {
  test(`closeSession ${label}`, async () => {
    const original = globalThis.fetch
    const originalWarn = console.warn
    const warnings = []
    console.warn = (...args) => warnings.push(args.map(String).join(' '))
    let count = 0
    globalThis.fetch = async (_url, init = {}) => {
      assert.equal(init.method, 'DELETE')
      const status = answers[count++]
      return reply(status, status === 409 ? { detail: 'live batch admission is still in progress; retry close' } : undefined,
        status === 409 ? '0' : null)
    }
    try {
      const reporter = new TestLookupReporter({ baseUrl: 'http://test.invalid', token: 'jwt', projectId: 'p' })
      await reporter.closeSession('s-1')
      assert.equal(count, deletes)
      assert.equal(warnings.length, warned ? 1 : 0)
    } finally {
      globalThis.fetch = original
      console.warn = originalWarn
    }
  })
}
