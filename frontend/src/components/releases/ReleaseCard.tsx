/**
 * ReleaseCard — the always-visible card per release. Four state variants:
 *
 *   in_progress: identity + verdict cluster + pass/fail bar + phase pipeline + blocker rows
 *   planning:    identity + the release's phases, or a dashed "no phases scoped yet" note
 *   released:    identity + verdict snapshot (no blockers)
 *   cancelled:   identity only, muted
 *
 * The verdict is the server's CURRENT recorded one (REL-01): the tiles are
 * Pass rate · Evidence · Runs of that verdict, coloured by the verdict itself
 * (GO green, NO_GO red, otherwise neutral) — there is no threshold in the
 * browser. "Evaluated {age}" says how old the snapshot is. A QA lead (the
 * page passes `onEvaluate` only to one) records a fresh verdict with
 * "Evaluate gate" / "Re-evaluate".
 *
 * On the list the whole card is clickable — selecting one opens the release's
 * detail (``ReleaseDetailPanel``) in a pane beside the list. On a release's own page
 * the card has no ``onClick``: the detail is already the page. The Evaluate
 * button, the external link, the blocker links and the "Release actions"
 * menu never also open the card.
 */
import type { MouseEvent } from 'react'
import { clsx } from 'clsx'
import { useNow } from '@/hooks/useNow'
import { Clock, CheckCircle2, BadgeCheck, ChevronRight, ExternalLink } from 'lucide-react'
import OverflowMenu, { type OverflowItem } from '@/components/ui/OverflowMenu'
import { formatDay, formatMonthDay } from '@/utils/formatters'
import type { DerivedRelease, GateDecision } from './types'
import GateBadge from './GateBadge'
import PhasePipeline from './PhasePipeline'
import BlockerRow from './BlockerRow'
import { passRateText, verdictAge } from './health'
import { safeHttpUrl } from './mapping'

const ICON_TILE: Record<GateDecision, { bg: string; fg: string; icon: typeof Clock }> = {
  go:              { bg: 'color-mix(in srgb, var(--status-passed) 14%, transparent)',  fg: 'var(--status-passed)', icon: CheckCircle2 },
  // W29a UIX-03: conditional is the warning hue (--gate-conditional), never skipped-gold.
  conditional:     { bg: 'color-mix(in srgb, var(--gate-conditional) 14%, transparent)',  fg: 'var(--gate-conditional)', icon: BadgeCheck },
  no_go:           { bg: 'color-mix(in srgb, var(--status-failed) 14%, transparent)',  fg: 'var(--status-failed)', icon: BadgeCheck },
  not_evaluated:   { bg: 'var(--color-accent-muted)', fg: 'var(--color-accent)', icon: Clock },
  never_evaluated: { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)', icon: Clock },
  cancelled:       { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)', icon: Clock },
}

/** The source a synced release links back to. */
const SOURCE_LABEL: Record<string, string> = { github: 'GitHub', jira: 'Jira' }

/** The button, the links and the menu act on their own; the card must not also open. */
function stopToCard(e: MouseEvent) {
  e.stopPropagation()
}

