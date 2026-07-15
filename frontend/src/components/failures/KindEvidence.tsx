/**
 * Failure-kind badge + evidence popover (AI-4).
 *
 * The kind triad (US-9.1/9.2) is an AI-derived classification — every
 * surface keeps the "AI-classified" provenance copy. AI-4 upgrades the
 * badge with an on-demand evidence popover: the deterministic checklist
 * (memory recall, infra shape, history pattern, status signal, classifier)
 * that backs the kind, with per-check verdicts and the confidence basis
 * ('human-corrected' when pinned by a correction, 'estimated' otherwise —
 * mirrors backend/app/services/kind_evidence.py).
 *
 * Evidence is fetched lazily — only when the popover opens — via
 * useKindEvidence (SWR), keyed by test_case_id (run-detail rows) or
 * project + fingerprint (/failures aggregates).
 */
import { useEffect, useRef, useState } from 'react'
import { kindDef } from '@/utils/failureKind'
import { useKindEvidence } from '@/hooks/useMetrics'
import type { KindEvidence, KindEvidenceCheck } from '@/types/analytics'

/** Compact color-coded pill for a failure kind. Tokens come from the
 *  existing palette (--kind-* aliases in index.css — no new hex). */
export function FailureKindBadge({ kind, compact }: { kind: string | null | undefined; compact?: boolean }) {
  const d = kindDef(kind)
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full font-semibold uppercase whitespace-nowrap"
      style={{
        fontSize: compact ? 9.5 : 10,
        letterSpacing: 'var(--tracking-wide)',
        padding: compact ? '1px 6px' : '2px 8px',
        background: `color-mix(in srgb, ${d.color} 14%, transparent)`,
        border: `1px solid color-mix(in srgb, ${d.color} 35%, transparent)`,
        color: d.color,
      }}
      title={`AI-classified failure kind: ${d.label} — ${d.desc}`}
    >
      <span aria-hidden className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: d.color }} />
      {d.label}
    </span>
  )
}

/** Fixed display labels for the checklist checks (backend vocabulary). */
export const KIND_CHECK_LABELS: Record<string, string> = {
  memory_recall: 'Memory recall',
  infra_shape: 'Infra shape',
  history_pattern: 'History pattern',
  status_signal: 'Status signal',
  classifier: 'Classifier',
}

const VERDICT_STYLES: Record<string, { symbol: string; color: string; label: string }> = {
  supports: { symbol: '✓', color: 'var(--status-passed)', label: 'supports' },
  contradicts: { symbol: '✗', color: 'var(--gate-no-go)', label: 'contradicts' },
  neutral: { symbol: '·', color: 'var(--color-text-muted)', label: 'neutral' },
  unavailable: { symbol: '—', color: 'var(--color-text-faint)', label: 'unavailable' },
}

function basisLabel(basis: string | null | undefined): { label: string; title: string } {
  if (basis === 'human_corrected') {
    return {
      label: 'human-corrected',
      title: 'Confidence pinned by an authoritative human correction of this classification',
    }
  }
  if (basis === 'empirical') {
    return {
      label: 'calibrated',
      title: 'Calibrated confidence — equals measured precision on labeled eval samples',
    }
  }
  return {
    label: 'estimated',
    title: 'Estimated heuristic confidence — deterministic re-weighing of existing signals, not empirically calibrated',
  }
}

function EvidenceCheckRow({ row }: { row: KindEvidenceCheck }) {
  const style = VERDICT_STYLES[row.verdict] ?? VERDICT_STYLES.neutral
  return (
    <li className="flex items-start gap-2 py-1" data-testid={`kind-check-${row.check}`}>
      <span
        aria-label={style.label}
        className="w-4 text-center font-bold shrink-0"
        style={{ color: style.color, fontSize: 11 }}
      >
        {style.symbol}
      </span>
      <span className="min-w-0">
        <span className="text-[11px] font-semibold text-[var(--color-text)]">
          {KIND_CHECK_LABELS[row.check] ?? row.check}
        </span>
        <span className="text-[10.5px]" style={{ color: style.color, marginLeft: 6 }}>
          {style.label}
        </span>
        <p className="text-[11px] text-[var(--color-text-muted)] m-0 mt-0.5">{row.detail}</p>
      </span>
    </li>
  )
}

export function KindEvidencePanel({ evidence }: { evidence: KindEvidence }) {
  const basis = basisLabel(evidence.confidence_basis)
  return (
    <div data-testid="kind-evidence-panel">
      <div className="flex items-center justify-between gap-3 pb-2 border-b border-[var(--color-border)]">
        <span className="flex items-center gap-2">
          <FailureKindBadge kind={evidence.kind} compact />
          <span
            className="text-[10px] text-[var(--color-text-faint)]"
            title="Kinds are derived by the failure analyzer from its category verdict and the failure shape — corrections feed the training loop."
          >
            AI-classified
          </span>
        </span>
        <span className="flex items-center gap-2">
          <span
            className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] cursor-help"
            title={basis.title}
          >
            {basis.label}
          </span>
          <span className="text-sm font-bold tabular-nums text-[var(--color-text)]">
            {evidence.confidence}%
          </span>
        </span>
      </div>
      <ul className="list-none m-0 p-0 pt-1.5">
        {evidence.checks.map(row => (
          <EvidenceCheckRow key={row.check} row={row} />
        ))}
      </ul>
    </div>
  )
}

/**
 * Kind badge that opens the evidence checklist on click. Falls back to the
 * plain badge when no lookup key is available or no evidence exists.
 */
export function KindBadgeWithEvidence({
  kind,
  testFingerprint,
  testCaseId,
  compact,
}: {
  kind: string | null | undefined
  testFingerprint?: string | null
  testCaseId?: string | null
  compact?: boolean
}) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLSpanElement>(null)
  const { data, isLoading } = useKindEvidence({ testFingerprint, testCaseId }, open)

  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  const hasLookup = Boolean(testCaseId || testFingerprint)
  if (!hasLookup) return <FailureKindBadge kind={kind} compact={compact} />

  return (
    <span ref={containerRef} className="relative inline-flex">
      <button
        type="button"
        aria-expanded={open}
        aria-label="Show failure-kind evidence (AI-classified)"
        className="bg-transparent border-0 p-0 m-0 cursor-pointer inline-flex"
        onClick={e => {
          // Rows hosting the badge often navigate on click — the popover
          // toggle must not bubble into that.
          e.stopPropagation()
          setOpen(o => !o)
        }}
      >
        <FailureKindBadge kind={kind} compact={compact} />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Failure-kind evidence checklist"
          className="absolute z-50 rounded-xl shadow-lg"
          onClick={e => e.stopPropagation()}
          style={{
            top: 'calc(100% + 6px)',
            right: 0,
            width: 320,
            maxWidth: '80vw',
            background: 'var(--color-bg-card)',
            border: '1px solid var(--color-border)',
            padding: '10px 12px',
            textAlign: 'left',
          }}
        >
          {isLoading && (
            <p className="text-[11px] text-[var(--color-text-muted)] m-0">Loading evidence…</p>
          )}
          {!isLoading && data?.kind_evidence && <KindEvidencePanel evidence={data.kind_evidence} />}
          {!isLoading && data && !data.kind_evidence && (
            <p className="text-[11px] text-[var(--color-text-muted)] m-0">
              No analysis evidence recorded for this failure yet — the kind shown is derived
              from the stored category only (AI-classified).
            </p>
          )}
        </div>
      )}
    </span>
  )
}
