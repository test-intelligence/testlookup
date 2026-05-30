/**
 * Right-rail panels for /releases:
 *   - ShippingThisWeek — releases due in the next 7 days, with a date tile
 *   - AgingSignals — stale releases (blocked, planning idle, UAT pending)
 *   - CompliancePacks — signed audit ZIPs (placeholder rows w/ deterministic SHA prefix)
 *   - RecentActivity — synthesised from release update timestamps
 *
 * Every panel is client-derived from the same ``DerivedRelease[]`` the
 * portfolio list uses. No new endpoints needed.
 */
import { clsx } from 'clsx'
import {
  ArrowRight,
  Clock,
  Download,
  FileText,
  ShieldCheck,
} from 'lucide-react'
import type { DerivedRelease } from './types'

// ── Helpers ────────────────────────────────────────────────────────────────

function startOfDay(d: Date) { const x = new Date(d); x.setHours(0, 0, 0, 0); return x }
function daysBetween(a: Date, b: Date) {
  return Math.round((startOfDay(a).getTime() - startOfDay(b).getTime()) / (24 * 3600 * 1000))
}

function PanelShell({
  title, count, action, children,
}: {
  title: string
  count?: number | string
  action?: { label: string; onClick: () => void }
  children: React.ReactNode
}) {
  return (
    <section
      className="rounded-xl border bg-[var(--color-bg-card)] overflow-hidden"
      style={{ borderColor: 'var(--color-border)' }}
    >
      <header className="flex items-center justify-between px-3.5 py-2.5 border-b" style={{ borderColor: 'var(--color-border)' }}>
        <h4 className="m-0 text-[13px] font-semibold text-[var(--color-text)]">{title}</h4>
        <div className="flex items-center gap-3">
          {count != null && (
            <span className="text-[11px] text-[var(--color-text-muted)]">{count}</span>
          )}
          {action && (
            <button
              type="button"
              onClick={action.onClick}
              className="text-[11.5px] text-[var(--color-accent)] hover:underline"
            >
              {action.label}
            </button>
          )}
        </div>
      </header>
      <div>{children}</div>
    </section>
  )
}

function EmptyRow({ children }: { children: React.ReactNode }) {
  return <div className="px-3.5 py-3 text-[12px] text-[var(--color-text-muted)]">{children}</div>
}

// ── Shipping this week ─────────────────────────────────────────────────────

interface ShippingItem { release: DerivedRelease; relDays: number; label: string }

export function ShippingThisWeek({ releases, onOpen }: { releases: DerivedRelease[]; onOpen: (id: string) => void }) {
  const today = new Date()
  const items: ShippingItem[] = releases
    .filter(r => r.dueAt && r.stage === 'in_progress')
    .map(r => {
      const d = new Date(r.dueAt!)
      const rel = daysBetween(d, today)
      const label = rel === 0 ? 'Today' : rel === 1 ? 'Tmr' : d.toLocaleDateString(undefined, { weekday: 'short' })
      return { release: r, relDays: rel, label }
    })
    .filter(it => it.relDays >= 0 && it.relDays <= 7)
    .sort((a, b) => a.relDays - b.relDays)

  return (
    <PanelShell title="Shipping this week" count={items.length || undefined}>
      {items.length === 0 ? (
        <EmptyRow>No releases due in the next 7 days.</EmptyRow>
      ) : (
        <ul className="m-0 p-0 list-none divide-y" style={{ borderColor: 'var(--color-border)' }}>
          {items.map(({ release: r, label }) => {
            const dot =
              r.gate.decision === 'go' ? '#22c55e'
              : r.gate.decision === 'conditional' ? '#eab308'
              : r.gate.decision === 'no_go' ? '#ef4444'
              : 'var(--color-text-muted)'
            return (
              <li key={r.id} className="flex items-center gap-3 px-3.5 py-2.5">
                <div
                  className="w-[42px] h-[42px] rounded-md border flex flex-col items-center justify-center shrink-0"
                  style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-secondary)' }}
                >
                  <div className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
                    {label}
                  </div>
                  <div className="text-[16px] font-semibold text-[var(--color-text)] leading-none mt-0.5 tabular-nums">
                    {r.dueAt ? new Date(r.dueAt).getDate() : '—'}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => onOpen(r.id)}
                  className="min-w-0 overflow-hidden text-left flex-1"
                  style={{ minWidth: 0 }}
                >
                  <div className="text-[12.5px] font-medium text-[var(--color-text)] truncate">{r.name}</div>
                  <div className="text-[11.5px] text-[var(--color-text-muted)] truncate">
                    {r.version ? `${r.version} · ` : ''}{r.blockers.length} blocker{r.blockers.length === 1 ? '' : 's'}
                  </div>
                </button>
                <span aria-hidden className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: dot }} />
              </li>
            )
          })}
        </ul>
      )}
    </PanelShell>
  )
}

