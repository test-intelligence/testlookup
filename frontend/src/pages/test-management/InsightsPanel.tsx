import type { ReactNode } from 'react'
import { FileText } from 'lucide-react'
import SidePanel from '@/components/ui/SidePanel'
import { useNow } from '@/hooks/useNow'
import type { ManagedTestCase } from '@/types/test-management'

/**
 * The Cases tab's "Insights" drawer (UX redesign P4 item 7): what used to be
 * the right rail beside the cases table, so the table has the page's width.
 * Every card is derived from data the tab already holds (the library-health
 * roll and the audit roll); nothing here is fetched by the drawer itself.
 */

export type HealthTag = 'Healthy' | 'Needs attention' | 'At risk'

export interface LibraryHealth {
  healthScore: number
  healthTag: HealthTag
  healthTone: string
  totalCases: number
  reviewCount: number
  staleCount: number
  deprecatedInActive: number
  oldestReviewDays: number
  activeCount: number
  automatedPct: number
  avgAgeDays: number
  olderThan180Pct: number
  /** Set when the rates describe a capped sample rather than the catalog. */
  statBasis: string | null
  /** When the cases payload arrived ("2m ago"). */
  refreshedAt: string
}

export interface StrategyGap { severity: 'critical' | 'warn'; title: string; sub: string; pill: string }
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

function HealthStat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="text-[10px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        {label}
      </div>
      <div className="font-bold tabular-nums leading-[1.1] text-[var(--color-text)]" style={{ fontSize: 19, letterSpacing: '-0.01em' }}>
        {value}
      </div>
      {sub && <div className="text-[10.5px] text-[var(--color-text-muted)]">{sub}</div>}
    </div>
  )
}

/**
 * The library-health breakdown behind the Cases tab's banner: the score and
 * its inputs (the banner carries the score and four facts). The empty-catalog
 * explanation lives here too: a panel of zeros reads exactly like a failed
 * fetch (user report 2026-05-15), so it says WHY there is nothing to grade.
 */
export function LibraryHealthCard(p: LibraryHealth) {
  const isEmptyCatalog = p.totalCases === 0
  return (
    <CasesCardShell title="Library health">
      <div className="px-4 py-3.5 space-y-3">
        {isEmptyCatalog ? (
          <p className="text-[12.5px] m-0" style={{ color: 'var(--color-text-secondary)' }}>
            <strong className="text-[var(--color-text)]">No authored test cases yet.</strong>{' '}
            Library health summarises the <strong>authored</strong> test-case catalog — not the
            execution rows ingested from CI runs — and renders once you have at least one authored
            case. Until then, the Suites tab shows the executions that have already streamed in.
          </p>
        ) : (
          <>
            <div className="flex items-baseline gap-3">
              <span className="font-bold tabular-nums leading-none" style={{ fontSize: 30, color: p.healthTone, letterSpacing: '-0.02em' }}>
                {p.healthScore}
              </span>
              <span className="text-[13px] text-[var(--color-text-muted)] font-medium">/ 100</span>
              <span className="text-[12px] font-semibold" style={{ color: p.healthTone }}>{p.healthTag}</span>
            </div>
            <p className="text-[12.5px] m-0" style={{ color: 'var(--color-text-secondary)' }}>
              <strong style={{ color: 'var(--color-text)' }}>{p.totalCases}</strong> case{p.totalCases === 1 ? '' : 's'} · <strong style={{ color: 'var(--color-text)' }}>{p.reviewCount}</strong> awaiting review, <strong style={{ color: 'var(--color-text)' }}>{p.staleCount}</strong> stale drafts over 30 days, <strong style={{ color: 'var(--color-text)' }}>{p.deprecatedInActive}</strong> deprecated still in active suites.
            </p>
            <div className="grid grid-cols-3 gap-3">
              <HealthStat label="Active" value={p.activeCount} />
              <HealthStat label="Automated" value={`${p.automatedPct}%`} sub="target 60%" />
              {/* A fourth stat, "Req coverage", once read a constant 87% for
                  every project. There is no requirements data to compute a
                  real one from, and an invented number is worse than none. */}
              <HealthStat label="Avg age" value={`${p.avgAgeDays}d`} sub={`${p.olderThan180Pct}% >180d`} />
            </div>
          </>
        )}
        {p.statBasis && (
          <p className="text-[11px] m-0 text-[var(--color-text-muted)]" data-testid="library-health-stat-basis-drawer">
            {p.statBasis}
          </p>
        )}
        {/* This row once read "Library indexed against prd:current ·
            main@HEAD": neither ref exists. The refresh time is real (it comes
            from useDataFreshness), so it is what remains. */}
        <p className="text-[11px] m-0 text-[var(--color-text-muted)]">Library refreshed {p.refreshedAt}</p>
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
              <Legend color="color-mix(in srgb, var(--status-passed) 55%, transparent)" label={`Automated · ${pct(auto)}%`} />
              <Legend color="color-mix(in srgb, var(--color-accent) 50%, transparent)" label={`Manual · ${pct(manual)}%`} />
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

export function ReviewQueueCard({ rows, onPick }: { rows: ManagedTestCase[]; onPick: (c: ManagedTestCase) => void }) {
  const now = useNow()  // captured at mount — avoids impure Date.now() in render
  return (
    <CasesCardShell title={`Review queue · ${rows.length}`}>
      {rows.length === 0 ? (
        <div className="px-4 py-6 text-center text-[12.5px] text-[var(--color-text-secondary)]">
          Caught up — review queue is empty.
        </div>
      ) : (
        <div>
          {rows.map(c => {
            const days = Math.floor((now - new Date(c.updated_at).getTime()) / 86400000)
            const ageColor = days >= 7 ? 'var(--status-failed)' : 'var(--color-text-muted)'
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => onPick(c)}
                className="grid items-center gap-2.5 w-full text-left hover:bg-[var(--color-bg-hover)] transition-colors"
                style={{
                  gridTemplateColumns: '1fr auto',
                  padding: '10px 16px',
                  borderBottom: '1px solid var(--color-border)',
                }}
              >
                <div className="min-w-0">
                  <div className="text-[12.5px] m-0 flex items-center gap-1.5">
                    <code className="font-mono text-[11px]" style={{ color: 'var(--color-accent)' }}>TC-{c.id.slice(0, 6).toUpperCase()}</code>
                    <span className="text-[var(--color-text)] truncate font-medium">{c.title}</span>
                  </div>
                  <div className="text-[10.5px] text-[var(--color-text-muted)] truncate mt-0.5">
                    {c.assignee_id ? c.assignee_id.slice(0, 8) : 'Unassigned'} · requested by {c.author_id ? c.author_id.slice(0, 8) : '—'}
                  </div>
                </div>
                <span className="text-[11px] tabular-nums" style={{ color: ageColor }}>
                  {days < 1 ? `${Math.max(1, Math.floor((now - new Date(c.updated_at).getTime()) / 3600000))}h` : `${days}d`}
                </span>
              </button>
            )
          })}
        </div>
      )}
    </CasesCardShell>
  )
}

