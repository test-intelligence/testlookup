import '@testing-library/jest-dom'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

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
