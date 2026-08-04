/**
 * Shared AI trust chrome (US-15.1, PMF Epic 15).
 *
 * Every AI-produced conclusion in the product renders through this one
 * component so the trust contract is identical everywhere:
 *
 *   - an "AI-suggested" badge (always — an AI output is never presented as
 *     a verdict),
 *   - an OPTIONAL confidence + calibration-basis chip. Optional is load-
 *     bearing: the chat copilot has no confidence at all and the component
 *     must not imply one exists. When a confidence IS shown it always
 *     carries its basis, because rules-engine confidences are self-declared
 *     `heuristic_estimate` (backend/app/services/confidence_bands.py — there
 *     is no labeled corpus), and nothing here may read as calibrated,
 *   - routing provenance: which engine actually answered (rules / ML / LLM)
 *     and which model, plus a LOUD fallback notice when the requested engine
 *     was unavailable and something else answered instead,
 *   - evidence links,
 *   - an optional low-confidence state, and
 *   - confirm / correct actions — rendered only when an `analysisId` exists
 *     to attach the feedback to (dead buttons are worse than no buttons).
 *
 * `basisLabel` lives here as the single source of truth: it used to be
 * copy-pasted verbatim into KindEvidence, AIAnalysisPanel and
 * InvestigatorCockpit.
 *
 * Backend contract note: the provenance block is being built in parallel
 * (`mode_used` / `mode_requested` / `fallback_from` / `fallback_reason`
 * alongside the existing `llm_provider` / `llm_model`). EVERY field is
 * treated as optional — `normalizeProvenance` returns null when nothing
 * usable is present, and older analyses that legitimately carry no
 * provenance degrade silently to no provenance line.
 */
import type { ReactNode } from 'react'
import { AlertTriangle, Bot, Check, ExternalLink, Pencil } from 'lucide-react'
import { clsx } from 'clsx'

// ── Calibration basis (single source of truth) ────────────────────────────

/**
 * Every basis value any backend surface emits today —
 * 'empirical' | 'heuristic_estimate' | 'human_corrected' | 'llm_weighted'.
 * Deliberately widened to `string`: new backend values must degrade to the
 * honest "estimated" default instead of failing the type-check and tempting
 * someone to cast them to 'empirical'.
 */
export type ConfidenceBasisValue = string

export interface BasisDescriptor {
  label: string
  title: string
}

/**
 * Label + tooltip for a confidence's calibration basis. The default — for
 * null, undefined, and any value we don't recognise — is "estimated", never
 * "calibrated": an unlabelled confidence is an uncalibrated confidence.
 */
export function basisLabel(basis: ConfidenceBasisValue | null | undefined): BasisDescriptor {
  if (basis === 'empirical') {
    return {
      label: 'calibrated',
      title: 'Calibrated confidence — equals measured precision on labeled eval samples',
    }
  }
  if (basis === 'human_corrected') {
    return {
      label: 'human-corrected',
      title: 'Confidence pinned by an authoritative human correction of this classification',
    }
  }
  if (basis === 'llm_weighted') {
    return {
      label: 'llm-weighted',
      title: 'LLM-weighted confidence — model self-assessment, not empirically calibrated',
    }
  }
  return {
    label: 'estimated',
    title:
      'Estimated heuristic confidence — deterministic re-weighing of existing signals, not empirically calibrated',
  }
}

/** Calibration-basis chip. Always rendered next to a confidence figure. */
export function BasisChip({ basis }: { basis: ConfidenceBasisValue | null | undefined }) {
  const d = basisLabel(basis)
  return (
    <span
      data-testid="ai-basis-chip"
      className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] cursor-help"
      title={d.title}
    >
      {d.label}
    </span>
  )
}

// ── Routing provenance ────────────────────────────────────────────────────

