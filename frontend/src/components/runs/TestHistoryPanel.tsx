import { useMemo } from "react";
import { clsx } from "clsx";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import TestAIHistoryStrip from "@/components/ai/TestAIHistoryStrip";
import TestDurationHistory from "@/components/charts/TestDurationHistory";
import {
  OUTLIER_RATIO,
  buildDurationHistoryModel,
} from "@/components/charts/testDurationHistory.model";
import { useTestCaseHistory } from "@/hooks/useRuns";
import {
  formatDuration,
  formatNumber,
  formatRunWhen,
} from "@/utils/formatters";
import type { TestHistoryDurationFields } from "@/types/executionMetrics";
import type {
  TestHistoryAIFields,
  TestHistoryAIPoint,
} from "@/types/aiTestHistory";
import type { TestCaseHistory, TestCaseHistoryPoint } from "@/types/runs";

const PASSED_STATUSES = ["PASSED"];
const FAILED_STATUSES = ["FAILED", "BROKEN"];

/** Single colored dot for one run in the pass/fail timeline. */
function HistoryDot({ point }: { point: TestCaseHistoryPoint }) {
  const status = point.status?.toUpperCase();
  const color = PASSED_STATUSES.includes(status)
    ? "bg-[var(--status-passed)]"
    : FAILED_STATUSES.includes(status)
      ? "bg-[var(--status-failed)]"
      : "bg-[var(--status-skipped)]";
  const when = formatRunWhen(point.created_at);
  const title = [
    point.run_label,
    point.status,
    formatDuration(point.duration_ms),
    when,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span
      className={clsx("inline-block h-3 w-3 shrink-0 rounded-full", color)}
      title={title}
      aria-label={title}
    />
  );
}

const CLASSIFICATION_BADGE: Record<string, string> = {
  QUARANTINE: "badge-failed",
  INVESTIGATE: "badge-broken",
  MONITOR: "badge-flaky",
  HEALTHY: "badge-passed",
};

function MetaRow({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </span>
      <span className="truncate text-right text-xs text-[var(--color-text-secondary)]">
        {value}
      </span>
    </div>
  );
}

const msOrNotReported = (ms: number | null | undefined) =>
  ms == null || !Number.isFinite(ms)
    ? "not reported"
    : formatDuration(Math.round(ms));

/**
 * EXEC-04: the test's duration per run, with the window's p50 / p95
 * (`duration_stats`) and the project baseline (`baseline`, mean ± 1σ). A run
 * without a duration is a gap; an older server that sends neither block gets
 * the chart alone, and no sentence about figures it never sent.
 */
function DurationHistorySection({
  data,
}: {
  data: TestCaseHistory & TestHistoryDurationFields;
}) {
  const model = useMemo(
    () =>
      buildDurationHistoryModel({
        history: data.history,
        durationStats: data.duration_stats,
        baseline: data.baseline,
      }),
    [data.history, data.duration_stats, data.baseline],
  );
  const stats = data.duration_stats;
  // Collapsed by default: the stats line answers "is it slow?" at a glance and the
  // page keeps its fold budget (the chart alone is ~270 px); the chart, baseline
  // and outlier note open on demand.
  const summary =
    stats?.measured && stats.p50_ms != null
      ? `p50 ${msOrNotReported(stats.p50_ms)} · p95 ${msOrNotReported(stats.p95_ms)}${model.outliers > 0 ? ` · ${formatNumber(model.outliers)} slow ${model.outliers === 1 ? "run" : "runs"}` : ""}`
      : model.measured > 0
        ? `${formatNumber(model.measured)} ${model.measured === 1 ? "run" : "runs"} with a duration`
        : "not reported";
  return (
    <section
      aria-label="Duration across runs"
      data-duration-history=""
      className="space-y-1.5"
    >
      <details data-duration-history-disclosure="" className="group">
        <summary className="cursor-pointer select-none text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          Duration across runs{" "}
          <span
            data-duration-history-summary=""
            className="normal-case tracking-normal text-xs text-[var(--color-text-secondary)]"
          >
            {summary}
          </span>
        </summary>
        <div className="mt-1.5 space-y-1.5">
          {model.measured > 0 ? (
            <TestDurationHistory model={model} />
          ) : (
            <p
              data-duration-history-state="none"
              className="text-xs text-[var(--color-text-muted)]"
            >
              No run of this test reported a duration.
            </p>
          )}
          {stats && (
            <p
              data-duration-stats={stats.measured ? "measured" : "unmeasured"}
              className="text-xs text-[var(--color-text-secondary)]"
            >
              {stats.measured
                ? `Last ${stats.window_days} days: p50 ${msOrNotReported(stats.p50_ms)} · p95 ${msOrNotReported(stats.p95_ms)} · max ${msOrNotReported(stats.max_ms)} over ${formatNumber(stats.n)} ${stats.n === 1 ? "execution" : "executions"}.`
                : `Last ${stats.window_days} days: ${(stats.reason ?? "not measured").replace(/[.]$/, "")}.`}
            </p>
          )}
          {data.baseline !== undefined && (
            <p
              data-duration-baseline-text={data.baseline ? "yes" : "no"}
              className="text-xs text-[var(--color-text-secondary)]"
            >
              {data.baseline
                ? `Baseline: mean ${msOrNotReported(data.baseline.mean_ms)} ± ${msOrNotReported(data.baseline.stddev_ms)} over ${formatNumber(data.baseline.sample_count)} runs${data.baseline.p95_ms != null ? `, p95 ${msOrNotReported(data.baseline.p95_ms)}` : ""}.`
                : "No duration baseline for this test yet."}
            </p>
          )}
          {model.outliers > 0 && (
            <p
              data-duration-outliers={model.outliers}
              className="text-xs text-[var(--status-failed)]"
            >
              {formatNumber(model.outliers)}{" "}
              {model.outliers === 1 ? "run took" : "runs took"} at least{" "}
              {OUTLIER_RATIO}× the{" "}
              {model.reference === "baseline" ? "baseline mean" : "p50"}{" "}
              (ringed).
            </p>
          )}
        </div>
      </details>
    </section>
  );
}

