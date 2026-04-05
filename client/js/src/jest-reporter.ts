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
 *       ['<rootDir>/node_modules/testlookup-reporter/dist/jest-reporter', {
 *         baseUrl:     process.env.TESTLOOKUP_URL     || 'http://localhost:8000',
 *         token:       process.env.TESTLOOKUP_TOKEN,
 *         projectId:   process.env.TESTLOOKUP_PROJECT_ID,
 *         buildNumber: process.env.TESTLOOKUP_BUILD   || `jest-${Date.now()}`,
 *         branch:      process.env.TESTLOOKUP_BRANCH,
 *         framework:   'jest',
 *       }],
 *     ],
 *   }
 *
 * Environment variables (alternative to inline config)
 * -----------------------------------------------------
 *   TESTLOOKUP_URL          TestLookup server base URL
 *   TESTLOOKUP_TOKEN        JWT access token
 *   TESTLOOKUP_PROJECT_ID   Target project UUID
 *   TESTLOOKUP_BUILD        CI build identifier (optional)
 *   TESTLOOKUP_BRANCH       Git branch name (optional)
 *   TESTLOOKUP_COMMIT       Git commit SHA (optional)
 */

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
  token?: string
  projectId?: string
  buildNumber?: string
  branch?: string
  commitHash?: string
  framework?: string
  batchSize?: number
  batchIntervalMs?: number
}

// ── Jest Reporter ──────────────────────────────────────────────────────────────

export default class TestLookupJestReporter {
  private readonly opts: Required<
    Pick<TestLookupJestReporterOptions, 'baseUrl' | 'token' | 'projectId'>
  > &
    TestLookupJestReporterOptions

  private reporter: TestLookupReporter | null = null
  private session: LiveSession | null = null
  private startTime = 0

  constructor(_globalConfig: JestConfig, options: TestLookupJestReporterOptions = {}) {
    const baseUrl   = options.baseUrl   ?? process.env['TESTLOOKUP_URL']          ?? ''
    const token     = options.token     ?? process.env['TESTLOOKUP_TOKEN']         ?? ''
    const projectId = options.projectId ?? process.env['TESTLOOKUP_PROJECT_ID']   ?? ''

    if (!baseUrl || !token || !projectId) {
      console.warn(
        '[TestLookup] Reporter disabled: missing baseUrl, token, or projectId. ' +
          'Set TESTLOOKUP_URL, TESTLOOKUP_TOKEN, TESTLOOKUP_PROJECT_ID env vars.',
      )
    }

    this.opts = {
      ...options,
      baseUrl,
      token,
      projectId,
      buildNumber:    options.buildNumber ?? process.env['TESTLOOKUP_BUILD'] ?? `jest-${Date.now()}`,
      branch:         options.branch      ?? process.env['TESTLOOKUP_BRANCH'],
      commitHash:     options.commitHash  ?? process.env['TESTLOOKUP_COMMIT'],
      framework:      options.framework   ?? 'jest',
    }
  }

  /** Called before any tests run. */
  async onRunStart(results: AggregatedResult): Promise<void> {
    if (!this.opts.baseUrl || !this.opts.token || !this.opts.projectId) return

    this.startTime = results.startTime ?? Date.now()

    try {
      this.reporter = new TestLookupReporter({
        baseUrl:   this.opts.baseUrl,
        token:     this.opts.token,
        projectId: this.opts.projectId,
        framework: this.opts.framework ?? 'jest',
        batchSize: this.opts.batchSize,
        batchIntervalMs: this.opts.batchIntervalMs,
      })

      this.session = await this.reporter.startSession({
        buildNumber: this.opts.buildNumber,
        branch:      this.opts.branch,
        commitHash:  this.opts.commitHash,
        totalTests:  results.numTotalTests,
      })

      console.log(`[TestLookup] Session started: ${this.session.sessionId}`)
    } catch (err) {
      console.error('[TestLookup] Failed to start session:', err)
      this.session = null
    }
  }

  /**
   * Called after each individual test case completes.
   * Requires Jest >= 27.4 (uses `onTestCaseResult`).
   * For older Jest, results are batched per file via `onTestResult`.
   */
  onTestCaseResult(_test: Test, testCaseResult: TestCaseResult): void {
    if (!this.session) return

    const status = this._mapStatus(testCaseResult.status)
    const suiteParts = testCaseResult.ancestorTitles
    const suiteName  = suiteParts.join(' > ') || undefined

    const error =
      testCaseResult.failureMessages.length > 0
        ? testCaseResult.failureMessages[0]!.split('\n')[0]
        : undefined
    const stackTrace =
      testCaseResult.failureMessages.length > 0
        ? testCaseResult.failureMessages.join('\n---\n')
        : undefined

    // Fire-and-forget: Jest doesn't await non-async reporter methods
    this.session
      .record(testCaseResult.fullName, status, testCaseResult.duration ?? 0, {
        suiteName,
        error,
        stackTrace,
      })
      .catch((e) => console.debug('[TestLookup] record error:', e))
  }

  /**
   * Fallback for Jest < 27.4: process the full test file result.
   * Called after each test *file* completes.
   */
  onTestResult(_test: Test, testResult: TestResult): void {
    if (!this.session) return

    for (const tc of testResult.testResults) {
      const status    = this._mapStatus(tc.status)
      const suiteName = tc.ancestorTitles.join(' > ') || undefined
      const error     = tc.failureMessages[0]?.split('\n')[0]
      const stack     = tc.failureMessages.join('\n---\n') || undefined

      this.session
        .record(tc.fullName, status, tc.duration ?? 0, { suiteName, error, stackTrace: stack })
        .catch((e) => console.debug('[TestLookup] record error:', e))
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
