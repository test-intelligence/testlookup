// Auth was always `Authorization: Bearer <token>`, so an API key -- which the
// server reads only from X-API-Key -- was refused with 401. And config-loader
// was never imported: testlookup.properties, TESTLOOKUP_API_KEY,
// TESTLOOKUP_ENDPOINT, _PROJECT, _LAUNCH, _SUITE and _RELEASE did nothing for
// the Jest and Mocha reporters.
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import { TestLookupReporter } from '../dist/testlookup-reporter.js'
import jestReporterModule from '../dist/jest-reporter.js'
import { clearTestLookupEnv, installFakeServer } from './fake-server.mjs'

const require = createRequire(import.meta.url)
const MochaReporter = require('../dist/mocha-reporter.js')
const JestReporter = jestReporterModule.default
/** Shaped like a minted key; not a key. */
const FAKE_KEY = 'qai_' + 'x'.repeat(43)

async function openAndClose(config) {
  const server = installFakeServer()
  try {
    const reporter = new TestLookupReporter({ baseUrl: 'http://testlookup.invalid', projectId: 'p', ...config })
    const session = await reporter.startSession({ buildNumber: 'b-1' })
    await session.close()
    await reporter.closeSession(session.sessionId)
    return server.calls
  } finally {
    server.restore()
  }
}

function authOf(call) {
  return { apiKey: call.headers['X-API-Key'], authorization: call.headers.Authorization }
}

test('an apiKey is sent as X-API-Key on session create and close', async () => {
  const calls = await openAndClose({ apiKey: FAKE_KEY })
  for (const call of calls.filter((c) => c.path.startsWith('/api/v1/stream/sessions'))) {
    assert.deepEqual(authOf(call), { apiKey: FAKE_KEY, authorization: undefined })
  }
})

test('a qai_ key passed as token is still sent as X-API-Key', async () => {
  const [create] = await openAndClose({ token: FAKE_KEY })
  assert.deepEqual(authOf(create), { apiKey: FAKE_KEY, authorization: undefined })
})

test('a JWT is sent as Bearer', async () => {
  const [create] = await openAndClose({ token: 'eyJhbGciOiJIUzI1NiJ9.e30.sig' })
  assert.deepEqual(authOf(create), { apiKey: undefined, authorization: 'Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig' })
})

test('the apiKey wins when both are given', async () => {
  const [create] = await openAndClose({ token: 'eyJhbGciOiJIUzI1NiJ9.e30.sig', apiKey: FAKE_KEY })
  assert.equal(authOf(create).apiKey, FAKE_KEY)
})

test('without any credential the reporter refuses to start', () => {
  assert.throws(() => new TestLookupReporter({ baseUrl: 'http://x.invalid', projectId: 'p' }), /apiKey or a token/)
})

test('verifySsl: false says it has no effect instead of silently doing nothing', (t) => {
  const warn = t.mock.method(console, 'warn', () => {})
  new TestLookupReporter({ baseUrl: 'http://x.invalid', projectId: 'p', token: 'jwt', verifySsl: false })
  assert.match(String(warn.mock.calls[0]?.arguments[0]), /NODE_EXTRA_CA_CERTS/)
})

// ── Reporters read testlookup.properties and TESTLOOKUP_* ─────────────────────

function inProjectDir(properties) {
  const dir = mkdtempSync(join(tmpdir(), 'tl-js-'))
  if (properties) writeFileSync(join(dir, 'testlookup.properties'), properties)
  const before = { cwd: process.cwd(), HOME: process.env.HOME, USERPROFILE: process.env.USERPROFILE }
  process.chdir(dir)
  process.env.HOME = dir
  process.env.USERPROFILE = dir
  clearTestLookupEnv()
  return () => {
    process.chdir(before.cwd)
    for (const k of ['HOME', 'USERPROFILE']) {
      if (before[k] === undefined) delete process.env[k]
      else process.env[k] = before[k]
    }
    clearTestLookupEnv()
  }
}

const PROPERTIES = [
  'testlookup.endpoint=http://from-file.invalid',
  `testlookup.api.key=${FAKE_KEY}`,
  'testlookup.project=EverGreen',
  'testlookup.launch=Smoke suite',
  'testlookup.release=2026.10',
  'testlookup.build=42',
  '',
].join('\n')

