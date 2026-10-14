// EXEC-01: a result carries when the test started (and its phase split, when the
// caller knows it) next to timestamp_ms, the finish. TestLookup turns the pair into
// the test's start / finish; a server that predates the fields ignores them.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'

import jestReporterModule from '../dist/jest-reporter.js'
import { LiveSession } from '../dist/testlookup-reporter.js'
import { clearTestLookupEnv, installFakeServer } from './fake-server.mjs'

const require = createRequire(import.meta.url)
const MochaReporter = require('../dist/mocha-reporter.js')
const JestReporter = jestReporterModule.default

function sentResults(server) {
  return server.calls
    .filter((c) => c.path === '/api/v1/stream/events/batch')
    .flatMap((c) => c.body.events)
    .filter((e) => e.event_type === 'test_result')
}

async function recordOne(opts) {
  clearTestLookupEnv()
  const server = installFakeServer()
  const session = new LiveSession({
    sessionId: 's-1', sessionToken: 'st-1', runId: 'r-1', baseUrl: 'http://testlookup.invalid',
    batchSize: 1, batchIntervalMs: 60_000, timeoutMs: 1_000,
  })
  try {
    await session.record('cart adds an item', 'PASSED', 1600, opts)
    return sentResults(server)[0]
  } finally {
    await session.close()
    server.restore()
  }
}

test('record sends the timing it is given', async () => {
  const event = await recordOne({
    startedAtMs: 1_791_972_001_250, setupMs: 120, callMs: 1600.4, teardownMs: 80, workerId: 'w1',
  })
  assert.equal(event.started_at_ms, 1_791_972_001_250)
  assert.deepEqual([event.setup_ms, event.call_ms, event.teardown_ms], [120, 1600, 80])
  assert.equal(event.worker_id, 'w1')
  assert.equal(typeof event.timestamp_ms, 'number', 'timestamp_ms is still the finish')
})

test('record without timing sends exactly the keys it always did', async () => {
  const event = await recordOne({})
  assert.deepEqual(Object.keys(event).sort(), ['duration_ms', 'event_type', 'status', 'test_name', 'timestamp_ms'])
})

test('an unusable timing figure is left out rather than sent', async () => {
  const event = await recordOne({ startedAtMs: Number.NaN, setupMs: -5, callMs: Infinity })
  for (const key of ['started_at_ms', 'setup_ms', 'call_ms']) assert.equal(key in event, false, key)
})

test('the Jest reporter sends the start Jest reports (startAt)', async () => {
  clearTestLookupEnv()
  const server = installFakeServer()
  try {
    const reporter = new JestReporter({ rootDir: '/repo' }, {
      baseUrl: 'http://testlookup.invalid', token: 'jwt', projectId: 'p', batchIntervalMs: 60_000,
    })
    await reporter.onRunStart({ numTotalTests: 2, startTime: Date.now() })
    const base = { ancestorTitles: ['cart'], status: 'passed', duration: 40, failureMessages: [], failureDetails: [] }
    reporter.onTestCaseResult({ path: '/repo/cart.test.js' }, { ...base, fullName: 'cart new', title: 'new', startAt: 1_791_972_001_250 })
    reporter.onTestCaseResult({ path: '/repo/cart.test.js' }, { ...base, fullName: 'cart old', title: 'old' })
    await reporter.onRunComplete(undefined, {})
    const byName = Object.fromEntries(sentResults(server).map((e) => [e.test_name, e]))
    assert.equal(byName['cart new'].started_at_ms, 1_791_972_001_250)
    assert.equal('started_at_ms' in byName['cart old'], false, 'Jest < 29 has no startAt: nothing is invented')
  } finally {
    server.restore()
  }
})

test('the Mocha reporter sends when each test began', async () => {
  clearTestLookupEnv()
  const server = installFakeServer()
  try {
    const handlers = {}
    const runner = { on(event, fn) { handlers[event] = fn; return this } }
    const reporter = new MochaReporter(runner, {
      reporterOptions: { testlookupUrl: 'http://testlookup.invalid', testlookupToken: 'jwt', testlookupProject: 'p' },
    })
    const mochaTest = (title) => ({
      fullTitle: () => `cart ${title}`, titlePath: () => ['cart', title], duration: 25,
      isPassed: () => true, isFailed: () => false, isPending: () => false, parent: { fullTitle: () => 'cart' },
    })
    const ran = mochaTest('adds an item')
    const pending = mochaTest('is pending')
    handlers.start()
    const before = Date.now()
    handlers.test(ran)
    handlers.pass(ran)
    handlers.pending(pending) // Mocha emits no 'test' event for a pending test
    await new Promise((resolve) => reporter.done(0, resolve))

    const byName = Object.fromEntries(sentResults(server).map((e) => [e.test_name, e]))
    const startedAt = byName['cart adds an item'].started_at_ms
    assert.ok(startedAt >= before && startedAt <= Date.now())
    assert.equal('started_at_ms' in byName['cart is pending'], false)
  } finally {
    server.restore()
  }
})
