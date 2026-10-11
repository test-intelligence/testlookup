// Jest and Mocha load reporters with require(). The exports map offered only
// an `import` condition while tsc emits CommonJS, so every
// require('testlookup-reporter/...') failed with ERR_PACKAGE_PATH_NOT_EXPORTED
// ("Could not resolve a module for a custom reporter" in Jest). And the Mocha
// reporter was `exports.default`: Mocha does `new (require(name))(...)`, so
// even the documented dist path crashed the run with "this._reporter is not a
// constructor". These resolve the package by its own name (self-reference),
// which goes through the exports map exactly as a consumer's require does.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'

import { clearTestLookupEnv } from './fake-server.mjs'

const require = createRequire(import.meta.url)
const SUBPATHS = ['testlookup-reporter', 'testlookup-reporter/jest-reporter', 'testlookup-reporter/mocha-reporter']

for (const name of SUBPATHS) {
  test(`require('${name}') resolves`, () => {
    assert.doesNotThrow(() => require(name))
  })

  test(`import('${name}') resolves`, async () => {
    await assert.doesNotReject(import(name))
  })
}

test('Mocha can construct the reporter it requires', () => {
  clearTestLookupEnv()
  const Reporter = require('testlookup-reporter/mocha-reporter')
  assert.equal(typeof Reporter, 'function')

  const events = []
  const runner = { on(event) { events.push(event); return this } }
  // What Mocha does with --reporter testlookup-reporter/mocha-reporter.
  const reporter = new Reporter(runner, {
    reporterOptions: { testlookupUrl: 'http://testlookup.invalid', testlookupToken: 'jwt', testlookupProject: 'p' },
  })
  assert.ok(events.includes('pass') && events.includes('fail') && events.includes('pending'))
  assert.equal(typeof reporter.done, 'function', 'Mocha waits on done() before exiting')
})

test('a shim written for the old default export still gets the class', () => {
  // e.g. module.exports = require('.../dist/mocha-reporter.js').default
  const Reporter = require('testlookup-reporter/mocha-reporter')
  assert.equal(Reporter.default, Reporter)
})

test('the Jest reporter is reachable as Jest unwraps it (module.default)', () => {
  const mod = require('testlookup-reporter/jest-reporter')
  assert.equal(typeof (mod.default ?? mod), 'function')
})
