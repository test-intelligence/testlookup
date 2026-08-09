/**
 * RightRail — 360px-wide details panel that sits beside the ComputeCanvas.
 *
 * Three states drive the body:
 *   - stage selected: KV table + tabbed view (Overview / Output / Activity / Logs)
 *   - decision selected: chosen vs not-taken branches + rationale
 *   - nothing selected: placeholder prompting the user to click a node
 *
 * The selection state is owned by the parent (one ``selectedId`` string that
 * may refer to a stage *or* the decision id) so node + diamond clicks share
 * the same UX surface.
 */
import { useState } from 'react'
import { clsx } from 'clsx'
import { CheckCircle2, Loader2, XCircle, ChevronRight, Sparkles } from 'lucide-react'
import type { ComputeStage, ComputeDecision, ComputeEdgeEvent, SelectedId } from './types'

interface RightRailProps {
  selectedId: SelectedId
  stages: ComputeStage[]
  decision: ComputeDecision | null
  /** Stage-scoped event list (used by the Activity tab). */
  events?: ComputeEdgeEvent[]
}

const STATUS_TONE: Record<ComputeStage['status'], { dot: string; pillBg: string; pillText: string; label: string }> = {
  done:    { dot: 'var(--status-passed)',          pillBg: 'color-mix(in srgb, var(--status-passed) 14%, transparent)',  pillText: 'var(--status-passed)', label: 'DONE' },
  running: { dot: 'var(--color-accent)', pillBg: 'var(--color-accent-muted)', pillText: 'var(--color-accent)', label: 'RUNNING' },
  failed:  { dot: 'var(--status-failed)',          pillBg: 'color-mix(in srgb, var(--status-failed) 14%, transparent)',  pillText: 'var(--status-failed)', label: 'FAILED' },
  skipped: { dot: 'var(--color-text-muted)', pillBg: 'var(--color-bg-secondary)', pillText: 'var(--color-text-muted)', label: 'SKIPPED' },
  pending: { dot: 'var(--color-text-muted)', pillBg: 'var(--color-bg-secondary)', pillText: 'var(--color-text-muted)', label: 'PENDING' },
}

function Blob({ children }: { children: React.ReactNode }) {
  return (
    <pre
      className="rounded-md border p-2.5 text-[11px] font-mono whitespace-pre-wrap break-words"
      style={{
        background: 'var(--color-bg)',
        borderColor: 'var(--color-border)',
        color: 'var(--color-text-secondary)',
      }}
    >
      {children}
    </pre>
  )
}

function KV({ k, v, tone }: { k: string; v: React.ReactNode; tone?: 'ok' | 'bad' }) {
  const valueColor =
    tone === 'ok' ? 'var(--status-passed)'
    : tone === 'bad' ? 'var(--status-failed)'
    : 'var(--color-text)'
  return (
    <div className="flex gap-3 text-[11.5px] py-1">
      <dt className="text-[var(--color-text-muted)] w-[95px] shrink-0">{k}</dt>
      <dd className="font-mono" style={{ color: valueColor }}>{v}</dd>
    </div>
  )
}

type Tab = 'overview' | 'output' | 'activity' | 'logs'

