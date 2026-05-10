/**
 * /agents — Workflow pane (Direction A: Subway Map).
 *
 * Replaces the previous flat-grid AgentStatusPage layout. Faithful port of
 * the design handoff in `TestLookup Design System (1)/design_handoff_workflows_pane/`
 * — clean horizontal DAG with stage cards on a 2-row track + an SVG edge
 * layer + a decision diamond + RunMeta strip + EventStrip + RightRail.
 *
 * Layout: TopBar / ModeTabs / Body { left: [SubwayTrack, RunMeta,
 * VerdictPanel?, EventStrip] | right: RightRail }.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  Activity, AlertTriangle, ArrowRight, Bot, Bug, ChevronDown, ChevronRight,
  Database, FileText, Layers, Loader2, RefreshCw, Shield, Stethoscope, Zap,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines } from '@/hooks/useAgentRuns'
import { useAIConfig } from '@/hooks/useAIConfig'
import { usePermissions } from '@/hooks/usePermissions'
import { useProjectChangeRedirect, useProjectChangeReset } from '@/hooks/useProjectChange'
import { useRun } from '@/hooks/useRuns'
import { useRunIntelligence } from '@/hooks/useRunIntelligence'
import agentService from '@/services/agentService'
import type { AgentPipelineRun, AgentStageResult } from '@/types/agent'

// ── Canonical deep-pipeline stage layout ────────────────────────────────────

type GlyphName =
  | 'database' | 'alert' | 'bot' | 'layers' | 'bug' | 'warn'
  | 'stethoscope' | 'file' | 'shield'

interface StageLayout {
  name: string
  desc: string
  glyph: GlyphName
  col: number
  row: 0 | 1
  branch: 'main' | 'llm' | 'parallel' | 'heuristic'
}

const STAGE_LAYOUT: Record<string, StageLayout> = {
  ingestion:           { name: 'Ingestion',           desc: 'Load, validate, and enrich the run data',           glyph: 'database',    col: 0, row: 0, branch: 'main' },
  anomaly:             { name: 'Anomaly Detection',   desc: 'Compare against baselines, surface regressions',    glyph: 'alert',       col: 1, row: 0, branch: 'main' },
  rca:                 { name: 'Root Cause Analysis', desc: 'ReAct investigation across logs, traces, history',  glyph: 'bot',         col: 3, row: 0, branch: 'llm' },
  failure_clustering:  { name: 'Failure Clustering',  desc: 'Group related failures into defect-shaped clusters', glyph: 'layers',     col: 3, row: 1, branch: 'parallel' },
  cluster:             { name: 'Failure Clustering',  desc: 'Group related failures into defect-shaped clusters', glyph: 'layers',     col: 3, row: 1, branch: 'parallel' },
  defect_triage:       { name: 'Defect Triage',       desc: 'Prepare Jira-ready defects and owner guidance',     glyph: 'bug',         col: 4, row: 1, branch: 'heuristic' },
  triage:              { name: 'Defect Triage',       desc: 'Prepare Jira-ready defects and owner guidance',     glyph: 'bug',         col: 4, row: 1, branch: 'heuristic' },
  flaky_sentinel:      { name: 'Flaky Sentinel',      desc: 'Detect recurring flaky or unstable tests',          glyph: 'warn',        col: 4, row: 1, branch: 'parallel' },
  flaky:               { name: 'Flaky Sentinel',      desc: 'Detect recurring flaky or unstable tests',          glyph: 'warn',        col: 4, row: 1, branch: 'parallel' },
  test_health:         { name: 'Test Health',         desc: 'Inspect test-code anti-patterns and health risks',  glyph: 'stethoscope', col: 5, row: 1, branch: 'parallel' },
  health:              { name: 'Test Health',         desc: 'Inspect test-code anti-patterns and health risks',  glyph: 'stethoscope', col: 5, row: 1, branch: 'parallel' },
  summary:             { name: 'Summary',             desc: 'Compose the executive and role-specific narrative', glyph: 'file',        col: 6, row: 0, branch: 'main' },
  release_risk:        { name: 'Release Risk',        desc: 'Turn the run into a go / conditional-go / no-go',   glyph: 'shield',      col: 7, row: 0, branch: 'main' },
  release:             { name: 'Release Risk',        desc: 'Turn the run into a go / conditional-go / no-go',   glyph: 'shield',      col: 7, row: 0, branch: 'main' },
}

const DECISION_COL = 2
const DECISION_ROW = 0
const DECISION_LABEL = 'route_analysis_mode'
const DECISION_CHOSEN = 'llm'

const GLYPH_ICON: Record<GlyphName, typeof Database> = {
  database: Database, alert: AlertTriangle, bot: Bot, layers: Layers, bug: Bug,
  warn: AlertTriangle, stethoscope: Stethoscope, file: FileText, shield: Shield,
}

type DisplayStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

interface DisplayStage extends StageLayout {
  id: string
  status: DisplayStatus
  startMs: number | null
  endMs: number | null
  durLabel: string
  raw: AgentStageResult
}

function toDisplayStatus(s: string | null | undefined): DisplayStatus {
  const normalized = (s ?? 'pending').toLowerCase()
  if (normalized === 'completed' || normalized === 'success' || normalized === 'done') return 'done'
  if (normalized === 'running' || normalized === 'in_progress') return 'running'
  if (normalized === 'failed' || normalized === 'error') return 'failed'
  if (normalized === 'skipped') return 'skipped'
  return 'pending'
}

function buildDisplayStages(stages: AgentStageResult[], pipelineStartedAt: string | null): DisplayStage[] {
  const t0 = pipelineStartedAt ? new Date(pipelineStartedAt).getTime() : null
  const out: DisplayStage[] = []
  for (const stage of stages) {
    const layout = STAGE_LAYOUT[stage.stage_name]
    if (!layout) continue
    const status = toDisplayStatus(stage.status)
    const startMs = stage.started_at && t0 != null
      ? Math.max(0, Math.round((new Date(stage.started_at).getTime() - t0) / 1000))
      : null
    const endMs = stage.completed_at && t0 != null
      ? Math.max(0, Math.round((new Date(stage.completed_at).getTime() - t0) / 1000))
      : null
    let durLabel = '—'
    if (startMs != null && endMs != null) {
      const d = endMs - startMs
      durLabel = d >= 60 ? `${Math.floor(d / 60)}m ${d % 60}s` : `${d}s`
    } else if (status === 'running' && startMs != null) {
      durLabel = 'running…'
    }
    out.push({ id: stage.stage_name, ...layout, status, startMs, endMs, durLabel, raw: stage })
  }
  return out
}

function elapsedSeconds(startedAt: string | null, completedAt: string | null): number {
  if (!startedAt) return 0
  const t0 = new Date(startedAt).getTime()
  const t1 = completedAt ? new Date(completedAt).getTime() : Date.now()
  return Math.max(0, Math.round((t1 - t0) / 1000))
}

function formatElapsed(seconds: number): string {
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  return `${seconds}s`
}

// Status → background/border/foreground tokens for both subway cards and
// status dots. Centralised so the views stay visually coherent.
const NODE_STYLE: Record<DisplayStatus, { bg: string; border: string; fg: string; dot: string }> = {
  done:    { bg: 'var(--color-bg-card)',           border: 'var(--color-border)',                 fg: 'rgb(63 185 80)',         dot: 'rgb(63 185 80)' },
  running: { bg: 'rgba(68,147,248,.04)',           border: 'rgba(68,147,248,.45)',                fg: 'var(--color-accent)',    dot: 'var(--color-accent)' },
  failed:  { bg: 'rgba(248,81,73,.04)',            border: 'rgba(248,81,73,.45)',                 fg: 'rgb(248 81 73)',         dot: 'rgb(248 81 73)' },
  skipped: { bg: 'transparent',                    border: 'var(--color-border)',                 fg: 'var(--color-text-faint)', dot: 'transparent' },
  pending: { bg: 'var(--color-bg-card)',           border: 'var(--color-border)',                 fg: 'var(--color-text-faint)', dot: 'var(--color-text-faint)' },
}

function statusOverride(s: DisplayStage, snapshot: boolean): DisplayStatus {
  return snapshot && (s.status === 'running' || s.status === 'pending') ? 'done' : s.status
}

// ── ModeTabs ────────────────────────────────────────────────────────────────

type ModeTab = 'live' | 'debug' | 'audit' | 'compare'

function ModeTabs({ mode, setMode, isLive }: { mode: ModeTab; setMode: (m: ModeTab) => void; isLive: boolean }) {
  const tabs: { id: ModeTab; label: string; icon: typeof Activity }[] = [
    { id: 'live', label: 'Live', icon: Loader2 },
    { id: 'debug', label: 'Debug', icon: Bug },
    { id: 'audit', label: 'Audit', icon: FileText },
    { id: 'compare', label: 'Compare', icon: Activity },
  ]
  return (
    <div className="flex items-center gap-1 px-6 border-b" style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-card)', height: 41 }}>
      {tabs.map(t => {
        const active = mode === t.id
        const TabIcon = t.icon
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => setMode(t.id)}
            className={clsx(
              'flex items-center gap-1.5 px-3 h-full text-[12px] transition-colors relative',
              active ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]',
            )}
          >
            <TabIcon className={clsx('h-3.5 w-3.5', t.id === 'live' && active && 'animate-spin')} />
            {t.label}
            {t.id === 'live' && active && isLive && (
              <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
            )}
            {active && <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[var(--color-accent)]" />}
          </button>
        )
      })}
    </div>
  )
}

// ── RunPicker — horizontal strip for switching pipelines ───────────────────

function RunPicker({
  pipelines, activePipelineId, onSelect,
}: {
  pipelines: AgentPipelineRun[]
  activePipelineId: string | null
  onSelect: (p: AgentPipelineRun) => void
}) {
  return (
    <div
      className="flex items-center gap-3 px-6 py-2 border-b overflow-x-auto"
      style={{ background: 'var(--color-bg-card)', borderColor: 'var(--color-border)' }}
    >
      <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)] flex-shrink-0">
        Recent runs
      </span>
      <span className="text-[10.5px] text-[var(--color-text-muted)] flex-shrink-0">
        {pipelines.length}
      </span>
      <div className="flex gap-1.5 flex-shrink-0">
        {pipelines.slice(0, 12).map(p => {
          const active = p.id === activePipelineId
          const status = (p.status ?? '').toLowerCase()
          const dot =
            status === 'running' ? 'var(--color-accent)' :
            status === 'failed' ? 'rgb(248 81 73)' :
            status === 'partial' ? 'rgb(210 153 34)' :
            status === 'completed' || status === 'success' ? 'rgb(63 185 80)' :
            'var(--color-text-faint)'
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => onSelect(p)}
              title={`${p.test_run_id} · ${status} · ${new Date(p.created_at).toLocaleString()}`}
              className="inline-flex items-center gap-1.5 px-2 py-1 rounded border text-[11px] font-mono whitespace-nowrap transition-colors"
              style={{
                background: active ? 'var(--color-accent-muted)' : 'var(--color-bg-secondary)',
                borderColor: active ? 'rgba(68,147,248,.40)' : 'var(--color-border)',
                color: active ? 'var(--color-accent)' : 'var(--color-text-secondary)',
              }}
            >
              <span
                className="rounded-full"
                style={{
                  width: 6,
                  height: 6,
                  background: dot,
                  boxShadow: status === 'running' ? '0 0 0 2px var(--color-accent-muted)' : 'none',
                  animation: status === 'running' ? 'pulse 1.6s infinite' : 'none',
                }}
              />
              {p.test_run_id.slice(0, 8)}
              <span style={{ color: active ? 'var(--color-accent)' : 'var(--color-text-muted)' }}>·</span>
              <span style={{ color: active ? 'var(--color-accent)' : 'var(--color-text-muted)' }}>
                {new Date(p.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
            </button>
          )
        })}
        {pipelines.length > 12 && (
          <span className="inline-flex items-center px-2 py-1 text-[11px] text-[var(--color-text-muted)]">
            +{pipelines.length - 12} more
          </span>
        )}
      </div>
    </div>
  )
}

// ── SubwayTrack — the main canvas ───────────────────────────────────────────

const COLS = 8
const COL_W = 188
const CARD_W = 172
const ROW_H = 132
const TOP_PAD = 28
const TRACK_W = COLS * COL_W

const cardX = (col: number) => col * COL_W + (COL_W - CARD_W) / 2
const cardCenterX = (col: number) => col * COL_W + COL_W / 2
const rowCenterY = (row: number) => TOP_PAD + row * ROW_H + 56

interface EdgeProps {
  from: [number, number]
  to: [number, number]
  active?: boolean
  dashed?: boolean
  ghost?: boolean
  curve?: boolean
  curveUp?: boolean
  muted?: boolean
  label?: string
  labelTone?: 'accent' | 'muted'
}

function Edge({ from, to, active, dashed, ghost, curve, curveUp, muted, label, labelTone }: EdgeProps) {
  const stroke = active ? 'var(--color-accent)'
    : muted ? 'var(--color-text-faint)'
    : ghost ? 'var(--color-text-faint)'
    : 'var(--color-border-light)'
  const sw = active ? 2.5 : 1.75
  const dashArr = dashed || ghost ? '5 5' : '0'
  const opacity = ghost ? 0.35 : 1
  let d: string
  if (curve || curveUp) {
    const mx = (from[0] + to[0]) / 2
    d = `M${from[0]},${from[1]} C ${mx},${from[1]} ${mx},${to[1]} ${to[0]},${to[1]}`
  } else {
    d = `M${from[0]},${from[1]} L${to[0]},${to[1]}`
  }
  return (
    <g>
      <path
        d={d}
        fill="none"
        stroke={stroke}
        strokeWidth={sw}
        strokeDasharray={dashArr}
        opacity={opacity}
        markerEnd={`url(#a-arrow-${active ? 'blue' : 'grey'})`}
      />
      {label && (
        <foreignObject x={(from[0] + to[0]) / 2 - 28} y={(from[1] + to[1]) / 2 - 11} width="56" height="22">
          <div className="flex justify-center">
            <span
              className="font-mono px-1.5 rounded border"
              style={{
                fontSize: 9.5,
                lineHeight: 1.6,
                background: labelTone === 'accent' ? 'var(--color-accent-muted)' : 'var(--color-bg-card)',
                color: labelTone === 'accent' ? 'var(--color-accent)' : 'var(--color-text-faint)',
                borderColor: labelTone === 'accent' ? 'rgba(68,147,248,.40)' : 'var(--color-border)',
              }}
            >
              {label}
            </span>
          </div>
        </foreignObject>
      )}
    </g>
  )
}

interface SubwayCardProps {
  stage: DisplayStage
  x: number
  y: number
  selected: boolean
  small?: boolean
  snapshot: boolean
  onClick: () => void
}

function SubwayCard({ stage, x, y, selected, small, snapshot, onClick }: SubwayCardProps) {
  const status = statusOverride(stage, snapshot)
  const styling = NODE_STYLE[status]
  const Icon = GLYPH_ICON[stage.glyph]
  return (
    <button
      type="button"
      onClick={onClick}
      title={stage.raw.error ?? (status === 'skipped' ? 'Skipped' : '')}
      className={clsx(
        'absolute rounded-[14px] border text-left transition-all hover:opacity-100',
        status === 'skipped' && 'hover:!opacity-100',
      )}
      style={{
        left: x,
        top: y,
        width: CARD_W,
        paddingTop: small ? 10 : 12,
        paddingBottom: small ? 10 : 12,
        paddingLeft: 14,
        paddingRight: 14,
        background: selected ? 'var(--color-bg-hover)' : styling.bg,
        borderColor: selected ? 'var(--color-accent)' : styling.border,
        borderStyle: status === 'skipped' ? 'dashed' : 'solid',
        boxShadow: selected ? '0 0 0 3px var(--color-accent-muted)' : 'none',
        opacity: status === 'skipped' ? 0.58 : 1,
        cursor: 'pointer',
        overflow: 'hidden',
      }}
    >
      <div className="flex items-center gap-2">
        <span
          className="inline-flex items-center justify-center w-[18px] h-[18px] rounded"
          style={{ color: styling.fg }}
        >
          <Icon className="h-3.5 w-3.5" />
        </span>
        <span
          className="flex-1 truncate"
          style={{
            fontSize: small ? 12.5 : 13.5,
            fontWeight: 600,
            color: selected ? 'var(--color-text)' : 'var(--color-text)',
          }}
        >
          {stage.name}
        </span>
        <span
          className="rounded-full"
          style={{
            width: 9,
            height: 9,
            background: styling.dot === 'transparent' ? 'transparent' : styling.dot,
            border: status === 'skipped' ? '1px dashed var(--color-text-faint)' : 'none',
            boxShadow: status === 'running' ? '0 0 0 3px var(--color-accent-muted)' : 'none',
            animation: status === 'running' ? 'pulse 1.6s infinite' : 'none',
          }}
        />
      </div>
      {!small && (
        <div className="mt-1.5 text-[11.5px] leading-snug" style={{ color: 'var(--color-text-muted)' }}>
          {stage.desc}
        </div>
      )}
      <div className="mt-2 flex items-center gap-1.5 font-mono text-[11px]" style={{ color: styling.fg }}>
        <span>{status}</span>
        {stage.durLabel !== '—' && (
          <>
            <span style={{ color: 'var(--color-text-faint)' }}>·</span>
            <span style={{ color: 'var(--color-text-secondary)' }}>{stage.durLabel}</span>
          </>
        )}
        {status === 'failed' && (
          <span style={{ color: 'rgb(210 153 34)' }}>· retry queued</span>
        )}
      </div>
      {status === 'running' && (
        <div
          className="absolute left-0 right-0 bottom-0 overflow-hidden"
          style={{ height: 2, background: 'var(--color-accent-muted)' }}
        >
          <span
            className="absolute top-0 bottom-0"
            style={{
              width: '35%',
              background: 'linear-gradient(90deg, transparent, var(--color-accent), transparent)',
              animation: 'testlookup-slide 1.6s infinite',
            }}
          />
        </div>
      )}
    </button>
  )
}

/**
 * Wraps SubwayTrack in a horizontally scale-to-fit container. The track has a
 * fixed natural width (TRACK_W + 48 padding ≈ 1552 px) that can't comfortably
 * shrink without a major coord rewrite, so on narrower windows we scale it
 * down via CSS transform and shrink the outer element's height to match. At
 * full width we render at 1.0 — no scale, no fuzziness. ResizeObserver keeps
 * us in sync with window resizes and split-pane drags.
 */
