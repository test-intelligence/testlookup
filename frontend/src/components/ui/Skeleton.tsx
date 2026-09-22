/**
 * Loading placeholders that hold the space of what is loading (VIZ-109).
 *
 * The point is NO LAYOUT SHIFT: every variant takes an explicit size, so the
 * content that replaces it lands where the placeholder was. `chart` draws a
 * faint axis-and-bars silhouette at the chart's own height.
 *
 * A skeleton is decoration: `aria-hidden`, no text. The CALLER owns the
 * loading announcement (`aria-busy` on the region, or a status line) — a
 * screen reader must not read out a row of grey bars.
 *
 * The pulse is `motion-safe:` only, so `prefers-reduced-motion: reduce` gets
 * a still placeholder.
 */
import type { CSSProperties } from 'react'

export type SkeletonVariant = 'line' | 'block' | 'chart'

export interface SkeletonProps {
  variant?: SkeletonVariant
  /** CSS width; default `100%`. */
  width?: number | string
  /** CSS height. Defaults: line 1em-ish (12px), block 96px, chart 240px. */
  height?: number | string
  /** `line` only: how many lines to stack. The last is shortened, as text is. */
  lines?: number
  className?: string
  'data-testid'?: string
}

const DEFAULT_HEIGHT: Record<SkeletonVariant, number> = { line: 12, block: 96, chart: 240 }

/** Relative bar heights for the chart silhouette — fixed, so it never flickers between renders. */
const CHART_BARS = [42, 64, 55, 78, 60, 86, 70, 52, 74, 66]

const SHIMMER = 'bg-[var(--color-bg-hover)] motion-safe:animate-pulse'

const toCss = (value: number | string): string => (typeof value === 'number' ? `${value}px` : value)

export default function Skeleton({
  variant = 'line',
  width = '100%',
  height,
  lines = 1,
  className = '',
  'data-testid': testId,
}: SkeletonProps) {
  const resolvedHeight = toCss(height ?? DEFAULT_HEIGHT[variant])

  if (variant === 'line') {
    const count = Math.max(1, Math.floor(lines))
    return (
      <div
        aria-hidden="true"
        data-skeleton="line"
        data-testid={testId}
        className={`flex flex-col gap-2 ${className}`}
        style={{ width: toCss(width) }}
      >
        {Array.from({ length: count }, (_, index) => (
          <div
            key={index}
            className={`rounded ${SHIMMER}`}
            style={{ height: resolvedHeight, width: count > 1 && index === count - 1 ? '60%' : '100%' }}
          />
        ))}
      </div>
    )
  }

  const box: CSSProperties = { width: toCss(width), height: resolvedHeight }

  if (variant === 'block') {
    return (
      <div
        aria-hidden="true"
        data-skeleton="block"
        data-testid={testId}
        className={`rounded-lg ${SHIMMER} ${className}`}
        style={box}
      />
    )
  }

  return (
    <div
      aria-hidden="true"
      data-skeleton="chart"
      data-testid={testId}
      className={`relative flex items-end gap-[4%] overflow-hidden rounded-lg border border-[var(--color-border)] px-[4%] pb-6 pt-4 ${className}`}
      style={box}
    >
      {/* The x axis the bars stand on. */}
      <div className="absolute inset-x-[4%] bottom-6 h-px bg-[var(--color-border)]" />
      {CHART_BARS.map((percent, index) => (
        <div key={index} className={`flex-1 rounded-t ${SHIMMER}`} style={{ height: `${percent}%` }} />
      ))}
    </div>
  )
}
