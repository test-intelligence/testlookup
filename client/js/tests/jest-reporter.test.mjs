// Jest >= 27.4 calls onTestCaseResult for each finished test AND onTestResult
// with the whole file afterwards. The reporter streamed from both, so every
// passed or failed test was recorded twice (a real Jest 29 run: 3 tests -> 5
// results). Skipped tests reach only onTestResult, so "ignore onTestResult on
// new Jest" would have dropped them instead.
import assert from 'node:assert/strict'
import test from 'node:test'

import jestReporterModule from '../dist/jest-reporter.js'
import { clearTestLookupEnv, installFakeServer } from './fake-server.mjs'

const JestReporter = jestReporterModule.default
const FILE = { path: '/repo/cart.test.js' }

function testCase(fullName, status) {
  return {
    ancestorTitles: ['cart'], fullName, title: fullName, status,
    duration: 3, failureMessages: status === 'failed' ? ['Error: boom\n  at x'] : [], failureDetails: [],
  }
}

async function runJest(drive) {
  clearTestLookupEnv()
  const server = installFakeServer()
  try {
    const reporter = new JestReporter({ rootDir: '/repo' }, {
      baseUrl: 'http://testlookup.invalid', token: 'jwt', projectId: 'p', batchIntervalMs: 60_000,
    })
    await reporter.onRunStart({ numTotalTests: 3, startTime: Date.now() })
    drive(reporter)
    await reporter.onRunComplete(undefined, {})
    return server.results()
  } finally {
    server.restore()
  }
}

const passed = testCase('cart adds an item', 'passed')
const failed = testCase('cart fails on purpose', 'failed')
const skipped = testCase('cart skipped one', 'pending')

test('Jest >= 27.4: each test is recorded once', async () => {
  const results = await runJest((r) => {
    r.onTestCaseResult(FILE, passed)
    r.onTestCaseResult(FILE, failed)
    r.onTestResult(FILE, { testFilePath: FILE.path, testResults: [passed, failed, skipped] })
  })
  assert.deepEqual(results.sort(), [
    'cart adds an item:PASSED', 'cart fails on purpose:FAILED', 'cart skipped one:SKIPPED',
  ])
})

test('Jest < 27.4 (no onTestCaseResult): onTestResult records the whole file', async () => {
  const results = await runJest((r) => {
    r.onTestResult(FILE, { testFilePath: FILE.path, testResults: [passed, failed, skipped] })
  })
  assert.equal(results.length, 3)
})

test('two tests sharing a name are both recorded, once each', async () => {
  const first = testCase('cart retries', 'passed')
  const second = testCase('cart retries', 'failed')
  const results = await runJest((r) => {
    r.onTestCaseResult(FILE, first)
    r.onTestCaseResult(FILE, second)
    r.onTestResult(FILE, { testFilePath: FILE.path, testResults: [first, second] })
  })
  assert.deepEqual(results.sort(), ['cart retries:FAILED', 'cart retries:PASSED'])
})

test('a skipped test sharing a streamed test\'s name is still recorded', async () => {
  // test.each rows often share a title; the skipped one never reaches
  // onTestCaseResult, so "seen this name" must not swallow it.
  const ran = testCase('cart retries', 'passed')
  const skippedTwin = testCase('cart retries', 'pending')
  const results = await runJest((r) => {
    r.onTestCaseResult(FILE, ran)
    r.onTestResult(FILE, { testFilePath: FILE.path, testResults: [ran, skippedTwin] })
  })
  assert.deepEqual(results.sort(), ['cart retries:PASSED', 'cart retries:SKIPPED'])
})

test('a second file with the same test names is not mistaken for the first', async () => {
  const other = { path: '/repo/other.test.js' }
  const results = await runJest((r) => {
    r.onTestCaseResult(FILE, passed)
    r.onTestResult(FILE, { testFilePath: FILE.path, testResults: [passed] })
    r.onTestCaseResult(other, passed)
    r.onTestResult(other, { testFilePath: other.path, testResults: [passed] })
  })
  assert.equal(results.length, 2)
})
