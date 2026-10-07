import { ReactNode } from 'react'
import { TrendingDown, TrendingUp, Minus } from 'lucide-react'
import { clsx } from 'clsx'

interface MetricData {
  value: string | number
  trend?: number | null
  /** `none`: no number to compare, only `trend_text` saying why. */
  trend_direction?: 'up' | 'down' | 'flat' | 'none'
  /**
   * The change line after the direction word, e.g. "2.9 pp vs previous
   * period". Without it: "<|trend|>% vs prev period".
   */
  trend_text?: string
}

interface Props {
  title: string
  metric?: MetricData
  icon: ReactNode
  accentColor?: string
  loading?: boolean
  /**
   * Which trend direction is GOOD for this metric. Pass-rate style metrics
   * default to 'up'; count-of-bad-things metrics (Failed, Flaky, Duration)
   * should pass 'down' so a falling trend renders green, not red.
   */
  positiveDirection?: 'up' | 'down'
  /**
   * The KPI-strip variant (UX redesign P0): ~72 px tall, a `--text-stat-lg` value
   * (24 px at the desk, raised in presentation mode),
   * no icon block, the change line on one line, and `sparkline` beside the
   * value.
   */
  compact?: boolean
  /** A small inline chart beside the value (compact variant only). */
  sparkline?: ReactNode
  /**
   * The label's hover text (compact variant; default: the label). A compact
   * label is one truncated line, so a strip's labels are kept short
   * (`kpi-labels.spec.ts`) and the full name goes here.
   */
  hint?: string
}

const DIRECTION_WORD = { up: 'Up', down: 'Down', flat: 'No change' } as const

/**
 * A headline number with an optional change line.
 *
 * The change line never relies on colour or the icon alone (WCAG 1.4.1): the
 * DIRECTION is a word ("Up" / "Down" / "No change") and whether that is good
 * is a word too ("better" / "worse"). The status hue only paints the icon,
 * which is decoration (`aria-hidden`).
 */
