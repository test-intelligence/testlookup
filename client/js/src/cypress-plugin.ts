/**
 * TestLookup live plugin for Cypress (W18 DEV-03).
 *
 *   // cypress.config.ts
 *   import testlookup from 'testlookup-reporter/cypress-plugin'
 *   export default defineConfig({ e2e: { setupNodeEvents(on, config) { testlookup(on, config, { testlookupProject: '<id>' }) } } })
 *
 * Granularity: Cypress hands results to the Node side per SPEC (``after:spec``),
 * so a spec's tests appear on ``/live`` together when the spec finishes, not one
 * by one. Retries come back as attempts: the last attempt is the result, with
 * ``metadata.attempt`` = the attempt count. Needs ``cypress run`` (``cypress open``
 * fires no ``after:spec`` unless ``experimentalInteractiveRunEvents`` is on).
 * Network errors are logged, never thrown.
 *
 * Supported: cypress >= 10 (setupNodeEvents).
 */
import { LiveAdapter, type LiveAdapterOptions } from './live-adapter'
import type { TestStatus } from './testlookup-reporter'

interface CyAttempt {
  state?: string
  duration?: number
  wallClockDuration?: number
  wallClockStartedAt?: string
  startedAt?: string
  error?: { message?: string; stack?: string } | null
}
interface CyTest {
  title: string[]
  state: 'passed' | 'failed' | 'pending' | 'skipped' | string
  duration?: number
  displayError?: string | null
  attempts?: CyAttempt[]
}
interface CySpecResults {
  tests?: CyTest[]
  stats?: { wallClockStartedAt?: string }
}
type On = (event: string, handler: (...args: any[]) => unknown) => void

const STATUS: Record<string, TestStatus> = { passed: 'PASSED', failed: 'FAILED', pending: 'SKIPPED', skipped: 'SKIPPED' }

/** Record one spec's tests. Exported for tests. */
export function recordSpec(adapter: LiveAdapter, spec: { relative?: string; name?: string } | undefined, results: CySpecResults | undefined): void {
  const specName = spec?.relative ?? spec?.name
  for (const t of results?.tests ?? []) {
    const attempts = t.attempts ?? []
    const last = attempts[attempts.length - 1]
    const duration = t.duration ?? last?.duration ?? last?.wallClockDuration ?? 0
    const started = last?.startedAt ?? last?.wallClockStartedAt
    const err = last?.error ?? undefined
    adapter.record(t.title.join(' > '), STATUS[t.state] ?? 'BROKEN', duration, {
      suiteName: specName,
      className: specName,
      error: err?.message ?? t.displayError ?? undefined,
      stackTrace: err?.stack,
      startedAtMs: started ? Date.parse(started) : undefined,
      metadata: { attempt: Math.max(1, attempts.length) },
    })
  }
}

export default function testlookupCypress(on: On, config: unknown, options: LiveAdapterOptions = {}): unknown {
  const adapter = new LiveAdapter('cypress', options)
  if (!adapter.enabled) return config
  on('before:run', () => {
    adapter.start()
  })
  on('after:spec', (spec: { relative?: string; name?: string }, results: CySpecResults) => {
    recordSpec(adapter, spec, results)
  })
  on('after:run', async () => {
    await adapter.finish()
  })
  return config
}
