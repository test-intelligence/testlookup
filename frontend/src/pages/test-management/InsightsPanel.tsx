import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { FileText } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import MetricCard from '@/components/ui/MetricCard'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import { useNow } from '@/hooks/useNow'
import LibraryCaseListRows from '@/pages/test-management/LibraryCaseListRows'
import {
  componentLine,
  FLAKY_ACTIVE_TITLE,
  HEALTH_LOAD_ERROR_TEXT,
  HEALTH_NEEDS_PROJECT_TEXT,
  NO_OWNER_TITLE,
  automatedPctText,
} from '@/pages/test-management/libraryHealthModel'
import type { LibraryCaseList, LibraryHealth, ReviewQueueSummary, StrategyGapRead } from '@/types/test-library'

/**
 * The Cases tab's "Insights" (UX redesign P4 item 7): what used to be the
 * right rail beside the cases table, so the table has the page's width.
 * Every card reads data the tab already holds — since W13 TCL-03 the
 * SERVER's library health (`GET /test-management/health`, read once on load
 * over the WHOLE library) and the audit roll; nothing here is fetched by the
 * section itself (opening it sends nothing). Only **Show all** of a Strategy
 * gaps list pages more, through its own hook (`LibraryCaseListRows`).
 *
 * While the answer loads, after it failed, or in All Projects, every card still
 * renders its heading, and its body says which — never zeros.
 *
 * It renders IN THE PAGE (W27b PAN-05): a read-only section named `Insights`
 * right under the health banner, above the filter bar, closed by default, its
 * cards in two columns from `lg` (each wider than the 440 px drawer they were
 * drawn for). X `Close panel` and Escape inside it close it.
 */

/** Where a card's health data comes from: the one read of the page. */
export interface HealthSource {
  projectId: string | null
  health: LibraryHealth | undefined
  error?: unknown
}

export interface RecentEvent { id: string; action?: string; actor_name?: string; entity_id?: string; created_at: string; event_type?: string }

export function CasesCardShell({
  title, rightSlot, children,
}: { title: ReactNode; rightSlot?: ReactNode; children?: ReactNode }) {
  return (
    <div
      className="overflow-hidden rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
    >
      <div
        className="flex items-center justify-between gap-2.5 px-4 py-3"
        style={{ borderBottom: '1px solid var(--color-border)' }}
      >
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">{title}</h3>
        {rightSlot && <div className="flex items-center gap-2.5 text-[12px] text-[var(--color-text-muted)]">{rightSlot}</div>}
      </div>
      {children}
    </div>
  )
}

/**
 * A card body while there is no health answer: All Projects (nothing asked),
 * a failed read, or one in flight. Never zeros.
 */
export function HealthPending({ projectId, error }: { projectId: string | null; error?: unknown }) {
  if (!projectId || error) {
    return (
      <p className="px-4 py-6 m-0 text-center text-sm text-[var(--color-text-secondary)]" data-health-pending="">
        {!projectId ? HEALTH_NEEDS_PROJECT_TEXT : HEALTH_LOAD_ERROR_TEXT}
      </p>
    )
  }
  return (
    <div className="flex justify-center py-6" data-health-pending="">
      <LoadingSpinner size="md" />
    </div>
  )
}

/** A health-backed card's shell with the pending body (its heading always rendered). */
export function HealthPendingCard({ title, projectId, error }: { title: string } & Omit<HealthSource, 'health'>) {
  return (
    <CasesCardShell title={title}>
      <HealthPending projectId={projectId} error={error} />
    </CasesCardShell>
  )
}

const TAG_INK: Record<string, string> = {
  Healthy: 'text-[var(--ui-success-ink)]',
  'Needs attention': 'text-[var(--ui-warning-ink)]',
  'At risk': 'text-[var(--ui-danger-ink)]',
}

export interface LibraryHealthCardProps extends HealthSource {
  /** = `health.total_cases`, the non-retired authored cases. */
  totalCases: number
  /** When the health answer arrived ("2m ago"). */
  refreshedAt: string
  /** The automation-target editor (QA lead+, one project; G-E), placed in the card. */
  targetEditor?: ReactNode
}

/**
 * The library-health breakdown behind the Cases tab's banner: the server's
 * score and tag, its inputs, and one line per component with BOTH numbers.
 * The empty-catalog explanation lives here too: a panel of zeros reads
 * exactly like a failed load (user report 2026-05-15), so it says WHY there
 * is nothing to grade.
 */
