/**
 * The Run page's **Timing** tab (EXEC-04, `/runs/:runId?tab=timing`): how long
 * the run took and where the time went, from `GET /api/v1/runs/{run_id}/timing`
 * (EXEC-03 contract §1).
 *
 *   Execution time   wall clock, test time (summed), parallelism, the setup /
 *                    teardown share, and where the figures come from;
 *   Phases           setup / test body / teardown over the run's tests;
 *   Timeline         the waterfall (`ExecutionTimeline`): one row per worker,
 *                    one bar per test, by status; a bar opens its test;
 *   Workers          one row per worker: tests, busy time, span;
 *   Suites           wall clock against summed time per suite;
 *   Slowest tests    the 20 slowest, with the baseline when the test has one;
 *   Duration regressions  EXEC-05's card, fed from the same answer.
 *
 * Absence is not health: `null` is "not reported", never "0 ms", and a block
 * the server could not measure shows its reason as plain text (an old JUnit
 * run: "report carried no timestamps"), never an empty chart frame. Names are
 * untrusted text from ingested files: React text children only.
 *
 * Mounted only while the tab is open, so the timing is asked for then.
 */
import { useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink, Timer } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import SidePanel from '@/components/ui/SidePanel'
import StatusBadge from '@/components/ui/StatusBadge'
import DurationRegressionsCard from '@/components/runs/DurationRegressionsCard'
import ExecutionTimeline from '@/components/charts/ExecutionTimeline'
import { buildExecutionTimelineModel } from '@/components/charts/executionTimeline.model'
import TestCaseBody from '@/pages/testCase/TestCaseBody'
import { useRunTiming } from '@/hooks/useExecutionMetrics'
import { executionMetricsService } from '@/services/executionMetricsService'
import type { DurationBaseline, RunTiming, RunTimingPhases, RunTimingTest } from '@/types/executionMetrics'
import { formatNumber, formatPercent } from '@/utils/formatters'
import {
  NOT_REPORTED,
  SUITE_SOURCE_LABEL,
  msText,
  ratioText,
  reasonSentence,
  runSourceText,
  shareText,
} from './runTimingText'

const SECTION_TITLE = 'm-0 text-sm font-semibold text-[var(--color-text)]'
const NOTE = 'text-xs text-[var(--color-text-muted)]'
const TH = 'px-2 py-1.5 text-left text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]'
const TD = 'px-2 py-1.5 text-[13px] text-[var(--color-text-secondary)] tabular-nums'

/** A block the server could not measure: its reason, as plain text. */
function Unmeasured({ block, reason, extra }: { block: string; reason: string | null | undefined; extra?: string }) {
  return (
    <p data-timing-unmeasured={block} className="mt-3 text-sm text-[var(--color-text-muted)]">
      {reasonSentence(reason)}
      {extra ? ` ${extra}` : ''}
    </p>
  )
}

function Kpi({ id, label, value, hint }: { id: string; label: string; value: string; hint?: string }) {
  const missing = value === NOT_REPORTED
  return (
    <div data-timing-kpi={id} data-measured={missing ? 'false' : 'true'} className="rounded-lg bg-[var(--color-bg-secondary)]/80 p-3">
      <p className="m-0 text-xs text-[var(--color-text-muted)]">{label}</p>
      <p
        data-timing-kpi-value=""
        className={
          missing
            ? 'm-0 mt-1 text-sm italic text-[var(--color-text-muted)]'
            : 'm-0 mt-1 text-xl font-bold tabular-nums text-[var(--color-text)]'
        }
      >
        {value}
      </p>
      {hint && <p className="m-0 mt-0.5 text-[11px] text-[var(--color-text-faint)]">{hint}</p>}
    </div>
  )
}

