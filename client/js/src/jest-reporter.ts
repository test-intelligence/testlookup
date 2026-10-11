/**
 * TestLookup — Jest Custom Reporter
 * ====================================
 * Streams individual test results to TestLookup in real-time.
 *
 * Setup (jest.config.js / jest.config.ts)
 * ----------------------------------------
 *   module.exports = {
 *     reporters: [
 *       'default',
 *       ['testlookup-reporter/jest-reporter', {
 *         // Every option is optional: unset ones come from TESTLOOKUP_* env
 *         // vars, then testlookup.properties / testlookup.yaml.
 *         baseUrl:     'http://localhost:8000',
 *         projectId:   'My Project',
 *         buildNumber: process.env.CI_PIPELINE_ID,
 *       }],
 *     ],
 *   }
 *
 * Options and their fallbacks (option > env var > testlookup.* file key)
 * -----------------------------------------------------------------------
 *   baseUrl       TESTLOOKUP_ENDPOINT / TESTLOOKUP_URL   testlookup.endpoint
 *   apiKey        TESTLOOKUP_API_KEY                     testlookup.api.key  (sent as X-API-Key)
 *   token         TESTLOOKUP_TOKEN                       testlookup.token    (JWT, Bearer)
 *   projectId     TESTLOOKUP_PROJECT / _PROJECT_ID       testlookup.project
 *   buildNumber   TESTLOOKUP_BUILD                       testlookup.build    (default jest-<ms>)
 *   branch        TESTLOOKUP_BRANCH                      testlookup.branch
 *   commitHash    TESTLOOKUP_COMMIT                      testlookup.commit
 *   launchName    TESTLOOKUP_LAUNCH                      testlookup.launch
 *   suiteName     TESTLOOKUP_SUITE                       testlookup.suite    (default: launch)
 *   releaseName   TESTLOOKUP_RELEASE                     testlookup.release
 *   framework     TESTLOOKUP_FRAMEWORK                   testlookup.framework (default jest)
 */

import { loadConfig } from './config-loader'
import { TestLookupReporter, LiveSession, type TestStatus } from './testlookup-reporter'

// ── Types (subset of @jest/reporters to avoid hard dependency) ─────────────────

interface JestConfig {
  rootDir: string
  [key: string]: unknown
}

interface TestCaseResult {
  ancestorTitles: string[]
  fullName: string
  title: string
  status: 'passed' | 'failed' | 'skipped' | 'pending' | 'todo' | 'disabled'
  duration?: number | null
  failureMessages: string[]
  failureDetails: unknown[]
}

interface TestResult {
  testFilePath: string
  testResults: TestCaseResult[]
}

interface AggregatedResult {
  numTotalTests: number
  numPassedTests: number
  numFailedTests: number
  numPendingTests: number
  numSkippedTests: number
  startTime: number
}

interface Test {
  path: string
}

// ── Reporter Options ───────────────────────────────────────────────────────────

interface TestLookupJestReporterOptions {
  baseUrl?: string
  apiKey?: string
  token?: string
  projectId?: string
  buildNumber?: string
  branch?: string
  commitHash?: string
  launchName?: string
  suiteName?: string
  releaseName?: string
  framework?: string
  batchSize?: number
  batchIntervalMs?: number
}

// ── Jest Reporter ──────────────────────────────────────────────────────────────

export default class TestLookupJestReporter {
  private readonly opts: TestLookupJestReporterOptions

  private reporter: TestLookupReporter | null = null
  private session: LiveSession | null = null
  private startTime = 0
  /**
   * Results already streamed by onTestCaseResult, per test file: fullName →
   * how many. Jest >= 27.4 calls onTestCaseResult for each finished test AND
   * onTestResult with the whole file afterwards; onTestResult skips what was
   * already streamed. A count, not a set: a file may repeat a test name, and
   * Jest does not call onTestCaseResult for skipped tests at all.
   */
  private readonly streamed = new Map<string, Map<string, number>>()

