/**
 * TestLookup — Mocha Reporter
 * ==============================
 * Streams individual test results to TestLookup in real-time.
 *
 * Setup (.mocharc.js / .mocharc.yaml)
 * --------------------------------------
 *   // .mocharc.js
 *   module.exports = {
 *     reporter: './node_modules/testlookup-reporter/dist/mocha-reporter',
 *     reporterOptions: {
 *       testlookupUrl:       process.env.TESTLOOKUP_URL,
 *       testlookupToken:     process.env.TESTLOOKUP_TOKEN,
 *       testlookupProject:   process.env.TESTLOOKUP_PROJECT_ID,
 *       testlookupBuild:     process.env.TESTLOOKUP_BUILD || `mocha-${Date.now()}`,
 *       testlookupBranch:    process.env.TESTLOOKUP_BRANCH,
 *     },
 *   }
 *
 *   // CLI
 *   mocha --reporter ./testlookup-mocha-reporter.js \
 *         --reporter-option testlookupUrl=http://localhost:8000 \
 *         --reporter-option testlookupToken=<jwt> \
 *         --reporter-option testlookupProject=<uuid>
 *
 * Environment variables (fallback when reporter options are absent)
 * -----------------------------------------------------------------
 *   TESTLOOKUP_URL          Server base URL
 *   TESTLOOKUP_TOKEN        JWT access token
 *   TESTLOOKUP_PROJECT_ID   Target project UUID
 *   TESTLOOKUP_BUILD        Build identifier
 *   TESTLOOKUP_BRANCH       Git branch
 *   TESTLOOKUP_COMMIT       Git commit SHA
 */

import { TestLookupReporter, LiveSession, type TestStatus } from './testlookup-reporter'

// ── Minimal Mocha type stubs (avoid hard dep on @types/mocha) ─────────────────

interface MochaRunner {
  on(event: string, listener: (...args: unknown[]) => void): this
  stats: { passes: number; failures: number; pending: number }
}

interface MochaTest {
  fullTitle(): string
  titlePath(): string[]
  duration?: number
  err?: Error & { stack?: string }
  isPassed(): boolean
  isFailed(): boolean
  isPending(): boolean
  parent?: { fullTitle(): string }
}

interface MochaOptions {
  reporterOptions?: {
    testlookupUrl?:     string
    testlookupToken?:   string
    testlookupProject?: string
    testlookupBuild?:   string
    testlookupBranch?:  string
    testlookupCommit?:  string
    [key: string]: unknown
  }
}

// ── Reporter ───────────────────────────────────────────────────────────────────

export default class TestLookupMochaReporter {
  private reporter: TestLookupReporter | null = null
  private session: LiveSession | null = null
  private readonly baseUrl: string
  private readonly token: string
  private readonly projectId: string
  private readonly buildNumber: string
  private readonly branch: string | undefined
  private readonly commitHash: string | undefined

  constructor(runner: MochaRunner, options: MochaOptions = {}) {
    const ro = options.reporterOptions ?? {}

    this.baseUrl     = ro.testlookupUrl     ?? process.env['TESTLOOKUP_URL']          ?? ''
    this.token       = ro.testlookupToken   ?? process.env['TESTLOOKUP_TOKEN']         ?? ''
    this.projectId   = ro.testlookupProject ?? process.env['TESTLOOKUP_PROJECT_ID']   ?? ''
    this.buildNumber = ro.testlookupBuild   ?? process.env['TESTLOOKUP_BUILD']         ?? `mocha-${Date.now()}`
    this.branch      = ro.testlookupBranch  ?? process.env['TESTLOOKUP_BRANCH']
    this.commitHash  = ro.testlookupCommit  ?? process.env['TESTLOOKUP_COMMIT']

    if (!this.baseUrl || !this.token || !this.projectId) {
      console.warn(
        '[TestLookup] Reporter disabled: missing URL, token, or projectId.',
      )
      return
    }

    this._attachHooks(runner)
  }

  // ── Runner hooks ───────────────────────────────────────────────────────────

  private _attachHooks(runner: MochaRunner): void {
    runner.on('start', () => this._onStart())
    runner.on('pass',  (test: unknown) => this._onPass(test as MochaTest))
    runner.on('fail',  (test: unknown) => this._onFail(test as MochaTest))
    runner.on('pending', (test: unknown) => this._onPending(test as MochaTest))
    runner.on('end',   () => this._onEnd())
  }

  private _onStart(): void {
    this.reporter = new TestLookupReporter({
      baseUrl:   this.baseUrl,
      token:     this.token,
      projectId: this.projectId,
      framework: 'mocha',
    })

    this.reporter
      .startSession({
        buildNumber: this.buildNumber,
        branch:      this.branch,
        commitHash:  this.commitHash,
      })
      .then((s) => {
        this.session = s
        console.log(`[TestLookup] Mocha session started: ${s.sessionId}`)
      })
      .catch((err) => {
        console.error('[TestLookup] Failed to start session:', err)
      })
  }

  private _onPass(test: MochaTest): void {
    this._record(test, 'PASSED')
  }

  private _onFail(test: MochaTest): void {
    this._record(test, 'FAILED')
  }

  private _onPending(test: MochaTest): void {
    this._record(test, 'SKIPPED')
  }

  private _onEnd(): void {
    if (!this.session || !this.reporter) return

    this.session
      .close()
      .then(() => this.reporter!.closeSession(this.session!.sessionId))
      .then(() => {
        const { sent, failed } = this.session!.stats
        console.log(`[TestLookup] Mocha session closed: sent=${sent} failed=${failed}`)
      })
      .catch((err) => console.error('[TestLookup] Failed to close session:', err))
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  private _record(test: MochaTest, status: TestStatus): void {
    if (!this.session) return

    const suite  = test.parent?.fullTitle() || undefined
    const error  = test.err?.message
    const stack  = test.err?.stack

    this.session
      .record(test.fullTitle(), status, test.duration ?? 0, {
        suiteName:  suite,
        error,
        stackTrace: stack,
      })
      .catch((e) => console.debug('[TestLookup] record error:', e))
  }
}
