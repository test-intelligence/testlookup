/**
 * TestLookup live formatter for Cucumber JS (W18 DEV-03).
 *
 *   npx cucumber-js --format testlookup-reporter/cucumber-formatter \
 *     --format-options '{"testlookupProject":"<id>"}'
 *
 * Reads Cucumber's message stream (the ``envelope`` events): each scenario is one
 * result when it finishes (``testCaseFinished``), named after the scenario, its
 * suite the feature. The scenario's status is its worst step (failed > ambiguous >
 * undefined > pending > skipped > passed); a retried scenario reports each attempt
 * (``metadata.attempt``). Cucumber awaits ``finished()`` before exiting, so the
 * session closes first. Network errors are logged, never thrown.
 *
 * Supported: @cucumber/cucumber >= 8 (messages-based formatters).
 */
import { epochMs, LiveAdapter, type LiveAdapterOptions } from './live-adapter'
import type { TestStatus } from './testlookup-reporter'

interface Broadcaster {
  on: (event: 'envelope', handler: (envelope: Record<string, any>) => void) => void
}
interface FormatterOptions {
  eventBroadcaster: Broadcaster
  parsedArgvOptions?: LiveAdapterOptions & Record<string, unknown>
  log?: (text: string) => void
}

const RANK = ['PASSED', 'UNKNOWN', 'SKIPPED', 'PENDING', 'UNDEFINED', 'AMBIGUOUS', 'FAILED']
const STATUS: Record<string, TestStatus> = {
  PASSED: 'PASSED', SKIPPED: 'SKIPPED', PENDING: 'SKIPPED', UNKNOWN: 'SKIPPED',
  UNDEFINED: 'BROKEN', AMBIGUOUS: 'BROKEN', FAILED: 'FAILED',
}

interface Running {
  testCaseId: string
  attempt: number
  startedMs?: number
  worst: string
  durationMs: number
  error?: string
}

class TestLookupCucumberFormatter {
  private readonly adapter: LiveAdapter
  private readonly pickles = new Map<string, { name: string; uri: string; tags: string[] }>()
  private readonly features = new Map<string, string>()
  private readonly testCases = new Map<string, string>()
  private readonly running = new Map<string, Running>()

  constructor(options: FormatterOptions) {
    this.adapter = new LiveAdapter('cucumber', options.parsedArgvOptions ?? {})
    options.eventBroadcaster.on('envelope', (e) => this.onEnvelope(e))
  }

  onEnvelope(e: Record<string, any>): void {
    try {
      if (e.testRunStarted) this.adapter.start()
      else if (e.gherkinDocument?.feature) this.features.set(e.gherkinDocument.uri, e.gherkinDocument.feature.name)
      else if (e.pickle) this.pickles.set(e.pickle.id, { name: e.pickle.name, uri: e.pickle.uri, tags: (e.pickle.tags ?? []).map((t: { name: string }) => t.name) })
      else if (e.testCase) this.testCases.set(e.testCase.id, e.testCase.pickleId)
      else if (e.testCaseStarted) {
        const s = e.testCaseStarted
        this.running.set(s.id, { testCaseId: s.testCaseId, attempt: (s.attempt ?? 0) + 1, startedMs: epochMs(s.timestamp), worst: 'PASSED', durationMs: 0 })
      } else if (e.testStepFinished) {
        const f = e.testStepFinished
        const r = this.running.get(f.testCaseStartedId)
        const res = f.testStepResult ?? {}
        if (r) {
          r.durationMs += res.duration ? Number(res.duration.seconds ?? 0) * 1000 + Math.round((res.duration.nanos ?? 0) / 1e6) : 0
          if (RANK.indexOf(res.status) > RANK.indexOf(r.worst)) r.worst = res.status
          if (res.message && !r.error) r.error = res.message
        }
      } else if (e.testCaseFinished) {
        this.finishCase(e.testCaseFinished.testCaseStartedId, e.testCaseFinished.timestamp)
      }
    } catch (err) {
      console.debug('[TestLookup] cucumber message error:', err)
    }
  }

  private finishCase(startedId: string, timestamp: unknown): void {
    const r = this.running.get(startedId)
    if (!r) return
    this.running.delete(startedId)
    const pickle = this.pickles.get(this.testCases.get(r.testCaseId) ?? '')
    const end = epochMs(timestamp)
    const duration = r.durationMs || (end !== undefined && r.startedMs !== undefined ? end - r.startedMs : 0)
    this.adapter.record(pickle?.name ?? r.testCaseId, STATUS[r.worst] ?? 'BROKEN', duration, {
      suiteName: pickle ? this.features.get(pickle.uri) ?? pickle.uri : undefined,
      className: pickle?.uri,
      error: r.error?.split('\n')[0],
      stackTrace: r.error,
      tags: pickle?.tags,
      startedAtMs: r.startedMs,
      metadata: { attempt: r.attempt, step_status: r.worst },
    })
  }

  /** Cucumber awaits this before the process exits. */
  async finished(): Promise<void> {
    await this.adapter.finish()
  }
}

// CommonJS ``module.exports`` IS the class (``export =``), with ``.default`` pointing at itself:
// require() and ESM import() (Cucumber, Vitest, an ESM config) both get it, whichever they read.
// ``export default`` compiled to ``exports.default`` only, and ``import()`` then handed back
// ``{ default: … }`` — "not a constructor" (found driving the adapters against a real server).
Object.assign(TestLookupCucumberFormatter, { default: TestLookupCucumberFormatter })
export = TestLookupCucumberFormatter