function Headline({ timing }: { timing: RunTiming }) {
  const { run, phases } = timing
  const phaseShare =
    phases.measured && (phases.setup_share != null || phases.teardown_share != null)
      ? `${shareText(phases.setup_share)} / ${shareText(phases.teardown_share)}`
      : NOT_REPORTED
  return (
    <section data-primary="" aria-label="Execution time" className="card">
      <h2 className={`${SECTION_TITLE} flex items-center gap-2`}>
        <Timer className="h-4 w-4 text-[var(--color-text-muted)]" aria-hidden="true" />
        Execution time
        {/* EXEC-06: every test of the run with its timing, through the authed client (never a bare href). */}
        <button
          type="button"
          className="btn-secondary ml-auto text-xs"
          data-timing-export=""
          onClick={() => void executionMetricsService.downloadRunTimingCsv(timing.run_id)}
        >
          Export CSV
        </button>
      </h2>
      <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi id="wall" label="Wall clock" value={msText(run.wall_clock_ms)} hint="How long the run took on the clock" />
        <Kpi id="summed" label="Test time (summed)" value={msText(run.summed_ms)} hint="Every test's duration, added up" />
        <Kpi id="parallelism" label="Parallelism" value={ratioText(run.parallelism)} hint="Summed time ÷ wall clock" />
        <Kpi id="phases" label="Setup / teardown share" value={phaseShare} hint="Of setup + test + teardown" />
      </div>
      <p data-timing-source={run.timing_source ?? 'none'} className={`${NOTE} mt-3`}>
        {runSourceText(run.timing_source)}
      </p>
      <p data-timing-counts="" className={NOTE}>
        {formatNumber(run.test_count)} {run.test_count === 1 ? 'test' : 'tests'} · {formatNumber(run.timed_count)} with start and
        finish times · {formatNumber(run.duration_count)} with a duration
      </p>
    </section>
  )
}

const PHASES: { key: 'setup_ms' | 'call_ms' | 'teardown_ms'; label: string; color: string }[] = [
  { key: 'setup_ms', label: 'Setup', color: 'var(--chart-series-2)' },
  { key: 'call_ms', label: 'Test', color: 'var(--chart-series-1)' },
  { key: 'teardown_ms', label: 'Teardown', color: 'var(--chart-series-3)' },
]

function Phases({ phases, testCount }: { phases: RunTimingPhases; testCount: number }) {
  const total = PHASES.reduce((sum, phase) => sum + (phases[phase.key] ?? 0), 0)
  return (
    <section aria-label="Setup, test and teardown" className="card">
      <h2 className={SECTION_TITLE}>Setup, test and teardown</h2>
      {!phases.measured ? (
        <Unmeasured block="phases" reason={phases.reason} />
      ) : (
        <div className="mt-3 space-y-2">
          {total > 0 && (
            <div data-timing-phase-bar="" className="flex h-3 w-full overflow-hidden rounded bg-[var(--color-bg-secondary)]">
              {PHASES.map((phase) => {
                const value = phases[phase.key]
                if (value == null || value <= 0) return null
                return (
                  <span
                    key={phase.key}
                    data-timing-phase={phase.key}
                    title={`${phase.label}: ${msText(value)}`}
                    style={{ width: `${(value / total) * 100}%`, background: phase.color }}
                  />
                )
              })}
            </div>
          )}
          <ul className="m-0 flex list-none flex-wrap gap-x-5 gap-y-1 p-0 text-[13px] text-[var(--color-text-secondary)]">
            {PHASES.map((phase) => (
              <li key={phase.key} data-timing-phase-total={phase.key} className="flex items-center gap-1.5">
                <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: phase.color }} />
                {phase.label}: <span className="tabular-nums text-[var(--color-text)]">{msText(phases[phase.key])}</span>
              </li>
            ))}
          </ul>
          <p className={NOTE}>
            {formatNumber(phases.tests_with_phases)} of {formatNumber(testCount)} tests reported a setup / test / teardown split.
          </p>
        </div>
      )}
    </section>
  )
}