/** Normalised routing provenance. All fields optional by contract. */
export interface AIProvenance {
  /** Engine that actually produced the output ("rules" / "ml" / "llm" / …). */
  modeUsed?: string | null
  /** Engine the caller asked for, when it differs from what answered. */
  modeRequested?: string | null
  /** Engine we fell back FROM. Set ⇒ render the fallback notice. */
  fallbackFrom?: string | null
  fallbackReason?: string | null
  llmProvider?: string | null
  llmModel?: string | null
}

const ENGINE_LABELS: Record<string, string> = {
  rules: 'rules engine',
  rule: 'rules engine',
  rules_engine: 'rules engine',
  heuristic: 'rules engine',
  ml: 'ML classifier',
  ml_classifier: 'ML classifier',
  classifier: 'ML classifier',
  llm: 'LLM',
  local_llm: 'local LLM',
  hybrid: 'hybrid pipeline',
}

/** Human label for an engine id; unknown ids render as-is (underscores out). */
export function engineLabel(mode: string | null | undefined): string {
  if (!mode) return 'unknown engine'
  return ENGINE_LABELS[mode.toLowerCase()] ?? mode.replace(/_/g, ' ')
}

function str(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

/**
 * Build an `AIProvenance` from whatever the backend actually sent. Accepts
 * both the new nested `provenance` block and the long-standing flat
 * `llm_provider` / `llm_model` fields, and returns **null** when nothing
 * usable is present so callers can simply pass the result through.
 */
export function normalizeProvenance(raw: unknown): AIProvenance | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  const nested =
    r.provenance && typeof r.provenance === 'object'
      ? (r.provenance as Record<string, unknown>)
      : {}
  const pick = (...keys: string[]): string | null => {
    for (const k of keys) {
      const hit = str(nested[k]) ?? str(r[k])
      if (hit) return hit
    }
    return null
  }
  const out: AIProvenance = {
    modeUsed: pick('mode_used', 'modeUsed', 'engine'),
    modeRequested: pick('mode_requested', 'modeRequested'),
    fallbackFrom: pick('fallback_from', 'fallbackFrom'),
    fallbackReason: pick('fallback_reason', 'fallbackReason'),
    llmProvider: pick('llm_provider', 'llmProvider'),
    llmModel: pick('llm_model', 'llmModel'),
  }
  const hasAny = Object.values(out).some(v => v != null)
  return hasAny ? out : null
}

// ── Evidence ──────────────────────────────────────────────────────────────

export interface AIEvidenceLink {
  /** Short source name — "splunk", "stack trace", "commit abc1234". */
  label: string
  /** Optional excerpt / detail shown next to the label. */
  detail?: string | null
  /** Optional href. Absent ⇒ rendered as plain text, not a dead link. */
  href?: string | null
  /** Open in a new tab (external evidence). */
  external?: boolean
}

// ── The component ─────────────────────────────────────────────────────────

export interface AISuggestionProps {
  /** What the AI suggested — "root cause", "failure kind", or a richer node
   *  such as a kind pill. Rendered next to the badge. */
  label?: ReactNode
  /** Override the badge copy. Defaults to "AI-suggested" — override only
   *  for an equally hedged phrasing ("AI-classified"), never a verdict. */
  badgeText?: string
  badgeTitle?: string
  /** 0-100. Omit entirely when the surface has no confidence — the chrome
   *  then renders none, rather than inventing one. */
  confidence?: number | null
  confidenceBasis?: ConfidenceBasisValue | null
  /** Explicit backend low-confidence marker. Renders the needs-review state. */
  lowConfidence?: boolean
  /** Routing provenance. Pass `normalizeProvenance(response)`. */
  provenance?: AIProvenance | null
  evidence?: AIEvidenceLink[]
  /** Feedback target. Confirm/correct render ONLY when this is set. */
  analysisId?: string | null
  onConfirm?: () => void
  onCorrect?: () => void
  /** Disables the action buttons while a submit is in flight. */
  busy?: boolean
  /** Already-confirmed — swaps the confirm button for a recorded note. */
  confirmed?: boolean
  /** The AI-produced body. */
  children?: ReactNode
  /** Drops the surrounding card chrome (for embedding inside one). */
  bare?: boolean
  className?: string
  'data-testid'?: string
}