interface GateCellProps {
  label: string
  value: string
  sub: string
  /** Drives the value colour — defaults to neutral. */
  tone?: 'good' | 'bad' | 'neutral'
}
function GateCell({ label, value, sub, tone = 'neutral' }: GateCellProps) {
  const color =
    tone === 'good' ? 'var(--status-passed)'
    : tone === 'bad'  ? 'var(--status-failed)'
    : 'var(--color-text)'
  return (
    <div data-gate-tile={label}>
      <div className="text-[9.5px] uppercase tracking-[0.08em] text-[var(--color-text-muted)]">{label}</div>
      <div className="text-[16px] tabular-nums font-semibold leading-none mt-1" style={{ color }}>{value}</div>
      <div className="text-[10.5px] text-[var(--color-text-faint)] mt-0.5">{sub}</div>
    </div>
  )
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

/** The recorded verdict's numbers: Pass rate · Evidence · Runs. */
function GateTiles({ gate }: { gate: DerivedRelease['gate'] }) {
  const tone = gate.decision === 'go' ? 'good' : gate.decision === 'no_go' ? 'bad' : 'neutral'
  return (
    <>
      <GateCell
        label="Pass rate"
        value={passRateText(gate.passRate)}
        sub={gate.evidence ? `over ${plural(gate.evidence, 'test')}` : 'no test has run'}
        tone={tone}
      />
      <GateCell
        label="Evidence"
        value={gate.evidence != null ? String(gate.evidence) : '—'}
        sub={`of ${gate.denominator ?? '—'} in scope`}
      />
      <GateCell
        label="Runs"
        value={gate.runCount != null ? String(gate.runCount) : '—'}
        sub="in the last verdict"
      />
    </>
  )
}

function PassFailBar({ totals }: { totals: NonNullable<DerivedRelease['totals']> }) {
  const total = Math.max(1, totals.passed + totals.failed + totals.broken + totals.skipped)
  const segs: { color: string; flex: number; label: string }[] = [
    { color: 'var(--status-passed)', flex: totals.passed,  label: 'passed' },
    { color: 'var(--status-failed)', flex: totals.failed,  label: 'failed' },
    { color: 'var(--status-broken)', flex: totals.broken,  label: 'broken' },
    { color: 'var(--status-skipped)', flex: totals.skipped, label: 'skipped' },
  ]
  return (
    <div className="flex items-center gap-3" data-pass-fail-bar="">
      <div
        className="flex flex-1 h-1.5 rounded-full overflow-hidden border"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-secondary)' }}
        aria-hidden
      >
        {segs.map(s => s.flex > 0 && (
          <span key={s.label} style={{ flex: s.flex, background: s.color }} />
        ))}
      </div>
      <div className="flex items-center gap-2 text-[11px] text-[var(--color-text-muted)] tabular-nums shrink-0">
        <Dot color="var(--status-passed)" label="passed" /> {totals.passed}
        <Dot color="var(--status-failed)" label="failed" /> {totals.failed}
        <Dot color="var(--status-broken)" label="broken" /> {totals.broken}
        <Dot color="var(--status-skipped)" label="skipped" /> {totals.skipped}
        <span className="text-[var(--color-text-faint)]">/ {total}</span>
      </div>
    </div>
  )
}
function Dot({ color, label }: { color: string; label: string }) {
  return <span aria-hidden title={label} className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: color }} />
}

/**
 * A release with no phases. Says how to scope it with what exists
 * (the release's detail has "Add Phase"). The two "(planned)" controls that
 * sat here — Clone from …, Generate from PRD, disabled since BUG-006 — are
 * gone: what is not built is not rendered (UX redesign P2/P5).
 */
function PlanningCallout({ releaseName }: { releaseName: string }) {
  return (
    <div
      data-planning-callout=""
      className="rounded-md border border-dashed p-3"
      style={{ borderColor: 'var(--color-border)' }}
    >
      <div className="text-[12.5px] font-medium text-[var(--color-text)]">No phases scoped yet</div>
      <div className="text-[11.5px] text-[var(--color-text-muted)]">
        Add {releaseName}&apos;s phases in its detail.
      </div>
    </div>
  )
}

interface ReleaseCardProps {
  release: DerivedRelease
  /** When true, the body collapses to the identity row only. */
  collapsed?: boolean
  onClick?: () => void
  /** Present => QA lead: the Evaluate button renders (in-progress and released stages only). */
  onEvaluate?: (releaseId: string) => void
  /** The evaluation of this release is in flight. */
  evaluating?: boolean
  /** Present => QA lead: "Set as active release" is in the Release actions menu (planning / in progress, not already active). */
  onSetActive?: (releaseId: string) => void
}