  constructor(_globalConfig: JestConfig, options: TestLookupJestReporterOptions = {}) {
    const cfg = loadConfig()
    const launchName = options.launchName ?? cfg.launchName
    this.opts = {
      ...options,
      baseUrl:     options.baseUrl     ?? cfg.baseUrl,
      apiKey:      options.apiKey      ?? cfg.apiKey,
      token:       options.token       ?? cfg.token,
      projectId:   options.projectId   ?? cfg.projectId,
      buildNumber: options.buildNumber ?? cfg.buildNumber ?? `jest-${Date.now()}`,
      branch:      options.branch      ?? cfg.branch,
      commitHash:  options.commitHash  ?? cfg.commitHash,
      launchName,
      suiteName:   options.suiteName   ?? cfg.suiteName ?? launchName,
      releaseName: options.releaseName ?? cfg.releaseName,
      framework:   options.framework   ?? cfg.framework ?? 'jest',
    }

    if (!this._configured()) {
      console.warn(
        '[TestLookup] Reporter disabled: missing baseUrl, apiKey/token, or projectId. ' +
          'Set TESTLOOKUP_ENDPOINT, TESTLOOKUP_API_KEY and TESTLOOKUP_PROJECT, or a testlookup.properties file.',
      )
    }
  }

  /** Called before any tests run. */
  async onRunStart(results: AggregatedResult): Promise<void> {
    if (!this._configured()) return

    this.startTime = results.startTime ?? Date.now()

    try {
      this.reporter = new TestLookupReporter({
        baseUrl:     this.opts.baseUrl!,
        apiKey:      this.opts.apiKey,
        token:       this.opts.token,
        projectId:   this.opts.projectId!,
        framework:   this.opts.framework,
        suiteName:   this.opts.suiteName,
        releaseName: this.opts.releaseName,
        batchSize:   this.opts.batchSize,
        batchIntervalMs: this.opts.batchIntervalMs,
      })

      this.session = await this.reporter.startSession({
        buildNumber: this.opts.buildNumber,
        branch:      this.opts.branch,
        commitHash:  this.opts.commitHash,
        launchName:  this.opts.launchName,
        totalTests:  results.numTotalTests,
      })

      console.log(`[TestLookup] Session started: ${this.session.sessionId}`)
    } catch (err) {
      console.error('[TestLookup] Failed to start session:', err)
      this.session = null
    }
  }

  /** Called after each individual test case completes (Jest >= 27.4). */
  onTestCaseResult(test: Test, testCaseResult: TestCaseResult): void {
    if (!this.session) return
    this._record(testCaseResult)
    const perFile = this.streamed.get(test.path) ?? new Map<string, number>()
    perFile.set(testCaseResult.fullName, (perFile.get(testCaseResult.fullName) ?? 0) + 1)
    this.streamed.set(test.path, perFile)
  }

  /**
   * Called after each test *file* completes, on every Jest version. Streams
   * the results onTestCaseResult did not: all of them before Jest 27.4,
   * skipped and todo tests after.
   */
  onTestResult(test: Test, testResult: TestResult): void {
    if (!this.session) return
    const perFile = this.streamed.get(test.path)
    this.streamed.delete(test.path)

    for (const tc of testResult.testResults) {
      const already = perFile?.get(tc.fullName) ?? 0
      if (already > 0) {
        perFile!.set(tc.fullName, already - 1)
        continue
      }
      this._record(tc)
    }
  }

  /** Called after all test files have run. */
  async onRunComplete(_contexts: unknown, _results: AggregatedResult): Promise<void> {
    if (!this.session || !this.reporter) return

    try {
      await this.session.close()
      await this.reporter.closeSession(this.session.sessionId)
      const { sent, failed } = this.session.stats
      console.log(`[TestLookup] Session closed: sent=${sent} failed=${failed}`)
    } catch (err) {
      console.error('[TestLookup] Failed to close session:', err)
    }
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  private _configured(): boolean {
    return Boolean(this.opts.baseUrl && (this.opts.apiKey || this.opts.token) && this.opts.projectId)
  }

  private _record(tc: TestCaseResult): void {
    const status    = this._mapStatus(tc.status)
    const suiteName = tc.ancestorTitles.join(' > ') || undefined
    const error     = tc.failureMessages[0]?.split('\n')[0]
    const stack     = tc.failureMessages.join('\n---\n') || undefined

    // Fire-and-forget: Jest doesn't await non-async reporter methods
    this.session!
      .record(tc.fullName, status, tc.duration ?? 0, { suiteName, error, stackTrace: stack })
      .catch((e) => console.debug('[TestLookup] record error:', e))
  }

  private _mapStatus(
    jestStatus: 'passed' | 'failed' | 'skipped' | 'pending' | 'todo' | 'disabled',
  ): TestStatus {
    switch (jestStatus) {
      case 'passed':   return 'PASSED'
      case 'failed':   return 'FAILED'
      case 'skipped':
      case 'pending':
      case 'todo':
      case 'disabled': return 'SKIPPED'
      default:         return 'SKIPPED'
    }
  }
}