// ── Aging signals ──────────────────────────────────────────────────────────

interface AgingSignal {
  release: DerivedRelease
  severity: 'warn' | 'red'
  reason: string
  ageDays: number
}

export function AgingSignals({ releases, onOpen }: { releases: DerivedRelease[]; onOpen: (id: string) => void }) {
  const today = new Date()
  const signals: AgingSignal[] = []
  for (const r of releases) {
    const lastTouched = new Date(r.source.updated_at || r.source.created_at)
    const age = daysBetween(today, lastTouched)
    if (r.stage === 'in_progress' && r.blockers.some(b => b.severity === 'red') && age > 3) {
      signals.push({ release: r, severity: 'red', reason: 'Blocked > 3 days', ageDays: age })
    } else if (r.stage === 'planning' && age > 7) {
      signals.push({ release: r, severity: 'warn', reason: 'Planning idle > 7 days', ageDays: age })
    } else if (r.stage === 'in_progress' && r.phases.find(p => p.key === 'uat' && p.state === 'active') && age > 2) {
      signals.push({ release: r, severity: 'warn', reason: 'UAT pending > 2 days', ageDays: age })
    } else if (r.stage === 'in_progress' && r.phases.some(p => p.state === 'active') && age > 3) {
      signals.push({ release: r, severity: 'warn', reason: 'Phase stalled > 3 days', ageDays: age })
    }
  }

  return (
    <PanelShell title="Aging signals" count={signals.length || undefined}>
      {signals.length === 0 ? (
        <EmptyRow>No aging signals in the current portfolio.</EmptyRow>
      ) : (
        <ul className="m-0 p-0 list-none divide-y" style={{ borderColor: 'var(--color-border)' }}>
          {signals.map(({ release: r, severity, reason, ageDays }) => (
            <li key={r.id} className="flex items-center gap-3 px-3.5 py-2.5">
              <button
                type="button"
                onClick={() => onOpen(r.id)}
                className="min-w-0 overflow-hidden text-left flex-1"
                style={{ minWidth: 0 }}
              >
                <div className="text-[12.5px] font-medium text-[var(--color-text)] whitespace-nowrap overflow-hidden text-ellipsis">
                  {r.name}{r.version && ` ${r.version}`}
                </div>
                <div className="text-[11.5px] text-[var(--color-text-muted)]">{reason}</div>
              </button>
              <span
                className={clsx(
                  'shrink-0 inline-flex items-center text-[10.5px] font-semibold tabular-nums px-1.5 py-0.5 rounded-full border',
                )}
                style={{
                  color: severity === 'red' ? '#fca5a5' : '#fcd34d',
                  background: severity === 'red' ? 'rgba(239,68,68,0.10)' : 'rgba(234,179,8,0.10)',
                  borderColor: severity === 'red' ? 'rgba(239,68,68,0.40)' : 'rgba(234,179,8,0.40)',
                }}
              >
                {ageDays}d
              </span>
            </li>
          ))}
        </ul>
      )}
    </PanelShell>
  )
}

// ── Compliance packs ───────────────────────────────────────────────────────

/** Deterministic 8-char sha256 prefix from a release id — keeps the rendering stable. */
function fakeSha(id: string): string {
  let hash = 0
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0
  return hash.toString(16).padStart(8, '0').slice(0, 8)
}

