/**
 * W03 AIH-04: a stored AI version of the run, read-only (contract §9.5). Shown on the Analysis tab INSTEAD of the
 * live sections while `?ai_version=` names a version that is not the latest: the verdict it reached, the run
 * summary written by the same pipeline, its categories, failure clusters and per-test conclusions, how it was
 * produced, and the decision report it cited.
 *
 * The run's `/intelligence` read is always live (only the decision report can be swapped there), so a stored
 * version is drawn from its own payload (`GET /runs/{id}/ai-history/{version_id}`), never by re-reading the run.
 * AI text is React text or `ChatMarkdown` (no raw HTML).
 */
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Archive, ArrowLeft } from 'lucide-react'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import ChatMarkdown from '@/components/chat/ChatMarkdown'
import { useAIHistory, useAIVersion } from '@/hooks/useAIHistory'
import type { AIDigest, AIRunSummaryPayload } from '@/types/aiHistory'
import FailureCategoryMark from '@/components/charts/FailureCategoryMark'
import { confidenceColor } from '@/utils/formatters'
import { engineLabel, engineWord, whenLabel } from './aiDigestDiff.model'
import { applyAiVersion, evidenceHref, runTestHref } from './aiHistoryUrl'

const TEST_ROWS = 50

function SnapshotSection({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section data-ai-snapshot-section={id} aria-label={title} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4">
      <h3 className="m-0 mb-2 text-[13px] font-semibold text-[var(--color-text)]">{title}</h3>
      {children}
    </section>
  )
}

/** The summary text a stored run summary carries (the executive summary, else layer 1), when it is text. */
function summaryText(payload: AIRunSummaryPayload | undefined): string | null {
  for (const value of [payload?.executive_summary, payload?.layer1_executive_summary]) {
    if (typeof value === 'string' && value.trim()) return value
  }
  return null
}

function PairedSummary({ runId, summaryVersion }: { runId: string; summaryVersion: number | null | undefined }) {
  const summaries = useAIHistory(summaryVersion != null ? runId : null, 'run_summary')
  const versionId = summaries.data?.versions?.find((v) => v.version === summaryVersion)?.version_id ?? null
  const summary = useAIVersion(runId, versionId)
  if (summaryVersion == null) {
    return <p className="m-0 text-[12px] text-[var(--color-text-muted)]">No summary was stored for this version.</p>
  }
  if (summaries.isLoading || summary.isLoading) return <LoadingSpinner size="sm" />
  const text = summaryText(summary.data?.payload as AIRunSummaryPayload | undefined)
  if (!text) {
    return <p className="m-0 text-[12px] text-[var(--color-text-muted)]">No summary was stored for this version.</p>
  }
  return <ChatMarkdown text={text} />
}