export default function ReleaseCard({
  release,
  collapsed = false,
  onClick,
  onEvaluate,
  evaluating = false,
  onSetActive,
}: ReleaseCardProps) {
  const { gate } = release
  const tile = ICON_TILE[gate.decision]
  const Icon = tile.icon
  const now = useNow()  // captured at mount — avoids impure Date.now() in render
  const muted = release.stage === 'cancelled'
  const isReleased = release.stage === 'released'
  const isPlanning = release.stage === 'planning'
  const isInProgress = release.stage === 'in_progress'
  const evaluated = gate.decision !== 'never_evaluated' && gate.decision !== 'cancelled'

  const showTiles = !isPlanning && !muted && evaluated
  const canEvaluate = onEvaluate != null && (isInProgress || isReleased)
  const externalUrl = safeHttpUrl(release.externalUrl)
  const sourceLabel = (release.sourceSystem && SOURCE_LABEL[release.sourceSystem.toLowerCase()]) || 'Source'

  const menuItems: OverflowItem[] = []
  if (onSetActive && (isPlanning || isInProgress) && !release.isActive) {
    menuItems.push({ label: 'Set as active release', onClick: () => onSetActive(release.id) })
  }

  const dueLabel = release.dueAt ? formatMonthDay(release.dueAt) : null
  const overdue = release.dueAt
    ? new Date(release.dueAt).getTime() < now && isInProgress
    : false

  const verdictLine = evaluated && gate.decidedAt
    ? (gate.decision === 'not_evaluated' && gate.insufficientReason
        ? gate.insufficientReason
        : `Evaluated ${verdictAge(gate.decidedAt, now)}`)
    : null

  const showPhases = release.phases.length > 0
  const showCallout = (isPlanning || isInProgress) && release.phases.length === 0
  const showBar = !isPlanning && release.totals != null
  const showBlockers = isInProgress && release.blockers.length > 0
  const hasBody = !collapsed && !muted && (showPhases || showCallout || showBar || showBlockers)

  return (
    <article
      onClick={onClick}
      className={clsx(
        'rounded-xl border bg-[var(--color-bg-card)] overflow-hidden transition-colors',
        onClick && 'cursor-pointer hover:border-[var(--color-border-light)]',
        muted && 'opacity-60',
      )}
      style={{ borderColor: 'var(--color-border)' }}
      aria-label={`${release.name}${release.version ? ' ' + release.version : ''} — ${gate.decision}`}
      data-release-id={release.id}
    >
      {/* Identity + verdict cluster */}
      <header className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 items-start px-4 pt-3.5 pb-3">
        <div className="min-w-0 flex items-start gap-3">
          <div
            className="w-8 h-8 rounded-md flex items-center justify-center shrink-0"
            style={{ background: tile.bg, color: tile.fg }}
          >
            <Icon className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="text-[15px] font-semibold text-[var(--color-text)] m-0 truncate max-w-[280px]">{release.name}</h3>
              {release.version && (
                <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{release.version}</code>
              )}
              <span className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-muted)] px-1.5 py-0.5 rounded-sm border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
                {release.stage.replace('_', ' ')}
              </span>
              {release.isActive && (
                <span
                  data-release-active=""
                  title="The project's active release: runs that no other rule matches land here"
                  className="text-[10px] font-semibold tracking-[0.08em] px-1.5 py-0.5 rounded-sm border text-[var(--color-accent)] bg-[var(--color-accent-muted)]"
                  style={{ borderColor: 'color-mix(in srgb, var(--color-accent) 40%, transparent)' }}
                >
                  ACTIVE
                </span>
              )}
              {externalUrl && (
                <a
                  href={externalUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={stopToCard}
                  data-release-external=""
                  title={`Open in ${sourceLabel}`}
                  className="inline-flex items-center gap-1 text-[11px] text-[var(--color-accent)] hover:underline"
                >
                  <ExternalLink aria-hidden className="h-3 w-3" />
                  {sourceLabel}
                </a>
              )}
            </div>
            <div className="mt-1 flex items-center gap-2 text-[11.5px] text-[var(--color-text-muted)] flex-wrap">
              {release.hasOwner ? (
                <>
                  <span
                    data-owner-chip="set"
                    className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-[var(--color-accent-muted)] text-[var(--color-accent)] text-[9px] font-semibold"
                    title={release.ownerName}
                  >{release.ownerInitials}</span>
                  <span>{release.ownerName}</span>
                </>
              ) : (
                <>
                  <span
                    data-owner-chip="none"
                    aria-hidden
                    className="inline-flex items-center justify-center w-5 h-5 rounded-full border border-dashed text-[var(--color-text-faint)] text-[9px]"
                    style={{ borderColor: 'var(--color-border)' }}
                  >–</span>
                  <span className="text-[var(--color-text-faint)]">No owner</span>
                </>
              )}
              {dueLabel && (
                <>
                  <span aria-hidden>·</span>
                  <span className={clsx(overdue && 'text-[var(--status-failed)] font-medium')}>
                    {overdue ? `Overdue (${dueLabel})` : `Due ${dueLabel}`}
                  </span>
                </>
              )}
              {isReleased && release.source.released_at && (
                <>
                  <span aria-hidden>·</span>
                  <span>Released {formatDay(release.source.released_at)}</span>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-4 shrink-0">
          <div className="flex flex-col items-end gap-1">
            <GateBadge decision={gate.decision} reason={gate.decision === 'not_evaluated' ? gate.insufficientReason ?? undefined : undefined} />
            {verdictLine && (
              <span
                data-gate-evaluated=""
                title={gate.decidedAt ?? undefined}
                className="text-[10.5px] text-[var(--color-text-faint)] text-right max-w-[240px]"
              >
                {verdictLine}
              </span>
            )}
          </div>
          {(canEvaluate || menuItems.length > 0) && (
            <div className="flex items-center gap-2">
              {canEvaluate && (
                <button
                  type="button"
                  data-testid="release-evaluate"
                  disabled={evaluating}
                  onClick={(e) => {
                    e.stopPropagation()
                    onEvaluate(release.id)
                  }}
                  className="h-8 px-2.5 rounded-md border text-[12px] font-medium text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] disabled:opacity-60 disabled:cursor-wait whitespace-nowrap"
                  style={{ borderColor: 'var(--color-border)' }}
                >
                  {evaluating ? 'Evaluating…' : evaluated ? 'Re-evaluate' : 'Evaluate gate'}
                </button>
              )}
              {menuItems.length > 0 && (
                <span onClick={stopToCard} className="inline-flex">
                  <OverflowMenu items={menuItems} label="Release actions" />
                </span>
              )}
            </div>
          )}
        </div>
      </header>

      {/* The recorded verdict's figures, a row of their own: beside the identity they left the
          release name ~70 px in a 618 px card ("2.."), owner and due date wrapping word by word. */}
      {showTiles && (
        <div data-gate-tiles="" className="grid grid-cols-3 gap-3 px-4 pb-3">
          <GateTiles gate={gate} />
        </div>
      )}

      {/* Body — body sections per state */}
      {hasBody && (
        <div className="px-4 pb-4 space-y-3">
          {/* The recorded verdict's status counts (in-progress / released). */}
          {showBar && release.totals && <PassFailBar totals={release.totals} />}

          {/* The release's own phases, in order. */}
          {showPhases && <PhasePipeline phases={release.phases} />}

          {/* No phases yet */}
          {showCallout && <PlanningCallout releaseName={release.name} />}

          {/* Blocker rows (in-progress only). Each row truncates its test name,
              so the list must not size the card: zero intrinsic width
              (`w-0 min-w-full`), the full width in layout. With a release open
              beside the list the card is ~510 px at 1280, and the untruncated
              names set its min-content (W27 PAN-02). */}
          {showBlockers && (
            <div data-card-blockers="" className="space-y-1.5 w-0 min-w-full">
              {release.blockers.slice(0, 3).map(b => <BlockerRow key={b.id} blocker={b} />)}
              {release.blockerCount > 3 && (
                <div data-blockers-more="" className="text-[11.5px] text-[var(--color-text-muted)] flex items-center gap-1">
                  <ChevronRight className="h-3 w-3" />
                  +{release.blockerCount - 3} more · open the release for the full list
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </article>
  )
}
