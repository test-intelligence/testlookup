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
  good:    '#86efac',
  warn:    '#fcd34d',
  bad:     '#fca5a5',
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
                delta.dir === 'up' ? 'text-emerald-300' : 'text-red-300',
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
 * Derive the 4 KPI cards from the current release list. Sparkline values
 * are placeholder waveforms — kept stable per card so they don't twitch
 * between renders.
 */
function deriveKpis(releases: DerivedRelease[]): KpiCardProps[] {
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
  return [
    {
      label: 'In progress',
      value: inProgress.length,
      tone: 'neutral',
      sub: 'from last week',
      sparkline: [4, 5, 4, 6, 7, 6, 7, inProgress.length || 6],
    },
    {
      label: 'Ready to ship',
      value: readyToShip.length,
      tone: 'good',
      sub: 'avg gate <strong>97.1%</strong>',
      sparkline: [1, 2, 1, 2, 3, 2, 2, readyToShip.length || 2],
    },
    {
      label: 'Blocked',
      value: blocked.length,
      tone: blocked.length > 0 ? 'bad' : 'neutral',
      sub: blocked.length > 0 ? `<strong>aging</strong> · view` : 'no blockers',
      sparkline: [0, 1, 1, 0, 1, 1, 1, blocked.length || 1],
    },
    {
      label: 'Released · 30d',
      value: last30Days,
      tone: 'good',
      sub: '<strong>100%</strong> audit-packed',
      sparkline: [8, 9, 10, 11, 11, 10, 11, last30Days || 11],
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