export function LibraryHealthCard(p: LibraryHealthCardProps) {
  const h = p.health
  if (!h) return <HealthPendingCard title="Library health" projectId={p.projectId} error={p.error} />
  const ink = (h.tag && TAG_INK[h.tag]) || 'text-[var(--color-text)]'
  const strong = (n: ReactNode) => <strong className="text-[var(--color-text)]">{n}</strong>
  return (
    <CasesCardShell title="Library health">
      <div className="px-4 py-3.5 space-y-3">
        {!h.measured ? (
          <p className="text-sm m-0 text-[var(--color-text-secondary)]">
            <strong className="text-[var(--color-text)]">No authored test cases yet.</strong>{' '}
            Library health summarises the <strong>authored</strong> test-case catalog — not the
            execution rows ingested from CI runs — and renders once you have at least one authored
            case. Until then, the Suites tab shows the executions that have already streamed in.
          </p>
        ) : (
          <>
            <div className="flex items-baseline gap-3">
              <span className={`text-3xl font-bold tabular-nums leading-none ${ink}`}>{h.score ?? '—'}</span>
              <span className="text-sm text-[var(--color-text-muted)] font-medium">/ 100</span>
              {h.tag && <span className={`text-xs font-semibold ${ink}`}>{h.tag}</span>}
            </div>
            <p className="text-sm m-0 text-[var(--color-text-secondary)]">
              {strong(p.totalCases)} case{p.totalCases === 1 ? '' : 's'} · {strong(h.review_queue.total)} awaiting review, {strong(h.stale_drafts)} stale drafts over 30 days, {strong(h.retired_in_open_plans)} deprecated still in active plans.
            </p>
            <div className="grid grid-cols-3 gap-3">
              <MetricCard size="sm" title="Active" metric={{ value: h.active_cases }} />
              <MetricCard size="sm" title="Automated" metric={{ value: automatedPctText(h) }} caption={`target ${h.automation_target_pct}%`} />
              <MetricCard
                size="sm"
                title="Avg age"
                metric={{ value: h.avg_age_days == null ? '—' : `${Math.round(h.avg_age_days)}d` }}
                caption={h.older_than_180_pct == null ? undefined : `${Math.round(h.older_than_180_pct)}% >180d`}
              />
            </div>
            {p.targetEditor}
            <ul className="m-0 p-0 list-none space-y-0.5" data-health-components="">
              {h.components.map((c) => (
                <li key={c.key} className="text-xs text-[var(--color-text-secondary)]" data-health-component={c.key}>
                  {componentLine(c)}
                </li>
              ))}
            </ul>
          </>
        )}
        {/* This row once read "Library indexed against prd:current ·
            main@HEAD": neither ref exists. The refresh time is real (it comes
            from useDataFreshness over the health answer), so it is what remains. */}
        <p className="text-xs m-0 text-[var(--color-text-muted)]">Library refreshed {p.refreshedAt}</p>
      </div>
    </CasesCardShell>
  )
}

// ── Automation coverage card ────────────────────────────────────────────
//
// Was "Coverage by requirement", reading "<N> requirements tracked · <M>
// covered (87%)". TestLookup has no requirements: N was the test-case count
// relabelled, and the uncovered bucket it was measured against was 15% of that
// same count, invented in the page body. The percentage could therefore never
// be anything but 87%, and the card named a domain the product does not model.
//
// Automated-vs-manual is the split the rows actually carry, so that is what
// this card now reports, under a title that says so.
export function AutomationCoverageCard({ auto, manual }: { auto: number; manual: number }) {
  const total = auto + manual
  const pct = (n: number) => (total > 0 ? Math.round((n / total) * 100) : 0)
  // W28 §2.7 (b): each half rounded on its own could add to 99 or 101
  // ("Automated · 38% / Manual · 63%"); the manual share is the rest of 100.
  const autoPct = pct(auto)
  const manualPct = total > 0 ? 100 - autoPct : 0
  return (
    <CasesCardShell title="Automation coverage">
      <div className="px-4 py-3.5">
        {total === 0 ? (
          <p className="text-[12px] text-[var(--color-text-muted)] m-0">
            No authored cases yet — nothing to split.
          </p>
        ) : (
          <>
            <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-3">
              <strong className="text-[var(--color-text)] font-semibold">{total}</strong> authored case{total === 1 ? '' : 's'} · <strong className="text-[var(--color-text)] font-semibold">{auto}</strong> automated (<strong className="text-[var(--color-text)] font-semibold">{pct(auto)}%</strong>)
            </p>
            <div
              className="flex h-7 rounded-md overflow-hidden border"
              style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-secondary)' }}
              role="img"
              aria-label={`Automation coverage: ${auto} automated, ${manual} manual`}
            >
              {auto > 0 && (
                <div className="flex items-center justify-center text-[10.5px] font-semibold tabular-nums" style={{ flex: auto, background: 'color-mix(in srgb, var(--status-passed) 55%, transparent)', color: 'white' }}>
                  {auto} auto
                </div>
              )}
              {manual > 0 && (
                <div className="flex items-center justify-center text-[10.5px] font-semibold tabular-nums" style={{ flex: manual, background: 'color-mix(in srgb, var(--color-accent) 50%, transparent)', color: 'white' }}>
                  {manual} manual
                </div>
              )}
            </div>
            <div className="flex flex-wrap gap-3 mt-2 text-[11px] text-[var(--color-text-muted)]">
              <Legend color="color-mix(in srgb, var(--status-passed) 55%, transparent)" label={`Automated · ${autoPct}%`} />
              <Legend color="color-mix(in srgb, var(--color-accent) 50%, transparent)" label={`Manual · ${manualPct}%`} />
            </div>
          </>
        )}
      </div>
    </CasesCardShell>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <i aria-hidden className="inline-block w-2 h-2 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  )
}