export default function AIVersionSnapshot({ runId, version }: { runId: string; version: number }) {
  const [, setSearchParams] = useSearchParams()
  const history = useAIHistory(runId)
  const versions = history.data?.versions ?? []
  const summary = versions.find((v) => v.version === version) ?? null
  const stored = useAIVersion(runId, summary?.version_id ?? null)
  const [allTests, setAllTests] = useState(false)

  const backToCurrent = () => setSearchParams((current) => applyAiVersion(current, null, versions))

  const banner = (
    <div
      data-ai-snapshot-banner=""
      role="note"
      className="flex flex-wrap items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-[12px]"
    >
      <Archive aria-hidden className="h-4 w-4 text-[var(--color-accent)]" />
      <span className="text-[var(--color-text)]">
        Viewing v{version}
        {summary && ` (${engineLabel(summary)}${whenLabel(summary.generated_at) ? ` · ${whenLabel(summary.generated_at)}` : ''})`}
        {' '}— this is a stored snapshot.
      </span>
      <button
        type="button"
        data-ai-snapshot-back=""
        onClick={backToCurrent}
        className="ml-auto inline-flex items-center gap-1 rounded border border-[var(--color-border)] px-2 py-0.5 text-[12px] text-[var(--color-text)] hover:bg-[var(--color-bg-hover)]"
      >
        <ArrowLeft aria-hidden className="h-3 w-3" /> Back to current
      </button>
    </div>
  )

  const gone = (history.data && !summary) || (summary && stored.error)
  if (gone) {
    return (
      <div data-ai-snapshot="" data-ai-snapshot-version={version} className="space-y-3">
        {banner}
        <EmptyState
          icon={<Archive className="h-8 w-8" />}
          title="That version no longer exists"
          description="It may have been pruned by the retention policy. Go back to the current analysis or pick another version."
        />
      </div>
    )
  }
  if (history.error) {
    return (
      <div data-ai-snapshot="" data-ai-snapshot-version={version} className="space-y-3">
        {banner}
        <EmptyState title="The AI history is unavailable" description="The stored versions could not be read. Try again in a moment." />
      </div>
    )
  }
  const digest = stored.data?.payload as AIDigest | undefined
  if (!digest || !summary) {
    return (
      <div data-ai-snapshot="" data-ai-snapshot-version={version} className="space-y-3">
        {banner}
        <div className="flex justify-center py-10"><LoadingSpinner /></div>
      </div>
    )
  }

  const verdict = digest.verdict ?? null
  const categories = Object.entries(digest.categories ?? {}).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
  const clusters = digest.clusters ?? []
  const tests = digest.tests ?? []
  const provenance = digest.provenance ?? null
  const modeCounts = Object.entries(provenance?.mode_counts ?? {}).sort((a, b) => (a[0] < b[0] ? -1 : 1))
  const reportRef = summary.decision_report_ref ?? null
  const shownTests = allTests ? tests : tests.slice(0, TEST_ROWS)

  return (
    <div data-ai-snapshot="" data-ai-snapshot-version={version} className="space-y-3">
      {banner}

      <SnapshotSection id="verdict" title="Verdict">
        <p className="m-0 text-[14px] font-semibold text-[var(--color-text)]">
          {verdict?.recommendation ?? 'No verdict was recorded'}
          {verdict?.risk_score != null && <span className="ml-2 text-[12px] font-normal text-[var(--color-text-muted)]">risk {verdict.risk_score}/100</span>}
          {verdict?.overridden && <span className="ml-2 text-[11px] font-normal text-[var(--color-text-muted)]">(the AI verdict before a human override)</span>}
        </p>
        <p className="m-0 mt-1 text-[11px] text-[var(--color-text-muted)]">
          {digest.analysed_tests} analysed test{digest.analysed_tests === 1 ? '' : 's'}
          {digest.avg_confidence != null && ` · average confidence ${digest.avg_confidence}%`}
          {digest.flaky_count > 0 && ` · ${digest.flaky_count} flaky`}
          {digest.requires_review_count > 0 && ` · ${digest.requires_review_count} to review`}
        </p>
        {reportRef && (
          <Link
            data-ai-snapshot-report=""
            to={evidenceHref(runId, reportRef.report_version, version)}
            className="mt-2 inline-block text-[12px] text-[var(--color-accent)] underline underline-offset-2"
          >
            Open decision report v{reportRef.report_version}
          </Link>
        )}
      </SnapshotSection>

      <SnapshotSection id="summary" title="Run summary">
        <PairedSummary runId={runId} summaryVersion={summary.summary_version} />
      </SnapshotSection>

      {categories.length > 0 && (
        <SnapshotSection id="categories" title="Failure categories">
          <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
            {categories.map(([category, count]) => (
              <li key={category} className="inline-flex items-center gap-1 rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-secondary)]">
                <FailureCategoryMark category={category} size={8} />
                {category} {count}
              </li>
            ))}
          </ul>
        </SnapshotSection>
      )}

      {clusters.length > 0 && (
        <SnapshotSection id="clusters" title={`Failure clusters (${clusters.length})`}>
          <ul className="m-0 list-none space-y-1.5 p-0">
            {clusters.map((c) => (
              <li key={c.signature} className="text-[12px]">
                <span className="font-medium text-[var(--color-text)]">{c.label || c.signature}</span>
                <span className="ml-1 text-[var(--color-text-muted)]">({c.size} test{c.size === 1 ? '' : 's'})</span>
                {c.representative_error && (
                  <p className="m-0 mt-0.5 truncate font-mono text-[11px] text-[var(--color-text-faint)]" title={c.representative_error}>
                    {c.representative_error}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </SnapshotSection>
      )}

      {tests.length > 0 && (
        <SnapshotSection id="tests" title={`Analysed tests (${tests.length})`}>
          <table className="w-full text-left text-[12px]">
            <thead>
              <tr className="text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">
                <th className="py-1 pr-2 font-medium">Test</th>
                <th className="py-1 pr-2 font-medium">Category</th>
                <th className="py-1 pr-2 font-medium">Confidence</th>
                <th className="py-1 font-medium">Suggested root cause</th>
              </tr>
            </thead>
            <tbody>
              {shownTests.map((t) => (
                <tr key={t.test_fingerprint} data-ai-snapshot-test={t.test_fingerprint} className="border-t border-[var(--color-border)] align-top">
                  <td className="py-1 pr-2">
                    <Link to={runTestHref(runId, t.test_id)} className="text-[var(--color-text)] hover:underline break-all">
                      {t.test_name || t.test_fingerprint}
                    </Link>
                  </td>
                  <td className={clsx('py-1 pr-2', t.category ? 'text-[var(--color-text-secondary)]' : 'text-[var(--color-text-muted)]')}>
                    {t.category && <FailureCategoryMark category={t.category} size={8} className="mr-1" />}
                    {t.category ?? 'UNCATEGORIZED'}
                  </td>
                  <td className={clsx('py-1 pr-2', t.confidence != null ? confidenceColor(t.confidence) : 'text-[var(--color-text-muted)]')}>
                    {t.confidence != null ? `${t.confidence}%` : '-'}
                  </td>
                  <td className="py-1 text-[var(--color-text-secondary)]">{t.root_cause_excerpt || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {tests.length > TEST_ROWS && (
            <button
              type="button"
              onClick={() => setAllTests((v) => !v)}
              className="mt-2 text-[12px] text-[var(--color-accent)] underline underline-offset-2"
            >
              {allTests ? `Show the first ${TEST_ROWS}` : `Show all ${tests.length}`}
            </button>
          )}
          {digest.limits?.tests_truncated && (
            <p className="m-0 mt-1 text-[11px] text-[var(--color-text-faint)]">
              The stored version kept {tests.length} of {digest.limits.tests_total} analysed tests.
            </p>
          )}
        </SnapshotSection>
      )}

      <SnapshotSection id="provenance" title="How this version was produced">
        <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-[12px]">
          <dt className="text-[var(--color-text-muted)]">Engine</dt>
          <dd className="m-0 text-[var(--color-text)]">{engineWord(provenance?.engine ?? summary.analysis_mode)}{summary.deterministic ? ' (deterministic)' : ''}</dd>
          <dt className="text-[var(--color-text-muted)]">Model</dt>
          <dd className="m-0 text-[var(--color-text)]">{[provenance?.llm_provider, provenance?.llm_model].filter(Boolean).join(' / ') || 'no model'}</dd>
          <dt className="text-[var(--color-text-muted)]">Fallback</dt>
          <dd className="m-0 text-[var(--color-text)]">
            {provenance?.fallback_used ? `used for ${provenance.fallback_count} test${provenance.fallback_count === 1 ? '' : 's'}` : 'not used'}
          </dd>
          {modeCounts.length > 0 && (
            <>
              <dt className="text-[var(--color-text-muted)]">Tests by engine</dt>
              <dd className="m-0 text-[var(--color-text)]">{modeCounts.map(([mode, n]) => `${engineWord(mode)} ${n}`).join(' · ')}</dd>
            </>
          )}
          <dt className="text-[var(--color-text-muted)]">Trigger</dt>
          <dd className="m-0 text-[var(--color-text)]">{summary.trigger === 'backfill' ? 'backfilled from the stored analysis' : summary.trigger}</dd>
        </dl>
        {provenance?.carried_over_tests != null && provenance.carried_over_tests > 0 && (
          <p data-ai-snapshot-carried-over="" className="m-0 mt-2 text-[11px] text-[var(--color-text-muted)]">
            {provenance.carried_over_tests} test{provenance.carried_over_tests === 1 ? ' was' : 's were'} not re-analysed by this pipeline:
            their conclusions were carried over from an earlier analysis.
          </p>
        )}
        {(provenance?.partial ?? []).length > 0 && (
          <p className="m-0 mt-1 text-[11px] text-[var(--color-text-muted)]">Partial: {(provenance?.partial ?? []).join(', ')}</p>
        )}
      </SnapshotSection>
    </div>
  )
}
