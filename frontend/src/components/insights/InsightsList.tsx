/**
 * The insights rows (W23a FAIL-11, contract §4.1), shared by the Overview card
 * and Inbox › Insights: one row per insight — the severity pill, the server's
 * title over a muted meta line, then the server-built link (`Latest run` for
 * perf) and `Dismiss` — and under the rows one muted line per detector worth a
 * word (not measured, or checked long ago). No row: the empty sentence and the
 * checks that ran (a check that did not run says so, never ✓).
 *
 * Dismiss (QA engineer and above) asks inline, under its row, through
 * `InlineConfirm`: `Dismiss for everyone in <project>?`; a refusal reads inline
 * (`role="alert"`). Every title, label and person is React text; a link is
 * rendered only when `isInsightPath` accepts the server's path (anything else
 * is shown as text).
 */
import { useState } from 'react'
import ScopedLink from '@/components/ui/ScopedLink'
import { usePermissions } from '@/hooks/usePermissions'
import { useNow } from '@/hooks/useNow'
import { refreshInsights } from '@/hooks/useInsights'
import { dismissInsight } from '@/services/insightsService'
import { extractErrorMessage } from '@/services/apiErrors'
import { SEVERITY_STYLE, severityLevel } from '@/components/ui/severity'
import { InlineConfirm } from '@/components/ui/inPage/InlineConfirm'
import { useInlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { isInsightPath, type Insight, type InsightList, type InsightRange } from '@/types/insights'
import {
  DISMISS_FAILED,
  DISMISS_LABEL,
  FLAG_OFF_SENTENCE,
  LATEST_RUN,
  RANGE_LABEL,
  RANGE_OPTIONS,
  detectorLines,
  dismissQuestion,
  emptyChecksLine,
  emptySentence,
  metaLine,
  orderInsights,
  severityWord,
} from './insightsModel'

/** A link-styled action (the app's text link: accent, underline on hover); `.btn-link` does not exist. */
export const INSIGHT_LINK_CLASS = 'text-xs text-[var(--color-accent)] hover:underline'

function serverSentence(err: unknown): string {
  const response = err && typeof err === 'object' ? (err as { response?: { data?: { detail?: unknown } } }).response : undefined
  return extractErrorMessage(response?.data?.detail, DISMISS_FAILED)
}

export interface InsightsListProps {
  projectId: string
  /** For the question: `Dismiss for everyone in <project>?`. */
  projectName: string
  data: InsightList
  range: InsightRange
  /** The card shows at most 5 rows; the tab every row it read. */
  maxRows?: number
  /** Whether dismissed rows are in `data` (Inbox › Show dismissed). */
  includeDismissed?: boolean
}

export default function InsightsList({ projectId, projectName, data, range, maxRows, includeDismissed = false }: InsightsListProps) {
  const now = useNow()
  const { isQaEngineer } = usePermissions()
  const slot = useInlineSlot<string>()
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  // A dismissed row leaves at once (or turns "Dismissed by …" when dismissed rows are shown); the reads revalidate.
  const [dismissed, setDismissed] = useState<Record<string, Insight>>({})

  if (data.enabled === false) {
    return <p data-insights-off="" className="m-0 px-4 py-3 text-sm text-[var(--color-text-secondary)]">{FLAG_OFF_SENTENCE}</p>
  }

  const rows = orderInsights(
    (Array.isArray(data.items) ? data.items : [])
      .map((item) => dismissed[item.id] ?? item)
      .filter((item) => includeDismissed || !item.dismissed_at),
  ).slice(0, maxRows ?? undefined)
  const lines = detectorLines(data.detectors, now)

  async function confirmDismiss(insight: Insight) {
    if (busy) return
    setBusy(true)
    setErrors((e) => {
      const next = { ...e }
      delete next[insight.id]
      return next
    })
    try {
      const saved = await dismissInsight(projectId, insight.id)
      const row = saved && typeof saved === 'object' && (saved as Insight).id === insight.id ? (saved as Insight) : null
      setDismissed((d) => ({ ...d, [insight.id]: row ?? { ...insight, dismissed_at: new Date().toISOString() } }))
      slot.close()
      void refreshInsights(projectId)
    } catch (err) {
      setErrors((e) => ({ ...e, [insight.id]: serverSentence(err) }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div data-insights-list="">
      {rows.length === 0 ? (
        <div data-insights-empty="" className="px-4 py-3">
          <p className="m-0 text-sm text-[var(--color-text)]">{emptySentence(range)}</p>
          <p data-insights-checks="" className="m-0 mt-1 text-xs text-[var(--color-text-muted)] break-words">
            {emptyChecksLine(data.detectors)}
          </p>
        </div>
      ) : (
        <ul className="m-0 list-none divide-y divide-[var(--color-border)] p-0">
          {rows.map((insight) => (
            <InsightRow
              key={insight.id}
              insight={insight}
              now={now}
              canDismiss={isQaEngineer}
              asking={slot.openKey === insight.id}
              busy={busy}
              error={errors[insight.id] ?? null}
              question={dismissQuestion(projectName)}
              onDismiss={() => slot.open(insight.id)}
              onCancel={() => slot.close()}
              onConfirm={() => void confirmDismiss(insight)}
            />
          ))}
        </ul>
      )}
      {lines.length > 0 && (
        <ul data-insights-detectors="" className="m-0 list-none space-y-0.5 border-t border-[var(--color-border)] px-4 py-2">
          {lines.map((line) => (
            <li key={line} className="text-xs text-[var(--color-text-muted)] break-words">{line}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** The `24 h` · `7 d` toggle (component state only: no storage). */
export function InsightsRangeToggle({ value, onChange }: { value: InsightRange; onChange: (range: InsightRange) => void }) {
  return (
    <div role="group" aria-label="Insights range" data-insights-range="" className="inline-flex items-center gap-1">
      {RANGE_OPTIONS.map((range) => (
        <button
          key={range}
          type="button"
          aria-pressed={value === range}
          data-range={range}
          onClick={() => onChange(range)}
          className={value === range ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'}
        >
          {RANGE_LABEL[range]}
        </button>
      ))}
    </div>
  )
}

interface InsightRowProps {
  insight: Insight
  now: number
  canDismiss: boolean
  asking: boolean
  busy: boolean
  error: string | null
  question: string
  onDismiss: () => void
  onCancel: () => void
  onConfirm: () => void
}

/**
 * The server's path as a link when the allow-list accepts it, else its label as text. A `ScopedLink`: `/flaky` is
 * a single-project route (`config/routeScope.ts`); the list only renders for one project, so no hint shows.
 */
function InsightLink({ path, label }: { path: string | null | undefined; label: string }) {
  if (path && isInsightPath(path)) {
    return <ScopedLink to={path} className={INSIGHT_LINK_CLASS} hintInTitleOnly>{label}</ScopedLink>
  }
  return <span data-insight-link-text="" className="text-xs text-[var(--color-text-muted)]">{label}</span>
}

function InsightRow({ insight, now, canDismiss, asking, busy, error, question, onDismiss, onCancel, onConfirm }: InsightRowProps) {
  const style = SEVERITY_STYLE[severityLevel(insight.severity) ?? 'P3']
  const linkLabel = insight.link?.label || insight.link?.path || ''
  return (
    <li data-insight-row={insight.id} data-insight-kind={insight.kind} data-insight-severity={insight.severity} className="px-4 py-3">
      <div className="flex items-start gap-3">
        <span
          data-insight-pill=""
          className="mt-0.5 inline-flex min-w-[60px] flex-none justify-center rounded-full border px-1.5 text-xs font-medium leading-5"
          style={{ color: style.fg, background: style.bg, borderColor: style.bd }}
        >
          {severityWord(insight.severity)}
        </span>
        <div className="min-w-0 flex-1 break-words">
          <p data-insight-title="" className="m-0 text-sm text-[var(--color-text)]">{insight.title || insight.subject_label}</p>
          <p data-insight-meta="" className="m-0 mt-0.5 text-xs text-[var(--color-text-muted)]">{metaLine(insight, now)}</p>
        </div>
        <div data-insight-actions="" className="flex flex-none items-center gap-3 whitespace-nowrap">
          {linkLabel && <InsightLink path={insight.link?.path} label={linkLabel} />}
          {insight.run_path && <InsightLink path={insight.run_path} label={LATEST_RUN} />}
          {canDismiss && !insight.dismissed_at && (
            <button type="button" data-insight-dismiss="" className="btn-ghost btn-sm" onClick={onDismiss} disabled={asking || busy}>
              {DISMISS_LABEL}
            </button>
          )}
        </div>
      </div>
      {asking && (
        <div className="mt-2">
          <InlineConfirm
            message={question}
            confirmLabel={DISMISS_LABEL}
            busy={busy}
            onConfirm={onConfirm}
            onCancel={onCancel}
            testId="insight-dismiss-confirm"
          />
        </div>
      )}
      {error && <p role="alert" data-insight-error="" className="m-0 mt-1 text-xs text-[var(--ui-danger-ink)]">{error}</p>}
    </li>
  )
}