async function jestSessionCreate() {
  const server = installFakeServer()
  try {
    const reporter = new JestReporter({ rootDir: process.cwd() }, { batchIntervalMs: 60_000 })
    await reporter.onRunStart({ numTotalTests: 0, startTime: Date.now() })
    await reporter.onRunComplete(undefined, {})
    return server.sessionCreate()
  } finally {
    server.restore()
  }
}

async function mochaSessionCreate(reporterOptions = {}) {
  const server = installFakeServer()
  try {
    const handlers = {}
    const runner = { on(event, fn) { handlers[event] = fn; return this } }
    const reporter = new MochaReporter(runner, { reporterOptions })
    handlers.start?.()
    await new Promise((resolve) => reporter.done(0, resolve))
    return server.sessionCreate()
  } finally {
    server.restore()
  }
}

for (const [name, sessionCreate] of [['Jest', jestSessionCreate], ['Mocha', mochaSessionCreate]]) {
  test(`${name} reporter runs from testlookup.properties alone`, async () => {
    const restore = inProjectDir(PROPERTIES)
    try {
      const create = await sessionCreate()
      assert.ok(create, 'no session was opened')
      assert.equal(create.headers['X-API-Key'], FAKE_KEY)
      assert.equal(create.body.project_id, 'EverGreen')
      assert.equal(create.body.launch_name, 'Smoke suite')
      assert.equal(create.body.suite_name, 'Smoke suite', 'the launch label is the suite fallback')
      assert.equal(create.body.release_name, '2026.10')
      assert.equal(create.body.build_number, '42')
    } finally {
      restore()
    }
  })

  test(`${name} reporter: TESTLOOKUP_* env vars beat the file`, async () => {
    const restore = inProjectDir(PROPERTIES)
    try {
      process.env.TESTLOOKUP_ENDPOINT = 'http://from-env.invalid'
      process.env.TESTLOOKUP_PROJECT = 'FromEnv'
      process.env.TESTLOOKUP_SUITE = 'Checkout'
      process.env.TESTLOOKUP_RELEASE = '2026.11'
      const create = await sessionCreate()
      assert.equal(create.body.project_id, 'FromEnv')
      assert.equal(create.body.suite_name, 'Checkout')
      assert.equal(create.body.release_name, '2026.11')
    } finally {
      restore()
    }
  })
}

test('a token in the environment beats an API key left in the file', async () => {
  const restore = inProjectDir(PROPERTIES.replace(FAKE_KEY, 'qai_REPLACE_ME'))
  try {
    process.env.TESTLOOKUP_TOKEN = 'eyJhbGciOiJIUzI1NiJ9.e30.env'
    const create = await jestSessionCreate()
    assert.equal(create.headers.Authorization, 'Bearer eyJhbGciOiJIUzI1NiJ9.e30.env')
    assert.equal(create.headers['X-API-Key'], undefined)
  } finally {
    restore()
  }
})

test('an explicit token option beats an API key from the environment', async () => {
  const restore = inProjectDir(PROPERTIES)
  const server = installFakeServer()
  try {
    process.env.TESTLOOKUP_API_KEY = FAKE_KEY
    const handlers = {}
    const runner = { on(event, fn) { handlers[event] = fn; return this } }
    const reporter = new MochaReporter(runner, { reporterOptions: { testlookupToken: 'eyJhbGciOiJIUzI1NiJ9.e30.opt' } })
    handlers.start()
    await new Promise((resolve) => reporter.done(0, resolve))
    assert.equal(server.sessionCreate().headers.Authorization, 'Bearer eyJhbGciOiJIUzI1NiJ9.e30.opt')
  } finally {
    server.restore()
    restore()
  }
})

test('Mocha: a result that finishes before the session opens is kept', async () => {
  const restore = inProjectDir(PROPERTIES)
  const server = installFakeServer()
  try {
    const handlers = {}
    const runner = { on(event, fn) { handlers[event] = fn; return this } }
    const reporter = new MochaReporter(runner, {})
    handlers.start()
    // Synchronously, before the session-create promise can resolve:
    handlers.pass({ fullTitle: () => 'cart adds an item', duration: 2, parent: { fullTitle: () => 'cart' } })
    await new Promise((resolve) => reporter.done(0, resolve))
    assert.deepEqual(server.results(), ['cart adds an item:PASSED'])
  } finally {
    server.restore()
    restore()
  }
})
