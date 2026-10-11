/**
 * TestLookup — Mocha Reporter
 * ==============================
 * Streams individual test results to TestLookup in real-time.
 *
 * Setup (.mocharc.js / .mocharc.yaml)
 * --------------------------------------
 *   // .mocharc.js
 *   module.exports = {
 *     reporter: 'testlookup-reporter/mocha-reporter',
 *     // Every option is optional: unset ones come from TESTLOOKUP_* env
 *     // vars, then testlookup.properties / testlookup.yaml.
 *     reporterOptions: {
 *       testlookupUrl:     'http://localhost:8000',
 *       testlookupProject: 'My Project',
 *       testlookupBuild:   process.env.CI_PIPELINE_ID,
 *     },
 *   }
 *
 *   // CLI
 *   mocha --reporter testlookup-reporter/mocha-reporter \
 *         --reporter-option testlookupUrl=http://localhost:8000 \
 *         --reporter-option testlookupProject=<uuid or name>
 *
 * Options and their fallbacks (option > env var > testlookup.* file key)
 * -----------------------------------------------------------------------
 *   testlookupUrl       TESTLOOKUP_ENDPOINT / TESTLOOKUP_URL   testlookup.endpoint
 *   testlookupApiKey    TESTLOOKUP_API_KEY                     testlookup.api.key (X-API-Key)
 *   testlookupToken     TESTLOOKUP_TOKEN                       testlookup.token   (JWT, Bearer)
 *   testlookupProject   TESTLOOKUP_PROJECT / _PROJECT_ID       testlookup.project
 *   testlookupBuild     TESTLOOKUP_BUILD                       testlookup.build   (default mocha-<ms>)
 *   testlookupBranch    TESTLOOKUP_BRANCH                      testlookup.branch
 *   testlookupCommit    TESTLOOKUP_COMMIT                      testlookup.commit
 *   testlookupLaunch    TESTLOOKUP_LAUNCH                      testlookup.launch
 *   testlookupSuite     TESTLOOKUP_SUITE                       testlookup.suite   (default: launch)
 *   testlookupRelease   TESTLOOKUP_RELEASE                     testlookup.release
 *   (framework)         TESTLOOKUP_FRAMEWORK                   testlookup.framework (default mocha)
 *
 * This module's export is the reporter class itself (``module.exports =``):
 * Mocha does ``new (require(name))(runner, options)`` and does not unwrap a
 * ``default`` export.
 */

import { loadConfig } from './config-loader'
import { TestLookupReporter, LiveSession, type TestStatus } from './testlookup-reporter'

// ── Minimal Mocha type stubs (avoid hard dep on @types/mocha) ─────────────────

interface MochaRunner {
  on(event: string, listener: (...args: unknown[]) => void): this
  stats?: { passes: number; failures: number; pending: number }
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
    testlookupApiKey?:  string
    testlookupToken?:   string
    testlookupProject?: string
    testlookupBuild?:   string
    testlookupBranch?:  string
    testlookupCommit?:  string
    testlookupLaunch?:  string
    testlookupSuite?:   string
    testlookupRelease?: string
    [key: string]: unknown
  }
}

// ── Reporter ───────────────────────────────────────────────────────────────────

class TestLookupMochaReporter {
  private reporter: TestLookupReporter | null = null
  /** Resolves to the open session, or null when it could not be opened. */
  private sessionReady: Promise<LiveSession | null> | null = null
  /** record() calls in flight, awaited before the session closes. */
  private readonly inFlight: Promise<unknown>[] = []
  private readonly baseUrl: string | undefined
  private readonly apiKey: string | undefined
  private readonly token: string | undefined
  private readonly projectId: string | undefined
  private readonly buildNumber: string
  private readonly branch: string | undefined
  private readonly commitHash: string | undefined
  private readonly launchName: string | undefined
  private readonly suiteName: string | undefined
  private readonly releaseName: string | undefined
  private readonly framework: string