function TimelineSection({
  timing,
  onSelect,
  selectedTestId,
}: {
  timing: RunTiming
  onSelect: (testId: string) => void
  selectedTestId: string | null
}) {
  const model = useMemo(() => buildExecutionTimelineModel(timing.timeline), [timing.timeline])
  const durationsOnly = timing.slowest.measured && timing.slowest.items.length > 0
  return (
    <section aria-label="Timeline" className="card">
      <h2 className={SECTION_TITLE}>Timeline</h2>
      {model ? (
        <div className="mt-3">
          <ExecutionTimeline model={model} onSelect={onSelect} selectedTestId={selectedTestId} />
        </div>
      ) : (
        <Unmeasured
          block="timeline"
          reason={timing.timeline.measured ? 'no test in this run carried readable start and finish times' : timing.timeline.reason}
          extra={durationsOnly ? 'This report does not record when each test started, so only durations are shown below.' : undefined}
        />
      )}
    </section>
  )
}

function Table({ head, children, label }: { head: ReactNode; children: ReactNode; label: string }) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table aria-label={label} className="w-full border-collapse">
        <thead className="border-b border-[var(--color-border)]">{head}</thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

function Workers({ timing }: { timing: RunTiming }) {
  const { workers } = timing
  return (
    <section aria-label="Worker lanes" className="card">
      <h2 className={SECTION_TITLE}>Workers</h2>
      {!workers.measured ? (
        <Unmeasured block="workers" reason={workers.reason} />
      ) : (
        <>
          <Table
            label="Workers"
            head={
              <tr>
                <th className={TH}>Worker</th>
                <th className={TH}>Tests</th>
                <th className={TH}>Busy</th>
                <th className={TH}>Span</th>
                <th className={TH}>Busy share</th>
              </tr>
            }
          >
            {workers.lanes.map((lane, index) => (
              <tr key={`${lane.worker_id ?? 'none'}:${index}`} data-timing-worker={lane.worker_id ?? ''} className="border-b border-[var(--color-border)]/50">
                <td className={TD}>{lane.worker_id ?? 'No worker reported'}</td>
                <td className={TD}>{formatNumber(lane.test_count)}</td>
                <td className={TD}>{msText(lane.busy_ms)}</td>
                <td className={TD}>{msText(lane.span_ms)}</td>
                <td className={TD}>
                  {lane.busy_ms != null && lane.span_ms != null && lane.span_ms > 0
                    ? formatPercent(lane.busy_ms / lane.span_ms, { from: 'ratio' })
                    : NOT_REPORTED}
                </td>
              </tr>
            ))}
          </Table>
          {workers.truncated && (
            <p className={`${NOTE} mt-2`}>
              Showing {formatNumber(workers.lanes.length)} of {formatNumber(workers.total)} workers (most tests first).
            </p>
          )}
        </>
      )}
    </section>
  )
}

function Suites({ timing }: { timing: RunTiming }) {
  const { suites } = timing
  return (
    <section aria-label="Suites" className="card">
      <h2 className={SECTION_TITLE}>Suites</h2>
      {!suites.measured ? (
        <Unmeasured block="suites" reason={suites.reason} />
      ) : (
        <>
          <Table
            label="Suites"
            head={
              <tr>
                <th className={TH}>Suite</th>
                <th className={TH}>Wall clock</th>
                <th className={TH}>Summed</th>
                <th className={TH}>Parallelism</th>
                <th className={TH}>Tests</th>
                <th className={TH}>Failed</th>
                <th className={TH}>Source</th>
              </tr>
            }
          >
            {suites.items.map((suite) => (
              <tr key={suite.suite_key} data-timing-suite={suite.suite_key} className="border-b border-[var(--color-border)]/50">
                <td className={`${TD} max-w-[18rem] truncate text-[var(--color-text)]`} title={suite.suite_name}>
                  {suite.suite_name}
                </td>
                <td className={TD}>{msText(suite.wall_ms)}</td>
                <td className={TD}>{msText(suite.summed_ms)}</td>
                <td className={TD}>{ratioText(suite.parallelism)}</td>
                <td className={TD}>
                  {formatNumber(suite.test_count)}
                  {suite.timed_count !== suite.test_count && (
                    <span className="text-[var(--color-text-muted)]"> ({formatNumber(suite.timed_count)} timed)</span>
                  )}
                </td>
                <td className={TD}>{formatNumber(suite.failed + suite.broken)}</td>
                <td className={TD}>
                  <span data-timing-suite-source={suite.source} className="rounded border border-[var(--color-border-light)] px-1.5 py-0.5 text-[11px]">
                    {SUITE_SOURCE_LABEL[suite.source] ?? suite.source}
                  </span>
                </td>
              </tr>
            ))}
          </Table>
          {suites.truncated && (
            <p className={`${NOTE} mt-2`}>
              Showing {formatNumber(suites.items.length)} of {formatNumber(suites.total)} suites.
            </p>
          )}
        </>
      )}
    </section>
  )
}