export function StrategyGapsCard({ gaps }: { gaps: StrategyGap[] }) {
  return (
    <CasesCardShell title="Strategy gaps">
      {gaps.length === 0 ? (
        <div className="px-4 py-6 text-center text-[12.5px] text-[var(--color-text-secondary)]">
          No gaps detected against current strategy.
        </div>
      ) : (
        <div>
          {gaps.map((g, i) => (
            <div
              key={i}
              className="grid items-center gap-2.5"
              style={{
                gridTemplateColumns: '1fr auto',
                padding: '9px 16px',
                borderBottom: i < gaps.length - 1 ? '1px solid var(--color-border)' : '0',
              }}
            >
              <div className="min-w-0">
                <div className="text-[12px] font-medium text-[var(--color-text)]">{g.title}</div>
                <div className="text-[10.5px] text-[var(--color-text-muted)]">{g.sub}</div>
              </div>
              <span
                className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10.5px] font-semibold"
                style={{
                  background: g.severity === 'critical' ? 'color-mix(in srgb, var(--status-failed) 14%, transparent)' : 'color-mix(in srgb, var(--status-broken) 14%, transparent)',
                  border: g.severity === 'critical' ? '1px solid color-mix(in srgb, var(--status-failed) 30%, transparent)' : '1px solid color-mix(in srgb, var(--status-broken) 30%, transparent)',
                  color: g.severity === 'critical' ? 'var(--status-failed)' : 'var(--status-broken)',
                }}
              >
                {g.pill}
              </span>
            </div>
          ))}
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
              : /deprecate|delete/.test(action)      ? { bg: 'rgba(120,113,108,0.16)', fg: '#a8a29e' }
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

/** The drawer itself: the library-health breakdown, then the former rail's cards. */
export default function InsightsPanel({
  open, onClose, children,
}: { open: boolean; onClose: () => void; children: ReactNode }) {
  return (
    <SidePanel open={open} onClose={onClose} title="Insights" width={440} closeLabel="Close panel">
      <div data-insights="" className="flex flex-col gap-3.5">
        {children}
      </div>
    </SidePanel>
  )
}