function ScaleToFit({ naturalW, naturalH, children }: { naturalW: number; naturalH: number; children: React.ReactNode }) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [scale, setScale] = useState(1)

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const compute = () => {
      const w = el.clientWidth
      if (!w) return
      // Don't scale up — the natural design is the maximum. Scale down only
      // when the available width is narrower than naturalW.
      const next = Math.min(1, w / naturalW)
      setScale(prev => (Math.abs(prev - next) > 0.005 ? next : prev))
    }
    compute()
    const obs = new ResizeObserver(compute)
    obs.observe(el)
    return () => obs.disconnect()
  }, [naturalW])

  return (
    <div ref={containerRef} className="w-full" style={{ overflow: 'hidden' }}>
      <div
        style={{
          width: naturalW,
          height: naturalH * scale,
          transform: `scale(${scale})`,
          transformOrigin: 'top left',
        }}
      >
        {children}
      </div>
    </div>
  )
}

function SubwayTrack({
  stages, selectedId, onSelect, snapshot, pipelineLabel,
}: {
  stages: DisplayStage[]
  selectedId: string | null
  onSelect: (id: string) => void
  snapshot: boolean
  pipelineLabel: string
}) {
  const decisionCenter = useMemo(
    () => ({ x: cardCenterX(DECISION_COL), y: rowCenterY(DECISION_ROW) }),
    [],
  )

  // Stage positions: pull canonical positions but let the col/row from
  // STAGE_LAYOUT drive the layout. Hand-tune triage and flaky's x because
  // they share col=4 in the canonical map.
  const cardLayoutFor = (stage: DisplayStage): { x: number; y: number; small: boolean; xShift: number } => {
    const small = stage.row === 1
    const xShift = stage.id.startsWith('triage') || stage.id.startsWith('defect_triage') ? -24
      : stage.id.startsWith('flaky') ? 18
      : 0
    return {
      x: cardX(stage.col) + 24 + xShift,
      y: TOP_PAD + stage.row * ROW_H + 70,
      small,
      xShift,
    }
  }

  const get = (key: string) => stages.find(s => s.id === key || s.id.startsWith(key + '_') || (key === 'cluster' && s.id === 'failure_clustering') || (key === 'flaky' && s.id === 'flaky_sentinel') || (key === 'health' && s.id === 'test_health') || (key === 'release' && s.id === 'release_risk') || (key === 'triage' && s.id === 'defect_triage'))

  // Reserve vertical space for the absolutely-positioned cards + SVG. Without
  // this, the parent collapses to the title strip's height and the next
  // sibling (RunMeta, VerdictPanel when snapshot is open, EventStrip) paints
  // on top of the bottom-row stage cards. Math:
  //   - SVG: top 70, height TOP_PAD + 2*ROW_H + 8 = 300   → bottom 370
  //   - Row 1 cards: top TOP_PAD + ROW_H + 70 = 230, ~88 px tall → bottom ~318
  //   - Decision diamond label: 32 px below diamond's bottom (≈218)
  //   - Outer padding-bottom: 48
  // Take the worst case + a small buffer.
  const TRACK_MIN_H = TOP_PAD + 2 * ROW_H + 8 + 70 + 48 + 12 // = 430

  return (
    <ScaleToFit naturalW={TRACK_W + 48} naturalH={TRACK_MIN_H}>
    <div
      className="relative"
      style={{
        width: TRACK_W + 48,
        minHeight: TRACK_MIN_H,
        padding: '24px 24px 48px',
        background: 'var(--color-bg)',
      }}
    >
      {/* Title strip */}
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
            Pipeline · {pipelineLabel}
          </div>
          <div className="text-[17px] font-semibold text-[var(--color-text)] mt-0.5">Stage flow</div>
        </div>
        <div className="flex gap-3.5 items-center text-[11.5px] text-[var(--color-text-muted)]">
          <Legend dot="green" label="Done" />
          <Legend dot="blue" label="Running" />
          <Legend dot="red" label="Failed" />
          <Legend dot="muted" label="Queued" />
          <Legend dot="dash" label="Skipped" />
        </div>
      </div>

      {/* SVG track lines under cards */}
      <svg
        width={TRACK_W}
        height={TOP_PAD + 2 * ROW_H + 8}
        className="absolute pointer-events-none"
        style={{ left: 24, top: 70 }}
      >
        <defs>
          <marker id="a-arrow-grey" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--color-border-light)" />
          </marker>
          <marker id="a-arrow-blue" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--color-accent)" />
          </marker>
        </defs>
        {/* Spine */}
        <Edge from={[cardCenterX(0), rowCenterY(0)]} to={[cardCenterX(1), rowCenterY(0)]} active />
        <Edge from={[cardCenterX(1), rowCenterY(0)]} to={[decisionCenter.x - 30, decisionCenter.y]} active />
        <Edge from={[decisionCenter.x + 30, decisionCenter.y]} to={[cardCenterX(3), rowCenterY(0)]} active label="llm" labelTone="accent" />
        <Edge from={[cardCenterX(3), rowCenterY(0)]} to={[cardCenterX(6), rowCenterY(0)]} dashed />
        <Edge from={[cardCenterX(6), rowCenterY(0)]} to={[cardCenterX(7), rowCenterY(0)]} dashed />
        {/* Heuristic branch from decision down */}
        <Edge from={[decisionCenter.x, decisionCenter.y + 30]} to={[cardCenterX(3), rowCenterY(1)]} curve dashed muted label="heuristic" labelTone="muted" />
        {/* Parallel lane */}
        <Edge from={[cardCenterX(3), rowCenterY(1)]} to={[cardCenterX(4), rowCenterY(1)]} active />
        <Edge from={[cardCenterX(4), rowCenterY(1)]} to={[cardCenterX(5), rowCenterY(1)]} dashed />
        <Edge from={[cardCenterX(5), rowCenterY(1)]} to={[cardCenterX(6), rowCenterY(0)]} dashed curveUp />
        {/* Triage skipped — ghost branch off cluster */}
        <Edge from={[cardCenterX(3), rowCenterY(1) + 24]} to={[cardCenterX(4), rowCenterY(1) + 36]} ghost />
      </svg>

      {/* Cards: row 0 */}
      {(['ingestion', 'anomaly', 'rca', 'summary', 'release'] as const).map(key => {
        const stage = get(key)
        if (!stage) return null
        const { x, y } = cardLayoutFor(stage)
        return (
          <SubwayCard
            key={stage.id}
            stage={stage}
            x={x}
            y={y}
            selected={selectedId === stage.id}
            snapshot={snapshot}
            onClick={() => onSelect(stage.id)}
          />
        )
      })}

      {/* Cards: row 1 parallel lane */}
      {(['cluster', 'triage', 'flaky', 'health'] as const).map(key => {
        const stage = get(key)
        if (!stage) return null
        const { x, y, small } = cardLayoutFor(stage)
        return (
          <SubwayCard
            key={stage.id}
            stage={stage}
            x={x}
            y={y}
            small={small}
            selected={selectedId === stage.id}
            snapshot={snapshot}
            onClick={() => onSelect(stage.id)}
          />
        )
      })}

      {/* Decision diamond */}
      <button
        type="button"
        onClick={() => onSelect(DECISION_LABEL)}
        title="Routing decision recorded by the orchestrator at runtime."
        className="absolute"
        style={{
          left: decisionCenter.x + 24 - 32,
          top: 70 + decisionCenter.y - 32,
          cursor: 'pointer',
        }}
      >
        <div
          className="grid place-items-center border"
          style={{
            width: 64,
            height: 64,
            transform: 'rotate(45deg)',
            borderColor: selectedId === DECISION_LABEL ? 'rgb(163 113 247)' : 'var(--color-border-light)',
            background: selectedId === DECISION_LABEL ? 'rgba(163,113,247,.10)' : 'var(--color-bg-card)',
            boxShadow: selectedId === DECISION_LABEL ? '0 0 0 3px rgba(163,113,247,.14)' : 'none',
            borderRadius: 4,
          }}
        >
          <div style={{ transform: 'rotate(-45deg)' }} className="text-[11px] text-[rgb(163_113_247)]">◇</div>
        </div>
        <div
          className="absolute font-mono text-[10px] whitespace-nowrap left-1/2 -translate-x-1/2"
          style={{ top: -28, color: 'var(--color-accent)' }}
        >
          → {DECISION_CHOSEN}
        </div>
        <div
          className="absolute font-mono text-[10px] whitespace-nowrap left-1/2 -translate-x-1/2"
          style={{ bottom: -32, color: 'var(--color-text-muted)' }}
        >
          {DECISION_LABEL}
        </div>
      </button>
    </div>
    </ScaleToFit>
  )
}

