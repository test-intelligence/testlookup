// W18 DEV-03: the Playwright, Vitest, Cypress and Cucumber adapters stream each result once, with the
// right status, attempt, suite and error, and a server error never fails the user's run.
import assert from 'node:assert/strict'
import test from 'node:test'

import playwrightModule from '../dist/playwright-reporter.js'
import vitestModule from '../dist/vitest-reporter.js'
import cypressModule from '../dist/cypress-plugin.js'
import cucumberModule from '../dist/cucumber-formatter.js'
import { clearTestLookupEnv, installFakeServer } from './fake-server.mjs'

const OPTS = { testlookupUrl: 'http://testlookup.invalid', testlookupToken: 'jwt', testlookupProject: 'p', batchIntervalMs: 60_000 }

function events(server) {
  return server.calls
    .filter((c) => c.path === '/api/v1/stream/events/batch')
    .flatMap((c) => c.body.events)
    .filter((e) => e.event_type === 'test_result')
}

async function withServer(fn) {
  clearTestLookupEnv()
  const server = installFakeServer()
  try {
    await fn(server)
    return server
  } finally {
    server.restore()
  }
}

// ── Playwright ──────────────────────────────────────────────────────────────

function pwTest(title, project = 'chromium') {
  const projectSuite = { title: project, project: () => ({ name: project }) }
  const fileSuite = { title: 'cart.spec.ts', parent: projectSuite }
  const describe = { title: 'cart', parent: fileSuite }
  return {
    title, parent: describe, location: { file: '/repo/tests/cart.spec.ts', line: 3 }, tags: ['@smoke'],
    titlePath: () => ['', project, 'cart.spec.ts', 'cart', title],
  }
}

test('Playwright: one result per attempt, retries as attempt 2, project as suite', async () => {
  const server = await withServer(async () => {
    const R = playwrightModule.default
    const r = new R(OPTS)
    r.onBegin()
    const t = pwTest('adds an item')
    r.onTestEnd(t, { status: 'failed', duration: 120, retry: 0, startTime: new Date(1000), workerIndex: 0, error: { message: 'boom', stack: 'at x' } })
    r.onTestEnd(t, { status: 'passed', duration: 90, retry: 1, startTime: new Date(2000), workerIndex: 1 })
    r.onTestEnd(pwTest('timeout', 'firefox'), { status: 'timedOut', duration: 30000, retry: 0, startTime: new Date(3000) })
    r.onTestEnd(pwTest('skipped'), { status: 'skipped', duration: 0, retry: 0, startTime: new Date(4000) })
    await r.onEnd()
  })
  const ev = events(server)
  assert.deepEqual(ev.map((e) => `${e.test_name}:${e.status}`), [
    'cart > adds an item:FAILED', 'cart > adds an item:PASSED', 'cart > timeout:FAILED', 'cart > skipped:SKIPPED',
  ])
  assert.equal(ev[0].error_message, 'boom')
  assert.equal(ev[1].metadata.attempt, 2)
  assert.equal(ev[0].suite_name, 'chromium')
  assert.equal(ev[2].suite_name, 'firefox')
  assert.ok(server.calls.some((c) => c.method === 'DELETE'), 'the session is closed')
})

// ── Vitest ──────────────────────────────────────────────────────────────────

test('Vitest 1/2: onTaskUpdate streams, onFinished adds the skipped, nothing twice', async () => {
  const server = await withServer(async () => {
    const R = vitestModule.default
    const r = new R(OPTS)
    const file = { id: 'f', name: 'math.test.ts', type: 'file', filepath: '/repo/math.test.ts' }
    const suite = { id: 's', name: 'math', type: 'suite', file }
    const ok = { id: 't1', name: 'adds', type: 'test', suite, file, result: { state: 'pass', duration: 4 } }
    const bad = { id: 't2', name: 'divides', type: 'test', suite, file, result: { state: 'fail', duration: 6, errors: [{ message: 'nope' }] } }
    const skip = { id: 't3', name: 'later', type: 'test', suite, file, mode: 'skip' }
    file.tasks = [{ ...suite, tasks: [ok, bad, skip] }]
    r.onInit({ state: { idMap: new Map([['t1', ok], ['t2', bad], ['t3', skip]]) } })
    r.onTaskUpdate([['t1', ok.result], ['t2', bad.result]])
    await r.onFinished([file])
  })
  assert.deepEqual(events(server).map((e) => `${e.test_name}:${e.status}`), [
    'math > adds:PASSED', 'math > divides:FAILED', 'math > later:SKIPPED',
  ])
  assert.equal(events(server)[1].suite_name, 'math.test.ts')
})

test('Vitest 3: onTestCaseResult streams each test once', async () => {
  const server = await withServer(async () => {
    const R = vitestModule.default
    const r = new R(OPTS)
    r.onInit({})
    const tc = { id: 'x', name: 'adds', fullName: 'math > adds', module: { moduleId: '/repo/math.test.ts' },
      result: () => ({ state: 'passed' }), diagnostic: () => ({ duration: 3, retryCount: 1 }) }
    r.onTestCaseResult(tc)
    r.onTestCaseResult(tc)
    await r.onFinished([])
  })
  const ev = events(server)
  assert.equal(ev.length, 1)
  assert.equal(ev[0].test_name, 'math > adds')
  assert.equal(ev[0].metadata.attempt, 2)
})

// ── Cypress ─────────────────────────────────────────────────────────────────

