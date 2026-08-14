/**
 * The attribution verdict, and the evidence behind it.
 *
 * Roadmap Phase 4's surface. Roughly 84% of pass→fail transitions involve a
 * flaky test, so a bare failure list is mostly noise; this leads each failure
 * with what it appears to *be*.
 *
 * Three rules this component exists to keep:
 *
 * 1. **Every verdict renders, including `UNCERTAIN`.** That is a real answer —
 *    the backend emits it when signals disagree rather than resolving to
 *    whichever looked strongest — so it gets a badge like any other, not a
 *    blank space that reads as "nothing to see".
 * 2. **The evidence is reachable.** When a verdict disagrees with an engineer,
 *    the useful question is which input was wrong. A badge alone cannot answer
 *    that, so the five signals are one click away.
 * 3. **Nothing here hides a failure.** `LIKELY_FLAKY` never means "safe to
 *    ignore" — roughly 1 in 6 newly-flaky tests reflected a real bug — so this
 *    component only ever annotates a row that is already displayed.
 */
import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

import type { AttributionItem, AttributionVerdict } from '@/types/attribution'

/**
 * Colour per verdict. Every member has an entry, so a new verdict cannot be
 * added without deciding how it looks — the vocabulary-subset defect class.
 */
const VERDICT_CLASS: Record<AttributionVerdict, string> = {
  LIKELY_YOUR_CHANGE: 'bg-[var(--status-failed)]/15 text-[var(--status-failed)]',
  LIKELY_INFRA: 'bg-[var(--status-broken)]/15 text-[var(--status-broken)]',
  LIKELY_FLAKY: 'bg-[var(--status-flaky)]/15 text-[var(--status-flaky)]',
  // Deliberately muted rather than invisible: uncertainty is information.
  UNCERTAIN: 'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]',
}

function formatPercent(value: number | null): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 100)}%`
}

export interface AttributionVerdictBadgeProps {
  attribution: AttributionItem
  /** Compact hides the expander — used in dense table rows. */
  compact?: boolean
}

export function AttributionVerdictBadge({
  attribution,
  compact = false,
}: AttributionVerdictBadgeProps) {
  const [open, setOpen] = useState(false)
  const { inputs } = attribution

  const badge = (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${
        VERDICT_CLASS[attribution.verdict]
      }`}
      title={attribution.rationale || attribution.verdict_description}
    >
      {attribution.verdict_label}
    </span>
  )

  if (compact) return badge

  return (
    <div className="text-sm">
      <button
        type="button"
        onClick={() => setOpen((x) => !x)}
        aria-expanded={open}
        className="flex items-center gap-1.5 text-left"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        )}
        {badge}
        <span className="text-xs text-[var(--color-text-muted)]">why?</span>
      </button>

      {open && (
        <div className="mt-2 space-y-2 rounded-lg p-3 ring-1 ring-[var(--color-border)]">
          <p className="text-[var(--color-text)]">{attribution.rationale}</p>

          {/* The five composed signals. Shown together because the verdict is
              only as trustworthy as the inputs a reader can check. */}
          <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
            <div>
              <dt className="text-[var(--color-text-muted)]">New failure</dt>
              <dd className="text-[var(--color-text)]">
                {inputs.is_new_failure ? 'yes — passed in the previous run' : 'no — already failing'}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--color-text-muted)]">Flakiness</dt>
              <dd className="text-[var(--color-text)]">
                {inputs.flaky_score === null
                  ? 'not scored'
                  : `${inputs.flaky_score.toFixed(2)} (${inputs.flaky_confidence} confidence)`}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--color-text-muted)]">Co-failure cluster</dt>
              <dd className="text-[var(--color-text)]">
                {inputs.cluster_key
                  ? `${inputs.cluster_key} · ${inputs.cluster_size} tests · ${(
                      inputs.cluster_cause_family ?? 'unknown'
                    ).replace(/_/g, ' ')}`
                  : 'none'}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--color-text-muted)]">Change overlap</dt>
              <dd className="text-[var(--color-text)]">
                {inputs.change_overlap === null
                  ? 'no commit range known'
                  : formatPercent(inputs.change_overlap)}
              </dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-[var(--color-text-muted)]">Classifier calibration</dt>
              <dd className="text-[var(--color-text)]">
                {inputs.calibration_mode}
                {inputs.calibration_specificity !== null
                  ? ` · measured specificity ${formatPercent(inputs.calibration_specificity)}`
                  : ' · not measured on this project yet'}
              </dd>
            </div>
          </dl>

          {inputs.changed_files.length > 0 && (
            <div className="text-xs">
              <span className="text-[var(--color-text-muted)]">Changed files: </span>
              <span className="font-mono text-[var(--color-text)]">
                {inputs.changed_files.slice(0, 5).join(', ')}
                {inputs.changed_files.length > 5
                  ? ` (+${inputs.changed_files.length - 5} more)`
                  : ''}
              </span>
            </div>
          )}

          {/* Rendered, not buried in a tooltip. A reader must not be able to
              take a verdict as permission to stop looking. */}
          <p className="text-xs italic text-[var(--color-text-muted)]">{attribution.policy}</p>
        </div>
      )}
    </div>
  )
}

export default AttributionVerdictBadge