export default function TestHistoryPanel({
  runId,
  testId,
}: {
  runId?: string;
  testId?: string;
}) {
  const { data, isLoading } = useTestCaseHistory(runId, testId);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <LoadingSpinner size="md" />
      </div>
    );
  }

  // `!data` alone was not enough. The body below dereferences `flakiness` and
  // `metadata` unguarded -- `flakiness.classification?.` guards the property
  // but not the object -- so a response that merely LACKS those sections threw
  // "Cannot read properties of undefined (reading 'classification')" and took
  // the whole test-case page into its error boundary ("Something went wrong
  // loading this page"). A panel with no history to show should render its
  // empty state, not destroy the page around it.
  if (!data || !data.flakiness || !data.metadata) {
    return (
      <div className="card py-8 text-center text-sm text-[var(--color-text-muted)]">
        No cross-run history available for this test.
      </div>
    );
  }

  const { history, flakiness, metadata } = data;
  // W03 AIH-05: the AI block per point and `ai_changes` ride the same read
  // (absent on an older server: no strip, nothing claimed).
  const aiHistory = history as TestHistoryAIPoint[];
  const hasAI = aiHistory.some((point) => point.ai);
  const classBadge =
    CLASSIFICATION_BADGE[flakiness.classification?.toUpperCase()] ?? "badge";

  const firstSeen = metadata.first_seen_run_label
    ? `${metadata.first_seen_run_label}${
        formatRunWhen(metadata.first_seen_at)
          ? ` · ${formatRunWhen(metadata.first_seen_at)}`
          : ""
      }`
    : formatRunWhen(metadata.first_seen_at) || null;
  const lastSeen = metadata.last_seen_run_label
    ? `${metadata.last_seen_run_label}${
        formatRunWhen(metadata.last_seen_at)
          ? ` · ${formatRunWhen(metadata.last_seen_at)}`
          : ""
      }`
    : formatRunWhen(metadata.last_seen_at) || null;

  return (
    <div className="card space-y-4 p-3">
      {/* Flakiness + counts header */}
      <div className="flex flex-wrap items-center gap-2">
        {flakiness.is_flaky && (
          <>
            <span className={classBadge}>{flakiness.classification}</span>
            <span className="badge-flaky">
              flaky · {flakiness.failure_rate_pct.toFixed(1)}% fail
            </span>
            <span className="badge bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
              impact {flakiness.impact_score.toFixed(1)}
            </span>
          </>
        )}
        <span className="ml-auto text-xs text-[var(--color-text-muted)]">
          <span className="text-[var(--status-passed)]">
            {flakiness.passed} passed
          </span>
          {" · "}
          <span className="text-[var(--status-failed)]">
            {flakiness.failed} failed
          </span>
          {" · "}
          {flakiness.total_runs} runs ({flakiness.window_days}d)
        </span>
      </div>

      {/* Pass/fail timeline (most-recent-first → render reversed so oldest is left) */}
      {history.length > 0 ? (
        <div className="space-y-1.5">
          <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
            Recent runs (oldest → newest)
          </p>
          <div className="flex flex-wrap items-center gap-1.5">
            {[...history].reverse().map((point, i) => (
              <HistoryDot key={point.run_id ?? `h-${i}`} point={point} />
            ))}
          </div>
        </div>
      ) : (
        <p className="text-xs text-[var(--color-text-muted)]">
          No prior runs for this test.
        </p>
      )}

      {hasAI && (
        <TestAIHistoryStrip
          history={aiHistory}
          changes={(data as TestCaseHistory & TestHistoryAIFields).ai_changes}
          runId={runId}
        />
      )}

      {history.length > 0 && (
        <DurationHistorySection
          data={data as TestCaseHistory & TestHistoryDurationFields}
        />
      )}

      {/* Metadata block */}
      <div className="border-t border-[var(--color-border)] pt-2">
        <MetaRow label="Suite" value={metadata.suite} />
        <MetaRow label="Owner" value={metadata.owner} />
        <MetaRow label="Severity" value={metadata.severity} />
        <MetaRow label="Feature" value={metadata.feature} />
        <MetaRow label="First seen" value={firstSeen} />
        <MetaRow label="Last seen" value={lastSeen} />
        <MetaRow
          label="Created"
          value={formatRunWhen(metadata.created_at) || null}
        />
        <MetaRow
          label="Updated"
          value={formatRunWhen(metadata.updated_at) || null}
        />
      </div>
    </div>
  );
}
