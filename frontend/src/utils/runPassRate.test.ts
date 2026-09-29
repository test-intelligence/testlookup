import { describe, expect, it } from 'vitest'

import { isRunInProgress, measuredRunPassRate } from './runPassRate'

// One rule for "does this run have a pass rate yet" (OD-18), read by /runs and
// the Intelligence Hub. The API reports `pass_rate: 0` for a run that is still
// running or ran no tests; that 0 is not a measurement.
describe('measuredRunPassRate', () => {
  it.each([
    ['RUNNING', 10, 0],
    ['running', 10, 0],
    ['IN_PROGRESS', 10, 0],
    ['in-progress', 10, 0],
    ['PENDING', 10, 0],
    ['QUEUED', 10, 0],
  ])('%s: not measured, whatever pass_rate says', (status, total, rate) => {
    expect(measuredRunPassRate({ status, total_tests: total, pass_rate: rate })).toBeNull()
  })

  it.each([
    ['no tests', { status: 'PASSED', total_tests: 0, pass_rate: 0 }],
    ['a missing total', { status: 'PASSED', total_tests: null, pass_rate: 0 }],
    ['no pass rate', { status: 'PASSED', total_tests: 10, pass_rate: null }],
    ['a non-finite pass rate', { status: 'PASSED', total_tests: 10, pass_rate: Number.NaN }],
  ])('%s: not measured', (_name, run) => {
    expect(measuredRunPassRate(run)).toBeNull()
  })

  it('a finished run with tests has its pass rate, 0 included', () => {
    expect(measuredRunPassRate({ status: 'FAILED', total_tests: 10, pass_rate: 0 })).toBe(0)
    expect(measuredRunPassRate({ status: 'PASSED', total_tests: 10, pass_rate: 92.5 })).toBe(92.5)
  })

  it('names the in-progress statuses the Runs page counts as in flight', () => {
    expect(['RUNNING', 'in_progress', 'Pending', 'queued'].map(isRunInProgress)).toEqual([true, true, true, true])
    expect(['PASSED', 'FAILED', 'BROKEN', '', null, undefined].map(isRunInProgress)).toEqual([false, false, false, false, false, false])
  })
})
