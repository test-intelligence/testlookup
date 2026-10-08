/**
 * The release-health words and counts (UX redesign P6): one definition for
 * every place that shows them — the banner above the release list, its
 * disclosure under the list and the count cards (`KpiStrip`) — so they can
 * never disagree. Pure: everything is counted from the `DerivedRelease` list.
 */
import type { BannerFact } from '@/components/ui/StatusBanner'
import type { DerivedRelease } from './types'

/** The release counts, each counted from the list. */
export interface ReleaseCounts {
  planning: number
  inProgress: number
  /** In-progress releases at gate `go`. */
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

/** The release's name, and its version when it says something the name does not. */
function nameOf(release: DerivedRelease): string {
  return release.version && release.version !== release.name ? `${release.name} (${release.version})` : release.name
}

/** The sentence about the release that ships next (the former verdict band's headline). */
export function releaseHeadline(highlighted: DerivedRelease): string {
  const name = nameOf(highlighted)
  const dueDate = highlighted.dueAt ? new Date(highlighted.dueAt) : null
  const dueLabel = dueDate ? dueDate.toLocaleDateString(undefined, { weekday: 'long' }) : null
  const blockers = highlighted.blockers.length
  switch (highlighted.gate.decision) {
    case 'go':
      return `${name} is ready to ship`
    case 'no_go':
      return `${name} is blocked${dueLabel ? ` — was due ${dueLabel}` : ''}`
    case 'conditional':
      return `${name} is on track — ${blockers} item${blockers === 1 ? '' : 's'} to clear${dueLabel ? ` before ${dueLabel}` : ''}`
    default:
      // Not evaluated yet. The highlighted release is always an IN-PROGRESS
      // one (the page picks it from those): never "is in planning".
      return `${name} is in progress, not evaluated yet${dueLabel ? ` — target ${dueLabel}` : ''}`
  }
}

/**
 * The composite against the gate, or why there is none yet (the former
 * band's second line). Plain text: the band rendered it through
 * `dangerouslySetInnerHTML`, an HTML sink one edit away from a release name.
 */
export function releaseLede(highlighted: DerivedRelease): string {
  const c = highlighted.gate.composite
  if (highlighted.gate.decision === 'not_evaluated' || c == null) {
    return 'No gate evaluation yet — phase results will populate the composite, coverage, and flake numbers once tests start landing.'
  }
  const n = highlighted.blockers.length
  return `Composite pass rate ${c.toFixed(1)}% ${c >= 95 ? 'meets' : 'is under'} the 95% gate. ${n} blocker${n === 1 ? '' : 's'} across this release.`
}

/** Every blocker of every in-progress release. */
export function openBlockerCount(inProgressReleases: DerivedRelease[]): number {
  return inProgressReleases.reduce((sum, r) => sum + r.blockers.length, 0)
}

/**
 * The banner's facts, at most four, the most important first: the composite
 * (when the gate has one), the open blockers, Ready to ship, Blocked, then
 * Released · 30d. In progress is not among them: the header's subtitle and
 * the status filter's "in progress" count already say it above the list.
 */
export function releaseHealthFacts(
  highlighted: DerivedRelease | null,
  inProgressReleases: DerivedRelease[],
  releases: DerivedRelease[],
): BannerFact[] {
  const counts = countReleases(releases)
  const composite = highlighted?.gate.composite
  const facts: BannerFact[] = []
  if (highlighted && highlighted.gate.decision !== 'not_evaluated' && composite != null) {
    facts.push({ label: 'Composite pass rate', value: `${composite.toFixed(1)}%` })
  }
  facts.push({ label: 'Open blockers', value: openBlockerCount(inProgressReleases) })
  facts.push({ label: 'Ready to ship', value: counts.readyToShip })
  facts.push({ label: 'Blocked', value: counts.blocked })
  facts.push({ label: 'Released · 30d', value: counts.released30d })
  return facts.slice(0, 4)
}
