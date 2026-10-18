/**
 * The History tab of a test case (W28 TCM-04): every version, newest first,
 * with who saved it, what changed (before → after, steps step by step), the
 * plan executions recorded against it, a "Compare with current" block, and
 * "Restore this version", asked IN the card (`InlineConfirm`) and saved as a
 * NEW version (history is never rewritten; the status stays as it is).
 *
 * Everything is React text: titles, steps, names, summaries. A masked
 * parameter's value is never in the DOM ("masked value changed"). The tints
 * are the theme's status tokens (before on --status-failed-bg, struck through;
 * after on --status-passed-bg).
 */
import { useMemo, useState } from 'react'
import { History } from 'lucide-react'
import toast from 'react-hot-toast'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import InlineConfirm from '@/components/ui/inPage/InlineConfirm'
import { errorSentence, testCaseEditingService } from '@/services/testCaseEditingService'
import type { ManagedTestCase, TestCaseVersion } from '@/types/test-management'
import { statusLabel } from './caseStatusModel'
import {
  changedFieldLabels,
  diffVersions,
  hasMaskedParameter,
  type DiffOptions,
  type FieldChange,
  type ParameterChange,
  type StepChange,
} from './caseHistoryModel'
import { fmtDateTime } from './format'

interface CaseHistoryPanelProps {
  caseItem: ManagedTestCase
  versions: TestCaseVersion[] | undefined
  isLoading: boolean
  /** TESTER+, authored, not retired, one project. */
  canRestore: boolean
  onRestored: (updated: ManagedTestCase) => void
  /** A 409 on restore: the host re-reads the case (getCaseFresh) and the history. */
  onStale: () => void
}

const SOURCE_CHIPS: Record<string, string> = { ai: 'AI', api: 'API' }

const BEFORE = 'rounded px-1 bg-[var(--status-failed-bg)] text-[var(--color-text)] line-through decoration-[var(--status-failed)] [overflow-wrap:anywhere]'
const AFTER = 'rounded px-1 bg-[var(--status-passed-bg)] text-[var(--color-text)] [overflow-wrap:anywhere]'
const LINK_BUTTON = 'text-xs text-[var(--color-accent)] hover:underline disabled:opacity-50 disabled:no-underline'

function Pair({ label, before, after, long }: { label: string; before?: string; after?: string; long?: boolean }) {
  if (long) {
    return (
      <li className="space-y-0.5">
        <span className="font-medium text-[var(--color-text-secondary)]">{label}:</span>
        <span className={`block whitespace-pre-wrap ${BEFORE}`} data-diff-before="">{before}</span>
        <span className={`block whitespace-pre-wrap ${AFTER}`} data-diff-after="">{after}</span>
      </li>
    )
  }
  return (
    <li className="[overflow-wrap:anywhere]">
      <span className="font-medium text-[var(--color-text-secondary)]">{label}:</span>{' '}
      <span className={BEFORE} data-diff-before="">{before}</span>
      {' → '}
      <span className={AFTER} data-diff-after="">{after}</span>
    </li>
  )
}

function OneSide({ label, value, tone }: { label: string; value?: string; tone?: 'after' | 'before' }) {
  return (
    <li className="[overflow-wrap:anywhere]">
      <span className="font-medium text-[var(--color-text-secondary)]">{label}</span>
      {value !== undefined && (
        <>
          {': '}
          <span className={tone === 'before' ? BEFORE : AFTER} data-diff-after="">{value}</span>
        </>
      )}
    </li>
  )
}

function stepLine(change: StepChange) {
  const key = `step-${change.position}-${change.kind}-${change.part ?? ''}`
  if (change.kind === 'added') return <OneSide key={key} label={`Step ${change.position} added`} value={change.after} />
  if (change.kind === 'removed') return <OneSide key={key} label={`Step ${change.position} removed`} />
  return (
    <Pair
      key={key}
      label={`Step ${change.position} ${change.part === 'expected' ? 'expected' : 'action'}`}
      before={change.before}
      after={change.after}
      long={(change.before?.length ?? 0) > 60 || (change.after?.length ?? 0) > 60}
    />
  )
}