export function CompliancePacks({ releases }: { releases: DerivedRelease[] }) {
  // Released → "Download" (signed pack); In-progress conditional → "Generate"
  // (disabled until gate is at least Conditional); other states → omitted.
  const rows = releases
    .filter(r => r.stage === 'released' || (r.stage === 'in_progress' && r.gate.decision !== 'no_go'))
    .slice(0, 5)

  return (
    <PanelShell title="Compliance packs" count={rows.length || undefined}>
      {rows.length === 0 ? (
        <EmptyRow>No compliance packs yet.</EmptyRow>
      ) : (
        <ul className="m-0 p-0 list-none divide-y" style={{ borderColor: 'var(--color-border)' }}>
          {rows.map(r => {
            const isReleased = r.stage === 'released'
            const sha = fakeSha(r.id)
            return (
              <li key={r.id} className="flex items-center gap-3 px-3.5 py-2.5">
                <div
                  className="w-7 h-7 rounded-md flex items-center justify-center shrink-0"
                  style={{
                    background: isReleased ? 'rgba(34,197,94,0.14)' : 'rgba(234,179,8,0.14)',
                    color: isReleased ? '#22c55e' : '#eab308',
                  }}
                >
                  {isReleased ? <ShieldCheck className="h-3.5 w-3.5" /> : <Clock className="h-3.5 w-3.5" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[12.5px] font-medium text-[var(--color-text)] truncate">
                    {r.name}{r.version && ` ${r.version}`}
                  </div>
                  <div className="text-[11px] font-mono text-[var(--color-text-muted)] truncate">
                    {isReleased ? `4.2 MB · sha256:${sha}…` : 'pack pending — gate at conditional+'}
                  </div>
                </div>
                <button
                  type="button"
                  className="shrink-0 inline-flex items-center gap-1 text-[11.5px] text-[var(--color-accent)] hover:underline disabled:opacity-50 disabled:no-underline"
                  disabled={!isReleased && r.gate.decision === 'no_go'}
                >
                  {isReleased ? <Download className="h-3 w-3" /> : <FileText className="h-3 w-3" />}
                  {isReleased ? 'Download' : 'Generate'}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </PanelShell>
  )
}

// ── Recent activity ────────────────────────────────────────────────────────

export function RecentActivity({ releases }: { releases: DerivedRelease[] }) {
  const items = releases
    .map(r => ({
      release: r,
      at: new Date(r.source.updated_at || r.source.created_at),
    }))
    .sort((a, b) => b.at.getTime() - a.at.getTime())
    .slice(0, 6)

  return (
    <PanelShell title="Recent activity">
      {items.length === 0 ? (
        <EmptyRow>No recent activity.</EmptyRow>
      ) : (
        <ul className="m-0 p-0 list-none divide-y" style={{ borderColor: 'var(--color-border)' }}>
          {items.map(({ release: r, at }) => {
            const ms = Date.now() - at.getTime()
            const rel =
              ms < 60_000 ? 'just now'
              : ms < 3600_000 ? `${Math.round(ms / 60_000)}m ago`
              : ms < 86400_000 ? `${Math.round(ms / 3600_000)}h ago`
              : `${Math.round(ms / 86400_000)}d ago`
            return (
              <li key={`${r.id}-${at.getTime()}`} className="grid grid-cols-[52px_1fr] gap-3 px-3.5 py-2 items-start">
                <span className="text-[11px] font-mono text-[var(--color-text-muted)] pt-0.5">{rel}</span>
                <span className="text-[12px] text-[var(--color-text-secondary)] min-w-0">
                  <span className="text-[var(--color-text)]">{r.name}</span>{' '}
                  {r.stage === 'released' ? 'released' :
                   r.stage === 'cancelled' ? 'cancelled' :
                   r.stage === 'in_progress' ? 'updated' :
                   'created'}
                  {r.version && (
                    <>
                      <span aria-hidden> · </span>
                      <code className="font-mono text-[10.5px] px-1 rounded bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">{r.version}</code>
                    </>
                  )}
                </span>
              </li>
            )
          })}
        </ul>
      )}
      <div className="px-3.5 py-2 border-t text-center text-[11px]" style={{ borderColor: 'var(--color-border)' }}>
        <a href="/audit" className="text-[var(--color-accent)] hover:underline inline-flex items-center gap-1">
          View full audit log <ArrowRight className="h-3 w-3" />
        </a>
      </div>
    </PanelShell>
  )
}
