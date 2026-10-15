/**
 * Right-rail panels for /releases:
 *   - ShippingThisWeek — releases due in the next 7 days, with a date tile
 *   - AgingSignals — stale releases (blocked, planning idle, UAT pending)
 *   - CompliancePacks — the packs that EXIST (`GET /api/v1/compliance-packs`),
 *     then releases ready for one, linking to the real panel
 *   - RecentActivity — the project's release events from the activity ledger
 *
 * The first two are client-derived from the same ``DerivedRelease[]`` the
 * portfolio list uses. The last two read the server (REL-04): a rail that
 * synthesised packs and activity from release rows printed a literal size, an
 * invented checksum and "updated" lines nobody wrote.
 */
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import { Link } from 'react-router-dom'
import { useNow } from '@/hooks/useNow'
import { useActivityFeed } from '@/hooks/useActivityFeed'
import { useProjectCompliancePacks } from '@/hooks/useProjectCompliancePacks'
import type { ActivityEvent, ActivityQuery } from '@/services/activityService'
import {
  compliancePackService,
  packSizeText,
  type ProjectCompliancePack,
} from '@/services/compliancePackService'
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

/** `just now` / `Nm ago` / `Nh ago` / `Nd ago` for an ISO time, against `now` (ms); `—` when unknown. */
function ageText(iso: string | null, now: number): string {
  if (!iso) return '—'
  const at = new Date(iso).getTime()
  if (Number.isNaN(at)) return '—'
  const ms = now - at
  return ms < 60_000 ? 'just now'
    : ms < 3600_000 ? `${Math.round(ms / 60_000)}m ago`
    : ms < 86400_000 ? `${Math.round(ms / 3600_000)}h ago`
    : `${Math.round(ms / 86400_000)}d ago`
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
    .filter((r): r is DerivedRelease & { dueAt: string } => Boolean(r.dueAt) && r.stage === 'in_progress')
    .map(r => {
      const d = new Date(r.dueAt)
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
        <ul className="m-0 p-0 list-none divide-y divide-[var(--color-border)]">
          {items.map(({ release: r, label }) => {
            const dot =
              r.gate.decision === 'go' ? 'var(--status-passed)'
              : r.gate.decision === 'conditional' ? 'var(--status-skipped)'
              : r.gate.decision === 'no_go' ? 'var(--status-failed)'
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
                    {/* `blockerCount`, not `blockers.length`: the card keeps only the first few. */}
                    {r.version ? `${r.version} · ` : ''}{r.blockerCount} blocker{r.blockerCount === 1 ? '' : 's'}
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
    } else if (r.stage === 'in_progress' && r.phases.find(p => p.phaseType === 'uat' && p.state === 'active') && age > 2) {
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
        <ul className="m-0 p-0 list-none divide-y divide-[var(--color-border)]">
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
                  color: severity === 'red' ? 'var(--status-failed)' : 'var(--status-broken)',
                  background: severity === 'red' ? 'color-mix(in srgb, var(--status-failed) 10%, transparent)' : 'color-mix(in srgb, var(--status-skipped) 10%, transparent)',
                  borderColor: severity === 'red' ? 'color-mix(in srgb, var(--status-failed) 40%, transparent)' : 'color-mix(in srgb, var(--status-skipped) 40%, transparent)',
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

/** Packs the rail lists; below this, releases ready for a pack fill the panel. */
const PACKS_SHOWN = 5
/** At most this many "Ready for a pack" rows. */
const READY_SHOWN = 3

/** The file name the release panel gives the same pack (`CompliancePackPanel.handleDownload`). */
const packFilename = (pack: ProjectCompliancePack) =>
  `testlookup-compliance-${pack.release_name || pack.release_id || pack.id}-${pack.generated_at.slice(0, 10)}.zip`

/**
 * Released, or in progress with a gate that is not a hard no, AND at least one linked run: a
 * pack snapshots a run, so generating one for a release with none answers "not available".
 */
const canTakePack = (r: DerivedRelease) =>
  (r.source.test_run_count ?? 0) > 0 &&
  (r.stage === 'released' || (r.stage === 'in_progress' && r.gate.decision !== 'no_go'))

async function downloadPack(pack: ProjectCompliancePack) {
  try {
    await compliancePackService.download(pack.id, packFilename(pack))
  } catch (err) {
    toast.error(`Download failed: ${(err as Error).message}`)
  }
}

/**
 * The compliance packs that exist (REL-04), newest first: the release, the
 * real size, the first 8 hex of the real ``manifest_sha256``, when it was
 * generated, and a Download of the ZIP itself (the authenticated blob route,
 * as the release panel downloads it). The size is ``packSizeText``, the
 * release panel's own text, so the two read the same for one pack.
 *
 * This panel once rendered ``4.2 MB · sha256:<8 hex>…`` from a literal and a
 * 32-bit hash of the release id, for releases with no pack at all; on an audit
 * surface whose premise is a tamper-evident SHA-256 manifest, an invented
 * checksum is the one thing that must never appear. Every figure here comes
 * from the pack row.
 *
 * Under five packs, releases that could take a pack but have none fill the
 * rest ("Ready for a pack"), each opening the release, where the real
 * ``CompliancePackPanel`` generates one. ``projectId`` ``null`` = every
 * project the caller can see.
 */
export function CompliancePacks({
  releases,
  onOpen,
  projectId,
}: {
  releases: DerivedRelease[]
  onOpen: (id: string) => void
  projectId: string | null
}) {
  const now = useNow()
  const { packs, total, isLoading, isError } = useProjectCompliancePacks(projectId)
  const shown = packs.slice(0, PACKS_SHOWN)
  const withPack = new Set(packs.map(p => p.release_id).filter(Boolean))
  const room = Math.min(READY_SHOWN, PACKS_SHOWN - shown.length)
  const ready = isLoading || isError
    ? []
    : releases.filter(r => canTakePack(r) && !withPack.has(r.id)).slice(0, Math.max(room, 0))

  return (
    <PanelShell title="Compliance packs" count={total || undefined}>
      {isError ? (
        <EmptyRow>Could not load compliance packs.</EmptyRow>
      ) : isLoading ? (
        <EmptyRow>Loading compliance packs…</EmptyRow>
      ) : shown.length === 0 ? (
        <EmptyRow>No compliance packs yet.</EmptyRow>
      ) : (
        <ul data-rail-packs="" className="m-0 p-0 list-none divide-y divide-[var(--color-border)]">
          {shown.map(pack => (
            <li key={pack.id} data-rail-pack={pack.id} className="flex items-center gap-3 px-3.5 py-2.5">
              <div
                className="w-7 h-7 rounded-md flex items-center justify-center shrink-0"
                style={{
                  background: pack.retired ? 'var(--color-bg-secondary)' : 'color-mix(in srgb, var(--status-passed) 14%, transparent)',
                  color: pack.retired ? 'var(--color-text-muted)' : 'var(--status-passed)',
                }}
              >
                <ShieldCheck className="h-3.5 w-3.5" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className="text-[12.5px] font-medium text-[var(--color-text)] truncate">
                    {pack.release_name == null
                      ? '(release deleted)'
                      : `${pack.release_name}${pack.release_version ? ` ${pack.release_version}` : ''}`}
                  </span>
                  {pack.retired && (
                    <span
                      data-rail-pack-retired=""
                      className="shrink-0 text-[10px] font-semibold uppercase tracking-[0.06em] px-1.5 py-px rounded border text-[var(--color-text-muted)]"
                      style={{ borderColor: 'var(--color-border)' }}
                    >
                      Retired
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-[var(--color-text-muted)] truncate" title={`sha256:${pack.manifest_sha256}`}>
                  {packSizeText(pack.bytes)} · <span className="font-mono">sha256:{pack.manifest_sha256.slice(0, 8)}</span> · {ageText(pack.generated_at, now)}
                </div>
              </div>
              <button
                type="button"
                onClick={() => void downloadPack(pack)}
                className="shrink-0 inline-flex items-center gap-1 text-[11.5px] text-[var(--color-accent)] hover:underline"
              >
                <Download className="h-3 w-3" />
                Download
              </button>
            </li>
          ))}
        </ul>
      )}
      {ready.length > 0 && (
        <div data-rail-ready="" className="border-t" style={{ borderColor: 'var(--color-border)' }}>
          <div className="px-3.5 pt-2 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
            Ready for a pack
          </div>
          <ul className="m-0 p-0 list-none">
            {ready.map(r => {
              const isReleased = r.stage === 'released'
              return (
                <li key={r.id} className="flex items-center gap-3 px-3.5 py-2">
                  <div
                    className="w-7 h-7 rounded-md flex items-center justify-center shrink-0"
                    style={{
                      background: 'color-mix(in srgb, var(--status-skipped) 14%, transparent)',
                      color: 'var(--status-skipped)',
                    }}
                  >
                    <Clock className="h-3.5 w-3.5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[12.5px] font-medium text-[var(--color-text)] truncate">
                      {r.name}{r.version && ` ${r.version}`}
                    </div>
                    <div className="text-[11px] text-[var(--color-text-muted)] truncate">
                      {isReleased ? 'Released — no pack yet' : 'In progress — no pack yet'}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => onOpen(r.id)}
                    className="shrink-0 inline-flex items-center gap-1 text-[11.5px] text-[var(--color-accent)] hover:underline"
                  >
                    <FileText className="h-3 w-3" />
                    View packs
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </PanelShell>
  )
}

// ── Recent activity ────────────────────────────────────────────────────────

/** The project's release events in the Activity feed (`?category=` seeds its filter). */
const RELEASE_ACTIVITY_HREF = '/activity?category=release'

/** At most this many rows; the request asks for exactly this many. */
const ACTIVITY_SHOWN = 8

/**
 * Module-level, so the feed hook's serialised key is the same object every
 * render. `category` is an array: Axios sends it as `?category=release`
 * (`services/api.ts` sets `paramsSerializer: { indexes: null }` globally).
 */
const RAIL_ACTIVITY_QUERY: ActivityQuery = { category: ['release'], limit: ACTIVITY_SHOWN }

/** The ledger summary, and who did it when a person did. */
function activityText(event: ActivityEvent): string {
  const by = event.actor?.type === 'user' && event.actor.name ? ` · by ${event.actor.name}` : ''
  return `${event.summary}${by}`
}

/**
 * The project's latest release events from the activity ledger (REL-04),
 * each in the ledger's own wording, with its actor and age, linking to the
 * event in the Activity feed. It used to be synthesised from release
 * ``updated_at`` stamps ("2026.11 updated"), which said nothing about what
 * happened or who did it.
 *
 * The ledger is per project (`GET /projects/{id}/activity`): in the
 * All-projects view (``projectId === null``) the panel says so and sends no
 * request. ``releases`` is accepted for the page's call site and unused.
 */
export function RecentActivity({
  projectId,
}: {
  releases: DerivedRelease[]
  projectId: string | null
}) {
  const now = useNow()  // captured at mount — avoids impure Date.now() in render
  const { events, isLoading, error } = useActivityFeed(projectId, RAIL_ACTIVITY_QUERY, projectId !== null)
  const items = events.slice(0, ACTIVITY_SHOWN)

  return (
    <PanelShell title="Recent activity">
      {projectId === null ? (
        <EmptyRow>Release activity is recorded per project — pick a project to see it.</EmptyRow>
      ) : error ? (
        <EmptyRow>Could not load recent activity.</EmptyRow>
      ) : isLoading ? (
        <EmptyRow>Loading recent activity…</EmptyRow>
      ) : items.length === 0 ? (
        <EmptyRow>No recent activity.</EmptyRow>
      ) : (
        <ul data-rail-activity="" className="m-0 p-0 list-none divide-y divide-[var(--color-border)]">
          {items.map(event => (
            <li key={event.id}>
              <Link
                to={`${RELEASE_ACTIVITY_HREF}&event=${encodeURIComponent(event.id)}`}
                data-rail-activity-event={event.id}
                className="grid grid-cols-[52px_1fr] gap-3 px-3.5 py-2 items-start hover:bg-[var(--color-bg-hover)]"
              >
                <span
                  className="text-[11px] font-mono text-[var(--color-text-muted)] pt-0.5"
                  title={event.occurred_at ?? undefined}
                >
                  {ageText(event.occurred_at, now)}
                </span>
                <span className="text-[12px] text-[var(--color-text-secondary)] min-w-0 break-words">
                  {activityText(event)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <div className="px-3.5 py-2 border-t text-center text-[11px]" style={{ borderColor: 'var(--color-border)' }}>
        {/* HON-09: `/audit` was never a route (the catch-all sent it to
            Overview). The release events live in the Activity feed, opened
            on its Release category; an in-app link keeps the SPA session. */}
        <Link
          to={RELEASE_ACTIVITY_HREF}
          className="text-[var(--color-accent)] hover:underline inline-flex items-center gap-1"
        >
          View full audit log <ArrowRight className="h-3 w-3" />
        </Link>
      </div>
    </PanelShell>
  )
}
