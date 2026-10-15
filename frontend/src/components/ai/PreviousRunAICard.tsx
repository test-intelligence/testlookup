/**
 * W03 AIH-03/04: "vs previous run" on the Analysis tab (contract §9.6). Compares this run's AI analysis with the
 * previous run's: the server picks the baseline (D42: same project, suite and branch, the newest run that finished
 * before this one), the reader can pick another (`?ai_baseline=`). It shows the verdict change, the suggested root
 * causes that are new, resolved or persisting, the tests whose AI conclusion changed (each opens the test), and the
 * narrative, labelled "AI narrative" or "deterministic summary" (D43: deterministic when no LLM wrote it).
 *
 * Nothing changed: one collapsed row. No baseline / nothing analysed: a sentence saying why. A failed read: an
 * "unavailable" note with Retry; it never blocks the rest of the tab and never toasts.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { GitCompare, RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import { useAICompare, usePreviousRun } from '@/hooks/useAIHistory'
import { useRuns } from '@/hooks/useRuns'
import type { AIPreviousRun } from '@/types/aiHistory'
import type { TestRun } from '@/types/runs'
import { buildCompareWithPreviousHref } from '@/utils/runComparisons'
import { categoryColor, formatRunWhen } from '@/utils/formatters'
import { baselineChipText, buildDiffModel, previousRunEmptyText, runLabel, type DiffTestRow } from './aiDigestDiff.model'
import { runTestHref } from './aiHistoryUrl'

export type PreviousRunCardState = 'loading' | 'collapsed' | 'expanded' | 'empty' | 'unavailable'

const MAX_TEST_ROWS = 10

function BaselinePicker({ runId, suiteName, baselineRunId, onChange }: {
  runId: string
  suiteName: string | null
  baselineRunId: string | null
  onChange: (id: string | null) => void
}) {
  const { data, isLoading } = useRuns(
    suiteName ? { page: 1, size: 50, days: 0, suite_name: suiteName } : { page: 1, size: 50, days: 0 },
    { ignoreGlobalRelease: true, quiet: true },
  )
  const runs = ((data?.items ?? []) as TestRun[]).filter((r) => r.id !== runId)
  return (
    <select
      data-previous-baseline-select=""
      aria-label="Baseline run"
      disabled={isLoading}
      value={baselineRunId ?? ''}
      onChange={(event) => onChange(event.target.value || null)}
      className="max-w-[340px] rounded border border-[var(--color-border)] bg-[var(--color-bg-card)] px-2 py-1 text-[12px] text-[var(--color-text)]"
    >
      <option value="">Default (previous on this branch)</option>
      {runs.map((r) => (
        <option key={r.id} value={r.id}>
          {`#${r.run_seq ?? r.build_number}`}{r.branch ? ` · ${r.branch}` : ''}{r.created_at ? ` · ${formatRunWhen(r.created_at)}` : ''}
        </option>
      ))}
    </select>
  )
}

function TestChangeRow({ row, runId }: { row: DiffTestRow; runId: string }) {
  const body = (
    <>
      <span className="font-medium text-[var(--color-text)] break-all">{row.name}</span>
      <span className={clsx('ml-2', row.category ? categoryColor(row.category) : 'text-[var(--color-text-muted)]')}>{row.detail}</span>
    </>
  )
  return row.testId ? (
    <Link
      data-ai-diff-row="test"
      data-test-fingerprint={row.fingerprint}
      to={runTestHref(runId, row.testId)}
      className="block rounded px-1 py-0.5 text-[12px] hover:bg-[var(--color-bg-hover)]"
    >
      {body}
    </Link>
  ) : (
    <span data-ai-diff-row="test" data-test-fingerprint={row.fingerprint} className="block px-1 py-0.5 text-[12px]">{body}</span>
  )
}

function ListBlock({ attr, title, items }: { attr: string; title: string; items: { key: string; label: string; detail: string }[] }) {
  if (items.length === 0) return null
  return (
    <div {...{ [attr]: '' }}>
      <p className="m-0 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">{title} ({items.length})</p>
      <ul className="m-0 list-none p-0">
        {items.slice(0, 5).map((item) => (
          <li key={item.key} className="py-0.5 text-[12px]">
            <span className="text-[var(--color-text)] break-words">{item.label}</span>
            <span className="ml-1 text-[var(--color-text-muted)]">({item.detail})</span>
          </li>
        ))}
        {items.length > 5 && <li className="text-[11px] text-[var(--color-text-faint)]">+{items.length - 5} more</li>}
      </ul>
    </div>
  )
}

export default function PreviousRunAICard({
  runId,
  baselineRunId = null,
  onChangeBaseline,
}: {
  runId: string
  /** An explicit baseline (`?ai_baseline=`); null = the server's previous run. */
  baselineRunId?: string | null
  onChangeBaseline: (id: string | null) => void
}) {
  const previous = usePreviousRun(runId)
  const compare = useAICompare(runId, { baselineRunId })
  const [picking, setPicking] = useState(false)

  const prev: AIPreviousRun | undefined = previous.data
  const data = compare.data
  const current = prev?.current ?? null
  const baseline = data?.baseline ?? null
  const suiteName = prev?.rule?.suite_name ?? current?.primary_suite_name ?? baseline?.primary_suite_name ?? null
  const diff = data?.diff ?? null
  const model = diff ? buildDiffModel(diff, { mode: 'cross_run' }) : null

  let state: PreviousRunCardState
  if (compare.error) state = 'unavailable'
  else if (!data) state = 'loading'
  else if (!baseline || data.reason || !diff) state = 'empty'
  else if (!diff.has_changes) state = 'collapsed'
  else state = 'expanded'

  const canPickBaseline = state !== 'loading' && state !== 'unavailable' && data?.reason !== 'no_primary_suite' && data?.reason !== 'run_lacks_suite'
  const changeBaseline = canPickBaseline && (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        data-previous-change-baseline=""
        onClick={() => setPicking((v) => !v)}
        className="text-[12px] text-[var(--color-accent)] underline underline-offset-2"
      >
        {picking ? 'Done' : 'Change baseline'}
      </button>
      {picking && (
        <BaselinePicker
          runId={runId}
          suiteName={suiteName}
          baselineRunId={baselineRunId}
          onChange={(id) => {
            setPicking(false)
            onChangeBaseline(id)
          }}
        />
      )}
    </span>
  )

  const chip = baseline && (
    <span data-previous-baseline="" title={baseline.selection_reason || undefined} className="text-[12px] text-[var(--color-text-secondary)]">
      {baselineChipText(baseline, current)}
    </span>
  )

  const compareHref = baseline
    ? buildCompareWithPreviousHref(
        { id: runId, primary_suite_name: current?.primary_suite_name ?? suiteName },
        { id: baseline.run_id, primary_suite_name: baseline.primary_suite_name },
      )
    : null

  const testRows: (DiffTestRow & { tag: string })[] = model
    ? [
        ...(model.tests.groups.find((g) => g.id === 'changed')?.rows ?? []).map((r) => ({ ...r, tag: 'changed' })),
        ...(model.tests.groups.find((g) => g.id === 'added')?.rows ?? []).map((r) => ({ ...r, tag: 'new failing' })),
      ]
    : []
  const clusterItems = (id: 'added' | 'removed' | 'matched') =>
    (model?.clusters.find((g) => g.id === id)?.rows ?? []).map((r) => ({ key: r.key, label: r.label, detail: r.detail }))

  return (
    <section
      data-previous-run-card=""
      data-previous-run-state={state}
      aria-label="Compared with the previous run"
      className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] px-4 py-3"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          <GitCompare aria-hidden className="h-3.5 w-3.5" /> vs previous run
        </span>
        {chip}
        {state === 'collapsed' && (
          <span data-previous-no-changes="" className="text-[12px] text-[var(--color-text-muted)]">
            No changes vs {runLabel(baseline)}
          </span>
        )}
        {baseline?.branch_mismatch && (
          <span data-previous-branch-mismatch="" className="text-[11px] text-[var(--status-broken)]">
            different branch ({baseline.branch ?? 'no branch'} vs {current?.branch ?? 'no branch'})
          </span>
        )}
        <span className="ml-auto inline-flex items-center gap-3">
          {changeBaseline}
          {compareHref && state !== 'empty' && (
            <Link to={compareHref} className="text-[12px] text-[var(--color-accent)] underline underline-offset-2">
              Open Run Compare
            </Link>
          )}
        </span>
      </div>

      {state === 'loading' && (
        <p className="m-0 mt-1 text-[12px] text-[var(--color-text-muted)]">Comparing with the previous run…</p>
      )}

      {state === 'unavailable' && (
        <p className="m-0 mt-1 flex items-center gap-2 text-[12px] text-[var(--color-text-muted)]">
          The comparison with the previous run is unavailable right now.
          <button
            type="button"
            data-previous-retry=""
            onClick={() => void compare.mutate()}
            className="inline-flex items-center gap-1 text-[var(--color-accent)] underline underline-offset-2"
          >
            <RefreshCw aria-hidden className="h-3 w-3" /> Retry
          </button>
        </p>
      )}

      {state === 'empty' && (
        <p data-previous-empty={data?.reason ?? ''} className="m-0 mt-1 text-[12px] text-[var(--color-text-muted)]">
          {previousRunEmptyText(data?.reason ?? null, {
            branch: current?.branch ?? prev?.rule?.branch ?? null,
            baseline,
            message: data?.message ?? null,
          })}
        </p>
      )}

      {state === 'expanded' && model && data && (
        <div className="mt-2 space-y-2.5">
          {model.provenance && (
            <p data-previous-provenance="" role="note" className="m-0 rounded-md bg-[var(--color-bg-secondary)] px-2 py-1 text-[12px] text-[var(--color-text)]">
              {model.provenance.note}
            </p>
          )}
          {diff?.verdict?.changed && (
            <p
              data-previous-verdict=""
              className={clsx(
                'm-0 text-[13px] font-semibold',
                diff.verdict.direction === 'worse' ? 'text-[var(--status-failed)]' : diff.verdict.direction === 'better' ? 'text-[var(--status-passed)]' : 'text-[var(--color-text)]',
              )}
            >
              {model.verdict?.text}
              {model.verdict?.risk && <span className="ml-2 text-[12px] font-normal text-[var(--color-text-muted)]">{model.verdict.risk}</span>}
            </p>
          )}
          <div className="grid gap-3 md:grid-cols-3">
            <ListBlock attr="data-previous-new-causes" title="New suggested root causes" items={clusterItems('added')} />
            <ListBlock attr="data-previous-resolved" title="Resolved" items={clusterItems('removed')} />
            <ListBlock attr="data-previous-persisting" title="Persisting" items={clusterItems('matched')} />
          </div>
          {testRows.length > 0 && (
            <div data-previous-tests-changed="">
              <p className="m-0 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
                Tests whose AI conclusion changed ({testRows.length})
              </p>
              <ul className="m-0 list-none p-0">
                {testRows.slice(0, MAX_TEST_ROWS).map((row) => (
                  <li key={row.key} className="flex items-baseline gap-2">
                    <span className="w-[76px] flex-shrink-0 text-[10px] uppercase tracking-wide text-[var(--color-text-faint)]">{row.tag}</span>
                    <span className="min-w-0 flex-1"><TestChangeRow row={row} runId={runId} /></span>
                  </li>
                ))}
                {testRows.length > MAX_TEST_ROWS && (
                  <li className="text-[11px] text-[var(--color-text-faint)]">+{testRows.length - MAX_TEST_ROWS} more in Run Compare</li>
                )}
              </ul>
            </div>
          )}
          {data.narrative && (
            <div data-previous-narrative="" className="rounded-md border border-[var(--color-border)] px-3 py-2">
              <p className="m-0 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
                <span data-previous-narrative-label="">{data.narrative.label}</span>
              </p>
              {data.narrative.executive_summary && (
                <p className="m-0 mt-1 text-[12px] text-[var(--color-text-secondary)]">{data.narrative.executive_summary}</p>
              )}
              {(data.narrative.ai_changes ?? []).length > 0 && (
                <ul className="m-0 mt-1 list-disc pl-5 text-[12px] text-[var(--color-text-secondary)]">
                  {data.narrative.ai_changes.map((line, i) => <li key={`${i}-${line}`}>{line}</li>)}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
