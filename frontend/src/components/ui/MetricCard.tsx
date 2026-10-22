import type { ReactNode } from 'react'
import { TrendingDown, TrendingUp, Minus } from 'lucide-react'
import { clsx } from 'clsx'

export interface MetricData {
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

/** The value's ink (W29a's -ink tokens: >= 4.5:1 on the card and the secondary surface in every theme). */
export type MetricTone = 'good' | 'warn' | 'bad' | 'neutral'

type DataAttributes = { [data: `data-${string}`]: string | undefined }

export interface MetricCardProps extends DataAttributes {
  /** The label: ONE truncated line; the long name goes in `hint` (kpi-labels.spec). */
  title: string
  metric?: MetricData
  loading?: boolean
  /**
   * Which trend direction is GOOD for this metric. Pass-rate style metrics
   * default to 'up'; count-of-bad-things metrics (Failed, Flaky, Duration)
   * should pass 'down' so a falling trend is "(better)", not "(worse)".
   */
  positiveDirection?: 'up' | 'down'
  /** A small inline chart beside the value (the aside slot). */
  sparkline?: ReactNode
  /** The label's hover text (default: the label). */
  hint?: string
  /** `md` (default) is the KPI-strip tile; `sm` an in-panel stat (a score dimension, a facet, a callout). */
  size?: 'md' | 'sm'
  /** The right end of the label row, not uppercase (a dimension's weight, "30%"). */
  note?: string
  /** One truncated 12 px line under the value (a facet's "in Run #12"); not a trend. */
  caption?: ReactNode
  /** The value's ink; default neutral (--color-text). */
  tone?: MetricTone
  /** 0-100: a 4 px bar in the aside slot (a score dimension); `null` draws the empty track. */
  bar?: number | null
  /**
   * Data attributes for the VALUE element (the root gets the others): the score tiles' `data-dimension-value`
   * that the page tests and specs read inside the tile.
   */
  valueData?: DataAttributes
  /** Layout only (opacity-50 on a placeholder, h-full); the surface is the tile's. */
  className?: string
}

const DIRECTION_WORD = { up: 'Up', down: 'Down', flat: 'No change' } as const

const TONE_INK: Record<MetricTone, string> = {
  good: 'text-[var(--ui-success-ink)]',
  warn: 'text-[var(--ui-warning-ink)]',
  bad: 'text-[var(--ui-danger-ink)]',
  neutral: 'text-[var(--color-text)]',
}

/** The bar's fill: the tone's hue (a 4 px mark, not text). */
const TONE_HUE: Record<MetricTone, string> = {
  good: 'var(--ui-success)',
  warn: 'var(--ui-warning)',
  bad: 'var(--ui-danger)',
  neutral: 'var(--color-text-muted)',
}

function dataAttributesOf(rest: Record<string, unknown>): DataAttributes {
  const out: DataAttributes = {}
  for (const [key, value] of Object.entries(rest)) {
    if (key.startsWith('data-') && (typeof value === 'string' || value === undefined)) out[key as `data-${string}`] = value
  }
  return out
}

/**
 * The ONE KPI tile (W29b UIX-09). `md` is the strip tile (`KpiStrip`): a `--text-stat-lg` value (24 px at the desk,
 * 40 in presentation mode), the label in the `.eyebrow` style, the change line on one line, a sparkline or a bar
 * beside the value. `sm` is the in-panel stat (`data-metric-card="stat"`, so the strip specs keep counting the
 * strip's tiles only): an 18 px `--text-stat-sm` value in the sunken well, an optional `note`, `bar` and `caption`.
 *
 * The change line never relies on colour or the icon alone (WCAG 1.4.1): the DIRECTION is a word ("Up" / "Down" /
 * "No change") and whether that is good is a word too ("better" / "worse"). The judgement's hue only paints the
 * icon, which is decoration (`aria-hidden`). Not a live region: a dashboard of polling tiles must not announce
 * itself.
 */
export default function MetricCard({
  title,
  metric,
  loading,
  positiveDirection = 'up',
  sparkline,
  hint,
  size = 'md',
  note,
  caption,
  tone = 'neutral',
  bar,
  valueData,
  className,
  ...rest
}: MetricCardProps) {
  const sm = size === 'sm'
  const dir = metric?.trend_direction
  const hasTrend = metric?.trend != null || Boolean(metric?.trend_text)
  const TrendIcon = dir === 'up' ? TrendingUp : dir === 'down' ? TrendingDown : Minus
  const judged = dir === 'up' || dir === 'down'
  const trendColor = judged
    ? dir === positiveDirection
      ? 'text-[var(--ui-success)]'
      : 'text-[var(--ui-danger)]'
    : 'text-[var(--color-text-muted)]'
  const trendText = metric?.trend_text ?? (metric?.trend != null ? `${Math.abs(metric.trend)}% vs prev period` : '')
  const word = dir && dir !== 'none' ? DIRECTION_WORD[dir] : null
  const judgement = judged ? (dir === positiveDirection ? '(better)' : '(worse)') : null
  const changeLine = [word, trendText].filter(Boolean).join(' ')
  const pct = typeof bar === 'number' && Number.isFinite(bar) ? Math.max(0, Math.min(100, bar)) : null

  return (
    // h-full (md): a card inside a link (KpiLink) fills the strip's row like a bare one does, so a row of tiles is
    // one height when one of them grows.
    <div
      {...dataAttributesOf(rest)}
      data-metric-card={sm ? 'stat' : 'compact'}
      className={clsx(
        sm
          ? 'min-w-0 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2.5 py-2'
          : 'card !px-4 !py-3 min-w-0 h-full',
        className,
      )}
    >
      {note ? (
        <p title={hint ?? title} className="eyebrow mb-1 flex justify-between gap-2 text-[var(--color-text-secondary)]">
          <span className="min-w-0 truncate">{title}</span>
          <span className="shrink-0 font-medium normal-case tracking-normal text-[var(--color-text-muted)]">{note}</span>
        </p>
      ) : (
        <p title={hint ?? title} className="eyebrow mb-1 truncate text-[var(--color-text-secondary)]">
          {title}
        </p>
      )}
      {/* The aside (a sparkline, a bar) takes what the value leaves and is capped to it; under 3rem it drops below
          the value instead. The room-sized value (40 px) left a w-24 sparkline no room: drawn over the value and out
          of the tile (P3 baseline, overview-presentation-kpis). 4rem was too much at the desk: on CI's DejaVu at
          1280 px "88.0%" left 60 px, and Trends' sparkline dropped a line (strip 100 -> 132). */}
      <div className={sm ? 'flex items-center gap-2' : 'flex flex-wrap items-end justify-between gap-x-2 gap-y-1'}>
        {loading ? (
          // The bar is decoration; the busy state is visually hidden TEXT (an aria-label on a role-less div is
          // prohibited: axe aria-prohibited-attr). Not a live region on purpose.
          <div className={clsx('animate-pulse rounded', sm ? 'h-6 w-12 bg-[var(--color-bg-hover)]' : 'h-7 w-20 bg-[var(--color-bg-secondary)]')}>
            <span className="sr-only">Loading</span>
          </div>
        ) : (
          // The stat tokens, never a fixed size: presentation mode raises them (40 px) like every metric value.
          // `leading-[1.3333]` keeps a 32 px line at the desk for the 24 px value (24 px for the 18 px one).
          <p
            {...valueData}
            className={clsx(
              'whitespace-nowrap leading-[1.3333] tabular-nums',
              sm ? 'text-[length:var(--text-stat-sm)] font-semibold' : 'text-[length:var(--text-stat-lg)] font-bold',
              TONE_INK[tone],
            )}
          >
            {metric?.value ?? '—'}
          </p>
        )}
        {sparkline && !loading && (
          <div data-metric-aside="" className="flex min-w-12 flex-1 justify-end [&>*]:max-w-full">
            {sparkline}
          </div>
        )}
        {bar !== undefined && !sparkline && !loading && (
          <span data-metric-bar="" className="h-1 min-w-12 flex-1 overflow-hidden rounded-full bg-[var(--color-border)]">
            {pct !== null && <i className="block h-full rounded-full" style={{ width: `${pct}%`, background: TONE_HUE[tone] }} />}
          </span>
        )}
      </div>
      {hasTrend && !loading && (
        <p
          data-metric-trend={dir ?? 'unknown'}
          title={[changeLine, judgement].filter(Boolean).join(' ')}
          className={clsx('mt-1 flex min-w-0 items-center gap-1 text-xs font-medium', trendColor)}
        >
          {dir !== 'none' && <TrendIcon aria-hidden="true" className="h-3 w-3 shrink-0" />}
          {/* Only the change text truncates: the judgement is what a narrow strip card must never cut
              ("vs previous period (bet…"). */}
          <span className="min-w-0 truncate text-[var(--color-text-secondary)]">{changeLine}</span>
          {judgement && (
            <span data-trend-judgement="" className="shrink-0 text-[var(--color-text-secondary)]">
              {judgement}
            </span>
          )}
        </p>
      )}
      {caption && !loading && (
        <div data-metric-caption="" className="mt-0.5 truncate text-xs text-[var(--color-text-secondary)]">
          {caption}
        </div>
      )}
    </div>
  )
}