test('Cypress: a spec\'s tests stream at after:spec, the last attempt counts', async () => {
  const server = await withServer(async () => {
    const handlers = {}
    const on = (event, fn) => { handlers[event] = fn }
    cypressModule.default(on, {}, OPTS)
    await handlers['before:run']()
    await handlers['after:spec']({ relative: 'cypress/e2e/login.cy.ts' }, {
      tests: [
        { title: ['login', 'works'], state: 'passed', duration: 800, attempts: [{ state: 'failed' }, { state: 'passed' }] },
        { title: ['login', 'rejects'], state: 'failed', displayError: 'AssertionError: x', attempts: [{ state: 'failed', wallClockDuration: 300 }] },
        { title: ['login', 'todo'], state: 'pending', attempts: [] },
      ],
    })
    await handlers['after:run']()
  })
  const ev = events(server)
  assert.deepEqual(ev.map((e) => `${e.test_name}:${e.status}`), ['login > works:PASSED', 'login > rejects:FAILED', 'login > todo:SKIPPED'])
  assert.equal(ev[0].metadata.attempt, 2)
  assert.equal(ev[1].error_message, 'AssertionError: x')
  assert.equal(ev[0].suite_name, 'cypress/e2e/login.cy.ts')
})

test('Cypress: disabled without configuration registers nothing', async () => {
  clearTestLookupEnv()
  const handlers = {}
  cypressModule.default((event, fn) => { handlers[event] = fn }, {}, {})
  assert.deepEqual(Object.keys(handlers), [])
})

// ── Cucumber ────────────────────────────────────────────────────────────────

test('Cucumber: one result per scenario attempt, worst step wins, feature as suite', async () => {
  const server = await withServer(async () => {
    const listeners = []
    const F = cucumberModule.default
    const f = new F({ eventBroadcaster: { on: (_e, fn) => listeners.push(fn) }, parsedArgvOptions: OPTS })
    const send = (e) => listeners.forEach((fn) => fn(e))
    const ts = (s) => ({ seconds: s, nanos: 0 })
    send({ testRunStarted: { timestamp: ts(1) } })
    send({ gherkinDocument: { uri: 'features/cart.feature', feature: { name: 'Cart' } } })
    send({ pickle: { id: 'pk1', name: 'Add an item', uri: 'features/cart.feature', tags: [{ name: '@smoke' }] } })
    send({ pickle: { id: 'pk2', name: 'Remove an item', uri: 'features/cart.feature', tags: [] } })
    send({ testCase: { id: 'tc1', pickleId: 'pk1' } })
    send({ testCase: { id: 'tc2', pickleId: 'pk2' } })
    send({ testCaseStarted: { id: 'r1', testCaseId: 'tc1', attempt: 0, timestamp: ts(10) } })
    send({ testStepFinished: { testCaseStartedId: 'r1', testStepResult: { status: 'PASSED', duration: { seconds: 0, nanos: 5e6 } } } })
    send({ testStepFinished: { testCaseStartedId: 'r1', testStepResult: { status: 'FAILED', message: 'Expected 1\nat step', duration: { seconds: 0, nanos: 7e6 } } } })
    send({ testCaseFinished: { testCaseStartedId: 'r1', timestamp: ts(11), willBeRetried: true } })
    send({ testCaseStarted: { id: 'r2', testCaseId: 'tc1', attempt: 1, timestamp: ts(12) } })
    send({ testStepFinished: { testCaseStartedId: 'r2', testStepResult: { status: 'PASSED', duration: { seconds: 0, nanos: 4e6 } } } })
    send({ testCaseFinished: { testCaseStartedId: 'r2', timestamp: ts(13) } })
    send({ testCaseStarted: { id: 'r3', testCaseId: 'tc2', attempt: 0, timestamp: ts(14) } })
    send({ testStepFinished: { testCaseStartedId: 'r3', testStepResult: { status: 'UNDEFINED' } } })
    send({ testCaseFinished: { testCaseStartedId: 'r3', timestamp: ts(15) } })
    send({ testRunFinished: { timestamp: ts(16) } })
    await f.finished()
  })
  const ev = events(server)
  assert.deepEqual(ev.map((e) => `${e.test_name}:${e.status}`), ['Add an item:FAILED', 'Add an item:PASSED', 'Remove an item:BROKEN'])
  assert.equal(ev[0].suite_name, 'Cart')
  assert.equal(ev[0].error_message, 'Expected 1')
  assert.equal(ev[1].metadata.attempt, 2)
  assert.equal(ev[0].duration_ms, 12)
})

// ── never fail the run ──────────────────────────────────────────────────────

test('a server that answers 500 never throws into the test runner', async () => {
  clearTestLookupEnv()
  const original = globalThis.fetch
  globalThis.fetch = async () => ({ status: 500, ok: false, headers: { get: () => null }, json: async () => ({}), text: async () => 'down' })
  try {
    const R = playwrightModule.default
    const r = new R(OPTS)
    r.onBegin()
    r.onTestEnd(pwTest('x'), { status: 'passed', duration: 1, retry: 0, startTime: new Date() })
    await r.onEnd()
    const handlers = {}
    cypressModule.default((e, fn) => { handlers[e] = fn }, {}, OPTS)
    await handlers['before:run']()
    await handlers['after:spec']({ relative: 'a.cy.ts' }, { tests: [{ title: ['a'], state: 'passed' }] })
    await handlers['after:run']()
  } finally {
    globalThis.fetch = original
  }
})
