/**
 * TestLookup live reporter for Vitest (W18 DEV-03).
 *
 *   // vitest.config.ts
 *   test: { reporters: ['default', ['testlookup-reporter/vitest-reporter', { testlookupProject: '<id>' }]] }
 *
 * Vitest's reporter API moved across majors, so both shapes are handled:
 *   - Vitest >= 3: ``onTestCaseResult(testCase)`` streams each test as it finishes;
 *   - Vitest 1 / 2: ``onTaskUpdate(packs)`` streams finished tests by task id;
 *   - every major: ``onFinished(files)`` records whatever was not streamed yet
 *     (skipped tests, or an older Vitest that streams nothing), then closes.
 * A test is recorded once whichever path saw it first. Network errors are logged,
 * never thrown.
 *
 * Supported: vitest >= 1.0 (tested shapes: 1.x/2.x task packs, 3.x TestCase).
 */
import { LiveAdapter, type LiveAdapterOptions } from './live-adapter'
import type { TestStatus } from './testlookup-reporter'

interface VTaskResult {
  state?: 'pass' | 'fail' | 'skip' | 'todo' | 'run' | 'queued' | 'only'
  duration?: number
  startTime?: number
  retryCount?: number
  errors?: Array<{ message?: string; stack?: string }>
}
interface VTask {
  id: string
  name: string
  type: 'test' | 'suite' | 'file' | 'custom'
  mode?: 'run' | 'skip' | 'todo' | 'only'
  suite?: VTask
  file?: VTask
  filepath?: string
  tasks?: VTask[]
  result?: VTaskResult
}
interface VContext {
  state?: { idMap?: Map<string, VTask> }
}
/** Vitest 3's reporter TestCase (the parts used). */
interface VTestCase {
  id?: string
  name: string
  fullName?: string
  module?: { moduleId?: string }
  result: () => { state: 'passed' | 'failed' | 'skipped' | 'pending'; errors?: Array<{ message?: string; stack?: string }> }
  diagnostic?: () => { duration?: number; startTime?: number; retryCount?: number } | undefined
}

function statusOf(state?: string, mode?: string): TestStatus | null {
  if (state === 'pass' || state === 'passed') return 'PASSED'
  if (state === 'fail' || state === 'failed') return 'FAILED'
  if (state === 'skip' || state === 'skipped' || state === 'todo' || mode === 'skip' || mode === 'todo') return 'SKIPPED'
  return null
}

/** ``describe > test`` from a task's suite chain (the file is the suite name, not part of the title). */
export function taskName(task: VTask): string {
  const parts: string[] = [task.name]
  let s = task.suite
  while (s && s.type === 'suite' && s.name) {
    parts.unshift(s.name)
    s = s.suite
  }
  return parts.join(' > ')
}

function fileOf(task: VTask): string | undefined {
  const f = task.file ?? (task.type === 'file' ? task : undefined)
  const path = f?.filepath ?? f?.name
  return path ? path.replace(/\\/g, '/').split('/').pop() : undefined
}

class TestLookupVitestReporter {
  private readonly adapter: LiveAdapter
  private ctx: VContext | undefined
  private readonly recorded = new Set<string>()

  constructor(options: LiveAdapterOptions = {}) {
    this.adapter = new LiveAdapter('vitest', options)
  }

  onInit(ctx: VContext): void {
    this.ctx = ctx
    this.adapter.start()
  }

  /** Vitest >= 3. */
  onTestCaseResult(testCase: VTestCase): void {
    const key = testCase.id ?? testCase.fullName ?? testCase.name
    if (this.recorded.has(key)) return
    const r = testCase.result()
    const status = statusOf(r.state)
    if (!status) return
    this.recorded.add(key)
    const d = testCase.diagnostic?.()
    const file = testCase.module?.moduleId?.replace(/\\/g, '/').split('/').pop()
    this.adapter.record(testCase.fullName ?? testCase.name, status, d?.duration ?? 0, {
      suiteName: file, className: testCase.module?.moduleId, error: r.errors?.[0]?.message,
      stackTrace: r.errors?.[0]?.stack, startedAtMs: d?.startTime,
      metadata: { attempt: (d?.retryCount ?? 0) + 1 },
    })
  }

  /** Vitest 1 / 2: ``[id, result, meta][]``. */
  onTaskUpdate(packs: Array<[string, VTaskResult | undefined, unknown?]>): void {
    for (const [id, result] of packs ?? []) {
      const task = this.ctx?.state?.idMap?.get(id)
      if (!task || task.type !== 'test') continue
      this._recordTask({ ...task, result: result ?? task.result })
    }
  }

  async onFinished(files: VTask[] = []): Promise<void> {
    const walk = (t: VTask) => {
      if (t.type === 'test') this._recordTask(t)
      for (const c of t.tasks ?? []) walk(c)
    }
    for (const f of files) walk(f)
    await this.adapter.finish()
  }

  private _recordTask(task: VTask): void {
    if (this.recorded.has(task.id)) return
    const status = statusOf(task.result?.state, task.mode)
    if (!status) return
    this.recorded.add(task.id)
    const r = task.result
    this.adapter.record(taskName(task), status, r?.duration ?? 0, {
      suiteName: fileOf(task), className: task.file?.filepath, error: r?.errors?.[0]?.message,
      stackTrace: r?.errors?.[0]?.stack, startedAtMs: r?.startTime,
      metadata: { attempt: (r?.retryCount ?? 0) + 1 },
    })
  }
}

export default TestLookupVitestReporter
