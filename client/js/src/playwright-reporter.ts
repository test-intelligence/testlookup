/**
 * TestLookup live reporter for Playwright Test (W18 DEV-03).
 *
 *   // playwright.config.ts
 *   reporter: [['list'], ['testlookup-reporter/playwright-reporter', { testlookupProject: '<id>' }]]
 *
 * Each finished attempt is one result as it finishes (``onTestEnd``): a retried
 * test shows attempt 1 failed, attempt 2 passed (``metadata.attempt``). The
 * Playwright project (chromium, firefox, …) is the result's suite and its
 * ``metadata.environment``, so the run lists one suite per project. Network
 * errors are logged, never thrown: the reporter cannot fail your run.
 *
 * Supported: @playwright/test >= 1.30 (the Reporter API is stable since 1.0).
 */
import { LiveAdapter, type LiveAdapterOptions } from './live-adapter'
import type { TestStatus } from './testlookup-reporter'

// Minimal structural types: the package type-checks without @playwright/test installed.
interface PwSuite {
  title: string
  parent?: PwSuite
  project?: () => { name?: string } | undefined
}
interface PwTestCase {
  title: string
  parent: PwSuite
  location?: { file: string; line: number }
  titlePath: () => string[]
  tags?: string[]
}
interface PwTestResult {
  status: 'passed' | 'failed' | 'timedOut' | 'skipped' | 'interrupted'
  duration: number
  retry: number
  startTime: Date
  workerIndex?: number
  error?: { message?: string; stack?: string }
  errors?: Array<{ message?: string; stack?: string }>
}

const STATUS: Record<PwTestResult['status'], TestStatus> = {
  passed: 'PASSED',
  failed: 'FAILED',
  timedOut: 'FAILED',
  interrupted: 'BROKEN',
  skipped: 'SKIPPED',
}

/** The project a test belongs to (the suite chain's top below the root). */
function projectName(test: PwTestCase): string | undefined {
  let s: PwSuite | undefined = test.parent
  while (s) {
    const p = s.project?.()
    if (p?.name) return p.name
    s = s.parent
  }
  return undefined
}

/** ``describe > test`` without the root, project and file segments Playwright prepends. */
function testName(test: PwTestCase): string {
  const path = test.titlePath().filter(Boolean)
  const file = test.location?.file?.replace(/\\/g, '/').split('/').pop()
  const project = projectName(test)
  const trimmed = path.filter((p, i) => !(i === 0 && p === project) && p.replace(/\\/g, '/').split('/').pop() !== file)
  return trimmed.join(' > ') || test.title
}

class TestLookupPlaywrightReporter {
  private readonly adapter: LiveAdapter

  constructor(options: LiveAdapterOptions = {}) {
    this.adapter = new LiveAdapter('playwright', options)
  }

  printsToStdio(): boolean {
    return false
  }

  onBegin(): void {
    this.adapter.start()
  }

  onTestEnd(test: PwTestCase, result: PwTestResult): void {
    const project = projectName(test)
    const err = result.error ?? result.errors?.[0]
    this.adapter.record(testName(test), STATUS[result.status] ?? 'BROKEN', result.duration, {
      suiteName: project ?? test.location?.file,
      // The project is part of the test's identity: the same spec under chromium and firefox is two
      // tests (as in Playwright's own reports), not one row overwritten by the other.
      className: project && test.location?.file ? `${test.location.file} [${project}]` : test.location?.file,
      error: err?.message,
      stackTrace: err?.stack,
      tags: test.tags,
      startedAtMs: result.startTime instanceof Date ? result.startTime.getTime() : undefined,
      workerId: result.workerIndex !== undefined ? String(result.workerIndex) : undefined,
      metadata: { attempt: result.retry + 1, retry: result.retry, ...(project ? { environment: project } : {}) },
    })
  }

  async onEnd(): Promise<void> {
    await this.adapter.finish()
  }
}

// CommonJS ``module.exports`` IS the class (``export =``), with ``.default`` pointing at itself:
// require() and ESM import() (Cucumber, Vitest, an ESM config) both get it, whichever they read.
// ``export default`` compiled to ``exports.default`` only, and ``import()`` then handed back
// ``{ default: … }`` — "not a constructor" (found driving the adapters against a real server).
Object.assign(TestLookupPlaywrightReporter, { default: TestLookupPlaywrightReporter })
export = TestLookupPlaywrightReporter
