/**
 * The release-health words and counts (UX redesign P6): one definition for
 * every place that shows them — the banner above the release list, its
 * disclosure under the list and the count cards (`KpiStrip`) — so they can
 * never disagree. Pure: everything is counted from the `DerivedRelease` list,
 * whose gate is the server's recorded verdict (REL-01). No threshold lives
 * here: a release is "ready to ship" because its recorded verdict is GO.
 */
import type { BannerFact } from '@/components/ui/StatusBanner'
import type { DerivedRelease } from './types'

/** The release counts, each counted from the list. */
export interface ReleaseCounts {
  planning: number
  inProgress: number
  /** In-progress releases whose recorded verdict is GO. */
  readyToShip: number
  /** In-progress releases at `no_go` or with a red blocker. */
  blocked: number
  /** Released in the last 30 days. */
  released30d: number
}

export function countReleases(releases: DerivedRelease[]): ReleaseCounts {
  const inProgress = releases.filter(r => r.stage === 'in_progress')
  const cutoff = Date.now() - 30 * 24 * 3600 * 1000
  return {
    planning: releases.filter(r => r.stage === 'planning').length,
    inProgress: inProgress.length,
    readyToShip: inProgress.filter(r => r.gate.decision === 'go').length,
    blocked: inProgress.filter(r => r.gate.decision === 'no_go' || r.blockers.some(b => b.severity === 'red')).length,
    released30d: releases.filter(r =>
      r.stage === 'released' && r.source.released_at &&
      new Date(r.source.released_at).getTime() >= cutoff,
    ).length,
  }
}

/** The banner with no release in progress: never a state the page does not have. */
export const NOTHING_IN_FLIGHT = 'No release in flight — create a new release or move a Planning release into progress.'

/** The lede of a release no QA lead has evaluated yet. */
export const NEVER_EVALUATED_LEDE = 'Not evaluated yet — a QA lead can record a verdict from the linked runs with Evaluate gate.'

/** The release's name, and its version when it says something the name does not. */
function nameOf(release: DerivedRelease): string {
  return release.version && release.version !== release.name ? `${release.name} (${release.version})` : release.name
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

/**
 * The terse age of a recorded verdict: `just now` / `5m ago` / `3h ago` /
 * `2d ago`. `now` is injected so a render stays pure; a future or
 * unparseable time reads `just now`.
 */
export function verdictAge(iso: string | null, now: number = Date.now()): string {
  if (!iso) return 'just now'
  const ms = now - new Date(iso).getTime()
  if (!Number.isFinite(ms) || ms < 0) return 'just now'
  const mins = Math.floor(ms / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

/** `92.4%`, or `—` when not measured. */
export function passRateText(passRate: number | null): string {
  return passRate != null ? `${passRate.toFixed(1)}%` : '—'
}

/** The sentence about the release that ships next (the former verdict band's headline). */
export function releaseHeadline(highlighted: DerivedRelease): string {
  const name = nameOf(highlighted)
  const dueDate = highlighted.dueAt ? new Date(highlighted.dueAt) : null
  const dueLabel = dueDate ? dueDate.toLocaleDateString(undefined, { weekday: 'long' }) : null
  const blockers = highlighted.blockerCount
  switch (highlighted.gate.decision) {
    case 'go':
      return `${name} is ready to ship`
    case 'no_go':
      return `${name} is blocked${dueLabel ? ` — was due ${dueLabel}` : ''}`
    case 'conditional':
      return `${name} is on track — ${blockers} item${blockers === 1 ? '' : 's'} to clear${dueLabel ? ` before ${dueLabel}` : ''}`
    default:
      // Not evaluated (yet). The highlighted release is always an IN-PROGRESS
      // one (the page picks it from those): never "is in planning".
      return `${name} is in progress, not evaluated yet${dueLabel ? ` — target ${dueLabel}` : ''}`
  }
}

/**
 * What the recorded verdict says, or that there is none yet (the former
 * band's second line). Plain text: the band rendered it through
 * `dangerouslySetInnerHTML`, an HTML sink one edit away from a release name.
 */
export function releaseLede(highlighted: DerivedRelease, now: number = Date.now()): string {
  const { gate, name } = highlighted
  const age = verdictAge(gate.decidedAt, now)
  const rate = passRateText(gate.passRate)
  const evidence = gate.evidence ?? 0
  switch (gate.decision) {
    case 'never_evaluated':
      return NEVER_EVALUATED_LEDE
    case 'go':
      return `${name} was Go ${age}: pass rate ${rate} over ${plural(evidence, 'test')} in ${plural(gate.runCount ?? 0, 'run')}.`
    case 'no_go':
      return `${name} was No-Go ${age}: ${plural(gate.blockingTotal, 'blocking item')}; pass rate ${rate} over ${plural(evidence, 'test')}.`
    case 'conditional':
      return `${name} was Conditional Go ${age}: ${plural(gate.blockingTotal, 'blocking item')}; pass rate ${rate} over ${plural(evidence, 'test')}.`
    case 'not_evaluated':
      return `${name} was not evaluated ${age}: ${gate.insufficientReason ?? 'not enough evidence'}`
    case 'cancelled':
    default:
      return `${name} is cancelled.`
  }
}

/** Every blocker of every in-progress release (the server's full count, not the few a card shows). */
export function openBlockerCount(inProgressReleases: DerivedRelease[]): number {
  return inProgressReleases.reduce((sum, r) => sum + (r.blockerCount ?? r.blockers.length), 0)
}

/**
 * The banner's facts, at most four, the most important first: the pass rate
 * (when the highlighted release's verdict was measured), the open blockers,
 * Ready to ship, Blocked, then Released · 30d. In progress is not among them:
 * the header's subtitle and the status filter's "in progress" count already
 * say it above the list.
 */
export function releaseHealthFacts(
  highlighted: DerivedRelease | null,
  inProgressReleases: DerivedRelease[],
  releases: DerivedRelease[],
): BannerFact[] {
  const counts = countReleases(releases)
  const passRate = highlighted?.gate.passRate
  const facts: BannerFact[] = []
  if (highlighted && highlighted.gate.measured && passRate != null) {
    facts.push({ label: 'Pass rate', value: passRateText(passRate) })
  }
  facts.push({ label: 'Open blockers', value: openBlockerCount(inProgressReleases) })
  facts.push({ label: 'Ready to ship', value: counts.readyToShip })
  facts.push({ label: 'Blocked', value: counts.blocked })
  facts.push({ label: 'Released · 30d', value: counts.released30d })
  return facts.slice(0, 4)
}