function Legend({ dot, label }: { dot: 'green' | 'blue' | 'red' | 'muted' | 'dash'; label: string }) {
  const styles: Record<typeof dot, React.CSSProperties> = {
    green: { background: 'rgb(63 185 80)' },
    blue: { background: 'var(--color-accent)', boxShadow: '0 0 0 3px var(--color-accent-muted)' },
    red: { background: 'rgb(248 81 73)' },
    muted: { background: 'var(--color-text-faint)' },
    dash: { background: 'transparent', border: '1px dashed var(--color-text-muted)' },
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="rounded-full flex-shrink-0" style={{ width: 9, height: 9, ...styles[dot] }} />
      {label}
    </span>
  )
}

// ── RunMeta strip ──────────────────────────────────────────────────────────

function RunMeta({
  pipeline, runShortId, totalTests, failedCount, snapshot, setSnapshot,
  summaryOpen, onToggleSummary,
}: {
  pipeline: AgentPipelineRun | null
  runShortId: string
  totalTests: number | null
  failedCount: number | null
  snapshot: boolean
  setSnapshot: (b: boolean) => void
  summaryOpen: boolean
  onToggleSummary: () => void
}) {
  return (
    <div
      className="flex items-center gap-3.5 px-6 py-2.5 border-t border-b flex-wrap"
      style={{ background: 'var(--color-bg-card)', borderColor: 'var(--color-border)' }}
    >
      <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">Run</span>
      <span className="font-mono text-[11px] text-[var(--color-text-secondary)]">{runShortId}</span>
      <span className="text-[var(--color-text-faint)]">·</span>
      <span className="text-[12px] text-[var(--color-text-secondary)]">{pipeline?.workflow_type ?? 'offline'}</span>
      <span className="text-[var(--color-text-faint)]">·</span>
      <span
        className="text-[9.5px] font-semibold tracking-wider uppercase px-1.5 py-0.5 rounded-full"
        style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-muted)' }}
      >
        {(pipeline?.workflow_type ?? 'offline').toUpperCase()}
      </span>
      {totalTests != null && (
        <>
          <span className="text-[var(--color-text-faint)]">·</span>
          <span className="font-mono text-[12px] text-[var(--color-text-secondary)]">
            {totalTests.toLocaleString()} tests
          </span>
        </>
      )}
      {failedCount != null && failedCount > 0 && (
        <>
          <span className="text-[var(--color-text-faint)]">·</span>
          <span className="font-mono text-[12px]" style={{ color: 'rgb(248 81 73)' }}>
            {failedCount} failed
          </span>
        </>
      )}
      <span className="flex-1" />
      {pipeline?.test_run_id && (
        <button
          type="button"
          onClick={onToggleSummary}
          className="inline-flex items-center gap-1.5 text-[11.5px] hover:underline"
          style={{ color: 'var(--color-accent)' }}
          aria-expanded={summaryOpen}
        >
          <FileText className="h-3.5 w-3.5" />
          {summaryOpen ? 'Hide summary report' : 'Show summary report'}
          {summaryOpen
            ? <ChevronDown className="h-3 w-3" />
            : <ChevronRight className="h-3 w-3" />}
        </button>
      )}
      <span className="w-px h-[18px]" style={{ background: 'var(--color-border)' }} />
      <button
        type="button"
        onClick={() => setSnapshot(!snapshot)}
        className="text-[11.5px] px-2.5 py-1 rounded border"
        style={{
          background: snapshot ? 'var(--color-accent-muted)' : 'var(--color-bg-secondary)',
          color: snapshot ? 'var(--color-accent)' : 'var(--color-text-secondary)',
          borderColor: snapshot ? 'rgba(68,147,248,.40)' : 'var(--color-border)',
        }}
      >
        {snapshot ? 'Showing completed-run snapshot' : 'Preview completed-run snapshot'}
      </button>
    </div>
  )
}

