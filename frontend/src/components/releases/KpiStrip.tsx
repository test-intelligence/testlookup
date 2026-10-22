/**
 * KpiStrip — the release counts, four cards: In progress · Ready to ship ·
 * Blocked · Released (30d). Since UX redesign P6 it sits in the collapsed
 * "Release health" disclosure under the list (`ReleaseHealth.tsx`): above the
 * list the page has ONE StatusBanner, which carries the most important of
 * these counts (the template: a banner OR a KPI strip, never both).
 *
 * A card's sub-line is JSX, never an HTML string: it used to be built as
 * `<strong>N</strong> …` and rendered with `dangerouslySetInnerHTML`. The
 * strings held only counts, but an HTML sink one edit away from a release
 * name is how an injection ships. The card's `sparkline` prop and its `MiniSparkline` went with it:
 * no caller ever passed one (a release list has no history), so the only
 * trend line this strip could draw was an invented one.
 */
import type { ReactNode } from 'react'
import MetricCard, { type MetricTone } from '@/components/ui/MetricCard'
import type { DerivedRelease } from './types'
import { countReleases, openBlockerCount } from './health'

interface KpiCardProps {
  label: string
  value: number | string
  /** The value's ink (MetricCard's tone: the -ink tokens, >= 4.5:1 on the card in every theme). */
  tone?: MetricTone
  delta?: { dir: 'up' | 'down'; value: number; period: string }
  /** The sub-line. Rendered as React children: text in it stays text. */
  sub?: ReactNode
}

/**
 * One card: the ONE KPI tile (W29b UIX-09, `MetricCard` md) — the label in the `.eyebrow` style, the 24 px
 * `--text-stat-lg` value in its tone's ink, the sub-line as the tile's caption, a delta as its change line ("Up 3 ·
 * vs last month (better)": up is good here, as the green / red delta was).
 */
function KpiCard({ label, value, tone = 'neutral', delta, sub }: KpiCardProps) {
  const metric = {
    value,
    ...(delta
      ? {
          trend: delta.value,
          trend_direction: delta.dir,
          trend_text: [String(delta.value), delta.period].filter(Boolean).join(' · '),
        }
      : {}),
  }
  return <MetricCard title={label} metric={metric} tone={tone} caption={sub} />
}

interface KpiStripProps {
  releases: DerivedRelease[]
  /** Optional fully-formed KPIs — overrides the client-side derivation. */
  override?: KpiCardProps[]
}

/**
 * Derive the 4 KPI cards from the current release list.
 *
 * Every number here is counted from `releases`. Nothing is invented.
 *
 * It used to be otherwise: each card carried a `sparkline` of hardcoded
 * literals — `[4, 5, 4, 6, 7, 6, 7, inProgress.length || 6]` — of which only
 * the final point was real, and even that fell back to a made-up constant when
 * the real count was 0. The strip therefore drew a seven-point trend line
 * nobody had measured, labelled `trend ending at N` for screen readers, and
 * rendered a genuine zero as a six. Two captions were hardcoded the same way:
 * `avg gate 97.1%` and `100% audit-packed`.
 *
 * `OverviewPage` was fixed for this exact rule in 2026-08 — a KPI must not
 * claim a trend it has no history for, and a caption must explain a missing
 * trend rather than deny the metric. There is no time series behind a release
 * list, so these cards carry no sparkline at all.
 */
function deriveKpis(releases: DerivedRelease[]): KpiCardProps[] {
  const { planning, inProgress, readyToShip, blocked, released30d } = countReleases(releases)
  // Every blocker the server recorded on an in-progress release (its
  // `blockerCount`), not the few a card has room for.
  const openBlockers = openBlockerCount(releases.filter(r => r.stage === 'in_progress'))
  // When the project has releases but they're all stuck at Planning,
  // the KPI strip used to render four zeros — accurate but useless.
  // Surface the Planning count as the In Progress sub-line so the
  // user can see "N at Planning · promote to In Progress" instead of
  // wondering whether the page is broken.
  // Only stated when it is true and counted. The previous fallback here read
  // "from last week", which described a comparison the card does not make.
  const inProgressSub = inProgress === 0 && planning > 0
    ? <><strong>{planning}</strong> at Planning · promote one to start tracking</>
    : undefined
  return [
    {
      label: 'In progress',
      value: inProgress,
      tone: 'neutral',
      sub: inProgressSub,
    },
    {
      label: 'Ready to ship',
      value: readyToShip,
      tone: 'good',
      sub: readyToShip > 0
        ? <><strong>{readyToShip}</strong> of {inProgress} in progress</>
        : undefined,
    },
    {
      label: 'Blocked',
      value: blocked,
      tone: blocked > 0 ? 'bad' : 'neutral',
      sub: blocked > 0
        ? (openBlockers > 0 ? <><strong>{openBlockers}</strong> blocking item{openBlockers === 1 ? '' : 's'} open</> : undefined)
        : 'no blockers',
    },
    {
      label: 'Released · 30d',
      value: released30d,
      tone: 'good',
    },
  ]
}

export default function KpiStrip({ releases, override }: KpiStripProps) {
  const cards = override ?? deriveKpis(releases)
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3.5">
      {cards.map((c, i) => <KpiCard key={`${c.label}-${i}`} {...c} />)}
    </div>
  )
}
