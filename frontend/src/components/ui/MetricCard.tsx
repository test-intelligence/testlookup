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