  constructor(runner: MochaRunner, options: MochaOptions = {}) {
    const ro = options.reporterOptions ?? {}
    const cfg = loadConfig()

    this.baseUrl     = ro.testlookupUrl     ?? cfg.baseUrl
    this.apiKey      = ro.testlookupApiKey  ?? cfg.apiKey
    this.token       = ro.testlookupToken   ?? cfg.token
    this.projectId   = ro.testlookupProject ?? cfg.projectId
    this.buildNumber = ro.testlookupBuild   ?? cfg.buildNumber ?? `mocha-${Date.now()}`
    this.branch      = ro.testlookupBranch  ?? cfg.branch
    this.commitHash  = ro.testlookupCommit  ?? cfg.commitHash
    this.launchName  = ro.testlookupLaunch  ?? cfg.launchName
    this.suiteName   = ro.testlookupSuite   ?? cfg.suiteName ?? this.launchName
    this.releaseName = ro.testlookupRelease ?? cfg.releaseName
    this.framework   = cfg.framework ?? 'mocha'

    if (!this.baseUrl || !(this.apiKey || this.token) || !this.projectId) {
      console.warn(
        '[TestLookup] Reporter disabled: missing URL, API key/token, or project. ' +
          'Set TESTLOOKUP_ENDPOINT, TESTLOOKUP_API_KEY and TESTLOOKUP_PROJECT, or a testlookup.properties file.',
      )
      return
    }

    this._attachHooks(runner)
  }

  /**
   * Mocha calls this after the run and waits for ``fn`` before exiting (even
   * with ``--exit``), so the final flush and the session close happen before
   * the process can end.
   */
  done(failures: number, fn: (failures: number) => void): void {
    this._finish()
      .catch((err) => console.error('[TestLookup] Failed to close session:', err))
      .finally(() => fn(failures))
  }

  // ── Runner hooks ───────────────────────────────────────────────────────────

  private _attachHooks(runner: MochaRunner): void {
    runner.on('start',   () => this._onStart())
    runner.on('pass',    (test: unknown) => this._record(test as MochaTest, 'PASSED'))
    runner.on('fail',    (test: unknown) => this._record(test as MochaTest, 'FAILED'))
    runner.on('pending', (test: unknown) => this._record(test as MochaTest, 'SKIPPED'))
  }

  private _onStart(): void {
    try {
      this.reporter = new TestLookupReporter({
        baseUrl:     this.baseUrl!,
        apiKey:      this.apiKey,
        token:       this.token,
        projectId:   this.projectId!,
        framework:   this.framework,
        suiteName:   this.suiteName,
        releaseName: this.releaseName,
      })
    } catch (err) {
      console.error('[TestLookup] Failed to start session:', err)
      return
    }

    this.sessionReady = this.reporter
      .startSession({
        buildNumber: this.buildNumber,
        branch:      this.branch,
        commitHash:  this.commitHash,
        launchName:  this.launchName,
      })
      .then((s) => {
        console.log(`[TestLookup] Mocha session started: ${s.sessionId}`)
        return s
      })
      .catch((err) => {
        console.error('[TestLookup] Failed to start session:', err)
        return null
      })
  }

  private async _finish(): Promise<void> {
    const session = this.sessionReady ? await this.sessionReady : null
    await Promise.all(this.inFlight)
    if (!session || !this.reporter) return
    await session.close()
    await this.reporter.closeSession(session.sessionId)
    const { sent, failed } = session.stats
    console.log(`[TestLookup] Mocha session closed: sent=${sent} failed=${failed}`)
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  /**
   * Fast tests finish before the session-create request returns, so a result
   * waits for the session rather than being dropped. The test's fields are
   * read now, while Mocha still holds them.
   */
  private _record(test: MochaTest, status: TestStatus): void {
    if (!this.sessionReady) return

    const name   = test.fullTitle()
    const suite  = test.parent?.fullTitle() || undefined
    const error  = test.err?.message
    const stack  = test.err?.stack
    const duration = test.duration ?? 0

    this.inFlight.push(
      this.sessionReady
        .then((s) => s?.record(name, status, duration, { suiteName: suite, error, stackTrace: stack }))
        .catch((e) => console.debug('[TestLookup] record error:', e)),
    )
  }
}

export = TestLookupMochaReporter
