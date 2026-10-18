/**
 * W03 AIH-05: the stored AI versions of ONE test result, opened from a chip of
 * the History tab's AI strip (contract W03 §9.8).
 *
 * The head is that run's current analysis, from the history read the strip
 * already has (no request). The versions come from
 * `GET /runs/{run_id}/tests/{test_id}/ai-history`, read only when the popover
 * opens and only when the history says versions exist (`version_count > 0`);
 * otherwise it says "No versions stored yet" without asking.
 *
 * Root causes, actor and model names are untrusted text: rendered as React text.
 */
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { X } from 'lucide-react'
import { useTestAIVersions } from '@/hooks/useTestAIVersions'
import type { AITestVersion, TestHistoryAI } from '@/types/aiTestHistory'
import { formatRunWhen, fromNow } from '@/utils/formatters'
import { AICategoryChip, AIConfidence } from './AICategoryChip'
import { engineText, versionLabel, versionSummary } from './aiTestHistory.model'

/** "3 hours ago", or "time not reported" for a missing / unparseable instant. */
function whenText(at: string | null | undefined): string {
  if (!at || Number.isNaN(Date.parse(at))) return 'time not reported'
  return fromNow(at)
}

function VersionRow({ version, latest }: { version: AITestVersion; latest: boolean }) {
  return (
    <li
      data-test-ai-version=""
      data-version={version.version}
      data-trigger={version.trigger}
      className="space-y-0.5 border-t border-[var(--color-border)] py-1.5 first:border-t-0"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span data-test-ai-version-label="" className="font-medium text-[var(--color-text)]">
          {versionLabel(version)}
        </span>
        {latest && (
          <span className="rounded bg-[var(--color-bg-secondary)] px-1 text-[10px] text-[var(--color-text-muted)]">
            latest
          </span>
        )}
        <AICategoryChip category={version.failure_category} />
        <AIConfidence confidence={version.confidence_score} />
        {/* A correction edits the AI row in place and inherits its model; the person decided, so no model name. */}
        {version.llm_model && version.trigger !== 'human_correction' && (
          <span data-test-ai-version-model="" className="text-[var(--color-text-muted)]">{version.llm_model}</span>
        )}
        <span
          className="ml-auto text-[var(--color-text-muted)]"
          title={formatRunWhen(version.created_at) || undefined}
        >
          {whenText(version.created_at)}
        </span>
      </div>
      {version.root_cause_excerpt ? (
        <p data-test-ai-version-excerpt="" className="text-[var(--color-text-secondary)] break-words">
          {version.root_cause_excerpt}
        </p>
      ) : (
        <p className="text-[var(--color-text-muted)]">Root cause not reported.</p>
      )}
    </li>
  )
}

export default function TestAIVersionsPopover({
  runId,
  runLabel,
  ai,
  onClose,
}: {
  /** The history point's run (null for a legacy point with no run id). */
  runId: string | null
  runLabel: string
  /** That point's AI block from the history read. */
  ai: TestHistoryAI
  onClose: () => void
}) {
  const hasVersions = ai.version_count > 0
  const { data, error, isLoading, mutate } = useTestAIVersions(hasVersions ? runId : null, ai.test_id)

  // Oldest -> newest, the strip's direction; the API sends newest first.
  const versions = data ? [...data.versions].sort((a, b) => a.version - b.version) : []
  const newest = versions.length > 0 ? versions[versions.length - 1].version : null

  let body: ReactNode
  if (!hasVersions) {
    body = (
      <p data-test-ai-versions-state="empty" className="text-[var(--color-text-muted)]">
        No versions stored yet
      </p>
    )
  } else if (!runId) {
    body = (
      <p data-test-ai-versions-state="unavailable" className="text-[var(--color-text-muted)]">
        This run has no id, so its stored versions cannot be read.
      </p>
    )
  } else if (isLoading && !data) {
    body = (
      <p data-test-ai-versions-state="loading" className="text-[var(--color-text-muted)]">
        Loading versions…
      </p>
    )
  } else if (error && !data) {
    body = (
      <p data-test-ai-versions-state="error" className="text-[var(--status-failed)]">
        Could not load the stored versions.{' '}
        <button type="button" className="underline" onClick={() => void mutate()}>
          Retry
        </button>
      </p>
    )
  } else if (versions.length === 0) {
    body = (
      <p data-test-ai-versions-state="empty" className="text-[var(--color-text-muted)]">
        No versions stored yet
      </p>
    )
  } else {
    body = (
      <>
        <p data-test-ai-version-summary="" className="text-[var(--color-text)]">
          {versionSummary(versions)}
        </p>
        <ol data-test-ai-versions="" className="mt-1">
          {versions.map((v) => (
            <VersionRow key={v.version_id} version={v} latest={v.version === newest} />
          ))}
        </ol>
        {data && data.total > versions.length && (
          <p className="text-[var(--color-text-muted)]">
            Showing the latest {versions.length} of {data.total} versions.
          </p>
        )}
      </>
    )
  }

  return (
    <div
      data-test-ai-popover=""
      data-run-id={runId ?? ''}
      role="region"
      aria-label={`AI analysis versions for ${runLabel}`}
      className="space-y-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-3 text-xs shadow-lg"
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
            AI analysis on {runLabel}
          </p>
          <div data-test-ai-current="" className="flex flex-wrap items-center gap-1.5">
            <AICategoryChip category={ai.category} />
            <AIConfidence confidence={ai.confidence} />
            <span className="text-[var(--color-text-muted)]">{engineText(ai.analysis_mode, ai.model)}</span>
            {ai.is_flaky && <span className="badge-flaky">flaky</span>}
            {ai.requires_human_review && (
              <span className="rounded border border-[var(--status-broken)]/40 px-1 text-[10px] text-[var(--status-broken)]">
                needs human review
              </span>
            )}
          </div>
          {ai.root_cause_excerpt ? (
            <p data-test-ai-current-excerpt="" className="text-[var(--color-text-secondary)] break-words">
              {ai.root_cause_excerpt}
            </p>
          ) : (
            <p className="text-[var(--color-text-muted)]">Root cause not reported.</p>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close AI versions"
          className="shrink-0 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="border-t border-[var(--color-border)] pt-2">
        <p className="mb-1 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          Stored versions{hasVersions ? ` (${ai.version_count})` : ''}
        </p>
        {body}
      </div>

      {runId && (
        <Link
          to={`/runs/${runId}/tests/${ai.test_id}`}
          data-test-ai-open-run=""
          className="inline-block text-[var(--color-accent)] hover:underline"
        >
          Open this run&apos;s result
        </Link>
      )}
    </div>
  )
}
