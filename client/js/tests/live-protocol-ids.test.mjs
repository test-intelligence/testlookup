import assert from 'node:assert/strict'
import test from 'node:test'

import { LiveSession } from '../dist/testlookup-reporter.js'

test('capacity retry reuses batch_id and event order', async () => {
  const originalFetch = globalThis.fetch
  const payloads = []
  globalThis.fetch = async (_url, init) => {
    payloads.push(JSON.parse(init.body))
    if (payloads.length === 1) return {
      status: 429,
      ok: false,
      headers: { get: () => '0' },
      json: async () => ({}),
    }
    return {
      status: 200,
      ok: true,
      headers: { get: () => null },
      json: async () => ({ accepted: 1 }),
    }
  }

  const session = new LiveSession({
    sessionId: 'session-1',
    sessionToken: 'token-1',
    runId: 'run-1',
    baseUrl: 'http://test.invalid',
    batchSize: 1,
    batchIntervalMs: 60_000,
    timeoutMs: 1_000,
  })

  try {
    await session.record('test_login', 'PASSED', 10)
    assert.equal(payloads.length, 2)
    assert.deepEqual(payloads[0], payloads[1])
    assert.match(payloads[0].batch_id, /^[0-9a-f-]{36}$/)
  } finally {
    await session.close()
    globalThis.fetch = originalFetch
  }
})
