/**
 * Shared AI trust chrome (US-15.1, PMF Epic 15).
 *
 * Every AI-produced conclusion in the product renders through this one
 * component so the trust contract is identical everywhere:
 *
 *   - a provenance badge (always; never a verdict). It says "AI-suggested" only
 *     when a language model answered (D-13): rules read "Rule-based suggestion",
 *     the classifier "Classifier suggestion" (`producedByWord`),
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
 * 'empirical' | 'heuristic_estimate' | 'human_corrected' | 'llm_weighted' |
 * 'project_hint' (FAIL-10: a QA lead's classification hint answered).
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
  if (basis === 'project_hint') {
    return {
      label: 'project hint',
      title: "Classification set by a project hint (a QA lead's rule), not by a model",
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
      className="text-2xs px-2 py-0.5 rounded-full bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] cursor-help"
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
  hint: 'project hint',
  // D-160: the analysis router's small-model tier.
  slm: 'small model',
  // D-149: the ReAct agent — the full investigation the fast classifier stands in for after a timeout.
  react_agent: 'full investigation',
  react: 'full investigation',
  // D-06: a reviewer's correction (routing_metadata.analysis_mode = "human_corrected").
  human_corrected: 'human review',
}

/** Human label for an engine id; unknown ids render as-is (underscores out). */
export function engineLabel(mode: string | null | undefined): string {
  if (!mode) return 'unknown engine'
  return ENGINE_LABELS[mode.toLowerCase()] ?? mode.replace(/_/g, ' ')
}

/** Badge text, tooltip and accessible name for what produced an output. */
export interface ProducedBy {
  badge: string
  title: string
  ariaLabel: string
}

const HEDGE = 'a suggestion for a human to confirm, not a verdict.'
const MODEL_ENGINES = new Set(['llm', 'local_llm', 'slm', 'react_agent', 'react', 'hybrid'])
const RULE_ENGINES = new Set(['rules', 'rule', 'rules_engine', 'heuristic'])
const CLASSIFIER_ENGINES = new Set(['ml', 'ml_classifier', 'classifier'])

/**
 * D-13: the badge names what answered. "AI" only when a language model did; a rules or classifier
 * result is never called AI. No engine recorded (the chat copilot, the fixer: model-only features)
 * keeps "AI-suggested"; an engine id we do not know reads as a plain "Suggestion".
 */
export function producedByWord(mode: string | null | undefined): ProducedBy {
  const m = mode?.trim().toLowerCase() ?? ''
  if (!m || MODEL_ENGINES.has(m)) {
    return { badge: 'AI-suggested', title: `Written by a language model: ${HEDGE}`, ariaLabel: 'AI-suggested output' }
  }
  if (RULE_ENGINES.has(m)) {
    return { badge: 'Rule-based suggestion', title: `Produced by the rules engine: ${HEDGE}`, ariaLabel: 'Rule-based suggestion' }
  }
  if (CLASSIFIER_ENGINES.has(m)) {
    return { badge: 'Classifier suggestion', title: `Produced by the classifier: ${HEDGE}`, ariaLabel: 'Classifier suggestion' }
  }
  if (m === 'hint') {
    return { badge: 'From a project hint', title: "Set by a project hint (a QA lead's rule), not by a model.", ariaLabel: 'From a project hint' }
  }
  if (m === 'human_corrected') {
    return { badge: 'Corrected by a person', title: 'A reviewer corrected this result.', ariaLabel: 'Corrected by a person' }
  }
  return { badge: 'Suggestion', title: `Produced by ${engineLabel(mode)}: ${HEDGE}`, ariaLabel: 'Suggestion' }
}

/** True when a language model produced the output (so a model name is worth printing). */
export function isModelEngine(mode: string | null | undefined): boolean {
  return MODEL_ENGINES.has(mode?.trim().toLowerCase() ?? '')
}

/**
 * D-149 / D-160: what happened to the engine that did not answer, as the middle of
 * "The {engine} {clause}, so this came from …". Only these codes reach the sentence; any other
 * code goes to the notice's tooltip, never into the visible text. `llm_error: <detail>` matches
 * on the part before the colon.
 */
const FALLBACK_REASON_CLAUSES: Record<string, string> = {
  slm_call_failed: 'did not answer',
  llm_unavailable: 'could not be reached',
  llm_error: 'returned an error',
  llm_timeout: 'did not answer in time',
  react_timeout: 'ran out of time',
  budget: 'is over its usage budget',
  step_llm_budget: 'used up its allowed model calls',
  tier_pinned: 'is not used, because the AI settings fix the engine',
}

/** The plain-words clause for a known reason code, or null for an unknown one (or none). */
export function fallbackReasonClause(reason: string | null | undefined): string | null {
  if (!reason) return null
  const code = reason.split(':')[0].trim().toLowerCase()
  return FALLBACK_REASON_CLAUSES[code] ?? null
}

/**
 * S-A-1: the stored-analysis read fills a missing provider / model with "unknown". Such a value
 * names nothing, so it is treated as absent everywhere the trust chrome prints a model.
 */
