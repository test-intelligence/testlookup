/**
 * ComputeNode — the 226×132 stage card used inside the compute canvas.
 *
 * Each card is absolutely positioned by its parent ``ComputeCanvas`` at the
 * coordinates from ``layout.POS``. The card itself is responsible for
 * status-based chrome (border, background, pill), the description, the 4-cell
 * metric grid, and the running/failed suffix rows.
 */
import { clsx } from 'clsx'
import { useNow } from '@/hooks/useNow'
import {
  AlertTriangle,
  Bot,
  Bug,
  CircleAlert,
  Database,
  FileText,
  Layers,
  Shield,
  Stethoscope,
} from 'lucide-react'
import type { ComputeStage } from './types'
import { NODE_W, NODE_H, POS } from './layout'

const GLYPH_MAP = {
  database: Database,
  alert: AlertTriangle,
  bot: Bot,
  layers: Layers,
  bug: Bug,
  warn: CircleAlert,
  stethoscope: Stethoscope,
  file: FileText,
  shield: Shield,
} as const

// Status → border, background, pill colours, icon tint. Pulled out so the
// renderer reads as a flat lookup instead of nested ternaries.
const STATUS_STYLE: Record<ComputeStage['status'], {
  border: string
  background: string
  pillText: string
  pillBg: string
  pillBorder: string
  iconBg: string
  iconFg: string
  opacity?: number
  borderDashed?: boolean
}> = {
  done: {
    border: 'var(--color-border)',
    background: 'var(--color-bg-card)',
    pillText: 'var(--status-passed)',
    pillBg: 'var(--status-passed-bg-soft)',
    pillBorder: 'color-mix(in srgb, var(--status-passed) 40%, transparent)',
    iconBg: 'var(--status-passed-bg-soft)',
    iconFg: 'var(--status-passed)',
  },
  running: {
    border: 'color-mix(in srgb, var(--color-accent) 50%, transparent)',
    background: 'color-mix(in srgb, var(--color-accent) 4%, transparent)',
    pillText: 'var(--color-accent)',
    pillBg: 'var(--color-accent-muted)',
    pillBorder: 'color-mix(in srgb, var(--color-accent) 40%, transparent)',
    iconBg: 'var(--color-accent-muted)',
    iconFg: 'var(--color-accent)',
  },
  failed: {
    border: 'color-mix(in srgb, var(--status-failed) 50%, transparent)',
    background: 'color-mix(in srgb, var(--status-failed) 4%, transparent)',
    pillText: 'var(--status-failed)',
    pillBg: 'var(--status-failed-bg-soft)',
    pillBorder: 'color-mix(in srgb, var(--status-failed) 40%, transparent)',
    iconBg: 'var(--status-failed-bg-soft)',
    iconFg: 'var(--status-failed)',
  },
  skipped: {
    border: 'var(--color-border)',
    background: 'transparent',
    pillText: 'var(--color-text-muted)',
    pillBg: 'var(--color-bg-secondary)',
    pillBorder: 'var(--color-border)',
    iconBg: 'var(--color-bg-secondary)',
    iconFg: 'var(--color-text-muted)',
    opacity: 0.55,
    borderDashed: true,
  },
  pending: {
    border: 'var(--color-border)',
    background: 'var(--color-bg-card)',
    pillText: 'var(--color-text-muted)',
    pillBg: 'var(--color-bg-secondary)',
    pillBorder: 'var(--color-border)',
    iconBg: 'var(--color-bg-secondary)',
    iconFg: 'var(--color-text-muted)',
  },
}

const PILL_LABEL: Record<ComputeStage['status'], string> = {
  done: 'done',
  running: 'running',
  failed: 'failed',
  skipped: 'skipped',
  pending: 'pending',
}

interface MetricCellProps {
  k: string
  v: string | number | null | undefined
  tone?: 'ok' | 'warn' | 'bad' | 'neutral'
}