/**
 * The baseline of a slow test, in words: "today's mean 900ms over 14 runs". It is the test's CURRENT baseline, which
 * on an older run already holds that run and the ones after it; the regressions card below judged the run against
 * the baseline before it, so the two numbers differ and each says which one it is.
 */
function baselineText(baseline: DurationBaseline): string {
  return `today's mean ${msText(baseline.mean_ms)} over ${formatNumber(baseline.sample_count)} runs`
}

/** The duration as a bar against the slowest, with a tick where the baseline mean sits. */
function DurationBar({ test, max }: { test: RunTimingTest; max: number }) {
  if (test.duration_ms == null || max <= 0) return null
  const width = Math.min(100, (test.duration_ms / max) * 100)
  const mean = test.baseline?.mean_ms
  return (
    <span className="relative inline-block h-1.5 w-24 rounded bg-[var(--color-bg-secondary)] align-middle" aria-hidden="true">
      <span className="absolute inset-y-0 left-0 rounded" style={{ width: `${width}%`, background: 'var(--chart-series-1)' }} />
      {mean != null && mean >= 0 && (
        <span
          data-timing-baseline-tick=""
          className="absolute -top-0.5 h-2.5 w-0.5 bg-[var(--color-text)]"
          style={{ left: `${Math.min(100, (mean / max) * 100)}%` }}
        />
      )}
    </span>
  )
}

function Slowest({ timing, onSelect }: { timing: RunTiming; onSelect: (testId: string) => void }) {
  const { slowest } = timing
  const max = Math.max(0, ...slowest.items.map((t) => t.duration_ms ?? 0), ...slowest.items.map((t) => t.baseline?.mean_ms ?? 0))
  return (
    <section aria-label="Slowest tests" className="card">
      {/* "20 slowest tests" only when the limit cut the list: a 6-test run read "20 slowest tests" over six rows. */}
      <h2 className={SECTION_TITLE}>
        {slowest.total > slowest.limit ? `${formatNumber(slowest.limit)} slowest tests` : 'Slowest tests'}
      </h2>
      {!slowest.measured ? (
        <Unmeasured block="slowest" reason={slowest.reason} />
      ) : (
        <>
          <Table
            label="Slowest tests"
            head={
              <tr>
                <th className={TH}>#</th>
                <th className={TH}>Test</th>
                <th className={TH}>Status</th>
                <th className={TH}>Duration</th>
                <th className={TH}>Against baseline</th>
                <th className={TH}>Setup · test · teardown</th>
                <th className={TH}>Worker</th>
              </tr>
            }
          >
            {slowest.items.map((test, index) => (
              <tr key={test.test_id} data-timing-slow-test={test.test_id} className="border-b border-[var(--color-border)]/50">
                <td className={TD}>{index + 1}</td>
                <td className={`${TD} max-w-[22rem]`}>
                  <button
                    type="button"
                    onClick={() => onSelect(test.test_id)}
                    className="block max-w-full truncate text-left text-[var(--color-text)] hover:text-[var(--color-accent)] hover:underline"
                    title={test.test_name}
                  >
                    {test.test_name}
                  </button>
                  {test.suite_name && <span className="block truncate text-[11px] text-[var(--color-text-muted)]">{test.suite_name}</span>}
                </td>
                <td className={TD}>
                  <StatusBadge status={test.status} />
                </td>
                <td className={TD}>
                  <span className="mr-2 text-[var(--color-text)]">{msText(test.duration_ms)}</span>
                  <DurationBar test={test} max={max} />
                </td>
                <td className={TD} data-timing-baseline={test.baseline ? 'yes' : 'no'}>
                  {test.baseline ? (
                    <>
                      <span className={test.baseline_ratio != null && test.baseline_ratio >= 1.5 ? 'font-medium text-[var(--status-failed)]' : undefined}>
                        {test.baseline_ratio != null ? `${ratioText(test.baseline_ratio)} baseline` : NOT_REPORTED}
                      </span>
                      <span className="block text-[11px] text-[var(--color-text-muted)]">{baselineText(test.baseline)}</span>
                    </>
                  ) : (
                    <span className="text-[var(--color-text-muted)]">no baseline yet</span>
                  )}
                </td>
                <td className={TD}>
                  {test.setup_ms == null && test.call_ms == null && test.teardown_ms == null
                    ? NOT_REPORTED
                    : `${msText(test.setup_ms)} · ${msText(test.call_ms)} · ${msText(test.teardown_ms)}`}
                </td>
                <td className={TD}>{test.worker_id ?? NOT_REPORTED}</td>
              </tr>
            ))}
          </Table>
          <p className={`${NOTE} mt-2`}>
            The {formatNumber(slowest.items.length)} longest of {formatNumber(slowest.total)}{' '}
            {slowest.total === 1 ? 'test' : 'tests'} with a duration. The tick on each bar is the test's baseline mean
            as it stands today; &ldquo;Did anything get slower?&rdquo; judged this run against the baseline before it.
          </p>
        </>
      )}
    </section>
  )
}