export function isKnownValue(value: unknown): value is string {
  if (typeof value !== 'string') return false
  const v = value.trim().toLowerCase()
  return v !== '' && v !== 'unknown' && v !== 'none'
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
  // S-A-1: "unknown" is the read's filler for a missing provider / model, not a name.
  const pickKnown = (...keys: string[]): string | null => {
    for (const k of keys) {
      for (const v of [nested[k], r[k]]) {
        if (isKnownValue(v)) return v.trim()
      }
    }
    return null
  }
  const out: AIProvenance = {
    modeUsed: pick('mode_used', 'modeUsed', 'engine'),
    modeRequested: pick('mode_requested', 'modeRequested'),
    fallbackFrom: pick('fallback_from', 'fallbackFrom'),
    fallbackReason: pick('fallback_reason', 'fallbackReason'),
    llmProvider: pickKnown('llm_provider', 'llmProvider'),
    llmModel: pickKnown('llm_model', 'llmModel'),
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
  /** Override the badge copy. Defaults to the words for `provenance.modeUsed`
   *  (`producedByWord`); override only with an equally hedged phrasing, never a verdict. */
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

/** Provenance pill ("AI-suggested", "Rule-based suggestion", ...). Exported for surfaces that only need the marker. */
export function AIBadge({ text, title, compact, mode }: {
  text?: string
  title?: string
  compact?: boolean
  /** Engine that answered; picks the words when `text` / `title` are not given (D-13). */
  mode?: string | null
}) {
  const words = producedByWord(mode)
  return (
    <span
      data-testid="ai-suggested-badge"
      className="inline-flex items-center gap-1 rounded-full eyebrow uppercase whitespace-nowrap"
      style={{
        padding: compact ? '1px 6px' : '2px 8px',
        background: 'var(--color-bg-hover)',
        border: '1px solid var(--color-border)',
        color: 'var(--color-text-secondary)',
      }}
      title={title ?? words.title}
    >
      <Bot aria-hidden className="h-2.5 w-2.5" />
      {text ?? words.badge}
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
  const modelBits = [prov?.llmProvider, prov?.llmModel].filter(isKnownValue)
  if (modelBits.length > 0) provBits.push(modelBits.join(' · '))
  // S-A-1: a stub no engine wrote is stored with a null engine and a fallback reason. Say so, rather
  // than "Produced by LLM · unknown · unknown".
  const noEngineAnswered =
    prov != null && !prov.modeUsed && modelBits.length === 0 &&
    Boolean(prov.fallbackFrom || prov.fallbackReason || prov.modeRequested)
  // D-149: "The LLM was unavailable — this came from the LLM instead" contradicts itself; an engine
  // cannot fall back to itself, so no notice then.
  const fromId = prov?.fallbackFrom?.toLowerCase() ?? null
  const usedId = prov?.modeUsed?.toLowerCase() ?? null
  const sameEngine = fromId !== null && fromId === usedId
  const showFallback = fromId !== null && !sameEngine
  const reasonClause = fallbackReasonClause(prov?.fallbackReason)
  // D-160: the backend's reason is "<code>: <detail>" (e.g. "llm_timeout: LLM timed out after 120 s").
  // The sentence carries only the plain words for a known code; the full raw string goes to the
  // tooltip, never into the visible text.
  const fallbackTitle = prov?.fallbackReason ? `Reason recorded: ${prov.fallbackReason}` : undefined
  // D-13: the badge, its tooltip and the section's accessible name say what answered.
  const words: ProducedBy = noEngineAnswered
    ? { badge: 'Needs review', title: 'No engine produced this output; a human should review it.', ariaLabel: 'Output for review' }
    : producedByWord(prov?.modeUsed)

  return (
    <section
      data-testid={testId}
      aria-label={words.ariaLabel}
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
          <AIBadge text={badgeText ?? words.badge} title={badgeTitle ?? words.title} />
          {label != null && label !== false && (
            <span className="text-2xs text-[var(--color-text-muted)] inline-flex items-center gap-1.5 min-w-0">
              {label}
            </span>
          )}
          {lowConfidence && (
            <span
              data-testid="ai-low-confidence"
              className="inline-flex items-center gap-1 text-2xs px-2 py-0.5 rounded-full"
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
      {noEngineAnswered ? (
        <p
          data-testid="ai-provenance"
          className="text-2xs text-[var(--color-text-muted)] m-0"
          title="No rules, classifier or model produced this output; it is a placeholder for a human to review."
        >
          No engine answered
        </p>
      ) : provBits.length > 0 && (
        <p
          data-testid="ai-provenance"
          className="text-2xs text-[var(--color-text-muted)] m-0"
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
          title={fallbackTitle}
          className="flex items-start gap-1.5 text-2xs m-0 rounded-md px-2.5 py-1.5"
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
            The {engineLabel(prov?.fallbackFrom)} {reasonClause ?? 'was unavailable'}
            {prov?.modeUsed
              ? <>, so this came from the {engineLabel(prov.modeUsed)} instead.</>
              : <>, and no other engine answered.</>}
          </span>
        </p>
      )}

      {children}

      {/* Evidence links. */}
      {evidence && evidence.length > 0 && (
        <div data-testid="ai-evidence">
          {/* leading-tight: fit remedy 4 (W29d) — the eyebrow's own 16 px line raised the test-case page's fold 1 px on CI. */}
          <p className="eyebrow uppercase leading-tight text-[var(--color-text-muted)] m-0 mb-1">
            Evidence
          </p>
          <ul className="list-none m-0 p-0 space-y-1">
            {evidence.map((ev, i) => (
              <li key={`${ev.label}-${i}`} className="text-xs leading-snug">
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
                className="inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)]"
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
                className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-md border transition-colors disabled:opacity-50 hover:bg-[var(--color-bg-hover)]"
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
              className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-md border transition-colors disabled:opacity-50 hover:bg-[var(--color-bg-hover)]"
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
