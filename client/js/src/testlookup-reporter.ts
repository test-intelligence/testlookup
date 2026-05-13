/**
 * TestLookup Reporter — JavaScript / TypeScript Client SDK
 * =========================================================
 * Streams test execution events to a TestLookup server in real-time.
 * Works in Node.js 18+ (native fetch) and browsers.
 *
 * Quick start (TypeScript / ESM)
 * --------------------------------
 *   import { TestLookupReporter } from './testlookup-reporter'
 *
 *   const reporter = new TestLookupReporter({
 *     baseUrl: 'http://localhost:8000',
 *     token: '<jwt>',
 *     projectId: '<uuid>',
 *   })
 *
 *   const session = await reporter.startSession({ buildNumber: 'build-42' })
 *   await session.record('login test', 'PASSED', 120)
 *   await session.record('checkout test', 'FAILED', 340, {
 *     error: 'AssertionError: expected 200, got 500',
 *   })
 *   await session.close()
 *   await reporter.closeSession(session.sessionId)
 *
 * Login helper (obtain JWT)
 * -------------------------
 *   const token = await TestLookupReporter.login(
 *     'http://localhost:8000', 'admin', 'secret'
 *   )
 */

import * as os from 'os'

// ── Constants ──────────────────────────────────────────────────────────────────
const BATCH_SIZE         = 50        // flush when buffer reaches this count
const BATCH_INTERVAL_MS  = 100       // flush at most every N ms
const MAX_BATCH_SIZE     = 1_000     // hard cap per HTTP call
const MAX_RETRIES        = 5
const RETRY_BASE_DELAY_MS = 500
const DEFAULT_TIMEOUT_MS  = 30_000

// ── Public Types ───────────────────────────────────────────────────────────────

export type TestStatus = 'PASSED' | 'FAILED' | 'SKIPPED' | 'BROKEN'
export type LogLevel   = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'

export interface ReporterConfig {
  /** TestLookup server URL, e.g. "http://localhost:8000" */
  baseUrl: string
  /** JWT access token (from /api/v1/auth/login) */
  token: string
  /** Target project UUID */
  projectId: string
  /** Human-readable label for this machine (default: os.hostname()) */
  clientName?: string
  /** Test framework name, e.g. "jest", "mocha", "vitest" (default: "javascript") */
  framework?: string
  /**
   * Default run-level suite identifier. Applied to every record() call that
   * doesn't supply its own suiteName. Pair with the matching config keys —
   * testlookup.suite (preferred) or testlookup.launch (fallback).
   */
  suiteName?: string
  /**
   * Default release this test run belongs to. Pair with testlookup.release /
   * TESTLOOKUP_RELEASE. When blank the server falls back to the project's
   * default release on session create.
   */
  releaseName?: string
  batchSize?: number
  batchIntervalMs?: number
  timeoutMs?: number
  /** Set false to skip TLS certificate validation (dev only) */
  verifySsl?: boolean
}

export interface SessionOptions {
  buildNumber?: string
  runId?: string
  branch?: string
  commitHash?: string
  totalTests?: number
  machineId?: string
  /** Human-readable launch label (analogous to ReportPortal's rp.launch). */
  launchName?: string
  /** Per-session suite override (beats ReporterConfig.suiteName). */
  suiteName?: string
  /** Per-session release override (beats ReporterConfig.releaseName). */
  releaseName?: string
  metadata?: Record<string, unknown>
}

export interface RecordOptions {
  suiteName?: string
  className?: string
  error?: string
  stackTrace?: string
  tags?: string[]
  metadata?: Record<string, unknown>
}

export interface SessionStats {
  sent: number
  failed: number
}

// ── Internal Types ─────────────────────────────────────────────────────────────

interface LiveEvent {
  event_type: 'test_result' | 'log' | 'metric'
  test_name: string | null
  status: string | null
  duration_ms?: number
  timestamp_ms: number
  suite_name?: string
  class_name?: string
  error_message?: string
  stack_trace?: string
  tags?: string[]
  metadata?: Record<string, unknown>
}

interface SessionCreateResponse {
  session_id: string
  session_token: string
  run_id: string
  project_id: string
  expires_in: number
  created_at: string
}

// ── TestLookupReporter ─────────────────────────────────────────────────────────

/**
 * Entry point for managing live execution sessions.
 *
 * One reporter instance can create multiple sessions sequentially or in parallel.
 */
export class TestLookupReporter {
  private readonly baseUrl: string
  private readonly token: string
  private readonly projectId: string
  private readonly clientName: string
  private readonly framework: string
  private readonly suiteName?: string
  private readonly releaseName?: string
  private readonly batchSize: number
  private readonly batchIntervalMs: number
  private readonly timeoutMs: number

  constructor(config: ReporterConfig) {
    this.baseUrl        = config.baseUrl.replace(/\/$/, '')
    this.token          = config.token
    this.projectId      = config.projectId
    this.clientName     = config.clientName ?? os.hostname()
    this.framework      = config.framework  ?? 'javascript'
    this.suiteName      = config.suiteName
    this.releaseName    = config.releaseName
    this.batchSize      = Math.min(config.batchSize ?? BATCH_SIZE, MAX_BATCH_SIZE)
    this.batchIntervalMs = config.batchIntervalMs ?? BATCH_INTERVAL_MS
    this.timeoutMs      = config.timeoutMs ?? DEFAULT_TIMEOUT_MS
  }