export default function MetricCard({
  title,
  metric,
  icon,
  accentColor = 'default',
  loading,
  positiveDirection = 'up',
  compact = false,
  sparkline,
  hint,
}: Props) {
  const dir = metric?.trend_direction
  const hasTrend = metric?.trend != null || Boolean(metric?.trend_text)
  const TrendIcon = dir === 'up' ? TrendingUp : dir === 'down' ? TrendingDown : Minus
  const judged = dir === 'up' || dir === 'down'
  const trendColor = judged
    ? dir === positiveDirection
      ? 'text-[var(--status-passed)]'
      : 'text-[var(--status-failed)]'
    : 'text-[var(--color-text-muted)]'
  const trendText = metric?.trend_text ?? (metric?.trend != null ? `${Math.abs(metric.trend)}% vs prev period` : '')
  const word = dir && dir !== 'none' ? DIRECTION_WORD[dir] : null

  const accentBg: Record<string, string> = {
    default: 'bg-white/5 text-[var(--color-text)]',
    green:   'bg-[var(--status-passed-bg)] text-[var(--status-passed)]',
    red:     'bg-[var(--status-failed-bg)] text-[var(--status-failed)]',
    amber:   'bg-[var(--status-broken-bg)] text-[var(--status-broken)]',
    purple:  'bg-[var(--status-flaky-bg)] text-[var(--status-flaky)]',
  }

  if (compact) {
    const judgement = judged ? (dir === positiveDirection ? '(better)' : '(worse)') : null
    const changeLine = [word, trendText].filter(Boolean).join(' ')
    return (
      <div data-metric-card="compact" className="card !px-4 !py-3 min-w-0">
        <p title={hint ?? title} className="mb-1 truncate text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
          {title}
        </p>
        {/* The aside (a sparkline, a hint) takes what the value leaves and is
            capped to it; under 4rem it drops below the value instead. The room-
            sized value (40 px) left a w-24 sparkline no room: drawn over the
            value and out of the tile (P3 baseline, overview-presentation-kpis). */}
        <div className="flex flex-wrap items-end justify-between gap-x-2 gap-y-1">
          {loading ? (
            <div className="h-7 w-20 animate-pulse rounded bg-[var(--color-bg-secondary)]">
              <span className="sr-only">Loading</span>
            </div>
          ) : (
            // The stat token, not `text-2xl`: the same 24 px at the desk, and
            // presentation mode raises it (40 px) like every other metric value.
            // `leading-[1.3333]` keeps text-2xl's 32 px line at the desk.
            <p className="whitespace-nowrap text-[length:var(--text-stat-lg)] leading-[1.3333] font-bold tabular-nums text-[var(--color-text)]">{metric?.value ?? '—'}</p>
          )}
          {sparkline && !loading && (
            <div data-metric-aside="" className="flex min-w-16 flex-1 justify-end [&>*]:max-w-full">
              {sparkline}
            </div>
          )}
        </div>
        {hasTrend && !loading && (
          <p
            data-metric-trend={dir ?? 'unknown'}
            title={[changeLine, judgement].filter(Boolean).join(' ')}
            className={clsx('mt-1 flex min-w-0 items-center gap-1 text-[11px] font-medium', trendColor)}
          >
            {dir !== 'none' && <TrendIcon aria-hidden="true" className="h-3 w-3 shrink-0" />}
            {/* Only the change text truncates: the judgement is what a narrow
                strip card must never cut ("vs previous period (bet…"). */}
            <span className="min-w-0 truncate text-[var(--color-text-secondary)]">{changeLine}</span>
            {judgement && (
              <span data-trend-judgement="" className="shrink-0 text-[var(--color-text-secondary)]">
                {judgement}
              </span>
            )}
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="card flex items-start justify-between gap-4">
      <div className="min-w-0 flex-1">
        {/* Text tokens: --color-text-secondary is >= 7.7:1 on the card in all
            six themes; --color-text-muted is 4.15:1 in lab (axe color-contrast). */}
        <p className="text-xs font-medium text-[var(--color-text-secondary)] uppercase tracking-wider mb-2">{title}</p>
        {loading ? (
          // aria-label on a role-less div is prohibited (axe aria-prohibited-attr,
          // serious) and ignored by screen readers: the bar is decoration, the
          // busy state is visually hidden TEXT. Not a live region on purpose.
          <div className="h-8 w-24 bg-[var(--color-bg-secondary)] rounded animate-pulse">
            <span className="sr-only">Loading</span>
          </div>
        ) : (
          <p className="text-3xl font-bold text-[var(--color-text)] tabular-nums whitespace-nowrap">{metric?.value ?? '—'}</p>
        )}
        {hasTrend && !loading && (
          <div
            data-metric-trend={dir ?? 'unknown'}
            className={clsx('flex flex-wrap items-center gap-x-1 mt-2 text-xs font-medium', trendColor)}
          >
            {dir !== 'none' && <TrendIcon aria-hidden="true" className="h-3 w-3 shrink-0" />}
            {/* The row's status hue paints the ICON (non-text, >= 3:1). The 12 px
                text takes a text token: the status hues fall below 4.5:1 on
                the card in four dark themes. The words carry the meaning. */}
            {word && (
              <span data-trend-direction="" className="text-[var(--color-text-secondary)]">
                {word}
              </span>
            )}
            <span className="text-[var(--color-text-secondary)]">{trendText}</span>
            {judged && (
              <span data-trend-judgement="" className="text-[var(--color-text-secondary)]">
                {dir === positiveDirection ? '(better)' : '(worse)'}
              </span>
            )}
          </div>
        )}
      </div>
      <div className={clsx('p-3 rounded-xl flex-shrink-0', accentBg[accentColor] ?? accentBg.default)}>
        {icon}
      </div>
    </div>
  )
}
