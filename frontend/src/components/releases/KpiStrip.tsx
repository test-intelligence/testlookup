/**
 * KpiStrip — the 4-pill row above the filter bar. In progress · Ready to
 * ship · Blocked · Released (30d). Each card lays out as a flex row with
 * label+value on the left and a mini sparkline as a flex-sibling on the
 * right — never absolutely positioned over the value (that was the bug
 * the previous version had).
 */
import { clsx } from 'clsx'
import { TrendingDown, TrendingUp } from 'lucide-react'
import type { DerivedRelease } from './types'

type Tone = 'good' | 'warn' | 'bad' | 'neutral'

const TONE_COLOR: Record<Tone, string> = {
  good:    'var(--status-passed)',
  warn:    'var(--status-broken)',
  bad:     'var(--status-failed)',
  neutral: 'var(--color-text)',
}

interface MiniSparklineProps {
  values: number[]
  tone: Tone
  width?: number
  height?: number
}
function MiniSparkline({ values, tone, width = 80, height = 28 }: MiniSparklineProps) {
  if (values.length < 2) return null
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = Math.max(1, max - min)
  const stepX = width / Math.max(1, values.length - 1)
  const points = values.map((v, i) => {
    const x = i * stepX
    const y = height - 4 - ((v - min) / span) * (height - 8)
    return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
  }).join(' ')
  return (
    <svg
      width={width}
      height={height}
      className="shrink-0"
      style={{ alignSelf: 'flex-end' }}
      aria-label={`trend ending at ${values[values.length - 1]}`}
    >
      <path d={points} fill="none" stroke={TONE_COLOR[tone]} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

interface KpiCardProps {
  label: string
  value: number | string
  tone?: Tone
  delta?: { dir: 'up' | 'down'; value: number; period: string }
  sub?: string
  sparkline?: number[]
}

function KpiCard({ label, value, tone = 'neutral', delta, sub, sparkline }: KpiCardProps) {
  return (
    <div
      className="rounded-xl border p-3 flex flex-col gap-1"
      style={{
        borderColor: 'var(--color-border)',
        background: 'var(--color-bg-card)',
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
            {label}
          </div>
          <div
            className="mt-1 font-bold tabular-nums leading-none"
            style={{ fontSize: 26, letterSpacing: '-0.02em', color: TONE_COLOR[tone] }}
          >
            {value}
          </div>
        </div>
        {sparkline && sparkline.length > 1 && (
          <MiniSparkline values={sparkline} tone={tone} />
        )}
      </div>
      {(sub || delta) && (
        <div className="text-[11.5px] text-[var(--color-text-muted)] flex items-center gap-1.5 flex-wrap">
          {delta && (
            <span
              className={clsx(
                'inline-flex items-center gap-0.5 font-semibold tabular-nums',
                delta.dir === 'up' ? 'text-[var(--status-passed)]' : 'text-[var(--status-failed)]',
              )}
            >
              {delta.dir === 'up' ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
              {delta.dir === 'up' ? '+' : '−'}{delta.value}
            </span>
          )}
          {delta && delta.period && <span>· {delta.period}</span>}
          {sub && <span dangerouslySetInnerHTML={{ __html: sub }} />}
        </div>
      )}
    </div>
  )
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
 * list, so these cards carry no sparkline at all. `override` remains for a
 * caller that genuinely has one.
 */
function deriveKpis(releases: DerivedRelease[]): KpiCardProps[] {
  const planning = releases.filter(r => r.stage === 'planning')
  const inProgress = releases.filter(r => r.stage === 'in_progress')
  const readyToShip = inProgress.filter(r => r.gate.decision === 'go')
  const blocked = inProgress.filter(r => r.gate.decision === 'no_go' || r.blockers.some(b => b.severity === 'red'))
  const last30Days = (() => {
    const cutoff = Date.now() - 30 * 24 * 3600 * 1000
    return releases.filter(r =>
      r.stage === 'released' && r.source.released_at &&
      new Date(r.source.released_at).getTime() >= cutoff,
    ).length
  })()
  // When the project has releases but they're all stuck at Planning,
  // the KPI strip used to render four zeros — accurate but useless.
  // Surface the Planning count as the In Progress sub-line so the
  // user can see "N at Planning · promote to In Progress" instead of
  // wondering whether the page is broken.
  // Only stated when it is true and counted. The previous fallback here read
  // "from last week", which described a comparison the card does not make.
  const inProgressSub = inProgress.length === 0 && planning.length > 0
    ? `<strong>${planning.length}</strong> at Planning · promote one to start tracking`
    : undefined
  return [
    {
      label: 'In progress',
      value: inProgress.length,
      tone: 'neutral',
      sub: inProgressSub,
    },
    {
      label: 'Ready to ship',
      value: readyToShip.length,
      tone: 'good',
      sub: readyToShip.length > 0
        ? `<strong>${readyToShip.length}</strong> of ${inProgress.length} in progress`
        : undefined,
    },
    {
      label: 'Blocked',
      value: blocked.length,
      tone: blocked.length > 0 ? 'bad' : 'neutral',
      sub: blocked.length > 0 ? undefined : 'no blockers',
    },
    {
      label: 'Released · 30d',
      value: last30Days,
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