function parameterLine(change: ParameterChange) {
  const key = `param-${change.name}-${change.kind}`
  if (change.kind === 'masked') return <OneSide key={key} label={`Parameter ${change.name}: masked value changed`} />
  if (change.kind === 'added') return <OneSide key={key} label={`Parameter ${change.name} added`} value={change.after} />
  if (change.kind === 'removed') return <OneSide key={key} label={`Parameter ${change.name} removed`} />
  return <Pair key={key} label={`Parameter ${change.name}`} before={change.before} after={change.after} />
}

function ChangeList({ changes, testId }: { changes: FieldChange[]; testId: string }) {
  return (
    <ul className="m-0 space-y-1 pl-0 list-none text-xs text-[var(--color-text)]" data-testid={testId}>
      {changes.map((change) => {
        if (change.steps) return change.steps.map(stepLine)
        if (change.parameters) return change.parameters.map(parameterLine)
        return <Pair key={change.key} label={change.label} before={change.before} after={change.after} long={change.long} />
      })}
    </ul>
  )
}

interface Notice {
  version: number
  text: string
  stale: boolean
}

export default function CaseHistoryPanel({ caseItem, versions, isLoading, canRestore, onRestored, onStale }: CaseHistoryPanelProps) {
  const [comparing, setComparing] = useState<ReadonlySet<number>>(() => new Set())
  const [confirming, setConfirming] = useState<number | null>(null)
  const [restoring, setRestoring] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)

  const ordered = useMemo(
    () => [...(versions ?? [])].sort((a, b) => b.version - a.version),
    [versions],
  )

  // Names this tab already knows (no new read): the case's owner and the people who saved a version.
  const diffOptions: DiffOptions = useMemo(() => {
    const names = new Map<string, string>()
    for (const v of ordered) {
      if (v.changed_by_id && v.changed_by_name) names.set(v.changed_by_id, v.changed_by_name)
    }
    if (caseItem.assignee_id && caseItem.owner) names.set(caseItem.assignee_id, caseItem.owner)
    return { nameOf: (id: string) => names.get(id) }
  }, [ordered, caseItem.assignee_id, caseItem.owner])

  if (isLoading) return <div className="flex justify-center py-12"><LoadingSpinner /></div>
  if (ordered.length === 0) {
    return <EmptyState icon={<History className="h-8 w-8" />} title="No history yet" description="Changes to this test case will appear here" />
  }

  const toggleCompare = (n: number) => {
    setComparing((current) => {
      const next = new Set(current)
      if (next.has(n)) next.delete(n)
      else next.add(n)
      return next
    })
  }

  const restore = async (n: number) => {
    setRestoring(true)
    setNotice(null)
    try {
      const updated = await testCaseEditingService.restoreVersion(caseItem.id, { version: n, expected_version: caseItem.version })
      setConfirming(null)
      if (updated.version === caseItem.version) {
        setNotice({ version: n, text: `v${n} matches the current version: nothing to restore.`, stale: false })
        return
      }
      toast.success(`Restored v${n} as v${updated.version}`)
      onRestored(updated)
    } catch (err) {
      setConfirming(null)
      const status = (err as { response?: { status?: number } } | null)?.response?.status
      if (status === 409) {
        setNotice({ version: n, text: 'This test case changed since you opened it.', stale: true })
      } else {
        setNotice({ version: n, text: errorSentence(err, 'This version could not be restored.'), stale: false })
      }
    } finally {
      setRestoring(false)
    }
  }

  return (
    <div className="space-y-3" data-case-history="">
      {ordered.map((v, index) => {
        const previous = ordered[index + 1]
        const changes = previous ? diffVersions(previous, v, diffOptions) : []
        const chip = v.source ? SOURCE_CHIPS[v.source] : undefined
        const isCurrent = v.version === caseItem.version
        const compareOpen = comparing.has(v.version)
        const compareChanges = compareOpen ? diffVersions(v, caseItem, diffOptions) : []
        const executions = Array.isArray(v.executions) ? v.executions : []
        return (
          <div key={v.id} data-history-version={String(v.version)} className="bg-[var(--color-bg-secondary)] rounded-lg p-4 space-y-2">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-sm font-medium text-[var(--color-text)]">v{v.version} — {v.change_type.replace(/_/g, ' ')}</span>
              <span className="text-xs text-[var(--color-text-muted)]">{fmtDateTime(v.created_at)}</span>
              {v.changed_by_name && (
                <span className="text-xs text-[var(--color-text-muted)] [overflow-wrap:anywhere]">by {v.changed_by_name}</span>
              )}
              {chip && (
                <span
                  className="rounded-full border border-[var(--color-border-light)] bg-[var(--color-bg-hover)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--color-text-secondary)]"
                  data-history-source={v.source ?? ''}
                >
                  {chip}
                </span>
              )}
              {isCurrent && <span className="text-[11px] text-[var(--color-text-faint)]">current</span>}
            </div>

            {v.change_summary && (
              <p className="m-0 text-xs text-[var(--color-text-muted)] [overflow-wrap:anywhere]">{v.change_summary}</p>
            )}

            {previous ? (
              changes.length > 0 && (
                <div data-history-changes="">
                  <p className="m-0 mb-1 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">What changed</p>
                  <ChangeList changes={changes} testId={`history-changes-v${v.version}`} />
                </div>
              )
            ) : (
              v.changed_fields && v.changed_fields.length > 0 && (
                <p className="m-0 text-[11px] text-[var(--color-text-muted)]">Changed: {changedFieldLabels(v.changed_fields).join(', ')}</p>
              )
            )}

            {executions.length > 0 && (
              <ul className="m-0 pl-0 list-none space-y-0.5 text-xs text-[var(--color-text-secondary)]" data-history-executions="">
                {executions.map((run) => (
                  <li key={run.item_id} className="[overflow-wrap:anywhere]">
                    Ran in {run.plan_name} · {run.execution_status.replace(/_/g, ' ')}
                    {run.executed_at ? ` · ${fmtDateTime(run.executed_at)}` : ''}
                  </li>
                ))}
              </ul>
            )}

            {(!isCurrent || (canRestore && v.version < caseItem.version)) && (
              <div className="flex flex-wrap items-center gap-3">
                {!isCurrent && (
                  <button type="button" className={LINK_BUTTON} aria-expanded={compareOpen} onClick={() => toggleCompare(v.version)}>
                    Compare with current
                  </button>
                )}
                {canRestore && v.version < caseItem.version && (
                  <button
                    type="button"
                    className={LINK_BUTTON}
                    disabled={restoring}
                    onClick={() => { setNotice(null); setConfirming(v.version) }}
                  >
                    Restore this version
                  </button>
                )}
              </div>
            )}

            {compareOpen && (
              <div
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3 space-y-1"
                data-history-compare=""
              >
                <p className="m-0 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                  v{v.version} → current (v{caseItem.version})
                </p>
                {compareChanges.length > 0 ? (
                  <ChangeList changes={compareChanges} testId={`history-compare-v${v.version}`} />
                ) : (
                  <p className="m-0 text-xs text-[var(--color-text-secondary)]">
                    {hasMaskedParameter(v) || hasMaskedParameter(caseItem)
                      ? 'Same as the current version (masked parameter values are not compared).'
                      : 'Same as the current version'}
                  </p>
                )}
              </div>
            )}

            {confirming === v.version && (
              <InlineConfirm
                message={<>Restore v{v.version} as a new version? The status stays {statusLabel(caseItem.status)}.</>}
                confirmLabel="Restore"
                busy={restoring}
                onConfirm={() => void restore(v.version)}
                onCancel={() => setConfirming(null)}
              />
            )}

            {notice && notice.version === v.version && (
              <div
                role={notice.stale ? 'alert' : 'status'}
                className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-secondary)]"
                data-history-restore-notice=""
              >
                <span className="[overflow-wrap:anywhere]">{notice.text}</span>
                {notice.stale && (
                  <button type="button" className="btn-secondary text-xs px-3 py-1" onClick={() => { setNotice(null); onStale() }}>
                    Reload
                  </button>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