  /** Register a new live session with the server and return a LiveSession object. */
  async startSession(opts: SessionOptions = {}): Promise<LiveSession> {
    // Per-session suite overrides the reporter-level default; either flows
    // both into the server payload (LiveSession.suite_name + TestRun) and
    // into the returned LiveSession as the record() default.
    const sessionSuite = opts.suiteName ?? this.suiteName
    // Per-session release wins; otherwise inherit testlookup.release /
    // TESTLOOKUP_RELEASE. Blank → server uses the project's default release.
    const sessionRelease = opts.releaseName ?? this.releaseName

    const payload: Record<string, unknown> = {
      project_id:  this.projectId,
      client_name: this.clientName,
      framework:   this.framework,
      machine_id:  opts.machineId ?? os.hostname(),
    }
    if (opts.buildNumber  != null) payload.build_number = opts.buildNumber
    if (opts.runId        != null) payload.run_id       = opts.runId
    if (opts.branch       != null) payload.branch       = opts.branch
    if (opts.commitHash   != null) payload.commit_hash  = opts.commitHash
    if (opts.totalTests   != null) payload.total_tests  = opts.totalTests
    if (opts.launchName   != null) payload.launch_name  = opts.launchName
    if (sessionSuite      != null) payload.suite_name   = sessionSuite
    if (sessionRelease    != null) payload.release_name = sessionRelease
    if (opts.metadata     != null) payload.metadata     = opts.metadata

    const data = await this._fetch<SessionCreateResponse>('POST', '/api/v1/stream/sessions', payload)

    return new LiveSession({
      sessionId:     data.session_id,
      sessionToken:  data.session_token,
      runId:         data.run_id,
      baseUrl:       this.baseUrl,
      batchSize:     this.batchSize,
      batchIntervalMs: this.batchIntervalMs,
      timeoutMs:     this.timeoutMs,
      suiteName:     sessionSuite,
    })
  }

  /** Mark a session as complete and trigger AI analysis pipeline. */
  async closeSession(sessionId: string): Promise<void> {
    try {
      await this._fetch<void>('DELETE', `/api/v1/stream/sessions/${sessionId}`)
    } catch (err) {
      console.warn(`[TestLookup] Failed to close session ${sessionId}:`, err)
    }
  }

  /**
   * Convenience: obtain a JWT access token via username/password login.
   *
   *   const token = await TestLookupReporter.login(url, 'admin', 'secret')
   */
  static async login(baseUrl: string, username: string, password: string): Promise<string> {
    const url  = baseUrl.replace(/\/$/, '') + '/api/v1/auth/login'
    const body = new URLSearchParams({ username, password })
    const resp = await fetch(url, {
      method:  'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body:    body.toString(),
    })
    if (!resp.ok) {
      const detail = await resp.text().catch(() => '')
      throw new Error(`Login failed (${resp.status}): ${detail}`)
    }
    const data = await resp.json() as { access_token: string }
    return data.access_token
  }

  // ── Private ────────────────────────────────────────────────────────────────

  private async _fetch<T>(method: string, path: string, body?: unknown): Promise<T> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeoutMs)

    try {
      const resp = await fetch(this.baseUrl + path, {
        method,
        headers: {
          Authorization:  `Bearer ${this.token}`,
          'Content-Type': 'application/json',
        },
        body:   body != null ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      })

      if (!resp.ok) {
        const detail = await resp.text().catch(() => '')
        throw new Error(`TestLookup HTTP ${resp.status} ${path}: ${detail}`)
      }

      if (resp.status === 204) return undefined as T
      return resp.json() as Promise<T>
    } finally {
      clearTimeout(timer)
    }
  }
}

// ── LiveSession ───────────────────────────────────────────────────────────────

interface LiveSessionConfig {
  sessionId: string
  sessionToken: string
  runId: string
  baseUrl: string
  batchSize: number
  batchIntervalMs: number
  timeoutMs: number
  /**
   * Default suite applied to record() events that don't carry their own
   * suiteName — keeps every test on a run tagged with the run-level suite
   * resolved from testlookup.suite / testlookup.launch.
   */
  suiteName?: string
}

/**
 * An active test execution session.
 *
 * Call `record()`, `log()`, and `metric()` as your tests run.
 * Call `close()` when done to flush remaining events.
 * Then call `reporter.closeSession(session.sessionId)` to finalise.
 */
export class LiveSession {
  readonly sessionId: string
  readonly runId: string

  private readonly sessionToken: string
  private readonly baseUrl: string
  private readonly batchSize: number
  private readonly timeoutMs: number
  private readonly defaultSuiteName?: string
  private readonly buffer: LiveEvent[] = []
  private flushTimer: ReturnType<typeof setInterval> | null = null
  private _stats = { sent: 0, failed: 0 }

