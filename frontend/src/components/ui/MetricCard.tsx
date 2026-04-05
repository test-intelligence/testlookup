import { ReactNode } from 'react'
import { TrendingDown, TrendingUp, Minus } from 'lucide-react'
import { clsx } from 'clsx'

interface MetricData {
  value: string | number
  trend?: number | null
  trend_direction?: 'up' | 'down' | 'flat'
}

interface Props {
  title: string
  metric?: MetricData
  icon: ReactNode
  accentColor?: string
  loading?: boolean
}

export default function MetricCard({ title, metric, icon, accentColor = 'default', loading }: Props) {
  const dir = metric?.trend_direction
  const TrendIcon = dir === 'up' ? TrendingUp : dir === 'down' ? TrendingDown : Minus
  const trendColor = dir === 'up' ? 'text-emerald-400' : dir === 'down' ? 'text-red-400' : 'text-[var(--color-text-muted)]'

  const accentBg: Record<string, string> = {
    default: 'bg-white/5 text-[var(--color-text)]',
    green:   'bg-emerald-500/10 text-emerald-400',
    red:     'bg-red-500/10 text-red-400',
    amber:   'bg-amber-500/10 text-amber-400',
    purple:  'bg-purple-500/10 text-purple-400',
  }

  return (
    <div className="card flex items-start justify-between gap-4">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">{title}</p>
        {loading ? (
          <div className="h-8 w-24 bg-[var(--color-bg-secondary)] rounded animate-pulse" />
        ) : (
          <p className="text-3xl font-bold text-[var(--color-text)] tabular-nums">{metric?.value ?? '—'}</p>
        )}
        {metric?.trend != null && !loading && (
          <div className={clsx('flex items-center gap-1 mt-2 text-xs font-medium', trendColor)}>
            <TrendIcon className="h-3 w-3" />
            <span>{Math.abs(metric.trend)}% vs prev period</span>
          </div>
        )}
      </div>
      <div className={clsx('p-3 rounded-xl flex-shrink-0', accentBg[accentColor] ?? accentBg.default)}>
        {icon}
      </div>
    </div>
  )
}
