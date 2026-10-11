import '@testing-library/jest-dom'
import { afterAll, afterEach } from 'vitest'
import { cleanup, configure } from '@testing-library/react'

import { assertNoNetworkRequests, installNetworkGuard } from './networkGuard'

/**
 * `findBy*` / `waitFor` wait 5 s, not Testing Library's 1 s default.
 *
 * Under the push gate (coverage on, the whole suite in parallel, often beside
 * other builds) a page's first data render or a lazy chunk's first load runs
 * past 1 s: ValueMetricsPage, the desktop shell's lazy settings layout and a
 * handful of lazy-chunk tests each failed the gate that way and passed alone
 * (2026-10-07/08). The budget measured the machine, not the code. It only
 * lengthens how long a REAL failure takes to report; a passing wait returns as
 * soon as its element appears. Negative assertions use `queryBy*`, unaffected.
 */
configure({ asyncUtilTimeout: 5_000 })

/**
 * Unmount every render between tests, unconditionally.
 *
 * Testing Library auto-cleans when globals are enabled, but a test that TIMES
 * OUT can still leave its tree mounted — and the next test in the file then
 * fails with a misleading `Found multiple elements`, pointing at innocent code
 * while the real fault is the earlier timeout. That cascade is exactly what
 * made ValueMetricsPage and AIConfigPage look independently broken.
 *
 * `cleanup()` is idempotent, so making it explicit costs nothing and keeps each
 * failure attributable to the test that actually caused it.
 */
afterEach(() => {
  cleanup()
})

/**
 * No test reaches the network (see `networkGuard.ts`): a real request is
 * blocked, and the test that made it fails here naming the method and URL. The
 * file-level check catches one that lands after the last test.
 */
installNetworkGuard()
afterEach(() => {
  assertNoNetworkRequests()
})
afterAll(() => {
  assertNoNetworkRequests()
})

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(globalThis, 'ResizeObserver', {
  writable: true,
  configurable: true,
  value: ResizeObserverMock,
})