/** "AI-suggested" pill. Exported for surfaces that only need the marker. */
export function AIBadge({ text = 'AI-suggested', title, compact }: {
  text?: string
  title?: string
  compact?: boolean
}) {
  return (
    <span
      data-testid="ai-suggested-badge"
      className="inline-flex items-center gap-1 rounded-full font-semibold uppercase whitespace-nowrap"
      style={{
        fontSize: compact ? 9.5 : 10,
        letterSpacing: 'var(--tracking-wide)',
        padding: compact ? '1px 6px' : '2px 8px',
        background: 'var(--color-bg-hover)',
        border: '1px solid var(--color-border)',
        color: 'var(--color-text-secondary)',
      }}
      title={
        title ??
        'Produced by the AI pipeline — a suggestion for a human to confirm, not a verdict.'
      }
    >
      <Bot aria-hidden className="h-2.5 w-2.5" />
      {text}
    </span>
  )
}

export default function AISuggestion({
  label,
  badgeText,
  badgeTitle,
  confidence,
  confidenceBasis,
  lowConfidence,
  provenance,
  evidence,
  analysisId,
  onConfirm,
  onCorrect,
  busy,
  confirmed,
  children,
  bare,
  className,
  'data-testid': testId = 'ai-suggestion',
}: AISuggestionProps) {
  const hasConfidence = typeof confidence === 'number' && Number.isFinite(confidence)
  const showActions = Boolean(analysisId) && Boolean(onConfirm || onCorrect)
  const prov = provenance ?? null
  const provBits: string[] = []
  if (prov?.modeUsed) provBits.push(engineLabel(prov.modeUsed))
  if (prov?.llmProvider || prov?.llmModel) {
    provBits.push([prov.llmProvider, prov.llmModel].filter(Boolean).join(' · '))
  }
  const showFallback = Boolean(prov?.fallbackFrom)

  return (
    <section
      data-testid={testId}
      aria-label="AI-suggested output"
      className={clsx(
        !bare && 'rounded-xl p-3.5',
        'space-y-2.5',
        className,
      )}
      style={
        bare
          ? undefined
          : { background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }
      }
    >
      {/* Header row — badge + label on the left, confidence on the right. */}
      <div className="flex items-center justify-between gap-3 flex-wrap pb-1.5 border-b border-[var(--color-border)]">
        <span className="flex items-center gap-2 min-w-0 flex-wrap">
          <AIBadge text={badgeText} title={badgeTitle} />
          {label != null && label !== false && (
            <span className="text-[11px] text-[var(--color-text-muted)] inline-flex items-center gap-1.5 min-w-0">
              {label}
            </span>
          )}
          {lowConfidence && (
            <span
              data-testid="ai-low-confidence"
              className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full"
              style={{
                background: 'color-mix(in srgb, var(--gate-conditional) 12%, transparent)',
                border: '1px solid color-mix(in srgb, var(--gate-conditional) 32%, transparent)',
                color: 'var(--gate-conditional)',
              }}
              title="The pipeline flagged this output as low confidence — a human should review it before it is acted on."
            >
              <AlertTriangle aria-hidden className="h-2.5 w-2.5" />
              low confidence — needs human review
            </span>
          )}
        </span>

        {hasConfidence && (
          <span className="flex items-center gap-2" data-testid="ai-confidence">
            <BasisChip basis={confidenceBasis} />
            <span className="text-sm font-bold tabular-nums text-[var(--color-text)]">
              {Math.round(confidence as number)}%
            </span>
          </span>
        )}
      </div>

      {/* Routing provenance — which engine actually answered. */}
      {provBits.length > 0 && (
        <p
          data-testid="ai-provenance"
          className="text-[11px] text-[var(--color-text-muted)] m-0"
          title="Which engine produced this output, and on which model."
        >
          Produced by {provBits.join(' · ')}
          {prov?.modeRequested && prov.modeRequested !== prov.modeUsed && !showFallback && (
            <> (requested: {engineLabel(prov.modeRequested)})</>
          )}
        </p>
      )}

      {/* Fallback notice — loud, because the answer is not what was asked for. */}
      {showFallback && (
        <p
          data-testid="ai-fallback-notice"
          className="flex items-start gap-1.5 text-[11px] m-0 rounded-md px-2.5 py-1.5"
          style={{
            background: 'color-mix(in srgb, var(--gate-conditional) 10%, transparent)',
            border: '1px solid color-mix(in srgb, var(--gate-conditional) 30%, transparent)',
            color: 'var(--color-text-secondary)',
          }}
        >
          <AlertTriangle
            aria-hidden
            className="h-3 w-3 flex-none mt-0.5"
            style={{ color: 'var(--gate-conditional)' }}
          />
          <span>
            The {engineLabel(prov?.fallbackFrom)} was unavailable — this came from the{' '}
            {engineLabel(prov?.modeUsed ?? 'rules')} instead.
            {prov?.fallbackReason ? ` ${prov.fallbackReason}` : ''}
          </span>
        </p>
      )}

      {children}

      {/* Evidence links. */}
      {evidence && evidence.length > 0 && (
        <div data-testid="ai-evidence">
          <p className="text-[10px] uppercase tracking-wider font-medium text-[var(--color-text-muted)] m-0 mb-1">
            Evidence
          </p>
          <ul className="list-none m-0 p-0 space-y-1">
            {evidence.map((ev, i) => (
              <li key={`${ev.label}-${i}`} className="text-[11.5px] leading-snug">
                {ev.href ? (
                  <a
                    href={ev.href}
                    {...(ev.external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
                    className="inline-flex items-center gap-1 font-medium hover:underline"
                    style={{ color: 'var(--color-accent)' }}
                  >
                    {ev.label}
                    {ev.external && <ExternalLink aria-hidden className="h-2.5 w-2.5" />}
                  </a>
                ) : (
                  <span className="font-medium text-[var(--color-text)]">{ev.label}</span>
                )}
                {ev.detail && (
                  <span className="text-[var(--color-text-muted)]"> — {ev.detail}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Confirm / correct — only with somewhere to send the feedback. */}
      {showActions && (
        <div className="flex items-center gap-2 flex-wrap pt-0.5" data-testid="ai-feedback-actions">
          {onConfirm &&
            (confirmed ? (
              <span
                className="inline-flex items-center gap-1 text-[11.5px] text-[var(--color-text-muted)]"
                data-testid="ai-confirmed"
              >
                <Check aria-hidden className="h-3 w-3" style={{ color: 'var(--status-passed)' }} />
                Confirmed — recorded for the training loop
              </span>
            ) : (
              <button
                type="button"
                onClick={onConfirm}
                disabled={busy}
                className="inline-flex items-center gap-1.5 text-[11.5px] font-medium px-2.5 py-1 rounded-md border transition-colors disabled:opacity-50 hover:bg-[var(--color-bg-hover)]"
                style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}
              >
                <Check aria-hidden className="h-3 w-3" />
                Confirm
              </button>
            ))}
          {onCorrect && !confirmed && (
            <button
              type="button"
              onClick={onCorrect}
              disabled={busy}
              className="inline-flex items-center gap-1.5 text-[11.5px] font-medium px-2.5 py-1 rounded-md border transition-colors disabled:opacity-50 hover:bg-[var(--color-bg-hover)]"
              style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}
            >
              <Pencil aria-hidden className="h-3 w-3" />
              Correct
            </button>
          )}
        </div>
      )}
    </section>
  )
}