// ── VerdictPanel + Blame strip ─────────────────────────────────────────────

function VerdictPanel({ pipeline, totalTests, failedCount, elapsedLabel }: {
  pipeline: AgentPipelineRun | null
  totalTests: number | null
  failedCount: number | null
  elapsedLabel: string
}) {
  const finishedAt = pipeline?.completed_at
    ? new Date(pipeline.completed_at).toLocaleTimeString()
    : '—'
  const passRate = totalTests && totalTests > 0
    ? Math.round(((totalTests - (failedCount ?? 0)) / totalTests) * 100)
    : null
  const blameShare = failedCount && failedCount > 0 ? 100 : 0
  const verdict = (failedCount ?? 0) > 0 ? 'CONDITIONAL GO' : 'GO'

  return (
    <div className="px-6 py-4" style={{ background: 'var(--color-bg)' }}>
      <div
        className="rounded-[14px] border p-4"
        style={{ background: 'var(--color-bg-card)', borderColor: 'var(--color-border)' }}
      >
        <div className="flex items-start gap-3 flex-wrap">
          <div className="flex-1 min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
              Build {pipeline?.test_run_id?.slice(0, 8) ?? '—'} · finished {finishedAt} · {elapsedLabel}
            </div>
            <div className="text-[17px] font-semibold text-[var(--color-text)] mt-1">
              {(failedCount ?? 0) > 0 ? `${failedCount} failure${failedCount === 1 ? '' : 's'} detected` : 'All checks passed'}
            </div>
          </div>
          <span
            className="text-[11px] font-bold px-2.5 py-1 rounded-full"
            style={{
              background: verdict === 'GO' ? 'rgba(63,185,80,.18)' : 'rgba(210,153,34,.18)',
              color: verdict === 'GO' ? 'rgb(63 185 80)' : 'rgb(210 153 34)',
            }}
          >
            {verdict}
          </span>
        </div>
        {/* 6-up metric grid */}
        <div className="grid grid-cols-6 gap-2 mt-4">
          <Metric label="Pass rate" value={passRate != null ? `${passRate}%` : '—'} tone={passRate != null && passRate < 90 ? 'amber' : undefined} />
          <Metric label="Total tests" value={totalTests != null ? totalTests.toLocaleString() : '—'} />
          <Metric label="Failed" value={failedCount != null ? String(failedCount) : '—'} tone={failedCount && failedCount > 0 ? 'red' : undefined} />
          <Metric label="Skipped" value="—" />
          <Metric label="Failure groups" value="—" />
          <Metric label="Anomalies" value="—" />
        </div>
        {/* Blame strip */}
        {(failedCount ?? 0) > 0 && (
          <div
            className="flex items-center gap-3 mt-3.5 px-3 py-2 rounded-lg border"
            style={{ background: 'var(--color-bg-secondary)', borderColor: 'var(--color-border)' }}
          >
            <AlertTriangle className="h-4 w-4" style={{ color: 'rgb(248 81 73)' }} />
            <span className="text-[12px]" style={{ color: 'var(--color-text-secondary)' }}>
              <span className="font-semibold" style={{ color: 'rgb(248 81 73)' }}>Product Bug</span> — {failedCount} failure{failedCount === 1 ? '' : 's'} ({blameShare}%)
            </span>
            <span className="flex-1" />
            <span
              className="rounded-full overflow-hidden"
              style={{ width: 80, height: 6, background: 'var(--color-bg-hover)' }}
            >
              <span
                className="block h-full"
                style={{ width: `${blameShare}%`, background: 'rgb(248 81 73)' }}
              />
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'red' | 'amber' }) {
  const color = tone === 'red' ? 'rgb(248 81 73)' : tone === 'amber' ? 'rgb(210 153 34)' : 'var(--color-text)'
  return (
    <div
      className="rounded-lg px-2 py-2.5 text-center"
      style={{ background: 'var(--color-bg-secondary)' }}
    >
      <div className="font-mono font-bold" style={{ fontSize: 18, color }}>{value}</div>
      <div className="text-[9.5px] font-semibold uppercase tracking-wider mt-0.5" style={{ color: 'var(--color-text-muted)' }}>
        {label}
      </div>
    </div>
  )
}

// ── InlineSummaryReport ────────────────────────────────────────────────────
// Lightweight inline view of the AI-generated executive summary so users
// don't have to leave /agents to read the verdict. Lazy-loaded by the parent
// — the hook is only mounted when `runId && open`. Falls back to a "Open
// full report" link for the long-tail content (citations, role actions, etc).

function InlineSummaryReport({ runId, open, onClose }: { runId: string | null; open: boolean; onClose: () => void }) {
  const { intelligence, isLoading, isError } = useRunIntelligence(open ? runId : null)
  if (!open) return null

  const exec = intelligence?.structured_summary?.executive_summary
    ?? intelligence?.structured_summary?.layer1_executive
    ?? null
  const incident = intelligence?.structured_summary?.layer2_incident ?? null
  const action = intelligence?.structured_summary?.layer4_action_plan ?? null
  const decision = intelligence?.release_decision ?? null

  const decisionTone = decision?.recommendation === 'GO'
    ? { bg: 'rgba(63,185,80,.18)', fg: 'rgb(63 185 80)' }
    : decision?.recommendation === 'CONDITIONAL_GO'
      ? { bg: 'rgba(210,153,34,.18)', fg: 'rgb(210 153 34)' }
      : decision?.recommendation === 'NO_GO'
        ? { bg: 'rgba(248,81,73,.18)', fg: 'rgb(248 81 73)' }
        : { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)' }

  return (
    <div
      className="px-6 py-4 border-t"
      style={{ background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
    >
      <div
        className="rounded-[14px] border"
        style={{ background: 'var(--color-bg-card)', borderColor: 'var(--color-border)' }}
      >
        <div
          className="flex items-center justify-between px-4 py-2.5"
          style={{ borderBottom: '1px solid var(--color-border)' }}
        >
          <div className="flex items-center gap-2">
            <FileText className="h-3.5 w-3.5 text-[var(--color-accent)]" />
            <span className="text-[12px] font-semibold text-[var(--color-text)]">Summary report · AI-generated</span>
            {decision?.recommendation && (
              <span
                className="text-[10px] font-bold px-2 py-0.5 rounded-full"
                style={{ background: decisionTone.bg, color: decisionTone.fg }}
              >
                {decision.recommendation.replace('_', ' ')}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {runId && (
              <Link
                to={`/runs/${runId}/intelligence`}
                className="text-[11px] text-[var(--color-accent)] hover:underline inline-flex items-center gap-1"
              >
                Open full report <ArrowRight className="h-3 w-3" />
              </Link>
            )}
            <button
              type="button"
              onClick={onClose}
              className="text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-1.5 py-0.5 rounded"
              title="Collapse"
            >
              ✕
            </button>
          </div>
        </div>

        <div className="p-4 space-y-4">
          {isLoading && (
            <div className="flex items-center gap-2 text-[12px] text-[var(--color-text-muted)]">
              <RefreshCw className="h-3.5 w-3.5 animate-spin" /> Loading summary…
            </div>
          )}
          {isError && (
            <p className="text-[12px]" style={{ color: 'rgb(248 81 73)' }}>
              Couldn't load the summary report. Try the full report link above.
            </p>
          )}
          {!isLoading && !isError && intelligence && !intelligence.intelligence_available && (
            <p className="text-[12px] text-[var(--color-text-muted)]">
              No AI summary has been generated for this run yet.
            </p>
          )}

          {exec && (
            <Section title="Executive summary">
              <p className="text-[12.5px] text-[var(--color-text-secondary)] whitespace-pre-wrap leading-relaxed">
                {exec}
              </p>
            </Section>
          )}

          {incident && (incident.what_failed || incident.likely_cause || incident.scope) && (
            <Section title="Incident">
              <div className="space-y-2 text-[12px]">
                {incident.what_failed && (
                  <div>
                    <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">What failed</span>
                    <p className="text-[var(--color-text-secondary)] mt-0.5">{incident.what_failed}</p>
                  </div>
                )}
                {incident.likely_cause && (
                  <div>
                    <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Likely cause</span>
                    <p className="text-[var(--color-text-secondary)] mt-0.5">{incident.likely_cause}</p>
                  </div>
                )}
                {incident.scope && (
                  <div>
                    <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Scope</span>
                    <p className="text-[var(--color-text-secondary)] mt-0.5">{incident.scope}</p>
                  </div>
                )}
              </div>
            </Section>
          )}

          {action && (action.immediate_mitigation || (action.fix_recommendations && action.fix_recommendations.length > 0)) && (
            <Section title="Action plan">
              <div className="space-y-2 text-[12px]">
                {action.immediate_mitigation && (
                  <div>
                    <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Immediate mitigation</span>
                    <p className="text-[var(--color-text-secondary)] mt-0.5">{action.immediate_mitigation}</p>
                  </div>
                )}
                {action.fix_recommendations && action.fix_recommendations.length > 0 && (
                  <div>
                    <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Fix recommendations</span>
                    <ul className="list-disc pl-5 mt-0.5 text-[var(--color-text-secondary)] space-y-0.5">
                      {action.fix_recommendations.slice(0, 5).map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </Section>
          )}

          {decision?.reasoning && (
            <Section title="Release decision">
              <p className="text-[12.5px] text-[var(--color-text-secondary)] leading-relaxed">{decision.reasoning}</p>
              {decision.blocking_issues && decision.blocking_issues.length > 0 && (
                <div className="mt-2">
                  <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Blocking issues</span>
                  <ul className="list-disc pl-5 mt-0.5 text-[12px] text-[var(--color-text-secondary)] space-y-0.5">
                    {decision.blocking_issues.map((b, i) => <li key={i}>{b}</li>)}
                  </ul>
                </div>
              )}
            </Section>
          )}

          {!isLoading && intelligence && !exec && !incident && !decision && (
            <p className="text-[12px] text-[var(--color-text-muted)]">
              The summary stage finished but produced no narrative content. Open the full report for raw stage outputs.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ── EventStrip ─────────────────────────────────────────────────────────────

interface FeedEvent {
  kind: 'started' | 'completed' | 'failed' | 'retry' | 'decision'
  stageId: string | null
  ts: number       // epoch ms — used for sorting
  at: string       // localized label — what we render
  what: string
}

// Tie-break order for events that share an exact timestamp. Newer-first list
// shows the *terminal* event above its own `started` (e.g. "Test Health · 0s"
// above "Test Health started"). Without this, ties preserve insertion order
// and we'd see "started" appear above "completed" — which reads as if the
// stage finished before it began.
const KIND_RANK: Record<FeedEvent['kind'], number> = {
  decision: 4,
  failed: 3,
  completed: 2,
  retry: 1,
  started: 0,
}

function buildFeed(stages: DisplayStage[]): FeedEvent[] {
  const events: FeedEvent[] = []
  for (const s of stages) {
    if (s.raw.started_at) {
      const d = new Date(s.raw.started_at)
      events.push({
        kind: 'started',
        stageId: s.id,
        ts: d.getTime(),
        at: d.toLocaleTimeString(),
        what: `${s.name} started`,
      })
    }
    if (s.status === 'failed' && s.raw.completed_at) {
      const d = new Date(s.raw.completed_at)
      events.push({
        kind: 'failed',
        stageId: s.id,
        ts: d.getTime(),
        at: d.toLocaleTimeString(),
        what: `${s.name} failed${s.raw.error ? ` · ${s.raw.error.slice(0, 80)}` : ''}`,
      })
    } else if (s.status === 'done' && s.raw.completed_at) {
      const d = new Date(s.raw.completed_at)
      events.push({
        kind: 'completed',
        stageId: s.id,
        ts: d.getTime(),
        at: d.toLocaleTimeString(),
        what: `${s.name} · ${s.durLabel}`,
      })
    }
  }
  events.sort((a, b) => b.ts - a.ts || KIND_RANK[b.kind] - KIND_RANK[a.kind])
  return events
}

function EventStrip({ events, onSelect }: { events: FeedEvent[]; onSelect: (id: string) => void }) {
  // Collapsed by default — the event feed is reference detail, not the focal
  // point of the page. Header acts as the toggle.
  const [open, setOpen] = useState(false)
  return (
    <div className="border-t" style={{ background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 w-full text-left px-6 py-2.5 hover:bg-[var(--color-bg-hover)] transition-colors"
        aria-expanded={open}
      >
        {open
          ? <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          : <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />}
        <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
          Event feed · live
        </span>
        <span className="text-[10.5px] text-[var(--color-text-faint)]">
          {events.length} event{events.length === 1 ? '' : 's'}
        </span>
      </button>
      {open && (
        <div className="px-6 pb-3.5">
          {events.length === 0 ? (
            <p className="py-3 text-[12px] text-[var(--color-text-muted)]">No events yet.</p>
          ) : (
            events.map((e, i) => (
              <button
                key={i}
                type="button"
                onClick={() => e.stageId && onSelect(e.stageId)}
                className="grid items-start gap-3 py-1.5 w-full text-left"
                style={{ gridTemplateColumns: '74px 1fr' }}
              >
                <span className="font-mono text-[10.5px] text-[var(--color-text-muted)]">{e.at}</span>
                <div>
                  <KindPill kind={e.kind} />
                  <span className="ml-2 text-[12px] text-[var(--color-text)]">{e.what}</span>
                </div>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}

function KindPill({ kind }: { kind: FeedEvent['kind'] }) {
  const tone =
    kind === 'failed' ? { bg: 'rgba(248,81,73,.18)', fg: 'rgb(248 81 73)' } :
    kind === 'completed' ? { bg: 'rgba(63,185,80,.18)', fg: 'rgb(63 185 80)' } :
    kind === 'decision' ? { bg: 'rgba(163,113,247,.18)', fg: 'rgb(163 113 247)' } :
    kind === 'retry' ? { bg: 'rgba(210,153,34,.18)', fg: 'rgb(210 153 34)' } :
    { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)' }
  return (
    <span className="font-mono text-[9.5px] px-1.5 py-0.5 rounded" style={{ background: tone.bg, color: tone.fg }}>
      {kind}
    </span>
  )
}

// ── RightRail ───────────────────────────────────────────────────────────────

function RightRail({ stage, decisionSelected, events }: { stage: DisplayStage | null; decisionSelected: boolean; events: FeedEvent[] }) {
  if (decisionSelected) {
    return (
      <div className="h-full overflow-y-auto p-4" style={{ background: 'var(--color-bg-card)' }}>
        <div className="flex items-center gap-2 mb-2">
          <span
            className="font-mono text-[10px] px-2 py-0.5 rounded"
            style={{ background: 'rgba(163,113,247,.18)', color: 'rgb(163 113 247)' }}
          >
            ◇ decision
          </span>
        </div>
        <h3 className="text-[16px] font-semibold text-[var(--color-text)]">{DECISION_LABEL}</h3>
        <p className="mt-1 text-[12px] text-[var(--color-text-muted)]">A branch chosen by the orchestrator at runtime.</p>
        <Section title="Outcome">
          <KV k="chosen" v={DECISION_CHOSEN} tone="green" />
          <KV k="alternatives" v="heuristic, llm" />
        </Section>
        <Section title="Rationale">
          <p className="text-[12px] text-[var(--color-text-secondary)]">
            Recorded by the orchestrator's <code className="px-1 bg-[var(--color-bg-secondary)] rounded">log_decision()</code> at the routing branch. Inspect <code className="px-1 bg-[var(--color-bg-secondary)] rounded">agent_stage_results.decision_log</code> for the full chain.
          </p>
        </Section>
      </div>
    )
  }

  if (!stage) {
    return (
      <div className="h-full grid place-items-center p-6" style={{ background: 'var(--color-bg-card)' }}>
        <p className="text-[12.5px] text-[var(--color-text-muted)] text-center max-w-xs">
          Select a stage card or the decision diamond to inspect its output, metrics, and logs.
        </p>
      </div>
    )
  }

  const stageEvents = events.filter(e => e.stageId === stage.id)
  const Icon = GLYPH_ICON[stage.glyph]

  return (
    <div className="h-full overflow-y-auto" style={{ background: 'var(--color-bg-card)' }}>
      <div className="p-4 border-b" style={{ borderColor: 'var(--color-border)' }}>
        <div className="flex items-center gap-2 mb-1.5">
          <span className="inline-flex h-4 w-4 rounded-full" style={{ background: NODE_STYLE[stage.status].dot }} />
          <span className="font-mono text-[10px] uppercase tracking-wider" style={{ color: NODE_STYLE[stage.status].fg }}>
            {stage.status}
          </span>
          <span className="font-mono text-[10px] text-[var(--color-text-muted)]">·</span>
          <span className="font-mono text-[10px] text-[var(--color-text-muted)]">{stage.durLabel}</span>
        </div>
        <h3 className="text-[16px] font-semibold text-[var(--color-text)] flex items-center gap-2">
          <Icon className="h-4 w-4" /> {stage.name}
        </h3>
        <p className="mt-1 text-[12px] text-[var(--color-text-muted)]">{stage.desc}</p>
      </div>

      <div className="p-4 space-y-4">
        {stage.status === 'failed' && stage.raw.error && (
          <div className="rounded border p-3" style={{ background: 'rgba(248,81,73,.06)', borderColor: 'rgba(248,81,73,.30)' }}>
            <div className="font-mono text-[10px] uppercase tracking-wider mb-1" style={{ color: 'rgb(248 81 73)' }}>Error</div>
            <pre className="font-mono text-[11px] text-[var(--color-text-secondary)] whitespace-pre-wrap break-all">{stage.raw.error}</pre>
          </div>
        )}

        <Section title="Metrics">
          <KV k="status" v={stage.status} tone={stage.status === 'done' ? 'green' : stage.status === 'failed' ? 'red' : undefined} />
          <KV k="started" v={stage.raw.started_at ? new Date(stage.raw.started_at).toLocaleTimeString() : '—'} />
          <KV k="completed" v={stage.raw.completed_at ? new Date(stage.raw.completed_at).toLocaleTimeString() : '—'} />
          <KV k="duration" v={stage.durLabel} />
        </Section>

        {stage.raw.result_data && (
          <Section title="Output">
            <pre
              className="rounded p-2.5 font-mono text-[11px] whitespace-pre-wrap break-all"
              style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
            >
              {JSON.stringify(stage.raw.result_data, null, 2).slice(0, 2000)}
            </pre>
          </Section>
        )}

        {stageEvents.length > 0 && (
          <Section title="Activity">
            {stageEvents.map((e, i) => (
              <div key={i} className="flex items-start gap-2 py-1">
                <span className="font-mono text-[10.5px] text-[var(--color-text-muted)]">{e.at}</span>
                <KindPill kind={e.kind} />
                <span className="text-[11.5px] text-[var(--color-text-secondary)]">{e.what}</span>
              </div>
            ))}
          </Section>
        )}
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)] mb-1.5">{title}</div>
      <div className="space-y-1">{children}</div>
    </div>
  )
}

function KV({ k, v, tone }: { k: string; v: string; tone?: 'green' | 'red' }) {
  const color = tone === 'green' ? 'rgb(63 185 80)' : tone === 'red' ? 'rgb(248 81 73)' : 'var(--color-text-secondary)'
  return (
    <div className="flex gap-2">
      <span className="text-[11px] text-[var(--color-text-muted)] w-[95px] shrink-0">{k}</span>
      <span className="font-mono text-[11px]" style={{ color }}>{v}</span>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function AgentWorkflowPage() {
  const { runId } = useParams<{ runId?: string }>()
  const navigate = useNavigate()
  const [mode, setMode] = useState<ModeTab>('live')
  const [selectedPipeline, setSelectedPipeline] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [snapshot, setSnapshot] = useState(false)
  const [summaryOpen, setSummaryOpen] = useState(false)
  const [triggerInput, setTriggerInput] = useState('')
  const [triggerSubmitting, setTriggerSubmitting] = useState(false)

  const { isQaEngineer } = usePermissions()
  const { data: aiConfig } = useAIConfig()
  const analysisMode = aiConfig?.analysis_mode ?? 'auto'

  useProjectChangeRedirect('/agents', Boolean(runId))
  useProjectChangeReset(() => {
    setSelectedPipeline(null)
    setSelectedId(null)
    setSnapshot(false)
  })

  // Always fetch ALL recent pipelines for the active project (no runId
  // filter) so the RunPicker can switch between unrelated runs even from
  // a deep link like /agents/run/{specific-uuid}. The focused pipeline is
  // resolved client-side by matching the URL's runId against this list.
  const { data: rawPipelines = [], isLoading: pipelinesLoading } = usePipelines()
  const pipelines = useMemo(() => [...rawPipelines].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  ), [rawPipelines])

  const activePipelineId = useMemo(() => {
    if (selectedPipeline) return selectedPipeline
    if (runId) {
      const match = pipelines.find(p => p.test_run_id === runId)
      if (match) return match.id
    }
    return pipelines[0]?.id ?? null
  }, [selectedPipeline, runId, pipelines])
  const activePipeline = pipelines.find(p => p.id === activePipelineId) ?? null

  const { data: stages = [], isLoading: stagesLoading } = usePipelineStages(activePipelineId)
  const { data: timeline } = usePipelineTimeline(activePipelineId)
  const { data: liveRuns = [] } = useActiveLiveRuns()

  const sourceStages = (timeline?.stages ?? stages) as AgentStageResult[]
  const displayStages = useMemo(
    () => buildDisplayStages(sourceStages, activePipeline?.started_at ?? null),
    [sourceStages, activePipeline?.started_at],
  )

  const pipelineStartedAt = activePipeline?.started_at ?? null
  const pipelineCompletedAt = activePipeline?.completed_at ?? null
  const elapsedSec = elapsedSeconds(pipelineStartedAt, pipelineCompletedAt)
  const elapsedLabel = pipelineStartedAt ? formatElapsed(elapsedSec) : '—'

  // Default selection: first running, else first failed, else null
  useEffect(() => {
    if (selectedId != null) return
    const firstRunning = displayStages.find(s => s.status === 'running')
    const firstFailed = displayStages.find(s => s.status === 'failed')
    setSelectedId(firstRunning?.id ?? firstFailed?.id ?? null)
  }, [displayStages, selectedId])

  // ESC clears selection
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setSelectedId(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const events = useMemo(() => buildFeed(displayStages), [displayStages])
  const selectedStage = displayStages.find(s => s.id === selectedId) ?? null
  const decisionSelected = selectedId === DECISION_LABEL
  const isLive = activePipeline?.status === 'running'
  const runShortId = activePipeline?.test_run_id?.slice(0, 8) ?? '—'

  // Test counts (RunMeta + VerdictPanel) live on TestRun, not on the
  // pipeline timeline. Same hook the runs/run-detail pages use; SWR
  // dedupes the request when other tabs already loaded the run.
  const { data: testRun } = useRun(activePipeline?.test_run_id)
  const totalTests = testRun?.total_tests ?? null
  const failedCount = testRun?.failed_tests ?? null

  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

  async function handleTriggerByRunId(e: React.FormEvent) {
    e.preventDefault()
    const id = triggerInput.trim()
    if (!UUID_RE.test(id)) {
      toast.error('Run ID must be a UUID. Find it on /runs.')
      return
    }
    setTriggerSubmitting(true)
    try {
      await agentService.triggerPipeline(id)
      toast.success('Pipeline queued.')
      setTriggerInput('')
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? (err as Error)?.message
        ?? 'Trigger failed'
      toast.error(detail)
    } finally {
      setTriggerSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col h-full" style={{ background: 'var(--color-bg)' }}>
      {/* TopBar */}
      <PageHeader
        title="Agent Pipeline"
        subtitle={`Workflow · Run ${runShortId}`}
        actions={
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className="text-[11px] font-medium px-2 py-1 rounded uppercase"
              style={{ background: 'rgba(163,113,247,.18)', color: 'rgb(163 113 247)' }}
            >
              {analysisMode} mode
            </span>
            {activePipeline && (
              <button
                type="button"
                onClick={() => setSummaryOpen(o => !o)}
                className="inline-flex items-center gap-1.5 text-[11.5px] px-2.5 py-1 rounded border"
                style={{
                  background: summaryOpen ? 'var(--color-accent)' : 'var(--color-accent-muted)',
                  color: summaryOpen ? 'white' : 'var(--color-accent)',
                  borderColor: 'rgba(68,147,248,.40)',
                  height: 32,
                }}
                title="Toggle the AI-generated executive summary inline"
                aria-expanded={summaryOpen}
              >
                <FileText className="h-3.5 w-3.5" />
                Summary report
                {summaryOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              </button>
            )}
            {isQaEngineer && (
              <form onSubmit={handleTriggerByRunId} className="flex items-center gap-1">
                <input
                  type="text"
                  value={triggerInput}
                  onChange={e => setTriggerInput(e.target.value)}
                  placeholder="Paste run UUID…"
                  className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-[11.5px] font-mono rounded px-2 py-1 w-[220px]"
                  disabled={triggerSubmitting}
                />
                <button
                  type="submit"
                  disabled={triggerSubmitting || !triggerInput.trim()}
                  className="inline-flex items-center justify-center h-7 w-7 rounded border text-[var(--color-text-muted)] disabled:opacity-50"
                  style={{ borderColor: 'var(--color-border)' }}
                  title="Queue pipeline for this run"
                >
                  {triggerSubmitting ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />}
                </button>
              </form>
            )}
          </div>
        }
      />

      <ModeTabs mode={mode} setMode={setMode} isLive={isLive} />

      {pipelines.length > 0 && (
        <RunPicker
          pipelines={pipelines}
          activePipelineId={activePipelineId}
          onSelect={(p) => {
            setSelectedPipeline(p.id)
            setSelectedId(null)
            navigate(`/agents/run/${p.test_run_id}`)
          }}
        />
      )}

      {mode !== 'live' ? (
        <div className="flex-1 grid place-items-center p-12">
          <div className="text-center">
            <Activity className="h-10 w-10 mx-auto text-[var(--color-text-muted)] mb-3" />
            <p className="text-[var(--color-text)] font-semibold">Coming in next iteration</p>
            <p className="text-[12px] text-[var(--color-text-muted)] mt-1 max-w-md">
              The {mode} mode is reserved for the upcoming dense-metrics / read-only-timeline / diff views.
              Switch to <span className="font-medium text-[var(--color-text-secondary)]">Live</span> to inspect this run.
            </p>
          </div>
        </div>
      ) : pipelinesLoading || stagesLoading ? (
        <div className="flex-1 grid place-items-center"><LoadingSpinner size="lg" /></div>
      ) : pipelines.length === 0 ? (
        <div className="flex-1 grid place-items-center p-12">
          <div className="text-center max-w-md">
            <Bot className="h-10 w-10 mx-auto text-[var(--color-text-muted)] mb-3" />
            <p className="text-[var(--color-text)] font-semibold">No agent pipelines yet</p>
            <p className="text-[12px] text-[var(--color-text-muted)] mt-1">
              Active analysis mode: <span className="font-mono">{analysisMode}</span>.
              {liveRuns.length > 0 && <> {liveRuns.length} live run{liveRuns.length === 1 ? '' : 's'} streaming.</>}
              {' '}Stream test results via the SDK or upload a report at <Link to="/runs" className="text-[var(--color-accent)] underline">/runs</Link>.
            </p>
          </div>
        </div>
      ) : (
        <div className="flex-1 flex min-h-0">
          <div className="flex-1 flex flex-col overflow-auto min-w-0">
            <SubwayTrack
              stages={displayStages}
              selectedId={selectedId}
              onSelect={setSelectedId}
              snapshot={snapshot}
              pipelineLabel={activePipeline?.workflow_type ?? 'offline'}
            />
            <RunMeta
              pipeline={activePipeline}
              runShortId={runShortId}
              totalTests={totalTests}
              failedCount={failedCount}
              snapshot={snapshot}
              setSnapshot={setSnapshot}
              summaryOpen={summaryOpen}
              onToggleSummary={() => setSummaryOpen(o => !o)}
            />
            {snapshot && (
              <VerdictPanel
                pipeline={activePipeline}
                totalTests={totalTests}
                failedCount={failedCount}
                elapsedLabel={elapsedLabel}
              />
            )}
            <InlineSummaryReport
              runId={activePipeline?.test_run_id ?? null}
              open={summaryOpen}
              onClose={() => setSummaryOpen(false)}
            />
            <EventStrip events={events} onSelect={setSelectedId} />
          </div>
          <aside
            className="flex-shrink-0 border-l"
            style={{ width: 360, background: 'var(--color-bg-card)', borderColor: 'var(--color-border)' }}
          >
            <RightRail stage={selectedStage} decisionSelected={decisionSelected} events={events} />
          </aside>
        </div>
      )}
    </div>
  )
}
