/**
 * W03 AIH-02/03/04: what changed between two AI analyses (contract §9.4). Draws `buildDiffModel` and nothing
 * else: provenance first (an engine change explains the category churn under it), then verdict, categories, root
 * causes (clusters), tests and actions, each section omitted when it has nothing to say.
 *
 * `versions` mode compares two analyses of one run; `cross_run` compares this run with a baseline run. A test row
 * links to the `to` side's test (`runId`); a removed / resolved test belongs to the `from` run, so it is plain
 * text unless the caller names that run (`fromRunId`).
 *
 * Every name, label and excerpt is AI or CI text: rendered as React text, never as markup.
 */
import { Link } from 'react-router-dom'
import { Info } from 'lucide-react'
import { clsx } from 'clsx'
import type { AIDiffMode, AIDigestDiff } from '@/types/aiHistory'
import { categoryColor } from '@/utils/formatters'
import { buildDiffModel, type DiffClusterGroup, type DiffTestGroup, type DiffTestRow } from './aiDigestDiff.model'
import { runTestHref } from './aiHistoryUrl'

const DIRECTION_TONE: Record<string, string> = {
  worse: 'text-[var(--status-failed)]',
  better: 'text-[var(--status-passed)]',
}

function More({ hidden }: { hidden: number }) {
  if (hidden <= 0) return null
  return <li className="text-[11px] text-[var(--color-text-faint)]">+{hidden} more</li>
}

function TestRow({ row, runId, fromRunId }: { row: DiffTestRow; runId: string; fromRunId?: string }) {
  const targetRun = row.side === 'to' ? runId : fromRunId
  const body = (
    <>
      <span className="font-medium text-[var(--color-text)] break-all">{row.name}</span>
      {row.suite && <span className="text-[var(--color-text-faint)]"> · {row.suite}</span>}
      <span className={clsx('ml-2', row.category ? categoryColor(row.category) : 'text-[var(--color-text-muted)]')}>
        {row.detail}
      </span>
    </>
  )
  if (row.testId && targetRun) {
    return (
      <Link
        data-ai-diff-row="test"
        data-test-fingerprint={row.fingerprint}
        to={runTestHref(targetRun, row.testId)}
        className="block rounded px-1 py-0.5 text-[12px] hover:bg-[var(--color-bg-hover)]"
      >
        {body}
      </Link>
    )
  }
  return (
    <span data-ai-diff-row="test" data-test-fingerprint={row.fingerprint} className="block px-1 py-0.5 text-[12px]">
      {body}
    </span>
  )
}

function TestGroups({ groups, runId, fromRunId, maxRows }: {
  groups: DiffTestGroup[]
  runId: string
  fromRunId?: string
  maxRows: number
}) {
  return (
    <div className="grid gap-2">
      {groups.map((group) => (
        <div key={group.id} data-ai-diff-group={group.id}>
          <p className="m-0 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
            {group.label} ({group.rows.length})
          </p>
          <ul className="m-0 list-none p-0">
            {group.rows.slice(0, maxRows).map((row) => (
              <li key={row.key}>
                <TestRow row={row} runId={runId} fromRunId={fromRunId} />
              </li>
            ))}
            <More hidden={group.rows.length - maxRows} />
          </ul>
        </div>
      ))}
    </div>
  )
}

function ClusterGroups({ groups, maxRows }: { groups: DiffClusterGroup[]; maxRows: number }) {
  return (
    <div className="grid gap-2 md:grid-cols-3">
      {groups.map((group) => (
        <div key={group.id} data-ai-diff-group={group.id}>
          <p className="m-0 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
            {group.label} ({group.rows.length})
          </p>
          <ul className="m-0 list-none p-0">
            {group.rows.slice(0, maxRows).map((row) => (
              <li key={row.key} data-ai-diff-row="cluster" data-matched-by={row.matchedBy ?? undefined} className="py-0.5 text-[12px]">
                <span className="text-[var(--color-text)] break-words">{row.label}</span>
                <span className="ml-1 text-[var(--color-text-muted)]">({row.detail})</span>
              </li>
            ))}
            <More hidden={group.rows.length - maxRows} />
          </ul>
        </div>
      ))}
    </div>
  )
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <div data-ai-diff-section={id} className="space-y-1.5">
      <h4 className="m-0 text-[12px] font-semibold text-[var(--color-text-secondary)]">{title}</h4>
      {children}
    </div>
  )
}