/**
 * The oldest five cases awaiting review, from the health answer (names
 * resolved, oldest first), and the queue's TOTAL; the Approvals tab is the
 * whole queue. A row opens its case (`?case=`; the page fetches it when it is
 * not on the current page).
 */
export function ReviewQueueCard({ projectId, health, error, onPick }: HealthSource & { onPick: (caseId: string) => void }) {
  const queue: ReviewQueueSummary | undefined = health?.review_queue
  if (!queue) return <HealthPendingCard title="Review queue" projectId={projectId} error={error} />
  return (
    <CasesCardShell title={`Review queue · ${queue.total}`}>
      {queue.items.length === 0 ? (
        <div className="px-4 py-6 text-center text-sm text-[var(--color-text-secondary)]">
          Caught up — review queue is empty.
        </div>
      ) : (
        <div>
          {queue.items.map((row) => (
            <button
              key={row.id}
              type="button"
              onClick={() => onPick(row.id)}
              data-review-queue-row={row.id}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2.5 w-full text-left px-4 py-2.5 border-b border-[var(--color-border)] hover:bg-[var(--color-bg-hover)] transition-colors"
            >
              <div className="min-w-0">
                <div className="text-sm m-0 flex items-center gap-1.5">
                  <code className="font-mono text-xs text-[var(--color-accent-ink)]">TC-{row.id.slice(0, 6).toUpperCase()}</code>
                  <span className="text-[var(--color-text)] truncate font-medium">{row.title}</span>
                </div>
                <div className="text-xs text-[var(--color-text-muted)] truncate mt-0.5">{row.owner ?? 'Unassigned'}</div>
              </div>
              <span className={`text-xs tabular-nums ${(row.age_days ?? 0) >= 7 ? 'text-[var(--ui-danger-ink)]' : 'text-[var(--color-text-muted)]'}`}>
                {row.age_days == null ? '—' : `${row.age_days} day${row.age_days === 1 ? '' : 's'}`}
              </span>
            </button>
          ))}
          {queue.total > queue.items.length && (
            <p className="m-0 px-4 py-2.5 text-xs text-[var(--color-text-muted)]">
              <Link to="/test-management?tab=approvals" className="text-[var(--color-accent-ink)] hover:underline">
                Open the Approvals tab
              </Link>{' '}
              for the other {queue.total - queue.items.length}.
            </p>
          )}
        </div>
      )}
    </CasesCardShell>
  )
}

const PILL_BY_SEVERITY: Record<string, string> = {
  critical: 'bg-[var(--ui-danger-bg)] border-[var(--ui-danger-bd)] text-[var(--ui-danger-ink)]',
  warn: 'bg-[var(--ui-warning-bg)] border-[var(--ui-warning-bd)] text-[var(--ui-warning-ink)]',
}

type ListKind = 'flaky_active' | 'no_owner'
const LIST_TITLES: Record<ListKind, string> = { flaky_active: FLAKY_ACTIVE_TITLE, no_owner: NO_OWNER_TITLE }

interface GapRow { kind: string; title: string; detail: string; pill: string | null; severity: string }

/**
 * The server's strategy gaps in its order. The two case lists ("Flaky and
 * active", "No owner") carry their rows in place; an UNMEASURED list has no
 * gap to show a count for, so it gets a row of its own saying why (never `0`).
 */
function gapRows(health: LibraryHealth): GapRow[] {
  const rows: GapRow[] = health.strategy_gaps.map((g: StrategyGapRead) => ({
    kind: g.kind, title: g.title, detail: g.detail, pill: g.pill, severity: g.severity,
  }))
  for (const kind of ['flaky_active', 'no_owner'] as const) {
    const list: LibraryCaseList = health[kind]
    if (list.measured === false && !rows.some((r) => r.kind === kind)) {
      rows.push({ kind, title: LIST_TITLES[kind], detail: '', pill: null, severity: 'warn' })
    }
  }
  return rows
}