  constructor(config: LiveSessionConfig) {
    this.sessionId    = config.sessionId
    this.sessionToken = config.sessionToken
    this.runId        = config.runId
    this.baseUrl      = config.baseUrl
    this.batchSize    = config.batchSize
    this.timeoutMs    = config.timeoutMs
    this.defaultSuiteName = config.suiteName

    // Background time-based flusher
    this.flushTimer = setInterval(() => {
      this._flushOnce().catch((e) =>
        console.debug('[TestLookup] flush error (non-fatal):', e),
      )
    }, config.batchIntervalMs)
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Record a single test result.
   *
   * @param testName   Full test name / identifier
   * @param status     PASSED | FAILED | SKIPPED | BROKEN
   * @param durationMs Execution time in milliseconds
   */
  async record(
    testName: string,
    status: TestStatus,
    durationMs = 0,
    opts: RecordOptions = {},
  ): Promise<void> {
    const event: LiveEvent = {
      event_type:  'test_result',
      test_name:   testName,
      status:      status.toUpperCase(),
      duration_ms: durationMs,
      timestamp_ms: Date.now(),
    }
    // Per-record suiteName wins; otherwise inherit the session-level default
    // resolved from testlookup.suite / testlookup.launch via ReporterConfig.
    const effectiveSuite = opts.suiteName ?? this.defaultSuiteName
    if (effectiveSuite)  event.suite_name    = effectiveSuite
    if (opts.className)  event.class_name    = opts.className
    if (opts.error)      event.error_message = opts.error
    if (opts.stackTrace) event.stack_trace   = opts.stackTrace
    if (opts.tags?.length) event.tags        = opts.tags
    if (opts.metadata)   event.metadata      = opts.metadata

    this.buffer.push(event)
    if (this.buffer.length >= this.batchSize) {
      await this._flushOnce()
    }
  }

  /** Record a log line (not a test result). */
  async log(
    message: string,
    level: LogLevel = 'INFO',
    metadata?: Record<string, unknown>,
  ): Promise<void> {
    this.buffer.push({
      event_type:   'log',
      test_name:    null,
      status:       null,
      timestamp_ms: Date.now(),
      metadata:     { level, message, ...(metadata ?? {}) },
    })
  }

  /** Record a numeric metric (e.g. memory usage, P99 response time). */
  async metric(
    name: string,
    value: number,
    unit = '',
    metadata?: Record<string, unknown>,
  ): Promise<void> {
    this.buffer.push({
      event_type:   'metric',
      test_name:    name,
      status:       null,
      duration_ms:  Math.round(value),
      timestamp_ms: Date.now(),
      metadata:     { value, unit, ...(metadata ?? {}) },
    })
  }

  /** Flush remaining events and stop the background timer. */
  async close(): Promise<void> {
    if (this.flushTimer != null) {
      clearInterval(this.flushTimer)
      this.flushTimer = null
    }
    await this._flushAll()
    console.debug(
      `[TestLookup] session ${this.sessionId.slice(0, 8)} closed: sent=${this._stats.sent} failed=${this._stats.failed}`,
    )
  }

  get stats(): SessionStats {
    return { ...this._stats }
  }

  // ── Internal ───────────────────────────────────────────────────────────────

  private async _flushOnce(): Promise<void> {
    if (this.buffer.length === 0) return
    const batch = this.buffer.splice(0, this.batchSize)
    await this._postBatch(batch)
  }

  private async _flushAll(): Promise<void> {
    while (this.buffer.length > 0) {
      const batch = this.buffer.splice(0, MAX_BATCH_SIZE)
      if (batch.length > 0) await this._postBatch(batch)
    }
  }

  private async _postBatch(events: LiveEvent[]): Promise<void> {
    const payload = {
      session_id: this.sessionId,
      run_id:     this.runId,
      events,
    }

    for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), this.timeoutMs)

      try {
        const resp = await fetch(`${this.baseUrl}/api/v1/stream/events/batch`, {
          method:  'POST',
          headers: {
            'Content-Type':   'application/json',
            'X-Session-Token': this.sessionToken,
          },
          body:   JSON.stringify(payload),
          signal: controller.signal,
        })
        clearTimeout(timer)

        if (resp.status === 401) {
          console.error('[TestLookup] Session token rejected — stopping flush')
          this._stats.failed += events.length
          return
        }

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

        const data = await resp.json() as { accepted?: number }
        this._stats.sent += data.accepted ?? events.length
        return
      } catch (err: unknown) {
        clearTimeout(timer)
        const isTransient =
          err instanceof TypeError ||
          (err as Error)?.name === 'AbortError'

        const delay = RETRY_BASE_DELAY_MS * Math.pow(2, attempt - 1)
        if (attempt === MAX_RETRIES || !isTransient) {
          console.error(
            `[TestLookup] Batch POST failed after ${attempt} attempt(s) (${events.length} events lost):`,
            err,
          )
          this._stats.failed += events.length
          return
        }
        console.warn(
          `[TestLookup] Batch POST attempt ${attempt} failed, retrying in ${delay}ms:`,
          (err as Error)?.message,
        )
        await new Promise((r) => setTimeout(r, delay))
      }
    }
  }
}
