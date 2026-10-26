/**
 * The session lifecycle every live adapter shares (W18 DEV-03): Playwright, Vitest, Cypress, Cucumber.
 *
 * One place for the rules the Jest and Mocha reporters already follow:
 *   - configuration from the adapter's options, then ``testlookup.properties`` / ``TESTLOOKUP_*``
 *     (``loadConfig``); a missing URL, credential or project disables the adapter with one warning;
 *   - a result that arrives before the session exists waits for it instead of being dropped;
 *   - every network error is logged and swallowed — streaming never fails the user's test run;
 *   - ``finish()`` waits for the results in flight, then closes the session.
 */
import { loadConfig, resolveCredentials } from './config-loader'
import { TestLookupReporter, LiveSession, type RecordOptions, type TestStatus } from './testlookup-reporter'

export interface LiveAdapterOptions {
  testlookupUrl?: string
  testlookupApiKey?: string
  testlookupToken?: string
  testlookupProject?: string
  testlookupBuild?: string
  testlookupBranch?: string
  testlookupCommit?: string
  testlookupLaunch?: string
  testlookupSuite?: string
  testlookupRelease?: string
  /** Batch flush interval passed to the core client (tests use a long one). */
  batchIntervalMs?: number
}

export class LiveAdapter {
  readonly enabled: boolean
  private reporter: TestLookupReporter | null = null
  private sessionReady: Promise<LiveSession | null> | null = null
  private readonly inFlight: Promise<unknown>[] = []
  private readonly settings: Required<Pick<LiveAdapterOptions, 'testlookupBuild'>> & LiveAdapterOptions & { framework: string }

  constructor(framework: string, options: LiveAdapterOptions = {}) {
    const cfg = loadConfig()
    const credentials = resolveCredentials({ apiKey: options.testlookupApiKey, token: options.testlookupToken })
    this.settings = {
      ...options,
      framework: cfg.framework ?? framework,
      testlookupUrl: options.testlookupUrl ?? cfg.baseUrl,
      testlookupApiKey: credentials.apiKey,
      testlookupToken: credentials.token,
      testlookupProject: options.testlookupProject ?? cfg.projectId,
      testlookupBuild: options.testlookupBuild ?? cfg.buildNumber ?? `${framework}-${Date.now()}`,
      testlookupBranch: options.testlookupBranch ?? cfg.branch,
      testlookupCommit: options.testlookupCommit ?? cfg.commitHash,
      testlookupLaunch: options.testlookupLaunch ?? cfg.launchName,
      testlookupSuite: options.testlookupSuite ?? cfg.suiteName ?? options.testlookupLaunch ?? cfg.launchName,
      testlookupRelease: options.testlookupRelease ?? cfg.releaseName,
    }
    const s = this.settings
    this.enabled = Boolean(s.testlookupUrl && (s.testlookupApiKey || s.testlookupToken) && s.testlookupProject)
    if (!this.enabled) {
      console.warn(
        `[TestLookup] ${framework} reporter disabled: missing URL, API key/token, or project. ` +
          'Set TESTLOOKUP_ENDPOINT, TESTLOOKUP_API_KEY and TESTLOOKUP_PROJECT, or a testlookup.properties file.',
      )
    }
  }

  /** Open the session (idempotent). Safe to call before any result. */
  start(extra: { launchName?: string } = {}): void {
    if (!this.enabled || this.sessionReady) return
    const s = this.settings
    try {
      this.reporter = new TestLookupReporter({
        baseUrl: s.testlookupUrl!,
        apiKey: s.testlookupApiKey,
        token: s.testlookupToken,
        projectId: s.testlookupProject!,
        framework: s.framework,
        suiteName: s.testlookupSuite,
        releaseName: s.testlookupRelease,
        ...(s.batchIntervalMs ? { batchIntervalMs: s.batchIntervalMs } : {}),
      })
    } catch (err) {
      console.error('[TestLookup] Failed to start session:', err)
      return
    }
    this.sessionReady = this.reporter
      .startSession({
        buildNumber: s.testlookupBuild,
        branch: s.testlookupBranch,
        commitHash: s.testlookupCommit,
        launchName: extra.launchName ?? s.testlookupLaunch,
      })
      .then((session) => {
        console.log(`[TestLookup] ${s.framework} session started: ${session.sessionId}`)
        return session
      })
      .catch((err) => {
        console.error('[TestLookup] Failed to start session:', err)
        return null
      })
  }

  /** Queue one result; it waits for the session. Never throws. */
  record(name: string, status: TestStatus, durationMs: number, opts: RecordOptions = {}): void {
    if (!this.enabled) return
    if (!this.sessionReady) this.start()
    if (!this.sessionReady) return
    this.inFlight.push(
      this.sessionReady
        .then((s) => s?.record(name, status, Math.max(0, Math.round(durationMs || 0)), opts))
        .catch((e) => console.debug('[TestLookup] record error:', e)),
    )
  }

  /** Wait for the results in flight, then close the session. Never throws. */
  async finish(): Promise<void> {
    try {
      const session = this.sessionReady ? await this.sessionReady : null
      await Promise.all(this.inFlight)
      if (!session || !this.reporter) return
      await session.close()
      await this.reporter.closeSession(session.sessionId)
      const { sent, failed } = session.stats
      console.log(`[TestLookup] ${this.settings.framework} session closed: sent=${sent} failed=${failed}`)
    } catch (err) {
      console.error('[TestLookup] Failed to close session:', err)
    }
  }
}

/** Epoch milliseconds from a Date, a number, or a ``{seconds, nanos}`` timestamp. */
export function epochMs(value: unknown): number | undefined {
  if (value instanceof Date) return value.getTime()
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (value && typeof value === 'object' && 'seconds' in value) {
    const v = value as { seconds: number | string; nanos?: number }
    return Number(v.seconds) * 1000 + Math.floor((v.nanos ?? 0) / 1e6)
  }
  return undefined
}
