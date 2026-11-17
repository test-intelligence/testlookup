import { describe, expect, it } from 'vitest'

import { isRunInProgress, measuredRunPassRate, noPassRateReason } from './runPassRate'

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

  // D-0005 (GPT-6): no rate without evidence. A run whose every result was skipped executed nothing; its stored
  // 0.0 is the documented "no rate" number (core/pass_rate.py), not a measured 0 %. The reviewer's controls.
  it.each([
    ['all skipped (STOPPED)', { status: 'STOPPED', total_tests: 1, passed_tests: 0, failed_tests: 0, broken_tests: 0, pass_rate: 0 }, null],
    ['all skipped (PASSED)', { status: 'PASSED', total_tests: 3, passed_tests: 0, failed_tests: 0, broken_tests: 0, pass_rate: 0 }, null],
    ['failure only', { status: 'FAILED', total_tests: 1, passed_tests: 0, failed_tests: 1, broken_tests: 0, pass_rate: 0 }, 0],
    ['broken only', { status: 'FAILED', total_tests: 1, passed_tests: 0, failed_tests: 0, broken_tests: 1, pass_rate: 0 }, 0],
    ['one pass one fail', { status: 'FAILED', total_tests: 2, passed_tests: 1, failed_tests: 1, broken_tests: 0, pass_rate: 50 }, 50],
    ['pass, fail, broken', { status: 'FAILED', total_tests: 3, passed_tests: 1, failed_tests: 1, broken_tests: 1, pass_rate: 33.33 }, 33.33],
    ['STOPPED with a pass and an unknown', { status: 'STOPPED', total_tests: 2, passed_tests: 1, failed_tests: 0, broken_tests: 0, pass_rate: 100 }, 100],
    ['empty', { status: 'PASSED', total_tests: 0, passed_tests: 0, failed_tests: 0, broken_tests: 0, pass_rate: 0 }, null],
    ['counts absent (an older payload)', { status: 'FAILED', total_tests: 10, pass_rate: 0 }, 0],
  ])('%s (D-0005)', (_name, run, expected) => {
    expect(measuredRunPassRate(run)).toBe(expected)
  })

  it('says why a run has no pass rate (D-0005)', () => {
    expect(noPassRateReason({ status: 'STOPPED', total_tests: 1, passed_tests: 0, failed_tests: 0, pass_rate: 0 }))
      .toBe('No pass rate: no test was executed (every result was skipped)')
    expect(noPassRateReason({ status: 'RUNNING', total_tests: 5, pass_rate: 0 })).toBe('No pass rate: the run is still in progress')
    expect(noPassRateReason({ status: 'PASSED', total_tests: 0, pass_rate: 0 })).toBe('No pass rate: the run reported no tests')
    expect(noPassRateReason({ status: 'FAILED', total_tests: 2, passed_tests: 1, failed_tests: 1, pass_rate: 50 })).toBeNull()
  })

  it('names the in-progress statuses the Runs page counts as in flight', () => {
    expect(['RUNNING', 'in_progress', 'Pending', 'queued'].map(isRunInProgress)).toEqual([true, true, true, true])
    expect(['PASSED', 'FAILED', 'BROKEN', '', null, undefined].map(isRunInProgress)).toEqual([false, false, false, false, false, false])
  })
})