function StageBody({ stage, events }: { stage: ComputeStage; events: ComputeEdgeEvent[] }) {
  const [tab, setTab] = useState<Tab>('overview')
  const stageEvents = events.filter(e => e.stage === stage.id)
  const confTone: 'ok' | 'bad' | undefined = (() => {
    if (stage.metrics.confidence == null) return undefined
    const n = parseFloat(String(stage.metrics.confidence))
    if (!Number.isFinite(n)) return undefined
    return n > 80 ? 'ok' : undefined
  })()
  const tone = STATUS_TONE[stage.status]

  return (
    <>
      {/* Head — status dot + pill + duration, then name, then desc. */}
      <div className="px-3.5 pt-3.5 pb-3 border-b border-[var(--color-border)]">
        <div className="flex items-center gap-1.5 text-[10.5px] uppercase tracking-[0.08em]">
          <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: tone.dot }} aria-hidden />
          <span
            className="px-1.5 py-0.5 rounded"
            style={{ color: tone.pillText, background: tone.pillBg }}
          >
            {tone.label}
          </span>
          {stage.dur && (
            <span className="text-[var(--color-text-muted)]">· {stage.dur}</span>
          )}
        </div>
        <h3 className="mt-1.5 text-[16px] font-semibold text-[var(--color-text)] m-0">{stage.name}</h3>
        <p className="mt-1 text-[11.5px] text-[var(--color-text-muted)] m-0">{stage.desc}</p>
      </div>

      {/* Tab bar */}
      <div className="flex gap-3 px-3.5 border-b border-[var(--color-border)] text-[11.5px]">
        {(['overview', 'output', 'activity', 'logs'] as Tab[]).map(t => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={clsx(
              'py-2 capitalize border-b-2',
              tab === t
                ? 'text-[var(--color-text)] border-[var(--color-accent)]'
                : 'text-[var(--color-text-muted)] border-transparent hover:text-[var(--color-text)]',
            )}
          >
            {t}
            {t === 'activity' && stageEvents.length > 0 && (
              <span className="ml-1 text-[10px] text-[var(--color-text-muted)]">({stageEvents.length})</span>
            )}
          </button>
        ))}
      </div>

      {/* Body — scrollable */}
      <div className="px-3.5 py-3 overflow-auto flex-1">
        {tab === 'overview' && (
          <>
            {stage.status === 'failed' && stage.error && (
              <div className="mb-3">
                <div className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-muted)] mb-1">Error</div>
                <Blob>{stage.error}</Blob>
              </div>
            )}
            {stage.status === 'skipped' && stage.skipReason && (
              <div className="mb-3">
                <div className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-muted)] mb-1">Skipped</div>
                <p className="text-[12.5px] text-[var(--color-text-secondary)]">{stage.skipReason}</p>
              </div>
            )}
            <dl className="m-0">
              <KV k="status" v={stage.status} />
              <KV k="confidence" v={stage.metrics.confidence ?? '—'} tone={confTone} />
              <KV k="evidence" v={stage.metrics.evidence ?? '—'} />
              <KV k="tokens" v={stage.metrics.tokens ?? '—'} />
              <KV k="cost" v={stage.metrics.cost ?? '—'} />
              <KV k="duration" v={stage.dur ?? '—'} />
            </dl>
          </>
        )}
        {tab === 'output' && (
          stage.output
            ? <Blob>{stage.output}</Blob>
            : <p className="text-[12.5px] text-[var(--color-text-muted)]">No output recorded for this stage.</p>
        )}
        {tab === 'activity' && (
          stageEvents.length === 0
            ? <p className="text-[12.5px] text-[var(--color-text-muted)]">No events for this stage yet.</p>
            : (
              <ul className="space-y-2 m-0 p-0 list-none">
                {stageEvents.map((e, i) => {
                  const icon =
                    e.kind === 'completed' ? <CheckCircle2 className="w-3.5 h-3.5 text-[var(--status-passed)]" /> :
                    e.kind === 'failed'    ? <XCircle      className="w-3.5 h-3.5 text-[var(--status-failed)]" /> :
                    e.kind === 'started'   ? <Loader2      className="w-3.5 h-3.5 text-[var(--color-accent)]" /> :
                    e.kind === 'retry'     ? <Sparkles     className="w-3.5 h-3.5 text-[var(--status-broken)]" /> :
                                              <ChevronRight className="w-3.5 h-3.5 text-[var(--color-text-muted)]" />
                  return (
                    <li key={`${e.stage}-${e.at}-${i}`} className="flex items-start gap-2 text-[12px]">
                      <span className="mt-0.5 shrink-0">{icon}</span>
                      <div>
                        <div className="text-[var(--color-text)]">{e.what}</div>
                        <div className="text-[10.5px] text-[var(--color-text-muted)]">{e.at}</div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )
        )}
        {tab === 'logs' && (
          stage.logs
            ? <Blob>{stage.logs}</Blob>
            : <p className="text-[12.5px] text-[var(--color-text-muted)]">No logs captured for this stage.</p>
        )}
      </div>
    </>
  )
}

function DecisionBody({ decision }: { decision: ComputeDecision }) {
  return (
    <div className="px-3.5 py-3.5 overflow-auto">
      <span
        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] uppercase tracking-[0.08em]"
        style={{ background: 'var(--color-purple-soft, color-mix(in srgb, var(--status-flaky) 14%, transparent))', color: 'var(--color-purple, var(--status-flaky))' }}
      >
        ◇ Decision
      </span>
      <h3 className="mt-2 text-[16px] font-semibold text-[var(--color-text)] m-0">{decision.label}</h3>
      <p className="mt-1 text-[11.5px] text-[var(--color-text-muted)] m-0">
        A branch chosen by the orchestrator at runtime.
      </p>

      <div className="mt-3 text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-muted)]">Outcome</div>
      <dl className="m-0 mb-3">
        <KV
          k="chosen"
          v={decision.chosen ?? <span className="text-[var(--color-text-muted)]">pending…</span>}
          tone={decision.chosen ? 'ok' : undefined}
        />
        {decision.at && <KV k="at" v={decision.at} />}
      </dl>

      <div className="mt-2 text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-muted)] mb-1">Branches</div>
      <div className="space-y-1.5 mb-3">
        {decision.chosen && (
          <div
            className="flex items-center justify-between px-2 py-1.5 rounded border text-[11.5px] font-mono"
            style={{
              background: 'var(--color-accent-muted)',
              borderColor: 'color-mix(in srgb, var(--color-accent) 40%, transparent)',
              color: 'var(--color-accent)',
            }}
          >
            <span>taken</span>
            <span>{decision.chosen}</span>
          </div>
        )}
        {decision.alternatives
          .filter(a => a !== decision.chosen)
          .map(alt => (
            <div
              key={alt}
              className="flex items-center justify-between px-2 py-1.5 rounded border text-[11.5px] font-mono"
              style={{
                background: 'var(--color-bg-secondary)',
                borderColor: 'var(--color-border)',
                color: 'var(--color-text-muted)',
              }}
            >
              <span>not taken</span>
              <span>{alt}</span>
            </div>
          ))}
      </div>

      {decision.rationale && (
        <>
          <div className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-muted)] mb-1">Rationale</div>
          <p className="text-[12.5px] text-[var(--color-text-secondary)] m-0">{decision.rationale}</p>
        </>
      )}
    </div>
  )
}

export default function RightRail({ selectedId, stages, decision, events = [] }: RightRailProps) {
  return (
    <aside
      className="flex flex-col border-l shrink-0"
      style={{
        width: 360,
        background: 'var(--color-bg-card)',
        borderColor: 'var(--color-border)',
      }}
    >
      {(() => {
        if (selectedId === null) {
          return (
            <div className="flex-1 flex items-center justify-center px-7 py-7 text-center">
              <p className="text-[12px] text-[var(--color-text-muted)] m-0">
                Select a stage in the flow to inspect it.
              </p>
            </div>
          )
        }
        if (decision && selectedId === decision.id) {
          return <DecisionBody decision={decision} />
        }
        const stage = stages.find(s => s.id === selectedId)
        if (!stage) {
          return (
            <div className="flex-1 flex items-center justify-center px-7 py-7 text-center">
              <p className="text-[12px] text-[var(--color-text-muted)] m-0">
                Selection not found — pick another node.
              </p>
            </div>
          )
        }
        return <StageBody stage={stage} events={events} />
      })()}
    </aside>
  )
}