function MetricCell({ k, v, tone = 'neutral' }: MetricCellProps) {
  const valueColor =
    tone === 'ok'   ? 'var(--status-passed)'
    : tone === 'warn' ? 'var(--status-broken)'
    : tone === 'bad'  ? 'var(--status-failed)'
    : 'var(--color-text)'
  return (
    <div
      className="rounded-md border px-1.5 py-1"
      style={{ background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
    >
      <div
        className="text-[9.5px] uppercase text-[var(--color-text-faint)]"
        style={{ letterSpacing: '0.06em' }}
      >
        {k}
      </div>
      <div className="text-[11.5px] font-mono leading-tight" style={{ color: valueColor }}>
        {v ?? '—'}
      </div>
    </div>
  )
}

function ProgressBar({ pct }: { pct: number }) {
  const clamped = Math.max(0, Math.min(100, pct))
  return (
    <div
      className="h-1 rounded-full overflow-hidden"
      style={{ background: 'var(--color-accent-muted)' }}
      aria-hidden
    >
      <div
        className="h-full"
        style={{ width: `${clamped}%`, background: 'var(--color-accent)' }}
      />
    </div>
  )
}

interface ComputeNodeProps {
  stage: ComputeStage
  selected: boolean
  onSelect: () => void
}

export default function ComputeNode({ stage, selected, onSelect }: ComputeNodeProps) {
  const pos = POS[stage.id]
  const style = STATUS_STYLE[stage.status]
  const Icon = GLYPH_MAP[stage.glyph] ?? FileText
  // Ticking clock so the running progress bar advances without calling the
  // impure Date.now() during render (react-hooks/purity).
  const now = useNow(500)

  // Confidence tone — handoff: ok when > 80%.
  const confValue = stage.metrics.confidence
  const confTone: MetricCellProps['tone'] = (() => {
    if (confValue == null) return 'neutral'
    const n = parseFloat(String(confValue))
    if (!Number.isFinite(n)) return 'neutral'
    return n > 80 ? 'ok' : 'neutral'
  })()
  // Cost tone — handoff: warn when parsed > $0.50.
  const costStr = stage.metrics.cost
  const costTone: MetricCellProps['tone'] = (() => {
    if (!costStr) return 'neutral'
    const n = parseFloat(String(costStr).replace('$', ''))
    if (!Number.isFinite(n)) return 'neutral'
    return n > 0.5 ? 'warn' : 'neutral'
  })()

  // Running progress — derived from start/eta. Fall back to a thin 0%
  // track when no timing data is available rather than flickering.
  const runningPct = (() => {
    if (stage.status !== 'running') return 0
    if (stage.startMs == null || stage.etaMs == null) return 0
    if (stage.etaMs <= stage.startMs) return 0
    const elapsed = now / 1000 - stage.startMs
    return ((elapsed) / (stage.etaMs - stage.startMs)) * 100
  })()
  const etaSeconds = stage.startMs != null && stage.etaMs != null
    ? Math.max(0, Math.round(stage.etaMs - stage.startMs))
    : null

  return (
    <button
      type="button"
      onClick={onSelect}
      title={stage.skipReason ?? undefined}
      className={clsx(
        'absolute text-left rounded-xl p-3 transition-all duration-150',
        'focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/50',
      )}
      style={{
        left: pos.x,
        top: pos.y,
        width: NODE_W,
        height: NODE_H,
        borderStyle: style.borderDashed ? 'dashed' : 'solid',
        borderWidth: '1px',
        borderColor: selected ? 'var(--color-accent)' : style.border,
        background: style.background,
        boxShadow: selected ? '0 0 0 3px var(--color-accent-muted)' : undefined,
        opacity: style.opacity,
        cursor: 'pointer',
      }}
    >
      {/* Head row — icon, name, status pill */}
      <div className="flex items-center gap-2">
        <div
          className="w-[22px] h-[22px] rounded-md flex items-center justify-center shrink-0"
          style={{ background: style.iconBg, color: style.iconFg }}
        >
          <Icon className="w-3.5 h-3.5" />
        </div>
        <div className="text-[13px] font-semibold text-[var(--color-text)] truncate flex-1">
          {stage.name}
        </div>
        <span
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9.5px] border"
          style={{ color: style.pillText, background: style.pillBg, borderColor: style.pillBorder }}
        >
          {stage.status === 'running' && (
            <span
              className="inline-block w-1 h-1 rounded-full"
              style={{ background: style.pillText }}
              aria-hidden
            />
          )}
          {PILL_LABEL[stage.status]}
        </span>
      </div>

      {/* Description — reserved height so cards align across rows */}
      <div
        className="mt-2 text-[11px] text-[var(--color-text-muted)] line-clamp-2"
        style={{ minHeight: 28, lineHeight: 1.4 }}
      >
        {stage.desc}
      </div>

      {/* 2×2 metric grid */}
      <div className="mt-2 grid grid-cols-2 gap-1.5">
        <MetricCell k="duration" v={stage.dur ?? '—'} />
        {stage.metrics.confidence != null ? (
          <MetricCell k="conf" v={stage.metrics.confidence} tone={confTone} />
        ) : (
          <MetricCell k="evidence" v={stage.metrics.evidence ?? '—'} />
        )}
        <MetricCell k="tokens" v={stage.metrics.tokens ?? '—'} />
        <MetricCell k="cost" v={stage.metrics.cost ?? '—'} tone={costTone} />
      </div>

      {/* Failed-state retry suffix */}
      {stage.status === 'failed' && (
        <div className="mt-1.5 flex items-center gap-1.5 text-[10.5px]" style={{ color: 'var(--status-failed)' }}>
          <CircleAlert className="w-3 h-3" />
          retry 1/3 queued
        </div>
      )}

      {/* Running-state progress + ETA */}
      {stage.status === 'running' && (
        <div className="mt-1.5">
          <ProgressBar pct={runningPct} />
          <div className="mt-1 flex justify-between text-[10px] font-mono text-[var(--color-text-muted)]">
            <span>{stage.dur ?? '—'}</span>
            {etaSeconds != null && <span>ETA {etaSeconds}s total</span>}
          </div>
        </div>
      )}
    </button>
  )
}
