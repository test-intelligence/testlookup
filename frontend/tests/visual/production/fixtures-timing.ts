/**
 * EXEC-04: the run Timing tab's answers (`GET /runs/{id}/timing`, the EXEC-03
 * contract) for `RUN_ID`, shared by the hermetic spec
 * (`tests/ci-e2e/run-timing-tab.spec.ts`) and the visual baselines
 * (`prod-run-timing.visual.spec.ts`), so the two can never draw different runs.
 *
 *  - `TIMED`: two workers, six tests each, back to back: 9.0 s on the clock,
 *    17.4 s summed; worker 0's third test failed and is 2x its baseline.
 *  - `NO_TIMESTAMPS`: an old JUnit run, durations only, no clock.
 */
import { type ApiHandlers } from '../../lib/production-pages'
import { isoAgo, RUN_ID } from './fixtures'
import { TEST_CASE, TEST_CASE_ID, TEST_CASE_NAME } from './fixtures-pages'
import { RUN_PAGE } from './fixtures-run'

export const TIMING_PATH = `/api/v1/runs/${RUN_ID}/timing`
export const REGRESSIONS_PATH = `/api/v1/runs/${RUN_ID}/duration-regressions`

const T0 = Date.parse(isoAgo(0, 1.5))
export const at = (ms: number) => new Date(T0 + ms).toISOString()

/** Two workers, six tests each, back to back: 9.0 s on the clock, 17.4 s summed. Worker 0's third test failed. */
const LANES: [string, number, number, number[]][] = [
  ['0', 0, 60, [1500, 1200, 1800, 1400, 1300, 1500]],
  ['1', 100, 40, [2000, 1100, 1600, 1700, 1300, 1000]],
]
export const testId = (worker: string, i: number) =>
  worker === '0' && i === 2 ? TEST_CASE_ID : `66666666-6666-4666-8666-${worker.padStart(6, '0')}${String(i).padStart(6, '0')}`

function timelineItems() {
  const items: Record<string, unknown>[] = []
  for (const [worker, start, gap, durations] of LANES) {
    let offset = start
    durations.forEach((d, i) => {
      const failed = worker === '0' && i === 2
      items.push({
        test_id: testId(worker, i),
        test_name: failed ? TEST_CASE_NAME : `auth test ${worker}.${i}`,
        suite_name: 'Auth',
        status: failed ? 'FAILED' : 'PASSED',
        worker_id: worker,
        started_at: at(offset),
        finished_at: at(offset + d),
        offset_ms: offset,
        duration_ms: d,
      })
      offset += d + gap
    })
  }
  return items.sort((a, b) => (a.offset_ms as number) - (b.offset_ms as number))
}

const slow = (id: string, name: string, duration: number, extra: Record<string, unknown> = {}) => ({
  test_id: id, test_name: name, test_fingerprint: `fp-${id}`, suite_name: 'Auth', status: 'PASSED', duration_ms: duration,
  started_at: null, finished_at: null, setup_ms: null, call_ms: null, teardown_ms: null, worker_id: null,
  timing_source: 'report', baseline: null, baseline_ratio: null, ...extra,
})

const SLOWEST = [
  slow(testId('1', 0), 'auth test 1.0', 2000, { worker_id: '1' }),
  slow(TEST_CASE_ID, TEST_CASE_NAME, 1800, {
    status: 'FAILED', worker_id: '0', setup_ms: 120, call_ms: 1600, teardown_ms: 80,
    baseline: { mean_ms: 900, stddev_ms: 40, p95_ms: 980, sample_count: 14, last_observed_ms: 1800, last_observed_at: at(4620) },
    baseline_ratio: 2,
  }),
  slow(testId('1', 3), 'auth test 1.3', 1700, { worker_id: '1' }),
]

const FINDINGS = {
  run_id: RUN_ID, schema_version: 1, measured: true, reason: null, stored: true, evaluated_at: at(9500),
  spikes: [{
    test_fingerprint: `fp-${TEST_CASE_ID}`, test_name: TEST_CASE_NAME, suite_name: 'Auth', observed_ms: 1800,
    baseline_mean_ms: 900, baseline_stddev_ms: 40, baseline_p95_ms: 980, sample_count: 14, sigma: 22.5, delta_ms: 900,
    ratio: 2, previous_ms: 910, was_slow_before: false, new: true,
  }],
  spike_count: 1, suites: [], suite_regression_count: 0,
  test_check: { measured: true, reason: null }, suite_check: { measured: true, reason: null }, notified: {},
}

export const TIMED = {
  run_id: RUN_ID, project_id: '11111111-1111-4111-8111-111111111111', status: 'FAILED', build_number: '240', branch: 'main', created_at: at(0),
  run: { wall_clock_ms: 9000, summed_ms: 17400, parallelism: 1.93, timing_source: 'report', exec_started_at: at(0), exec_finished_at: at(9000), test_count: 12, timed_count: 12, duration_count: 12 },
  phases: { measured: true, reason: null, tests_with_phases: 12, setup_ms: 1440, call_ms: 14000, teardown_ms: 960, setup_share: 0.0883, teardown_share: 0.0589 },
  suites: {
    measured: true, reason: null, total: 1, truncated: false,
    items: [{ suite_name: 'Auth', suite_key: 'auth', source: 'report', started_at: at(0), finished_at: at(9000), wall_ms: 9000, summed_ms: 17400, setup_ms: null, teardown_ms: null, test_count: 12, timed_count: 12, passed: 11, failed: 1, broken: 0, skipped: 0, unknown: 0, parallelism: 1.93 }],
  },
  slowest: { limit: 20, total: 12, measured: true, reason: null, items: SLOWEST },
  workers: {
    measured: true, reason: null, total: 2, truncated: false,
    lanes: [
      { worker_id: '0', test_count: 6, busy_ms: 8700, started_at: at(0), finished_at: at(9000), span_ms: 9000 },
      { worker_id: '1', test_count: 6, busy_ms: 8700, started_at: at(100), finished_at: at(9000), span_ms: 8900 },
    ],
  },
  timeline: { measured: true, reason: null, limit: 2000, total: 12, truncated: false, origin: at(0), end: at(9000), items: timelineItems() },
  duration_findings: FINDINGS,
}

/** An old JUnit run: durations only, no clock. */
export const NO_TIMESTAMPS = {
  ...TIMED,
  run: { wall_clock_ms: null, summed_ms: 17400, parallelism: null, timing_source: 'derived_sum', exec_started_at: null, exec_finished_at: null, test_count: 12, timed_count: 0, duration_count: 12 },
  phases: { measured: false, reason: 'report carried no setup/teardown split', tests_with_phases: 0, setup_ms: null, call_ms: null, teardown_ms: null, setup_share: null, teardown_share: null },
  suites: { measured: false, reason: 'suite timing was not recorded for this run yet', total: 0, truncated: false, items: [] },
  slowest: { limit: 20, total: 12, measured: true, reason: null, items: SLOWEST.map((t) => ({ ...t, worker_id: null, setup_ms: null, call_ms: null, teardown_ms: null })) },
  workers: { measured: false, reason: 'report carried no timestamps', total: 0, truncated: false, lanes: [] },
  timeline: { measured: false, reason: 'report carried no timestamps', limit: 2000, total: 0, truncated: false, origin: null, end: null, items: [] },
  duration_findings: null,
}

/** The run page (and the test opened beside the tab) with this timing answer. */
export const withTiming = (body: unknown): ApiHandlers => [[TIMING_PATH, () => body], ...TEST_CASE, ...RUN_PAGE]