export default function AIDigestDiff({
  diff,
  fromLabel,
  toLabel,
  runId,
  fromRunId,
  maxRows = 20,
  mode,
}: {
  diff: AIDigestDiff
  fromLabel: string
  toLabel: string
  /** The run whose tests the `to` side links to. */
  runId: string
  /** The `from` run, to link a removed / resolved test (cross-run). */
  fromRunId?: string
  maxRows?: number
  mode?: AIDiffMode
}) {
  const model = buildDiffModel(diff, { mode })
  return (
    <section data-ai-diff="" data-ai-diff-mode={model.mode} aria-label="AI analysis differences" className="space-y-3">
      <p className="m-0 text-[11px] text-[var(--color-text-muted)]">
        <span className="font-medium text-[var(--color-text-secondary)]">{fromLabel}</span>
        {' -> '}
        <span className="font-medium text-[var(--color-text-secondary)]">{toLabel}</span>
      </p>
      {model.isEmpty && (
        <p data-ai-diff-empty="" className="m-0 text-[12px] text-[var(--color-text-muted)]">{model.emptyText}</p>
      )}
      {model.sections.map((id) => {
        switch (id) {
          case 'provenance':
            return model.provenance && (
              <div
                key={id}
                data-ai-diff-section="provenance"
                role="note"
                className="flex items-start gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-[12px]"
              >
                <Info aria-hidden className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-[var(--color-accent)]" />
                <div className="min-w-0">
                  <p className="m-0 text-[var(--color-text)]">{model.provenance.note}</p>
                  {model.provenance.changes.length > 0 && (
                    <p className="m-0 mt-0.5 text-[11px] text-[var(--color-text-muted)]">{model.provenance.changes.join(' · ')}</p>
                  )}
                </div>
              </div>
            )
          case 'verdict':
            return model.verdict && (
              <Section key={id} id="verdict" title="Verdict">
                <p className={clsx('m-0 text-[13px] font-semibold', DIRECTION_TONE[model.verdict.direction] ?? 'text-[var(--color-text)]')}>
                  {model.verdict.text}
                  {model.verdict.risk && (
                    <span className="ml-2 text-[12px] font-normal text-[var(--color-text-muted)]">{model.verdict.risk}</span>
                  )}
                </p>
              </Section>
            )
          case 'categories':
            return (
              <Section key={id} id="categories" title="Categories">
                <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
                  {model.categories.map((row) => (
                    <li
                      key={row.category}
                      data-ai-diff-category={row.category}
                      title={`${row.from} -> ${row.to}`}
                      className={clsx('rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[11px]', categoryColor(row.category))}
                    >
                      {row.text}
                    </li>
                  ))}
                </ul>
              </Section>
            )
          case 'clusters':
            return (
              <Section key={id} id="clusters" title={model.mode === 'cross_run' ? 'Suggested root causes' : 'Failure clusters'}>
                <ClusterGroups groups={model.clusters} maxRows={maxRows} />
              </Section>
            )
          case 'tests':
            return (
              <Section key={id} id="tests" title="Tests">
                <TestGroups groups={model.tests.groups} runId={runId} fromRunId={fromRunId} maxRows={maxRows} />
                {(model.tests.persistingUnchanged > 0 || model.tests.truncated) && (
                  <p className="m-0 text-[11px] text-[var(--color-text-faint)]">
                    {model.tests.persistingUnchanged > 0 && `${model.tests.persistingUnchanged} analysed test${model.tests.persistingUnchanged === 1 ? '' : 's'} unchanged.`}
                    {model.tests.truncated && ' The list was cut at 200 changes per group; the counts are complete.'}
                  </p>
                )}
              </Section>
            )
          case 'actions':
            return (
              <Section key={id} id="actions" title="Recommended actions">
                <ul className="m-0 list-none space-y-0.5 p-0 text-[12px]">
                  {model.actions.added.map((a) => (
                    <li key={`+${a}`} data-ai-diff-action="added" className="text-[var(--color-text)]">+ {a}</li>
                  ))}
                  {model.actions.removed.map((a) => (
                    <li key={`-${a}`} data-ai-diff-action="removed" className="text-[var(--color-text-muted)] line-through">- {a}</li>
                  ))}
                </ul>
              </Section>
            )
          default:
            return null
        }
      })}
    </section>
  )
}