/** The name of a test the timing answer mentions (the side panel's title). */
function testNameOf(timing: RunTiming | undefined, testId: string | null): string {
  if (!timing || !testId) return 'Test'
  return (
    timing.slowest.items.find((t) => t.test_id === testId)?.test_name ??
    timing.timeline.items.find((t) => t.test_id === testId)?.test_name ??
    'Test'
  )
}

export default function RunTimingTab({ runId }: { runId: string }) {
  const { data, error, isLoading } = useRunTiming(runId)
  const [openTestId, setOpenTestId] = useState<string | null>(null)

  if (!data && (isLoading || !error)) {
    return (
      <section data-primary="" aria-label="Execution time" data-timing-loading="" className="card flex justify-center py-10">
        <LoadingSpinner />
      </section>
    )
  }
  // A failed read with nothing cached, or an answer without its run block (not this contract).
  if (!data || !data.run || !data.phases || !data.suites || !data.slowest || !data.workers || !data.timeline) {
    return (
      <section data-primary="" aria-label="Execution time" className="card">
        <h2 className={SECTION_TITLE}>Execution time</h2>
        <p data-timing-error="" className="mt-3 text-sm text-[var(--status-failed)]">
          Could not load the timing of this run.
        </p>
      </section>
    )
  }

  return (
    <div className="space-y-4" data-run-timing-tab="">
      <Headline timing={data} />
      <TimelineSection timing={data} onSelect={setOpenTestId} selectedTestId={openTestId} />
      <Slowest timing={data} onSelect={setOpenTestId} />
      <div className="grid gap-4 xl:grid-cols-2">
        <Suites timing={data} />
        <Workers timing={data} />
      </div>
      <Phases phases={data.phases} testCount={data.run.test_count} />
      <DurationRegressionsCard runId={runId} findings={data.duration_findings} />

      <SidePanel
        open={openTestId !== null}
        onClose={() => setOpenTestId(null)}
        title={testNameOf(data, openTestId)}
        width={640}
        footer={
          openTestId && (
            <Link
              to={`/runs/${runId}/tests/${openTestId}`}
              className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] hover:underline"
            >
              Open full page <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </Link>
          )
        }
      >
        {openTestId && <TestCaseBody key={openTestId} runId={runId} testId={openTestId} compact />}
      </SidePanel>
    </div>
  )
}