export function StrategyGapsCard({ projectId, health, error, onOpenCase }: HealthSource & { onOpenCase: (caseId: string) => void }) {
  if (!health) return <HealthPendingCard title="Strategy gaps" projectId={projectId} error={error} />
  const rows = gapRows(health)
  return (
    <CasesCardShell title="Strategy gaps">
      {rows.length === 0 ? (
        <div className="px-4 py-6 text-center text-sm text-[var(--color-text-secondary)]">
          No gaps detected against current strategy.
        </div>
      ) : (
        <div>
          {rows.map((g) => {
            const listKind = g.kind === 'flaky_active' || g.kind === 'no_owner' ? (g.kind as ListKind) : null
            return (
              <div key={g.kind} data-strategy-gap={g.kind} className="px-4 py-2.5 border-b border-[var(--color-border)] last:border-b-0">
                <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2.5">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-[var(--color-text)]">{g.title}</div>
                    {g.detail && <div className="text-xs text-[var(--color-text-muted)]">{g.detail}</div>}
                  </div>
                  {g.pill && (
                    <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full border text-xs font-semibold ${PILL_BY_SEVERITY[g.severity] ?? PILL_BY_SEVERITY.warn}`}>
                      {g.pill}
                    </span>
                  )}
                </div>
                {listKind && projectId && (
                  <LibraryCaseListRows projectId={projectId} kind={listKind} list={health[listKind]} onOpenCase={onOpenCase} />
                )}
              </div>
            )
          })}
        </div>
      )}
    </CasesCardShell>
  )
}

export function RecentActivityCard({ events }: { events: RecentEvent[] }) {
  const now = useNow()  // captured at mount — avoids impure Date.now() in render
  return (
    <CasesCardShell title="Recent activity">
      {events.length === 0 ? (
        <div className="px-4 py-6 text-center text-[12.5px] text-[var(--color-text-secondary)]">
          No recent activity.
        </div>
      ) : (
        <div>
          {events.map((e, i) => {
            const ms = now - new Date(e.created_at).getTime()
            const min = Math.max(1, Math.floor(ms / 60000))
            const ageLabel = min < 60 ? `${min}m` : min < 1440 ? `${Math.floor(min / 60)}h` : `${Math.floor(min / 1440)}d`
            const action = (e.action ?? e.event_type ?? 'updated').toLowerCase()
            const palette = /create|new/.test(action) ? { bg: 'color-mix(in srgb, var(--status-passed) 14%, transparent)',    fg: 'var(--status-passed)' }
              : /review|approve/.test(action)        ? { bg: 'color-mix(in srgb, var(--status-broken) 14%, transparent)',  fg: 'var(--status-broken)' }
              : /deprecate|delete/.test(action)      ? { bg: 'color-mix(in srgb, var(--color-text-muted) 12%, transparent)', fg: 'var(--color-text-secondary)' }
              : /ai|generate/.test(action)           ? { bg: 'color-mix(in srgb, var(--status-flaky) 14%, transparent)', fg: 'var(--status-flaky)' }
              : { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)' }
            return (
              <div
                key={e.id || i}
                className="grid items-center gap-2.5"
                style={{
                  gridTemplateColumns: '22px 1fr auto',
                  padding: '9px 16px',
                  borderBottom: i < events.length - 1 ? '1px solid var(--color-border)' : '0',
                }}
              >
                <span className="inline-flex items-center justify-center rounded-full" style={{ width: 22, height: 22, background: palette.bg, color: palette.fg }}>
                  <FileText className="h-3 w-3" />
                </span>
                <div className="text-[12px] text-[var(--color-text-secondary)] truncate">
                  <strong className="text-[var(--color-text)] font-medium">{e.actor_name ?? 'Someone'}</strong>
                  {' '}
                  {action.replace(/_/g, ' ')}
                  {' '}
                  {e.entity_id && (
                    <code className="font-mono text-[11px]" style={{ color: 'var(--color-accent)' }}>
                      TC-{e.entity_id.slice(0, 6).toUpperCase()}
                    </code>
                  )}
                </div>
                <span className="text-[10.5px] tabular-nums text-[var(--color-text-muted)]">{ageLabel}</span>
              </div>
            )
          })}
        </div>
      )}
    </CasesCardShell>
  )
}

/** The section itself: the library-health breakdown, then the former rail's cards. Nothing while closed. */
export default function InsightsPanel({
  open, onClose, children,
}: { open: boolean; onClose: () => void; children: ReactNode }) {
  if (!open) return null
  return (
    <InlineFormSection as="aside" title="Insights" onClose={onClose} closeLabel="Close panel">
      <div data-insights="" className="grid gap-3.5 lg:grid-cols-2">
        {children}
      </div>
    </InlineFormSection>
  )
}
